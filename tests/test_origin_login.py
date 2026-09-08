"""Production login/origin failure (apex.viability.in → login → 403
'Cross-origin request blocked') — reproduction, contract, and regression tests.

Production topology (verified via DNS): apex.viability.in → CNAME
apex-flow-7mr9.onrender.com → Cloudflare edge → Render → Flask. The SAME origin
serves the page and /api, so the browser login POST is same-origin — yet the
old gate compared Origin only against the cross-origin ALLOWED_ORIGINS list
and rejected it. These tests pin the corrected semantics:

- A well-formed Origin equal to the request's own scheme/host/port is
  same-origin and never requires the cross-origin allowlist.
- Genuine cross-origin mutations still require the strict allowlist.
- CSRF (token + cookie) remains fully enforced on every mutation.
"""
import http.client
import json
import threading

import pytest

from backend.app import create_app
from tests.conftest import TEST_PASSWORD

APEX_ORIGIN = 'https://apex.viability.in'


def make_app(app, **extra):
    cfg = {k: app.config[k] for k in ('DATABASE_URL', 'SECRET_KEY', 'IDEMPOTENCY_HASH_KEYS',
                                      'OUTBOX_ENCRYPTION_KEY', 'TEST_ADMIN_URL')}
    # Production topology fidelity: behind Cloudflare → Render the app runs
    # with TRUST_PROXY=1 (documented deployment requirement), so this is the
    # default here too. The apex origin is deliberately NOT allowlisted:
    # every acceptance below proves the same-origin rule, not the allowlist.
    cfg.update({'APP_ENV': 'test', 'TESTING': True, 'TRUST_PROXY': True,
                'ALLOWED_ORIGINS': ['https://unrelated-partner.example']})
    cfg.update(extra)
    created = create_app(cfg)
    created.config['TEST_ADMIN_URL'] = cfg['TEST_ADMIN_URL']
    return created


CANONICAL_HOST = 'apex.viability.in'


def host_client(app):
    """Client with the conftest auto-CSRF disabled: every header a real browser
    sends (Host, Origin, X-CSRF-Token) is explicit in these tests."""
    c = app.test_client()
    c.auto_security = False
    return c


def with_host(client, path, method='GET', origin=None, headers=None, token=None, body=None, **kw):
    """Send exactly what the browser sends: canonical Host, browser-controlled
    Origin, and the CSRF token for mutations (fetchAPI always attaches it)."""
    headers = dict(headers or {})
    headers['Host'] = CANONICAL_HOST
    if origin and origin.startswith('https://'):
        # The trusted edge proxy (Cloudflare/Render) appends this; ProxyFix
        # uses it to restore the https scheme the browser actually used.
        headers.setdefault('X-Forwarded-Proto', 'https')
    if origin:
        headers['Origin'] = origin
    if token:
        headers['X-CSRF-Token'] = token
    if body is not None:
        kw['json'] = body
    return client.open(path, method=method, headers=headers, **kw)


def bootstrap(client, origin=None):
    r = with_host(client, '/api/auth/csrf', origin=origin)
    assert r.status_code == 200
    return r.get_json()['data']['csrf_token']


@pytest.fixture
def prod_like_app(app):
    return make_app(app)


# ── Reproduction of the reported production failure ─────────────────────────

def test_original_failure_genuinely_cross_origin_still_rejected(prod_like_app):
    """A cross-site mutation with victim cookies is rejected with exactly the
    production error the user saw — this path must never open."""
    c = host_client(prod_like_app)
    assert with_host(c, '/api/auth/csrf').status_code == 200
    r = with_host(c, '/api/auth/login', 'POST', origin='https://attacker.example',
                  body={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
    assert r.status_code == 403
    assert r.get_json()['error'] == 'CSRF'
    assert r.get_json()['message'] == 'Cross-origin request blocked'


def test_original_failure_same_origin_login_now_succeeds(prod_like_app):
    """THE production defect: a same-origin login POST (Origin == page/API
    origin, host apex.viability.in) must succeed even though the cross-origin
    allowlist does not contain the canonical origin. Before the fix this
    returned 403 'Cross-origin request blocked'."""
    c = host_client(prod_like_app)
    token = bootstrap(c, origin=APEX_ORIGIN)
    r = with_host(c, '/api/auth/login', 'POST', origin=APEX_ORIGIN, token=token,
                  body={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.headers.get('Set-Cookie')


def test_login_without_origin_header_still_works(prod_like_app):
    """Non-browser clients and Origin-stripping proxies send no Origin; the
    gate is unchanged for them. The browser-equivalent request still carries
    the CSRF token (fetchAPI always attaches it, even for login)."""
    c = host_client(prod_like_app)
    token = bootstrap(c)
    r = with_host(c, '/api/auth/login', 'POST', token=token,
                  body={'email': 'admin@apexflow.com', 'password': 'wrong'})
    assert r.status_code == 401


# ── Full success criterion: login → session → protected API → logout ────────

def test_canonical_origin_end_to_end_flow(prod_like_app):
    c = host_client(prod_like_app)
    # 1-2. clean session: bootstrap CSRF (what the frontend fetchAPI does first)
    boot = with_host(c, '/api/auth/csrf', origin=None)
    assert boot.status_code == 200
    token = boot.get_json()['data']['csrf_token']
    # 3-4. login establishes the session cookie (browser sends cookie + token)
    r = with_host(c, '/api/auth/login', 'POST', origin=APEX_ORIGIN, token=token,
                  json={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
    assert r.status_code == 200
    cookie_header = r.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in cookie_header and 'SameSite=Lax' in cookie_header
    # login rotated the CSRF token (frontend refetches: csrfPromise = null)
    token = bootstrap(c, origin=APEX_ORIGIN)
    # 5. authenticated GET succeeds
    assert with_host(c, '/api/shipments', origin=APEX_ORIGIN).status_code == 200
    # 6. authenticated mutation with CSRF succeeds (201 shipment)
    r = with_host(c, '/api/shipments', 'POST', origin=APEX_ORIGIN, token=token,
                  body={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST001'},
                  headers={'Idempotency-Key': 'e2e-origin-00000000001'})
    assert r.status_code == 201, r.get_data(as_text=True)
    # forged cross-site mutation with stolen cookies but attacker Origin: blocked
    forged = with_host(c, '/api/shipments', 'DELETE', origin='https://attacker.example')
    assert forged.status_code == 403
    # missing CSRF token on a same-origin mutation: blocked by CSRF (not origin)
    missing = with_host(c, '/api/shipments', 'POST', origin=APEX_ORIGIN,
                        body={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST001'},
                        headers={'Idempotency-Key': 'e2e-origin-00000000002'})
    assert missing.status_code == 403 and missing.get_json()['error'] == 'CSRF'
    # 7. logout succeeds
    assert with_host(c, '/api/auth/logout', 'POST', origin=APEX_ORIGIN,
                     token=token).status_code == 200
    # 8. subsequent protected request is rejected
    assert with_host(c, '/api/shipments', origin=APEX_ORIGIN).status_code == 401


# ── Trusted-proxy topology (Cloudflare → Render): scheme detection ──────────

def test_proxy_topology_same_origin_via_forwarded_proto(app):
    """With TRUST_PROXY=1 the https scheme arrives via X-Forwarded-Proto and
    ProxyFix; the same-origin check must then match the https Origin."""
    tp = make_app(app, TRUST_PROXY=True)
    c = host_client(tp)
    boot = c.open('/api/auth/csrf', headers={'Host': CANONICAL_HOST,
                                             'X-Forwarded-Proto': 'https'})
    assert boot.status_code == 200
    token = boot.get_json()['data']['csrf_token']
    r = c.open('/api/auth/login', method='POST', headers={
        'Host': CANONICAL_HOST,
        'Origin': APEX_ORIGIN,                    # https origin from the browser
        'X-Forwarded-Proto': 'https',             # appended by the trusted proxy
        'X-CSRF-Token': token,                    # browser always attaches it
    }, json={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
    assert r.status_code == 200, r.get_data(as_text=True)


def test_proxy_headers_ignored_without_trust_proxy(prod_like_app):
    """Without TRUST_PROXY, X-Forwarded-Proto must NOT upgrade the scheme
    (fail closed: https Origin vs plain-http request is not same-origin and
    the origin is not allowlisted)."""
    c = host_client(prod_like_app)
    r = c.open('/api/auth/login', method='POST', headers={
        'Host': CANONICAL_HOST,
        'Origin': APEX_ORIGIN, 'X-Forwarded-Proto': 'https',
    }, json={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
    assert r.status_code == 403


# ── Malformed / hostile origins fail closed to the allowlist ────────────────

@pytest.mark.parametrize('origin', [
    'not-a-url',
    'https://attacker.example:badport',
    'https://attacker.example/../../',
    'https://user:pass@apex.viability.in',
    'https://apex.viability.in.evil.example',
    'javascript:alert(1)',
    'file:///etc/passwd',
])
def test_malformed_origins_never_bypass(prod_like_app, origin):
    c = host_client(prod_like_app)
    r = with_host(c, '/api/auth/login', 'POST', origin=origin,
                  body={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
    assert r.status_code == 403, origin


def test_port_and_scheme_mismatches_are_cross_origin(prod_like_app):
    c = host_client(prod_like_app)
    for origin in ('http://apex.viability.in',        # scheme downgrade
                   'https://apex.viability.in:8443',  # port mismatch
                   'https://apex.viability.in.evil'):
        r = with_host(c, '/api/auth/login', 'POST', origin=origin,
                      body={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
        assert r.status_code == 403, origin


def test_explicit_cross_origin_allowlist_still_honored(app):
    """A genuinely separate frontend origin keeps working ONLY via the strict
    allowlist (no wildcard, no reflection)."""
    partner = make_app(app, ALLOWED_ORIGINS=['https://portal.partner.example'])
    c = host_client(partner)
    token = bootstrap(c, origin='https://portal.partner.example')
    r = with_host(c, '/api/auth/login', 'POST', origin='https://portal.partner.example',
                  token=token, body={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
    assert r.status_code == 200
    # reflected/wildcard origins stay untrusted
    for origin in ('https://portal.partner.example.evil.example', 'null', '*'):
        r = with_host(c, '/api/auth/login', 'POST', origin=origin,
                      body={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
        assert r.status_code == 403, origin


# ── Provider independence: no provider hostname in the browser contract ─────

def test_frontend_contains_no_provider_or_local_hosts():
    from pathlib import Path
    for path in list(Path('frontend').rglob('*.js')) + list(Path('frontend').rglob('*.html')):
        text = path.read_text().lower()
        for banned in ('onrender.com', 'northflank', 'koyeb', 'localhost', '127.0.0.1',
                       'http://apex', 'https://apex-flow'):
            assert banned not in text, f'{path} references {banned}'


# ── Real HTTP server, browser-equivalent flow (no Flask test client) ────────

def test_real_http_server_full_login_flow(app):
    """Browser-equivalent proof over real TCP/HTTP against a real WSGI server:
    csrf bootstrap → login (Origin set, apex origin NOT in the allowlist) →
    authenticated GET → authenticated POST with CSRF → logout → 401."""
    cfg = {k: app.config[k] for k in ('DATABASE_URL', 'SECRET_KEY', 'IDEMPOTENCY_HASH_KEYS',
                                      'OUTBOX_ENCRYPTION_KEY')}
    cfg.update({'APP_ENV': 'test', 'TESTING': True, 'TRUST_PROXY': True,
                'ALLOWED_ORIGINS': ['https://unrelated-partner.example']})
    application = create_app(cfg)

    from werkzeug.serving import make_server
    server = make_server('127.0.0.1', 0, application)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)

        def request(method, path, origin, extra=None, body=None):
            headers = {'Host': 'apex.viability.in', 'Origin': origin,
                       'Content-Type': 'application/json'}
            headers.update(extra or {})
            payload = json.dumps(body) if body is not None else None
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp, (json.loads(data) if data and resp.getheader('Content-Type', '').startswith('application/json') else {})

        # same-origin against the canonical origin, via the trusted-proxy path
        common = {'X-Forwarded-Proto': 'https'}
        resp, csrf_body = request('GET', '/api/auth/csrf', APEX_ORIGIN, common)
        assert resp.status == 200
        token = csrf_body['data']['csrf_token']
        session_cookie = (resp.getheader('Set-Cookie') or '').split(';', 1)[0]

        resp, body = request('POST', '/api/auth/login', APEX_ORIGIN,
                             {**common, 'X-CSRF-Token': token, 'Cookie': session_cookie},
                             {'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
        assert resp.status == 200, body
        set_cookie = resp.getheader('Set-Cookie') or ''
        assert 'HttpOnly' in set_cookie and 'SameSite=Lax' in set_cookie
        cookie = set_cookie.split(';', 1)[0]

        # login rotated the CSRF token — the browser refetches it
        resp, refetched = request('GET', '/api/auth/csrf', APEX_ORIGIN, {**common, 'Cookie': cookie})
        assert resp.status == 200
        token = refetched['data']['csrf_token']

        resp, _ = request('GET', '/api/shipments', APEX_ORIGIN, {**common, 'Cookie': cookie})
        assert resp.status == 200
        resp, body = request('POST', '/api/shipments', APEX_ORIGIN,
                             {**common, 'Cookie': cookie, 'X-CSRF-Token': token,
                              'Idempotency-Key': 'real-http-e2e-000000001'},
                             {'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST001'})
        assert resp.status == 201, body
        # forged cross-site DELETE with the stolen cookie: origin-gated
        resp, _ = request('DELETE', '/api/shipments/SHP001', 'https://attacker.example',
                          {**common, 'Cookie': cookie})
        assert resp.status == 403
        resp, _ = request('POST', '/api/auth/logout', APEX_ORIGIN,
                          {**common, 'Cookie': cookie, 'X-CSRF-Token': token})
        assert resp.status == 200
        resp, _ = request('GET', '/api/shipments', APEX_ORIGIN, {**common, 'Cookie': cookie})
        assert resp.status == 401
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
