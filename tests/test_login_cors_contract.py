"""DB-free contract tests for the production login path (2026-09-08 incident).

These tests pin the security contract of the login request path WITHOUT touching
PostgreSQL: the origin gate, CSRF, cookie flags, CORS preflight, and header
hygiene all run before any database work, and every credential-verifying
function used here is a narrow test double. Database-backed end-to-end login is
covered separately by tests/test_origin_login.py (Flask client + real WSGI) and
tests/test_gunicorn_factory_boot.py (gunicorn factory subprocess).

Production facts these tests encode (verified against the live service):
- apex.viability.in and apex-flow-7mr9.onrender.com served the same stale build;
  its origin gate answered POST /api/auth/login with 403
  {"error":"CSRF","message":"Cross-origin request blocked"} even for the
  SAME-ORIGIN apex POST, because the app computed its own origin over plain
  http (no TRUST_PROXY behind the TLS edge) and the apex origin was not in the
  cross-origin allowlist.
- The corrected contract (backend/app.py::_origin_matches_request): a
  well-formed Origin equal to the request's own scheme/host/port is same-origin
  and never needs the allowlist; genuine cross-origin access needs the exact
  allowlist entry; everything else is 403. No wildcard CORS anywhere.
"""
import secrets

import pytest
from cryptography.fernet import Fernet

import backend.app as backend_app
import backend.routes as backend_routes

APEX_ORIGIN = 'https://apex.viability.in'
PARTNER_ORIGIN = 'https://portal.partner.example'
ATTACKER_ORIGIN = 'https://attacker.example'


def _env(monkeypatch, **overrides):
    """Boot the real create_app() with production-shaped config, no database.

    validate_database() is a no-op here; all requests in this module stop
    before (or are mocked away from) any SQL.
    """
    monkeypatch.setattr(backend_app, 'validate_database', lambda: None)
    cfg = {
        'APP_ENV': overrides.pop('APP_ENV', 'production'),
        'SECRET_KEY': secrets.token_hex(32),
        'DATABASE_URL': 'postgresql://apex_app:pw@db.example.invalid:5432/apex?sslmode=verify-full',
        'OUTBOX_ENCRYPTION_KEY': Fernet.generate_key().decode(),
        'IDEMPOTENCY_HASH_KEYS': [secrets.token_hex(32), secrets.token_hex(32)],
        'ALLOWED_ORIGINS': [APEX_ORIGIN],   # canonical production origin
        'TRUST_PROXY': True,                 # canonical edge rewrites forwarded headers
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'SESSION_COOKIE_SECURE': True,
    }
    cfg.update(overrides)
    return backend_app.create_app(cfg)


def _login_ok(monkeypatch):
    """Narrow double for backend.routes.authenticate_user: success path.

    Mirrors the session rotation the real authenticate_user performs on
    success (clear + new sid + fresh CSRF token) so the Set-Cookie contract of
    the login response is observable without a database."""
    def fake(email, password, ip='unknown'):
        from flask import session
        assert email and password is not None
        session.clear()
        session['sid'] = 'probe-' + secrets.token_urlsafe(24)
        session['csrf_token'] = secrets.token_urlsafe(32)
        session.permanent = True
        return ({'user_id': 'USR001', 'name': 'Probe Admin', 'email': email,
                 'memberships': [{'tenant_id': 't', 'role': 'ADMIN'}]}, None)
    monkeypatch.setattr(backend_routes, 'authenticate_user', fake)


def _login_fail(monkeypatch):
    def fake(email, password, ip='unknown'):
        return None, 'Invalid email or password'
    monkeypatch.setattr(backend_routes, 'authenticate_user', fake)


def _headers(host=APEX_ORIGIN.split('//')[1], proto='https', origin=None):
    h = {'Host': host}
    if proto:
        h['X-Forwarded-Proto'] = proto
    if origin:
        h['Origin'] = origin
    return h


def _bootstrap(client, origin=None, host='apex.viability.in', proto='https'):
    r = client.open('/api/auth/csrf', headers=_headers(host=host, proto=proto, origin=origin))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['data']['csrf_token']


def _post_login(client, origin, token, host='apex.viability.in', proto='https',
                body=None, extra=None):
    return client.open('/api/auth/login', method='POST', headers={
        **_headers(host=host, proto=proto, origin=origin),
        'X-CSRF-Token': token, **(extra or {}),
    }, json=body or {'email': 'admin@apexflow.com', 'password': 'pw'})


# ── 1. same-origin login (the reported production defect) ───────────────────

def test_same_origin_login_accepted_without_allowlisting_apex(monkeypatch):
    """THE incident case: apex origin NOT in the cross-origin allowlist, but the
    request IS same-origin (Origin == request origin after ProxyFix restores
    https). Must succeed — a same-origin POST is not cross-site and must not
    depend on the cross-origin allowlist."""
    env_app = _env(monkeypatch, ALLOWED_ORIGINS=['https://unrelated.example'])
    c = env_app.test_client()
    token = _bootstrap(c, origin=APEX_ORIGIN)
    _login_ok(monkeypatch)
    r = _post_login(c, APEX_ORIGIN, token)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()['success'] is True


def test_same_origin_login_ok_when_origin_allowlisted_without_proxy(monkeypatch):
    """TRUST_PROXY=0 (plain-http app behind TLS edge): an https Origin cannot be
    recognized as same-origin, so the canonical origin must be allowlisted. This
    is the fail-closed operational fallback documented in OPERATIONS.md."""
    env_app = _env(monkeypatch, TRUST_PROXY=False,
                   ALLOWED_ORIGINS=[APEX_ORIGIN])
    c = env_app.test_client()
    token = _bootstrap(c, origin=APEX_ORIGIN, proto=None)
    _login_ok(monkeypatch)
    r = _post_login(c, APEX_ORIGIN, token, proto=None)
    assert r.status_code == 200, r.get_data(as_text=True)


def test_same_origin_login_rejected_when_misconfigured(monkeypatch):
    """Exact reproduction of the production failure: proxy not trusted AND the
    canonical apex origin absent from the allowlist -> the same-origin POST is
    rejected with the exact message users reported. Pins the failure mode so it
    is caught by CI instead of only in production."""
    env_app = _env(monkeypatch, TRUST_PROXY=False,
                   ALLOWED_ORIGINS=['https://unrelated.example'])
    c = env_app.test_client()
    token = _bootstrap(c, origin=APEX_ORIGIN, proto=None)
    _login_ok(monkeypatch)
    r = _post_login(c, APEX_ORIGIN, token, proto=None)
    assert r.status_code == 403
    body = r.get_json()
    assert body['error'] == 'CSRF'
    assert body['message'] == 'Cross-origin request blocked'


# ── 3. invalid origins rejected / fail closed ────────────────────────────────

@pytest.mark.parametrize('origin', [
    ATTACKER_ORIGIN,
    'https://apex.viability.in.evil.example',
    'http://apex.viability.in',        # scheme downgrade is cross-origin
    'https://apex.viability.in:8443',  # port mismatch is cross-origin
    'not-a-url',
])
def test_invalid_or_cross_origin_post_rejected(monkeypatch, origin):
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    token = _bootstrap(c, origin=APEX_ORIGIN)
    r = _post_login(c, origin, token)
    assert r.status_code == 403, origin
    assert r.get_json()['error'] == 'CSRF'


# ── 4. OPTIONS / preflight (narrow CORS only for genuine cross-origin) ───────

def test_preflight_allowlisted_partner_gets_exact_headers(monkeypatch):
    env_app = _env(monkeypatch, ALLOWED_ORIGINS=[PARTNER_ORIGIN])
    c = env_app.test_client()
    r = c.open('/api/auth/login', method='OPTIONS', headers={
        'Origin': PARTNER_ORIGIN,
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type,x-csrf-token',
    })
    assert r.status_code in (200, 204)
    acao = r.headers.get('Access-Control-Allow-Origin')
    assert acao == PARTNER_ORIGIN, acao          # exact origin, never '*'
    assert r.headers.get('Access-Control-Allow-Credentials') == 'true'
    allowed_headers = (r.headers.get('Access-Control-Allow-Headers') or '').lower()
    assert 'content-type' in allowed_headers and 'x-csrf-token' in allowed_headers
    allowed_methods = (r.headers.get('Access-Control-Allow-Methods') or '').upper()
    assert 'POST' in allowed_methods


def test_preflight_unknown_origin_gets_no_cors_headers(monkeypatch):
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    r = c.open('/api/auth/login', method='OPTIONS', headers={
        'Origin': ATTACKER_ORIGIN,
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type,x-csrf-token',
    })
    assert r.headers.get('Access-Control-Allow-Origin') is None


def test_preflight_from_same_origin_never_wildcard(monkeypatch):
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    r = c.open('/api/auth/login', method='OPTIONS', headers={
        'Origin': APEX_ORIGIN,
        'Access-Control-Request-Method': 'POST',
        'X-Forwarded-Proto': 'https', 'Host': 'apex.viability.in',
    })
    acao = r.headers.get('Access-Control-Allow-Origin')
    assert acao != '*'
    assert acao in (None, APEX_ORIGIN)


# ── 5. credentials / cookies ────────────────────────────────────────────────

def test_session_cookie_flags_production(monkeypatch):
    """Set-Cookie on the CSRF bootstrap: HttpOnly, Secure, SameSite=Lax, host-only
    (no Domain) — browsers never need cross-site cookie sending."""
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    r = c.open('/api/auth/csrf', headers=_headers(origin=APEX_ORIGIN))
    assert r.status_code == 200
    cookie = r.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in cookie
    assert 'Secure' in cookie
    assert 'SameSite=Lax' in cookie
    assert 'Domain=' not in cookie
    assert 'Path=/' in cookie


def test_login_success_rotates_session_cookie(monkeypatch):
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    token = _bootstrap(c, origin=APEX_ORIGIN)
    _login_ok(monkeypatch)
    r = _post_login(c, APEX_ORIGIN, token)
    assert r.status_code == 200
    set_cookie = r.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in set_cookie and 'SameSite=Lax' in set_cookie and 'Secure' in set_cookie


def test_login_failure_returns_401(monkeypatch):
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    token = _bootstrap(c, origin=APEX_ORIGIN)
    _login_fail(monkeypatch)
    r = _post_login(c, APEX_ORIGIN, token)
    assert r.status_code == 401
    assert r.get_json()['success'] is False


# ── 6. CSRF behavior ────────────────────────────────────────────────────────

def test_login_without_csrf_token_rejected(monkeypatch):
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    _bootstrap(c, origin=APEX_ORIGIN)          # establish a session
    _login_ok(monkeypatch)
    r = _post_login(c, APEX_ORIGIN, token='')
    assert r.status_code == 403
    assert r.get_json()['error'] == 'CSRF'


def test_login_with_wrong_csrf_token_rejected(monkeypatch):
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    _bootstrap(c, origin=APEX_ORIGIN)
    r = _post_login(c, APEX_ORIGIN, token='forged-token')
    assert r.status_code == 403
    assert r.get_json()['error'] == 'CSRF'


# ── 7/9. no wildcard CORS anywhere; frontend uses relative /api only ─────────

def test_no_wildcard_access_control_anywhere(monkeypatch):
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    token = _bootstrap(c, origin=APEX_ORIGIN)
    _login_ok(monkeypatch)
    responses = [
        c.open('/api/auth/csrf', headers=_headers(origin=APEX_ORIGIN)),
        _post_login(c, APEX_ORIGIN, token),
        c.open('/login.html'),
        c.open('/api/auth/login', method='OPTIONS', headers={
            'Origin': PARTNER_ORIGIN, 'Access-Control-Request-Method': 'POST'}),
    ]
    for r in responses:
        acao = r.headers.get('Access-Control-Allow-Origin')
        assert acao != '*', acao


def test_csp_connect_src_self_on_login_page(monkeypatch):
    """The login page's CSP connect-src 'self' makes any cross-origin fetch from
    the apex page a browser-level error — a second independent guarantee that
    production browsers cannot call a provider origin."""
    env_app = _env(monkeypatch)
    c = env_app.test_client()
    r = c.open('/login.html')
    assert r.status_code == 200
    csp = r.headers.get('Content-Security-Policy', '')
    assert "connect-src 'self'" in csp


def test_frontend_api_base_is_relative_and_login_posts_to_auth_login():
    from pathlib import Path
    app_js = Path('frontend/js/app.js').read_text()
    auth_js = Path('frontend/js/auth.js').read_text()
    assert "const API_BASE = '/api';" in app_js
    assert "fetch(`${API_BASE}${endpoint}`" in app_js
    assert "fetchAPI('/auth/login'" in auth_js
    text = (app_js + auth_js).lower()
    for banned in ('https://', 'http://', '//apex-flow', 'onrender'):
        assert banned not in text, f'frontend JS contains absolute URL fragment: {banned}'
