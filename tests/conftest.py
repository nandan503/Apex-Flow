"""Real PostgreSQL only. Tests require a disposable database ending in _test."""
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from psycopg2.extensions import parse_dsn
from flask.testing import FlaskClient
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash
import pytest

from backend.app import create_app
from backend.database import connect
from backend.migrate import migrate
from tests.demo_data import seed_demo_data

TEST_PASSWORD = secrets.token_urlsafe(24)
SECRET = secrets.token_hex(32)
OUTBOX_KEY = Fernet.generate_key().decode()
TENANT_A = '00000000-0000-4000-8000-000000000001'
TENANT_B = '00000000-0000-4000-8000-000000000002'


class APIClient(FlaskClient):
    def open(self, *args, **kwargs):
        method = kwargs.get('method', 'GET').upper()
        auto_security = kwargs.pop('auto_security', True)
        if method not in ('GET', 'HEAD', 'OPTIONS') and auto_security:
            bootstrap = super().open('/api/auth/csrf', method='GET')
            headers = dict(kwargs.get('headers') or {})
            headers.setdefault('X-CSRF-Token', bootstrap.get_json()['data']['csrf_token'])
            headers.setdefault('Idempotency-Key', str(uuid.uuid4()))
            kwargs['headers'] = headers
        return super().open(*args, **kwargs)


@pytest.fixture(scope='session')
def database_urls():
    admin = os.environ.get('TEST_DATABASE_ADMIN_URL')
    if not admin:
        pytest.fail('TEST_DATABASE_ADMIN_URL must point to a disposable PostgreSQL database ending in _test')
    parsed = parse_dsn(admin)
    if not parsed.get('dbname', '').endswith('_test'):
        pytest.fail('Refusing to reset a database not ending in _test')
    password = secrets.token_urlsafe(32)
    conn = connect(admin)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname='apex_app'")
            if not cur.fetchone():
                cur.execute('CREATE ROLE apex_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT')
            cur.execute('ALTER ROLE apex_app PASSWORD %s', (password,))
            cur.execute('DROP SCHEMA public CASCADE')
            cur.execute('CREATE SCHEMA public')
    finally:
        conn.close()
    migrate(admin)
    # Application requires URI form; psycopg2's DSN parser supports both only internally.
    from urllib.parse import urlencode
    host = parsed.get('host', 'localhost')
    port = parsed.get('port', '5432')
    if host.startswith('/'):
        runtime = f"postgresql://apex_app:{password}@/{parsed['dbname']}?{urlencode({'host':host})}"
    else:
        runtime = f"postgresql://apex_app:{password}@{host}:{port}/{parsed['dbname']}"
    return admin, runtime


def seed_tenant(conn, tenant_id, prefix=''):
    with conn.cursor() as cur:
        cur.execute('INSERT INTO tenants(tenant_id,name) VALUES (%s,%s)', (tenant_id, 'Test tenant '+prefix))
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    # Fixture adapter namespaces known demo IDs, never used in application code.
    class FixtureCursor:
        def __init__(self): self.cur = conn.cursor()
        def execute(self, sql, params=None): return self.cur.execute(sql, params)
        def executemany(self, sql, rows):
            import re
            def convert(v):
                if isinstance(v, str):
                    return re.sub(r'\b(CUST\d+|VEH\d+|DRV\d+|SHP\d+|DEL\d+|RTE\d+|WH\d+|NOTIF\d+|INV-2026-\d+)', lambda m: prefix+m[0], v)
                if prefix and isinstance(v, int) and 'INSERT INTO shipment_status_history ' in sql:
                    return v + 100
                return v
            self.cur.executemany(sql, [tuple(convert(v) for v in r) for r in rows])
    class FixtureConn:
        def cursor(self): return FixtureCursor()
    seed_demo_data(FixtureConn())
    with conn.cursor() as cur:
        for i, role in enumerate(['ADMIN', 'MANAGER', 'DRIVER', 'CUSTOMER'], 1):
            user_id = f'{prefix}USR00{i}'
            cur.execute('INSERT INTO users(user_id,name,email,password_hash) VALUES (%s,%s,%s,%s)',
                        (user_id, role, f'{prefix}{role.lower()}@apexflow.com', generate_password_hash(TEST_PASSWORD)))
            cur.execute('INSERT INTO memberships(tenant_id,user_id,role,customer_id,driver_id) VALUES (%s,%s,%s,%s,%s)',
                        (tenant_id,user_id,role,prefix+'CUST001' if role=='CUSTOMER' else None,prefix+'DRV001' if role=='DRIVER' else None))
            if role in ('CUSTOMER','DRIVER'):
                cur.execute('UPDATE notifications SET recipient_user_id=%s WHERE tenant_id=%s AND user_role=%s', (user_id, tenant_id, role))


@pytest.fixture
def app(database_urls):
    admin, runtime = database_urls
    conn = connect(admin)
    try:
        with conn, conn.cursor() as cur:
            cur.execute('TRUNCATE tenants, users, login_attempts RESTART IDENTITY CASCADE')
            seed_tenant(conn, TENANT_A)
            seed_tenant(conn, TENANT_B, 'B')
            cur.execute("SELECT setval(pg_get_serial_sequence('shipment_status_history','history_id'), (SELECT max(history_id) FROM shipment_status_history))")
    finally:
        conn.close()
    application = create_app({
        'APP_ENV': 'test', 'TESTING': True, 'DATABASE_URL': runtime,
        'SECRET_KEY': SECRET, 'IDEMPOTENCY_HASH_KEYS': [secrets.token_hex(32)], 'OUTBOX_ENCRYPTION_KEY': OUTBOX_KEY,
        'ALLOWED_ORIGINS': ['http://localhost', 'http://127.0.0.1'],
    })
    application.test_client_class = APIClient
    application.config['TEST_ADMIN_URL'] = admin
    with application.app_context():
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, email, password=TEST_PASSWORD):
    return client.post('/api/auth/login', json={'email': email, 'password': password})


def set_delivery_otp_for_tests(shipment_id, otp_code, driver_id=None):
    from flask import current_app
    conn = connect(current_app.config['TEST_ADMIN_URL'])
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE deliveries SET otp_code=%s, otp_attempts=0, otp_expires_at=%s, status='In Progress' WHERE shipment_id=%s",
                        (generate_password_hash(otp_code), datetime.now(timezone.utc)+timedelta(minutes=30), shipment_id))
    finally:
        conn.close()
