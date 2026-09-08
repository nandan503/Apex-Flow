"""SPRINT 3 — F-1 database credential boundary: adversarial verification.

Attacks are executed directly through PostgreSQL as the SAME runtime role the
application uses (apex_app), not merely through the HTTP API.
"""
import hmac

import pytest

from backend.database import connect

TENANT_A = '00000000-0000-4000-8000-000000000001'


def runtime_conn(app):
    return connect(app.config['DATABASE_URL'])


def account_digest(app, email):
    return hmac.new(app.config['SECRET_KEY'].encode(), f'account:{email}'.encode(), 'sha256').hexdigest()


def test_f1_original_exploit_bulk_hash_read_fails(app):
    """The exact F-1 exploit: SELECT password_hash for all users of all tenants."""
    conn = runtime_conn(app)
    with conn, conn.cursor() as cur:
        with pytest.raises(Exception, match='permission denied'):
            cur.execute('SELECT count(*), count(password_hash) FROM users')
        conn.rollback()
        with pytest.raises(Exception, match='permission denied'):
            cur.execute("SELECT password_hash FROM users WHERE user_id LIKE 'B%'")
        conn.rollback()
        with pytest.raises(Exception, match='permission denied'):
            cur.execute("SELECT json_agg(password_hash) FROM users")
    conn.close()


def test_f1_runtime_role_grants_are_column_scoped(app):
    conn = runtime_conn(app)
    with conn, conn.cursor() as cur:
        cur.execute("""SELECT privilege_type, column_name FROM information_schema.column_privileges
                       WHERE grantee='apex_app' AND table_name='users' ORDER BY column_name""")
        cols = {r['column_name'] for r in cur.fetchall()}
        cur.execute("SELECT has_table_privilege('apex_app', 'users', 'SELECT') AS table_select")
        table_select = cur.fetchone()['table_select']
        # The SECURITY DEFINER boundary functions exist with fixed search_path.
        cur.execute("""SELECT prosecdef, proconfig,
                              has_function_privilege('apex_app', 'fn_login_material(text,text)', 'EXECUTE') AS can_exec
                       FROM pg_proc WHERE proname='fn_login_material'""")
        fn = cur.fetchone()
    conn.close()
    assert table_select is False                      # no table-wide SELECT
    assert cols == {'user_id', 'name', 'email', 'phone', 'active', 'created_at'}
    assert fn['prosecdef'] is True                    # SECURITY DEFINER (documented)
    assert fn['can_exec'] is True
    assert 'search_path=pg_catalog, public' in ' '.join(fn['proconfig'] or [])


def test_f1_security_definer_functions_are_locked_down(app):
    conn = runtime_conn(app)
    with conn, conn.cursor() as cur:
        cur.execute("""SELECT proname, prosecdef, proconfig, pg_get_userbyid(proowner) AS owner
                       FROM pg_proc WHERE pronamespace='public'::regnamespace AND prosecdef""")
        funcs = {r['proname']: r for r in cur.fetchall()}
        # Exactly two SECURITY DEFINER functions exist and are owned by the
        # migration/table owner, not by the runtime role.
        assert set(funcs) == {'fn_login_material', 'fn_login_success'}
        for row in funcs.values():
            assert row['owner'] != 'apex_app'
            assert 'search_path=pg_catalog, public' in ' '.join(row['proconfig'] or [])
        # Runtime role cannot replace or drop them.
        for stmt in ('CREATE OR REPLACE FUNCTION fn_login_success(text) RETURNS void LANGUAGE sql AS $$ SELECT 1 $$',
                     'DROP FUNCTION fn_login_success(text)'):
            with pytest.raises(Exception, match='permission|owner'):
                cur.execute(stmt)
            conn.rollback()
    conn.close()


def test_f1_normal_login_still_succeeds_and_wrong_password_fails(client):
    from tests.conftest import TEST_PASSWORD
    assert client.post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    fresh = client.application.test_client()
    assert fresh.post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': 'totally-wrong'}).status_code == 401


def test_f1_function_returns_single_candidate_not_bulk_channel(app):
    """The lookup is one-row-per-explicit-candidate; there is no way to pull
    more than the candidate row per call, and unknown emails yield nothing."""
    conn = runtime_conn(app)
    with conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM fn_login_material(%s, %s)',
                    (account_digest(app, 'admin@apexflow.com'), 'admin@apexflow.com'))
        row = cur.fetchone()
        assert row['out_user_id'] == 'USR001' and ':' in row['out_password_hash']
        cur.execute('SELECT count(*) FROM fn_login_material(%s, %s)',
                    (account_digest(app, 'admin@apexflow.com'), 'admin@apexflow.com'))
        assert cur.fetchone()[0] == 1
    conn.close()


def test_f1_disabled_user_cannot_authenticate_via_function_path(app, client):
    from tests.conftest import TEST_PASSWORD
    assert client.post('/api/auth/login', json={'email': 'driver@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    client.post('/api/auth/logout')
    conn = connect(app.config['TEST_ADMIN_URL'])
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET active=false WHERE user_id='USR003'")
    conn.close()
    fresh = app.test_client()
    r = fresh.post('/api/auth/login', json={'email': 'driver@apexflow.com', 'password': TEST_PASSWORD})
    assert r.status_code == 401
    conn = connect(app.config['TEST_ADMIN_URL'])
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET active=true WHERE user_id='USR003'")
    conn.close()


def test_f1_privileged_control_plane_still_rotates_hashes(app):
    """Operator reset-password runs under the privileged control-plane role and
    must keep working (it never used the runtime role)."""
    from backend.cli import reset_password
    from tests.conftest import TEST_PASSWORD
    conn = connect(app.config['TEST_ADMIN_URL'])
    with conn, conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE user_id='USR002'")
        old_hash = cur.fetchone()[0]
    conn.close()
    # reset_password reads the password via getpass; monkeypatch the prompt source.
    import getpass
    original = getpass.getpass
    getpass.getpass = lambda *a, **k: 'rotated-pass-123456789'
    try:
        reset_password(app.config['TEST_ADMIN_URL'], 'USR002')
    finally:
        getpass.getpass = original
    conn = connect(app.config['TEST_ADMIN_URL'])
    with conn, conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE user_id='USR002'")
        assert cur.fetchone()[0] != old_hash
        cur.execute('SELECT count(*) FROM auth_sessions WHERE user_id=%s', ('USR002',))
        assert cur.fetchone()[0] == 0  # sessions revoked with the rotation
    conn.close()
    fresh = app.test_client()
    assert fresh.post('/api/auth/login', json={'email': 'manager@apexflow.com', 'password': TEST_PASSWORD}).status_code == 401
    assert fresh.post('/api/auth/login', json={'email': 'manager@apexflow.com', 'password': 'rotated-pass-123456789'}).status_code == 200
