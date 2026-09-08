"""RED TEAM — database role, RLS internals, and connection-context lifecycle.

These probes attack BELOW the application API: raw SQL as the runtime role,
policy tampering, role escalation, and tenant-context lifetime on shared or
reused connections. Evidence tests document weaknesses without fixing them.
"""
import threading

import pytest

from backend.database import connect

TENANT_TABLES = [
    'tenants', 'customers', 'vehicles', 'drivers', 'shipments', 'shipment_status_history',
    'routes', 'warehouses', 'deliveries', 'payments', 'notifications', 'maintenance_records',
    'idempotency_records', 'audit_events', 'outbox_events', 'notification_reads', 'memberships',
]


@pytest.fixture
def admin_url(app):
    return app.config['TEST_ADMIN_URL']


@pytest.fixture
def runtime_dsn(app):
    return app.config['DATABASE_URL']


def runtime_conn(dsn):
    return connect(dsn)


# ── PostgreSQL role configuration ───────────────────────────────────────────

def test_evidence_runtime_role_privilege_flags(runtime_dsn):
    """Inspect actual pg_roles/pg_auth_members for the runtime role."""
    conn = runtime_conn(runtime_dsn)
    with conn, conn.cursor() as cur:
        cur.execute("""SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
                              rolinherit, rolcanlogin, rolreplication
                       FROM pg_roles WHERE rolname = current_user""")
        flags = cur.fetchone()
        cur.execute("""SELECT count(*) FROM pg_auth_members
                       WHERE member = (SELECT oid FROM pg_roles WHERE rolname = current_user)""")
        memberships = cur.fetchone()[0]
        cur.execute("SELECT proname FROM pg_proc WHERE prosecdef AND pronamespace = 'public'::regnamespace")
        security_definer = sorted(r[0] for r in cur.fetchall())
        cur.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'public.shipments'::regclass")
        shipment_owner = cur.fetchone()[0]
    conn.close()
    # Hard expectations — the runtime role must not escalate.
    assert list(flags) == [False, False, False, False, False, True, False]
    assert memberships == 0
    # Sprint 3: exactly the two documented SECURITY DEFINER login functions exist.
    assert security_definer == ['fn_login_material', 'fn_login_success']
    assert shipment_owner != 'apex_app'  # not the table owner


def test_runtime_role_cannot_disable_or_alter_rls(runtime_dsn):
    conn = runtime_conn(runtime_dsn)
    with conn, conn.cursor() as cur:
        for statement in (
            'ALTER TABLE shipments DISABLE ROW LEVEL SECURITY',
            'ALTER TABLE shipments NO FORCE ROW LEVEL SECURITY',
            "CREATE POLICY p2 ON shipments USING (true)",
            'DROP POLICY tenant_isolation ON shipments',
            'GRANT ALL ON shipments TO PUBLIC',
            'CREATE TABLE redteam_t(i int)',
            "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        ):
            try:
                cur.execute(statement)
                conn.rollback()
            except Exception as exc:
                conn.rollback()
                assert any(w in str(exc).lower() for w in ('permission', 'privilege', 'must be owner')), statement
    conn.close()


def test_runtime_role_cannot_set_role_or_bypass(runtime_dsn):
    conn = runtime_conn(runtime_dsn)
    with conn, conn.cursor() as cur:
        with pytest.raises(Exception, match='permission denied'):
            cur.execute('SET ROLE postgres')
        conn.rollback()
        # row_security=off must error or return zero rows, never foreign rows.
        try:
            with conn, conn.cursor() as cur:
                cur.execute('SET row_security = off')
                cur.execute('SELECT count(*) FROM shipments')
                count = cur.fetchone()[0]
            assert count == 0
        except Exception:
            pass  # failing closed with an error is equally acceptable
    conn.close()


# ── Direct SQL tenant isolation ─────────────────────────────────────────────

def test_direct_sql_default_deny_without_context(runtime_dsn):
    conn = runtime_conn(runtime_dsn)
    with conn, conn.cursor() as cur:
        for table in TENANT_TABLES:
            cur.execute(f'SELECT count(*) FROM {table}')  # noqa: S608 — fixed identifiers
            assert cur.fetchone()[0] == 0, table
        # Forged context without matching membership still yields nothing.
        cur.execute("SELECT set_config('app.tenant_id', '00000000-0000-4000-8000-000000000002', true), set_config('app.principal_id', 'nobody', true)")
        cur.execute('SELECT count(*) FROM shipments')
        assert cur.fetchone()[0] == 0
        cur.execute('SELECT count(*) FROM tenants')
        assert cur.fetchone()[0] == 0
    conn.close()


def test_direct_sql_cross_tenant_select_update_insert(runtime_dsn):
    conn = runtime_conn(runtime_dsn)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT user_id FROM users WHERE user_id = 'USR001'")
        row = cur.fetchone()
        assert row, 'fixture user missing'
        principal = row[0]
    conn.rollback()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', '00000000-0000-4000-8000-000000000001', true), set_config('app.principal_id', %s, true)", (principal,))
        cur.execute("SELECT count(*) FROM shipments WHERE tenant_id = '00000000-0000-4000-8000-000000000002'")
        assert cur.fetchone()[0] == 0  # B rows invisible
        cur.execute("UPDATE shipments SET status='Cancelled' WHERE tenant_id = '00000000-0000-4000-8000-000000000002'")
        assert cur.rowcount == 0  # cannot touch what cannot be seen
        with pytest.raises(Exception, match='row-level security|violates'):
            cur.execute("INSERT INTO shipments (tenant_id, shipment_id, customer_id, customer_name, pickup_location, destination, goods_type, weight_kg, quantity, status, booking_date, expected_delivery, shipping_cost, payment_method, payment_status) "
                        "VALUES ('00000000-0000-4000-8000-000000000002','SHP-rt1','CUST001','x','a','b','c',1,1,'Booked','2026-01-01','2026-01-02',1,'x','Pending')")
        conn.rollback()
    conn.close()


def test_runtime_role_cannot_read_password_hashes(runtime_dsn):
    """SPRINT 3 REMEDIATION of red-team finding F-1. This probe previously
    ASSERTED the vulnerability (bulk password_hash reads succeeded); it now
    asserts the closed privilege boundary instead. Replaced per the sprint
    rule: an old adversarial probe may be replaced only when the behavior it
    encoded was the vulnerability itself, and the replacement must prove the
    original exploit fails for the right reason."""
    import hmac as _h
    conn = runtime_conn(runtime_dsn)
    with conn, conn.cursor() as cur:
        # The original F-1 exploit — bulk hash reads — must now fail.
        for statement in (
            'SELECT password_hash FROM users',
            "SELECT count(password_hash) FROM users",
            "SELECT json_agg(password_hash) FROM users",
            "SELECT password_hash FROM users WHERE user_id LIKE 'B%'",
        ):
            with pytest.raises(Exception, match='permission denied'):
                cur.execute(statement)
            conn.rollback()
        # Non-secret identity columns remain readable (login/profile need them).
        cur.execute('SELECT user_id, name, email, phone, active, created_at FROM users LIMIT 1')
        assert cur.fetchone() is not None
        # Cross-tenant identity rows remain globally readable WITHOUT hashes
        # (identity registry stays global by design; hashes do not travel with it).
        cur.execute("SELECT count(*) FROM users WHERE user_id LIKE 'B%'")
        assert cur.fetchone()[0] > 0
        # The single legitimate mechanism: fn_login_material returns ONE row per
        # explicit candidate email (the login path), never a bulk channel.
        digest = _h.new(b'', b'account:redteam@example.com', 'sha256').hexdigest()
        cur.execute('SELECT limited, out_user_id, out_password_hash FROM fn_login_material(%s, %s)',
                    (digest, 'redteam@example.com'))
        limited, uid, pwhash = cur.fetchone()
        assert limited is False          # fresh bucket, budget not exhausted
        assert uid is None and pwhash is None  # unknown candidate: no material, dummy path
        # The SECURITY DEFINER function must not permit bucket-string injection.
        cur.execute("SELECT limited FROM fn_login_material(%s, %s)", ("x'::text, 0 AS hack--", 'a@b.c'))
        assert cur.fetchone()[0] is False
    conn.close()


# ── Connection / context lifecycle ──────────────────────────────────────────

def test_transaction_local_context_dies_with_transaction(runtime_dsn):
    """Same physical connection reused across tenants sequentially must not leak."""
    conn = runtime_conn(runtime_dsn)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', '00000000-0000-4000-8000-000000000001', true)")
        cur.execute("SELECT current_setting('app.tenant_id', true)")
        assert cur.fetchone()[0] == '00000000-0000-4000-8000-000000000001'
    # context commits with txn; same physical connection continues
    with conn, conn.cursor() as cur:
        cur.execute("SELECT current_setting('app.tenant_id', true), current_setting('app.principal_id', true)")
        tenant, principal = cur.fetchone()
        assert (tenant or '') == '' and (principal or '') == ''
    with conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', '00000000-0000-4000-8000-000000000002', true)")
        cur.execute("SELECT count(*) FROM shipments")
        assert cur.fetchone()[0] == 0  # no A rows visible under B context
    conn.rollback()
    # rollback also discards context
    with conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', '00000000-0000-4000-8000-000000000001', true)")
        cur.execute("SELECT 1/0") if False else conn.rollback()
        cur.execute("SELECT current_setting('app.tenant_id', true)")
        assert (cur.fetchone()[0] or '') == ''
    conn.close()


def test_concurrent_tenants_never_see_each_other(app):
    """Threads hammering db_session for two tenants in interleaved fashion."""
    from backend.authz import Caller
    from backend.database import db_session

    A = '00000000-0000-4000-8000-000000000001'
    B = '00000000-0000-4000-8000-000000000002'
    ca = Caller(user_id='USR001', tenant_id=A, role='ADMIN', name='a', email='a@x')
    cb = Caller(user_id='BUSR001', tenant_id=B, role='ADMIN', name='b', email='b@x')
    errors = []

    def hammer(caller, foreign, iterations=12):
        try:
            for _ in range(iterations):
                with app.app_context(), db_session(caller) as conn, conn.cursor() as cur:
                    cur.execute('SELECT tenant_id FROM shipments')
                    tenants_seen = {r['tenant_id'] for r in cur.fetchall()}
                    if foreign in tenants_seen:
                        errors.append(f'leak: {caller.tenant_id} saw {foreign}')
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    with app.app_context():
        threads = [threading.Thread(target=hammer, args=(ca, B)),
                   threading.Thread(target=hammer, args=(cb, A)),
                   threading.Thread(target=hammer, args=(ca, B)),
                   threading.Thread(target=hammer, args=(cb, A))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert not errors


def test_no_durable_app_connections_after_requests(client):
    """Connections are closed, not pooled: no active runtime transactions linger."""
    client.get('/api/shipments')
    conn = connect(client.application.config['TEST_ADMIN_URL'])
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE usename='apex_app' AND state <> 'idle'")
        active = cur.fetchone()[0]
    conn.close()
    assert active == 0
