"""Authentication is global; roles and ownership belong to memberships, not cookies."""
import hashlib
import hmac
import ipaddress
import secrets
from functools import wraps

from flask import session, request, g, current_app
from werkzeug.security import check_password_hash, generate_password_hash

from backend.database import identity_session
from backend.authz import Caller
from backend.errors import AuthzError, AppError
from backend.utils import error_response

_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))

# Login-limit contract (docs/SECURITY_MODEL.md "Login limit contract"):
# - Account bucket: failed attempts only, DB-enforced in fn_login_material
#   (atomic upsert, shared across all instances/providers), reset on success.
# - Source bucket: failed attempts only, app-side atomic upsert. The source is
#   the canonicalized TCP peer; with TRUST_PROXY=1 it is the rightmost
#   proxy-appended X-Forwarded-For value (ProxyFix). Unparseable -> 'unknown'.
_ACCOUNT_LIMIT = 5   # enforced in migration 002 fn_login_material (count > 5)
_SOURCE_LIMIT = 30   # failed logins per source per 1-minute window


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _principal():
    sid = session.get('sid')
    if not isinstance(sid, str):
        return None
    with identity_session() as conn, conn.cursor() as cur:
        cur.execute('''SELECT u.user_id, u.name, u.email, u.phone, u.created_at
                       FROM auth_sessions s JOIN users u ON u.user_id=s.user_id
                       WHERE s.token_hash=%s AND s.expires_at > now() AND u.active''', (token_hash(sid),))
        row = cur.fetchone()
        return dict(row) if row else None


def session_authenticated():
    return _principal() is not None


def load_session_caller():
    if getattr(g, 'caller', None):
        return g.caller
    user = _principal()
    if not user:
        session.clear()
        return None
    with identity_session() as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.principal_id', %s, true)", (user['user_id'],))
        cur.execute('SELECT tenant_id, role, customer_id, driver_id FROM memberships WHERE user_id=%s ORDER BY tenant_id',
                    (user['user_id'],))
        memberships = [dict(r) for r in cur.fetchall()]
    selector = request.headers.get('X-Tenant-ID')
    selected = next((m for m in memberships if m['tenant_id'] == selector), None) if selector else (
        memberships[0] if len(memberships) == 1 else None)
    if selected is None:
        raise AuthzError('An authorized tenant must be selected')
    caller = Caller(user_id=user['user_id'], name=user['name'], email=user['email'], **selected)
    g.caller = caller
    return caller


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not load_session_caller():
            return error_response('Authentication required', 401)
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if g.caller.role not in roles:
                return error_response('Insufficient privilege', 403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def _bucket_digest(label, value):
    # Bucket keys are keyed HMAC digests: no emails or IPs stored in the table.
    return hmac.new(current_app.secret_key.encode(), f'{label}:{value}'.encode(), hashlib.sha256).hexdigest()


def _source_identity(ip):
    """Canonical source identity for the source bucket (F-2 contract).

    TRUST_PROXY=0: this is the TCP peer — X-Forwarded-For is never read, so a
    client cannot influence it. TRUST_PROXY=1: ProxyFix already replaced the
    peer with the rightmost proxy-appended XFF value. Anything unparseable
    collapses into the conservative shared 'unknown' bucket."""
    try:
        return str(ipaddress.ip_address(ip))
    except (TypeError, ValueError):
        return 'unknown'


def _record_source_failure(cur, source):
    cur.execute('''INSERT INTO login_attempts(bucket, count, expires_at) VALUES (%s, 1, now()+interval '1 minute')
                   ON CONFLICT (bucket) DO UPDATE SET
                   count=CASE WHEN login_attempts.expires_at <= now() THEN 1 ELSE login_attempts.count+1 END,
                   expires_at=CASE WHEN login_attempts.expires_at <= now() THEN now()+interval '1 minute' ELSE login_attempts.expires_at END''',
                (_bucket_digest('ip', source),))


def authenticate_user(email, password, ip='unknown'):
    email = email.strip().lower()
    source = _source_identity(ip)
    with identity_session() as conn, conn.cursor() as cur:
        # Source pre-check reads the failures-only bucket; the atomic increment
        # happens on the failure path so successes never consume source budget.
        cur.execute('SELECT count FROM login_attempts WHERE bucket=%s AND expires_at > now()',
                    (_bucket_digest('ip', source),))
        row = cur.fetchone()
        if row and row[0] >= _SOURCE_LIMIT:
            raise AppError('Too many login attempts', 429, 'RATE_LIMITED')
        # Account budget and credential material are fetched in one atomic,
        # DB-enforced call; password hashes are not directly readable by the
        # runtime role (migration 002).
        cur.execute('''SELECT limited, attempt_count, out_user_id, out_name, out_email,
                              out_active, out_password_hash
                       FROM fn_login_material(%s, %s)''',
                    (_bucket_digest('account', email), email))
        grant = cur.fetchone()
        if grant['limited']:
            raise AppError('Too many login attempts', 429, 'RATE_LIMITED')
        valid = check_password_hash(grant['out_password_hash'] or _DUMMY_HASH, password)
        if not grant['out_user_id'] or not valid or not grant['out_active']:
            _record_source_failure(cur, source)
            return None, 'Invalid email or password'
        # Success: reset the caller's own account bucket (failures-only model).
        cur.execute('SELECT fn_login_success(%s)', (_bucket_digest('account', email),))
        revoke = session.get('sid')
        if revoke:
            cur.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (token_hash(revoke),))
        sid = secrets.token_urlsafe(32)
        cur.execute("INSERT INTO auth_sessions(token_hash, user_id, expires_at) VALUES (%s,%s,now()+interval '8 hours')",
                    (token_hash(sid), grant['out_user_id']))
        cur.execute("SELECT set_config('app.principal_id', %s, true)", (grant['out_user_id'],))
        cur.execute('SELECT tenant_id, role FROM memberships WHERE user_id=%s ORDER BY tenant_id', (grant['out_user_id'],))
        memberships = [dict(m) for m in cur.fetchall()]
        user = {'user_id': grant['out_user_id'], 'name': grant['out_name'], 'email': grant['out_email'], 'memberships': memberships}
        if len(memberships) == 1:
            user.update(memberships[0])
    from backend.logger import log_login_success
    log_login_success(email=email, user_id=user['user_id'], role='MEMBERSHIP_SCOPED', ip=ip)
    session.clear()
    session['sid'] = sid
    session['csrf_token'] = secrets.token_urlsafe(32)
    session.permanent = True
    return user, None


def logout_session():
    sid = session.get('sid')
    if sid:
        with identity_session() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (token_hash(sid),))
    session.clear()


def get_user_by_id(user_id):
    # /me is the only consumer; don't expose a generic user lookup API.
    caller = load_session_caller()
    if not caller or caller.user_id != user_id:
        return None
    from dataclasses import asdict
    return asdict(caller)
