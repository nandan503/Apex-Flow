import os
import tempfile

os.environ.setdefault('SECRET_KEY', 'unit-test-secret-key-not-for-prod-use')
os.environ.setdefault('FLASK_ENV', 'development')
os.environ.setdefault(
    'DATABASE_PATH',
    os.path.join(tempfile.gettempdir(), 'apexflow-bootstrap-test.db'),
)
os.environ.setdefault('ALLOWED_ORIGINS', 'http://localhost,http://127.0.0.1')

import pytest

from backend.app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    db = tmp_path / 'apexflow-test.db'
    monkeypatch.setenv('DATABASE_PATH', str(db))
    monkeypatch.setenv('FLASK_ENV', 'development')
    monkeypatch.setenv('SECRET_KEY', 'unit-test-secret-key-not-for-prod-use')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    application = create_app({
        'TESTING': True,
        'DATABASE_PATH': str(db),
        'SECRET_KEY': 'unit-test-secret-key-not-for-prod-use',
        'RATELIMIT_ENABLED': False,
    })
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, email, password):
    return client.post('/api/auth/login', json={'email': email, 'password': password})
