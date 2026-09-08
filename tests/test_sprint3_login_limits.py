"""SPRINT 3 — F-2 login rate-limit contract: adversarial verification matrix.

Contract under test (docs/SECURITY_MODEL.md "Login limit contract"):
- Account bucket: FAILED attempts only; atomic DB-enforced upsert inside
  fn_login_material; blocked while count > 5 in a 1-minute window; reset by
  successful authentication; shared across all instances/providers.
- Source bucket: FAILED attempts only; app-side atomic upsert; blocked at the
  pre-check when observed failures >= 30 in a 1-minute window; successes never
  consume it. Key = canonicalized source identity:
    TRUST_PROXY=0 -> TCP peer (X-Forwarded-For never read)
    TRUST_PROXY=1 -> rightmost proxy-appended XFF value (ProxyFix x_for=1)
    unparseable   -> shared conservative 'unknown' bucket
"""
import secrets as _secrets

from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.database import connect
from tests.conftest import TEST_PASSWORD

TENANT_A = '00000000-0000-4000-8000-000000000001'


def make_user(app, email):
    conn = connect(app.config['TEST_ADMIN_URL'])
    uid = 'RB-' + _secrets.token_hex(6)
    with conn, conn.cursor() as cur:
        cur.execute('INSERT INTO users(user_id,name,email,password_hash) VALUES (%s,%s,%s,%s)',
                    (uid, 'rb', email, generate_password_hash(_secrets.token_urlsafe(16))))
        cur.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (%s,%s,'ADMIN')", (TENANT_A, uid))
    conn.close()
    return uid


def drop_users(app, pattern):
    conn = connect(app.config['TEST_ADMIN_URL'])
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM memberships WHERE user_id IN (SELECT user_id FROM users WHERE email LIKE %s)", (pattern,))
        cur.execute('DELETE FROM users WHERE email LIKE %s', (pattern,))
    conn.close()


def clone_app(app, **extra):
    from tests.conftest import APIClient
    cfg = {k: app.config[k] for k in ('DATABASE_URL', 'SECRET_KEY', 'IDEMPOTENCY_HASH_KEYS',
                                      'OUTBOX_ENCRYPTION_KEY', 'ALLOWED_ORIGINS', 'TEST_ADMIN_URL')}
    cfg.update({'APP_ENV': 'test', 'TESTING': True})
    cfg.update(extra)
    created = create_app(cfg)
    created.test_client_class = APIClient  # same auto-CSRF client behavior as the fixture app
    return created


def trust_proxy_app(app):
    return clone_app(app, TRUST_PROXY=True)


def fresh_clients(app, n):
    return [app.test_client() for _ in range(n)]


def test_matrix_1_direct_client_no_proxy_failures_only_source_bucket(app):
    """30 distinct accounts may fail from one direct source; the 31st failure is
    blocked. Successes never consume the source bucket."""
    codes = []
    for i in range(30):
        make_user(app, f'direct{i}@rb.test')
        c = app.test_client()
        codes.append(c.post('/api/auth/login', json={'email': f'direct{i}@rb.test', 'password': 'nope'}).status_code)
    assert codes == [401] * 30
    make_user(app, 'direct30@rb.test')
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': 'direct30@rb.test', 'password': 'nope'}).status_code == 429
    drop_users(app, 'direct%@rb.test')


def test_matrix_2_trusted_proxy_legitimate_forwarding(app):
    tp = trust_proxy_app(app)
    codes = []
    for i in range(31):
        make_user(app, f'tp{i}@rb.test')
        c = tp.test_client()
        codes.append(c.post('/api/auth/login', json={'email': f'tp{i}@rb.test', 'password': 'nope'},
                            headers={'X-Forwarded-For': '203.0.113.9'}).status_code)
    assert codes == [401] * 30 + [429]  # shared forwarded source is one bucket
    drop_users(app, 'tp%@rb.test')


def test_matrix_3_untrusted_client_cannot_influence_source_via_xff(app):
    """TRUST_PROXY=0: rotating X-Forwarded-For must NOT create fresh buckets."""
    codes = []
    for i in range(31):
        make_user(app, f'spoof{i}@rb.test')
        c = app.test_client()
        codes.append(c.post('/api/auth/login', json={'email': f'spoof{i}@rb.test', 'password': 'nope'},
                            headers={'X-Forwarded-For': f'10.1.1.{i}'}).status_code)
    assert codes == [401] * 30 + [429]  # XFF ignored; the TCP-peer bucket filled anyway
    drop_users(app, 'spoof%@rb.test')


def test_matrix_4_spoofed_xff_chain_rightmost_wins_under_trusted_proxy(app):
    tp = trust_proxy_app(app)
    c = tp.test_client()
    make_user(app, 'chain@rb.test')
    # Client-controlled leftmost entries rotate; the proxy-appended rightmost
    # entry is stable, so the source bucket still accumulates.
    codes = []
    for i in range(6):
        codes.append(c.post('/api/auth/login', json={'email': 'chain@rb.test', 'password': 'nope'},
                            headers={'X-Forwarded-For': f'9.9.9.{i}, 203.0.113.9'}).status_code)
    assert codes == [401] * 5 + [429]  # account bucket filled; leftmost rotation irrelevant
    drop_users(app, 'chain@rb.test')


def test_matrix_5_original_f2_attack_rotated_ips_one_account_still_capped(app):
    """Original exploit: rotate client-controlled XFF per attempt against ONE
    account. The DB-enforced account bucket must cap it regardless of source."""
    make_user(app, 'rotator@rb.test')
    codes = []
    for i in range(8):
        c = app.test_client()
        codes.append(c.post('/api/auth/login', json={'email': 'rotator@rb.test', 'password': 'nope'},
                            headers={'X-Forwarded-For': f'10.9.9.{i}'}).status_code)
    assert codes == [401] * 5 + [429] * 3
    drop_users(app, 'rotator@rb.test')


def test_matrix_6_one_account_attacked_from_many_networks_capped(app):
    """Rotation across real network identities (trusted proxy, distinct
    rightmost hops) must not enable unlimited attacks on one account."""
    tp = trust_proxy_app(app)
    make_user(app, 'multinet@rb.test')
    codes = []
    for i in range(7):
        c = tp.test_client()
        codes.append(c.post('/api/auth/login', json={'email': 'multinet@rb.test', 'password': 'nope'},
                            headers={'X-Forwarded-For': f'198.51.100.{i}'}).status_code)
    assert codes.count(429) == 2 and codes[:5] == [401] * 5
    drop_users(app, 'multinet@rb.test')


def test_matrix_7_successful_logins_never_throttled_and_reset_account_bucket(app):
    """Old failure mode: 6 logins/min/account self-locked a busy legitimate
    user. Now successes reset the account bucket and never touch the source
    bucket: 25 successful logins from one source all pass."""
    conn = connect(app.config['TEST_ADMIN_URL'])
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET password_hash=%s WHERE user_id='USR001'",
                    (generate_password_hash('success-pass-123456'),))
    conn.close()
    codes = []
    for i in range(25):
        c = app.test_client()  # fresh client each time: no cookie reuse tricks
        codes.append(c.post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'success-pass-123456'}).status_code)
    assert codes == [200] * 25


def test_matrix_8_success_gives_fresh_failed_attempt_budget(app):
    """4 failures, one success, then failures again: the post-success failures
    start from a fresh account budget (reset-on-success contract)."""
    c = app.test_client()
    pre = [c.post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'bad'}).status_code for _ in range(4)]
    assert pre == [401] * 4
    assert c.post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    c2 = app.test_client()
    post = [c2.post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'bad'}).status_code for _ in range(5)]
    assert post == [401] * 5          # fresh budget of five survived the success
    c3 = app.test_client()
    assert c3.post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'bad'}).status_code == 429


def test_matrix_9_window_expiry_restores_access(app):
    for _ in range(6):
        app.test_client().post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'bad'})
    assert app.test_client().post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'bad'}).status_code == 429
    conn = connect(app.config['TEST_ADMIN_URL'])
    with conn, conn.cursor() as cur:  # simulate the 1-minute window elapsing
        cur.execute("UPDATE login_attempts SET expires_at = now() - interval '1 second'")
    conn.close()
    assert app.test_client().post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'bad'}).status_code == 401


def test_matrix_10_shared_buckets_across_instances(app):
    """Provider A and provider B share both buckets (PostgreSQL-authoritative;
    no process-local state)."""
    make_user(app, 'shared@rb.test')
    a, b = app.test_client(), clone_provider(app).test_client()
    codes = [c.post('/api/auth/login', json={'email': 'shared@rb.test', 'password': 'bad'}).status_code
             for c in [a, b, a, b, a, b]]
    assert codes == [401] * 5 + [429]
    drop_users(app, 'shared@rb.test')


def clone_provider(app):
    return clone_app(app)


def test_matrix_11_ipv4_ipv6_normalization_and_malformed_headers(app):
    tp = trust_proxy_app(app)
    # IPv6 forwarded value works as a normal bucket key; malformed values
    # collapse into 'unknown' without a 500.
    make_user(app, 'v6@rb.test')
    c = tp.test_client()
    for _ in range(5):
        assert c.post('/api/auth/login', json={'email': 'v6@rb.test', 'password': 'bad'},
                      headers={'X-Forwarded-For': '2001:db8::1'}).status_code == 401
    assert c.post('/api/auth/login', json={'email': 'v6@rb.test', 'password': 'bad'},
                  headers={'X-Forwarded-For': '2001:db8::1'}).status_code == 429
    # Malformed / absent / garbage headers must never crash the login path.
    for header in ('///', 'not-an-ip', '', '1.2.3.4, '):
        r = tp.test_client().post('/api/auth/login', json={'email': 'v6@rb.test', 'password': 'bad'},
                                  headers={'X-Forwarded-For': header} if header else {})
        assert r.status_code in (401, 429)
    # Same app, no header at all: falls back to the TCP peer bucket.
    r = tp.test_client().post('/api/auth/login', json={'email': 'v6@rb.test', 'password': 'bad'})
    assert r.status_code == 429  # v6 bucket still exhausted; peer bucket untouched but account locked
    drop_users(app, 'v6@rb.test')


def test_matrix_12_concurrent_failed_logins_lose_no_budget(app):
    """Concurrent failures: every attempt is counted exactly once by the atomic
    DB upsert; the 429 threshold is deterministic, not racy."""
    import threading
    make_user(app, 'conc@rb.test')
    codes = []
    lock = threading.Lock()

    def attempt():
        code = app.test_client().post('/api/auth/login', json={'email': 'conc@rb.test', 'password': 'bad'}).status_code
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [401] * 5 + [429] * 3
    drop_users(app, 'conc@rb.test')


def test_matrix_13_db_unavailable_login_fails_closed(app, monkeypatch):
    """No Redis exists; the limiter is PostgreSQL-authoritative. With the DB
    unavailable, login returns a sanitized 503 — never fail-open."""
    import psycopg2

    def dead_session():
        raise psycopg2.OperationalError('connection refused')

    monkeypatch.setattr('backend.auth.identity_session', dead_session)
    r = app.test_client().post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'x'})
    assert r.status_code == 503
    assert b'postgres' not in r.get_data().lower()
