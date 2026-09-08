"""Native PostgreSQL transactions. No SQL translation, implicit tenant, or DDL.

Every tenant service requires a membership-derived Caller. The first transaction
revalidates membership, advisory-locks the tenant (coarse correctness baseline), and uses
SET LOCAL context. Nested services reuse the same transaction; only its owner
commits. Connections are closed, not pooled, so state cannot cross requests.
"""
from contextlib import contextmanager
from contextvars import ContextVar

import psycopg2
from psycopg2.extras import DictCursor
from flask import current_app

from backend.errors import AuthzError

_active = ContextVar('apex_transaction', default=None)


def connect(url=None):
    conn = psycopg2.connect(url or current_app.config['DATABASE_URL'],
                            connect_timeout=5, cursor_factory=DictCursor,
                            options='-c timezone=UTC -c search_path=pg_catalog,public,pg_temp '
                                    '-c statement_timeout=10000 -c lock_timeout=5000 '
                                    '-c idle_in_transaction_session_timeout=15000')
    return conn


@contextmanager
def identity_session():
    """Global identity registry only. Never use for tenant business operations."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


@contextmanager
def db_session(caller):
    if caller is None or not caller.tenant_id:
        raise AuthzError('Authorized tenant context required')
    active = _active.get()
    if active:
        identity, conn = active
        if identity != caller:
            raise AuthzError('Cannot change identity inside a transaction')
        yield conn
        return
    with identity_session() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true), set_config('app.principal_id', %s, true)",
                        (caller.tenant_id, caller.user_id))
            # Serialize current operations, including direct CLI/service calls.
            # Hash collisions only over-serialize; never authorize. Timeouts are retryable.
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (caller.tenant_id,))
            cur.execute('SELECT tenant_id FROM tenants WHERE tenant_id = %s', (caller.tenant_id,))
            if not cur.fetchone():
                raise AuthzError('Tenant membership required')
            cur.execute('''SELECT m.role, m.customer_id, m.driver_id FROM memberships m
                           JOIN users u ON u.user_id = m.user_id
                           WHERE m.tenant_id = %s AND m.user_id = %s AND u.active''',
                        (caller.tenant_id, caller.user_id))
            member = cur.fetchone()
            if not member or tuple(member) != (caller.role, caller.customer_id, caller.driver_id):
                raise AuthzError('Tenant membership changed; authenticate again')
        token = _active.set((caller, conn))
        try:
            yield conn
        finally:
            _active.reset(token)


def validate_database():
    """Read-only boot check: fail closed on privileged runtime or schema drift."""
    with identity_session() as conn, conn.cursor() as cur:
        cur.execute('''SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit
                       FROM pg_roles WHERE rolname = current_user''')
        if any(cur.fetchone()):
            raise RuntimeError('Runtime DB role must be non-superuser, NOBYPASSRLS, NOCREATEROLE, NOCREATEDB, NOINHERIT')
        cur.execute('SELECT current_user')
        if cur.fetchone()[0] != 'apex_app':
            raise RuntimeError('Use the dedicated apex_app runtime database role')
        cur.execute('SELECT 1 FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles WHERE rolname = current_user)')
        if cur.fetchone():
            raise RuntimeError('Runtime DB role must not be a member of other roles')
        cur.execute("SELECT version, checksum FROM schema_migrations ORDER BY version")
        from backend.migrate import migration_files
        expected = [(p.name, checksum) for p, checksum in migration_files()]
        if [tuple(r) for r in cur.fetchall()] != expected:
            raise RuntimeError('Database migrations do not match application; run the explicit migration job')
        cur.execute("""SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       pg_get_userbyid(c.relowner) = current_user AS owned
                       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                       WHERE n.nspname='public' AND c.relkind='r'""")
        identity_tables = {'users', 'auth_sessions', 'login_attempts', 'schema_migrations'}
        for row in cur.fetchall():
            if row['owned'] or (row['relname'] not in identity_tables and not (row['relrowsecurity'] and row['relforcerowsecurity'])):
                raise RuntimeError('Unsafe runtime table ownership or missing FORCE RLS')
