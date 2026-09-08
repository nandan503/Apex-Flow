"""Deployment reproduction (Phase 8): boot the exact production command

    gunicorn 'backend.app:create_app()' --bind 0.0.0.0:${PORT:-5050}

against a real PostgreSQL (the CI service database) and verify
GET /health/live, GET /health/ready, CSRF bootstrap, and the complete
same-origin login flow over real TCP/HTTP, plus invalid-origin rejection.

Skipped locally when gunicorn is absent or no disposable database is
configured (the sandbox/CI matrix that provisions TEST_DATABASE_ADMIN_URL
runs it).
"""
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
import uuid

import pytest

from cryptography.fernet import Fernet

from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.skipif(
    shutil.which('gunicorn') is None, reason='gunicorn binary not installed')


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _wait_ready(port, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def test_gunicorn_factory_serves_health_and_same_origin_login(database_urls, app):
    """Exact gunicorn factory invocation from Dockerfile/Procfile."""
    if shutil.which('gunicorn') is None:
        pytest.skip('gunicorn binary not installed')
    admin, runtime = database_urls
    port = _free_port()
    origin = f'http://127.0.0.1:{port}'
    env = dict(os.environ)
    for key in ('APP_ENV', 'DATABASE_URL', 'SECRET_KEY', 'OUTBOX_ENCRYPTION_KEY',
                'IDEMPOTENCY_HASH_KEYS', 'ALLOWED_ORIGINS', 'TRUST_PROXY', 'PORT'):
        env.pop(key, None)
    env.update({
        'APP_ENV': 'test',
        'DATABASE_URL': runtime,
        'SECRET_KEY': secrets.token_hex(32),
        'OUTBOX_ENCRYPTION_KEY': Fernet.generate_key().decode(),
        'IDEMPOTENCY_HASH_KEYS': f'{secrets.token_hex(32)},{secrets.token_hex(32)}',
        'ALLOWED_ORIGINS': origin,
        'TRUST_PROXY': '0',
        'PORT': str(port),
    })
    proc = subprocess.Popen(
        ['gunicorn', 'backend.app:create_app()', '--bind', f'127.0.0.1:{port}',
         '--workers', '1', '--timeout', '15', '--log-level', 'warning'],
        cwd=os.getcwd(), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        assert _wait_ready(port), 'gunicorn did not start listening'
        cookies = {}

        def request(method, path, origin_hdr=None, extra=None, body=None):
            headers = {'Host': f'127.0.0.1:{port}', 'Content-Type': 'application/json'}
            if origin_hdr:
                headers['Origin'] = origin_hdr
            headers.update(extra or {})
            if cookies:
                headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
            payload = json.dumps(body) if body is not None else None
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            for kv in resp.getheaders():
                if kv[0].lower() == 'set-cookie':
                    name, value = kv[1].split(';', 1)[0].split('=', 1)
                    cookies[name] = value
            try:
                parsed = json.loads(data) if data else {}
            except ValueError:
                parsed = {}
            return resp.status, dict(resp.getheaders()), parsed

        status, _, body = request('GET', '/health/live')
        assert status == 200 and body.get('status') == 'live'

        status, _, body = request('GET', '/health/ready')
        assert status == 200 and body.get('status') == 'ready', body

        status, _, body = request('GET', '/api/auth/csrf')
        assert status == 200, body
        token = body['data']['csrf_token']
        assert cookies.get('session')

        # Same-origin login (Origin == request host) succeeds end-to-end.
        status, headers, body = request(
            'POST', '/api/auth/login', origin_hdr=origin,
            extra={'X-CSRF-Token': token, 'Idempotency-Key': str(uuid.uuid4())},
            body={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD})
        assert status == 200, body
        set_cookie = headers.get('Set-Cookie') or headers.get('set-cookie') or ''
        assert 'HttpOnly' in set_cookie and 'SameSite=Lax' in set_cookie

        # Login rotated the CSRF token; refetch like the browser does.
        status, _, body = request('GET', '/api/auth/csrf')
        token = body['data']['csrf_token']

        # Authenticated same-origin mutation still enforces CSRF.
        status, _, body = request('POST', '/api/shipments', origin_hdr=origin,
                                  extra={'X-CSRF-Token': token,
                                         'Idempotency-Key': 'gunicorn-boot-00000001'},
                                  body={'pickup': 'Delhi', 'destination': 'Jaipur',
                                        'customer_id': 'CUST001'})
        assert status == 201, body

        # Hostile cross-origin POST is rejected by the origin gate.
        status, _, body = request('POST', '/api/shipments',
                                  origin_hdr='https://attacker.example')
        assert status == 403 and body.get('error') == 'CSRF'

        # Preflight from the allowlisted same host gets exact CORS headers.
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        conn.request('OPTIONS', '/api/auth/login', headers={
            'Origin': origin,
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type,x-csrf-token',
        })
        resp = conn.getresponse()
        resp.read()
        hdrs = dict(resp.getheaders())
        conn.close()
        assert resp.status in (200, 204)
        assert hdrs.get('Access-Control-Allow-Origin') == origin
        assert hdrs.get('Access-Control-Allow-Credentials') == 'true'
        assert 'x-csrf-token' in (hdrs.get('Access-Control-Allow-Headers') or '').lower()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
