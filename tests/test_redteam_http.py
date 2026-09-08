"""RED TEAM — HTTP auth lifecycle, CSRF, idempotency scope, OTP, bounds, headers."""
import uuid

import pytest

from backend.database import connect
from tests.conftest import TEST_PASSWORD

TENANT_A = '00000000-0000-4000-8000-000000000001'
TENANT_B = '00000000-0000-4000-8000-000000000002'


def admin(app):
    return connect(app.config['TEST_ADMIN_URL'])


def login(client, email, password):
    return client.post('/api/auth/login', json={'email': email, 'password': password})


# ── Session/membership lifecycle ─────────────────────────────────────────────

def test_role_demoted_mid_session_blocks_and_no_cached_replay(app, client):
    r = login(client, 'manager@apexflow.com', TEST_PASSWORD)
    assert r.status_code == 200
    key = uuid.uuid4().hex
    r1 = client.post('/api/shipments', json={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST002'},
                     headers={'Idempotency-Key': key})
    assert r1.status_code == 201
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE memberships SET role='CUSTOMER', customer_id='CUST001' "
                    "WHERE tenant_id=%s AND user_id='USR002'", (TENANT_A,))
    conn.close()
    # existing session must lose manager privileges immediately
    assert client.get('/api/vehicles').status_code == 403
    assert client.get('/api/drivers').status_code == 403
    # replaying the historical manager mutation must NOT return the cached 201
    r2 = client.post('/api/shipments', json={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST002'},
                     headers={'Idempotency-Key': key})
    assert r2.status_code in (403, 404)
    assert r2.headers.get('Idempotency-Replayed') != 'true'


def test_membership_removed_mid_session_blocks_tenant(app, client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM memberships WHERE tenant_id=%s AND user_id='USR002'", (TENANT_A,))
    conn.close()
    assert client.get('/api/shipments').status_code == 403
    assert client.get('/api/auth/me').status_code == 403
    # logout must still succeed (destruction path must never be blocked)
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/shipments').status_code == 401


def test_tenant_a_removed_b_kept_exact_semantics(app, client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    conn = admin(app)
    with conn, conn.cursor() as cur:
        # second membership in tenant B (customer-linked, satisfies CHECK)
        cur.execute("INSERT INTO memberships(tenant_id,user_id,role,customer_id) "
                    "VALUES (%s,'USR002','CUSTOMER','BCUST001')", (TENANT_B,))
        cur.execute("DELETE FROM memberships WHERE tenant_id=%s AND user_id='USR002'", (TENANT_A,))
    conn.close()
    # stale selector for removed tenant: denied, never silently remapped
    r_a = client.get('/api/shipments', headers={'X-Tenant-ID': TENANT_A})
    assert r_a.status_code == 403
    # EXACT RESULT: one remaining membership auto-selects (no forced chooser)
    r_none = client.get('/api/shipments')
    assert r_none.status_code == 200  # tenant B context, tenant A data stays invisible
    body = r_none.get_json()['data']
    assert all('CUST001' != s.get('customer_id') or s.get('customer_id') is None for s in body)
    # valid selector for remaining tenant works
    r_b = client.get('/api/shipments', headers={'X-Tenant-ID': TENANT_B})
    assert r_b.status_code == 200


def test_revoked_and_expired_session_tokens(app, client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE auth_sessions SET expires_at = now() - interval '1 second'")
    conn.close()
    assert client.get('/api/shipments').status_code == 401
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute('DELETE FROM auth_sessions')
    conn.close()
    assert client.get('/api/shipments').status_code == 401


def test_login_enumeration_responses_identical(client):
    r1 = login(client, 'ghost@apexflow.com', 'wrong-password-xyz')
    r2 = login(client, 'manager@apexflow.com', 'definitely-wrong')
    assert r1.status_code == r2.status_code == 401
    assert r1.get_data() == r2.get_data()


def test_ip_budget_shared_across_accounts(app, client):
    """DOCUMENTED FINDING: the per-IP budget (30/min) is global per source IP.
    Behind a proxy without TRUST_PROXY every user shares one bucket — an
    availability risk; with TRUST_PROXY=1 the bucket is spoofable via
    X-Forwarded-For (account bucket still caps at 5/min)."""
    emails = [f'ipburner{i}@apexflow.com' for i in range(10)]
    conn = admin(app)
    from werkzeug.security import generate_password_hash
    import secrets as _s
    with conn, conn.cursor() as cur:
        for e in emails:
            cur.execute("INSERT INTO users(user_id,name,email,password_hash) VALUES (%s,%s,%s,%s)",
                        (f'RB-{e}', 'rb', e, generate_password_hash(_s.token_urlsafe(16))))
            cur.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (%s,%s,'ADMIN')",
                        (TENANT_A, f'RB-{e}'))
    conn.close()
    # 4 attempts per account stays under the per-account cap of 5:
    # any 429 can only be the shared per-IP bucket (30/min).
    codes = [login(client, e, 'nope-wrong').status_code for e in emails for _ in range(4)]
    assert codes.count(401) == 30 and codes.count(429) == 10


def test_xff_spoofing_rotates_ip_buckets(app):
    """With TRUST_PROXY the client fully controls the bucket key (proxy must
    scrub XFF); the ACCOUNT cap is the surviving control."""
    import secrets as _s
    from werkzeug.security import generate_password_hash
    conn = admin(app)
    uid = 'RB-XFF'
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users(user_id,name,email,password_hash) VALUES (%s,%s,%s,%s)",
                    (uid, 'xff', 'xffburner@apexflow.com', generate_password_hash(_s.token_urlsafe(16))))
        cur.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (%s,%s,'ADMIN')", (TENANT_A, uid))
    conn.close()
    codes = []
    for i in range(8):
        c = app.test_client()
        codes.append(c.post('/api/auth/login', json={'email': 'xffburner@apexflow.com', 'password': 'x'},
                            headers={'X-Forwarded-For': f'10.9.9.{i}'}).status_code)
    assert codes.count(429) >= 1  # account cap holds despite IP rotation


def test_csrf_token_from_other_session_rejected(app, client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    other = app.test_client()
    assert login(other, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    foreign = other.get('/api/auth/csrf').get_json()['data']['csrf_token']
    r = client.post('/api/vehicles', json={'registration_number': 'RB-1'},
                    headers={'X-CSRF-Token': foreign})
    assert r.status_code == 403


# ── Idempotency scope attacks ────────────────────────────────────────────────

def test_same_key_isolated_across_users_and_tenants(app, client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    a1 = client.post('/api/shipments', json={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST002'},
                     headers={'Idempotency-Key': 'sharedkey-000000000001'})
    assert a1.status_code == 201
    other = app.test_client()
    assert login(other, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200  # same tenant, different user
    a2 = other.post('/api/shipments', json={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST002'},
                    headers={'Idempotency-Key': 'sharedkey-000000000001'})
    assert a2.status_code == 201  # independent record, second logical shipment
    assert a1.get_json()['data']['shipment_id'] != a2.get_json()['data']['shipment_id']
    b = app.test_client()
    assert login(b, 'Bmanager@apexflow.com', TEST_PASSWORD).status_code == 200
    a3 = b.post('/api/shipments', json={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'BCUST002'},
                headers={'Idempotency-Key': 'sharedkey-000000000001'})
    assert a3.status_code == 201  # other tenant: fully independent
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM idempotency_records WHERE key='sharedkey-000000000001'")
        assert cur.fetchone()[0] == 3
    conn.close()


def test_stale_pending_never_persisted_visible(app, client):
    """PENDING is transaction-internal; no committed PENDING may exist."""
    login(client, 'manager@apexflow.com', TEST_PASSWORD)
    client.post('/api/shipments', json={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST002'},
                headers={'Idempotency-Key': 'finalkey-0000000000001'})
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM idempotency_records WHERE state='PENDING'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM idempotency_records WHERE state='FAILED' AND response_body IS NULL")
        assert cur.fetchone()[0] == 0
    conn.close()


# ── Bounds / resource exhaustion ─────────────────────────────────────────────

def test_oversized_request_body_rejected(client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    big = {'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_name': 'Acme',
           'description': 'x' * (200 * 1024)}
    r = client.post('/api/shipments', json=big)
    assert r.status_code == 413


def test_pagination_and_sort_bounded(client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    assert client.get('/api/shipments?limit=1000000').status_code == 400
    assert client.get('/api/shipments?limit=abc').status_code == 400
    assert client.get('/api/shipments?offset=-5').status_code == 400
    assert client.get("/api/shipments?sort=tenant_id;DROP").status_code == 400
    assert client.get('/api/shipments?direction=ASC;').status_code == 400


def test_reports_accept_no_cap_parameter_and_return_full_set(client):
    """DOCUMENTED FINDING: analytics reports have no limit — whole-tenant rows."""
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.get('/api/reports/shipments?limit=1')
    assert r.status_code == 200
    payload = r.get_json()['data']
    assert isinstance(payload, list)  # no server-side cap honored


# ── OTP protocol ─────────────────────────────────────────────────────────────

def test_otp_never_in_create_response_and_stored_encrypted(app, client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/shipments', json={'pickup': 'Delhi', 'destination': 'Jaipur', 'customer_id': 'CUST002'})
    body = r.get_data(as_text=True)
    assert 'otp' not in body.lower()
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT payload FROM outbox_events ORDER BY created_at DESC LIMIT 1")
        raw = cur.fetchone()[0]
        cur.execute('SELECT otp_code FROM deliveries ORDER BY ctid DESC LIMIT 1')
        stored = cur.fetchone()[0]
    conn.close()
    assert 'otp' not in raw.lower()          # encrypted envelope hides content
    assert ':' in stored                     # werkzeug hash, not plaintext


def test_otp_expiry_enforced(app, client):
    from tests.conftest import set_delivery_otp_for_tests
    assert login(client, 'driver@apexflow.com', TEST_PASSWORD).status_code == 200
    shipment = 'SHP001'
    set_delivery_otp_for_tests(shipment, '654321', driver_id='DRV001')
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE deliveries SET otp_expires_at = now() - interval '1 minute' WHERE shipment_id=%s", (shipment,))
    conn.close()
    r = client.post('/api/deliveries/confirm', json={'shipment_id': shipment, 'otp_code': '654321'})
    assert r.status_code == 400 and 'expired' in r.get_json()['message'].lower()


def test_cross_tenant_delivery_confirm_404(client):
    assert login(client, 'Bdriver@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/deliveries/confirm', json={'shipment_id': 'SHP001', 'otp_code': '123456'})
    assert r.status_code == 400  # same sanitized failure as unknown in-tenant shipment


# ── Headers / boundaries ─────────────────────────────────────────────────────

def test_preflight_from_disallowed_origin_gets_no_acao(app):
    r = app.test_client().options('/api/shipments', headers={
        'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'POST'})
    assert r.headers.get('Access-Control-Allow-Origin') is None


def test_api_responses_carry_correlation_and_no_store(client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.get('/api/shipments')
    assert r.headers.get('X-Request-ID')
    assert r.headers.get('Cache-Control') == 'no-store'
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'
    assert r.headers.get('X-Frame-Options') == 'DENY'
    assert 'frame-ancestors' in r.headers.get('Content-Security-Policy', '')


@pytest.fixture(scope='module')
def prod_app(database_urls):
    """Production-mode app for response-boundary tests (cookies/HSTS/headers).

    The DATABASE_URL keeps the mandatory production `sslmode=verify-full`
    marker (config contract). Over TCP test services that do not terminate
    TLS (GitHub Actions postgres service), a live connection cannot complete
    verification, so boot-time validate_database() is stubbed for THIS
    fixture only — it is fully exercised against a real database by
    test_startup_validation_is_read_only... and the socket-based suite.
    Tests using this fixture must therefore avoid DB-touching endpoints.
    """
    from backend.app import create_app
    import backend.app as app_module
    import secrets as _s
    admin_dsn, runtime = database_urls
    prod_dsn = runtime + ('&' if '?' in runtime else '?') + 'sslmode=verify-full'
    # TLS-verification is untestable against non-TLS CI services (see docstring):
    # stub boot validation for this fixture only; restored immediately after.
    original_validate = app_module.validate_database
    app_module.validate_database = lambda: None
    try:
        application = create_app({
            'APP_ENV': 'production',
            'DATABASE_URL': prod_dsn,
            'SECRET_KEY': _s.token_hex(32),
            'IDEMPOTENCY_HASH_KEYS': [_s.token_hex(32)],
            'OUTBOX_ENCRYPTION_KEY': __import__('cryptography.fernet', fromlist=['Fernet']).Fernet.generate_key().decode(),
            'ALLOWED_ORIGINS': ['https://prod.example'],
        })
    finally:
        app_module.validate_database = original_validate
    return application


def test_production_cookie_and_hsts(prod_app):
    c = prod_app.test_client()
    r = c.get('/api/auth/csrf')  # session bootstrap; no database access
    assert r.status_code == 200
    cookie = r.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie and 'Secure' in cookie
    page = c.get('/')
    assert page.headers.get('Strict-Transport-Security', '').startswith('max-age=')
    assert page.headers.get('Content-Security-Policy')
    assert page.headers.get('X-Frame-Options') == 'DENY'


def test_health_endpoints_reveal_nothing(prod_app):
    c = prod_app.test_client()
    live = c.get('/health/live')
    ready = c.get('/health/ready')
    # live must be up; ready reflects database reachability — over the non-TLS
    # CI test service the production verify-full connection cannot complete,
    # which must surface as the sanitized 503, never an error page.
    assert live.status_code == 200
    assert ready.status_code in (200, 503)
    for r in (live, ready):
        body = r.get_data(as_text=True).lower()
        # No credentials, DSN parts, TLS settings, hosts, or internals may leak.
        for banned in ('postgres://', 'apex_app', 'password', 'secret', 'sslmode',
                       'verify-full', '5432', '127.0.0.1', 'localhost', '/home/',
                       'traceback', 'exception'):
            assert banned not in body, (banned, body)
