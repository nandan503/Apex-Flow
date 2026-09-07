from functools import wraps
from flask import session, request
from werkzeug.security import check_password_hash
from backend.database import get_db_connection
from backend.models import row_to_dict
from backend.utils import error_response
from backend.logger import (
    log_login_success, log_login_failure,
    log_auth_required, log_forbidden
)


# ── Auth Decorators ────────────────────────────────────────────────────────────

def login_required(f):
    """Reject any request that does not have a valid server-side session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            log_auth_required(
                endpoint=request.endpoint or request.path,
                ip=request.remote_addr
            )
            return error_response('Authentication required', 401)
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    """Reject authenticated users whose role is not in the allowed set."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('user_id'):
                log_auth_required(
                    endpoint=request.endpoint or request.path,
                    ip=request.remote_addr
                )
                return error_response('Authentication required', 401)
            if session.get('role') not in roles:
                log_forbidden(
                    user_id=session.get('user_id', '?'),
                    role=session.get('role', '?'),
                    endpoint=request.endpoint or request.path,
                    ip=request.remote_addr
                )
                return error_response(
                    f"Forbidden — requires role: {' or '.join(roles)}", 403
                )
            return f(*args, **kwargs)
        return decorated
    return decorator


# ── Auth Service Functions ─────────────────────────────────────────────────────

def authenticate_user(email, password, ip='unknown'):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user_row = cursor.fetchone()
    conn.close()

    if not user_row:
        log_login_failure(email=email, ip=ip, reason='user_not_found')
        return None, "Invalid email or password"

    user = row_to_dict(user_row)
    if not check_password_hash(user['password_hash'], password):
        log_login_failure(email=email, ip=ip, reason='bad_password')
        return None, "Invalid email or password"

    log_login_success(
        email=email,
        user_id=user['user_id'],
        role=user['role'],
        ip=ip
    )

    # Never return the password hash
    user.pop('password_hash', None)
    return user, None


def get_user_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, name, email, role, phone, created_at FROM users WHERE user_id = ?",
        (user_id,)
    )
    user = row_to_dict(cursor.fetchone())
    conn.close()
    return user
