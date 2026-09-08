"""Application-local configuration. Importing modules never opens or mutates a DB."""
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from cryptography.fernet import Fernet

BASE_DIR = str(Path(__file__).resolve().parent.parent)


def load_config(overrides=None):
    env = os.environ
    config = {
        'APP_ENV': env.get('APP_ENV', env.get('FLASK_ENV', 'production')),
        'SECRET_KEY': env.get('SECRET_KEY'),
        'DATABASE_URL': env.get('DATABASE_URL'),
        'OUTBOX_ENCRYPTION_KEY': env.get('OUTBOX_ENCRYPTION_KEY'),
        'IDEMPOTENCY_HASH_KEYS': [x.strip() for x in (env.get('IDEMPOTENCY_HASH_KEYS') or '').split(',') if x.strip()],
        'OUTBOX_DELIVERY_URL': env.get('OUTBOX_DELIVERY_URL'),
        'OUTBOX_API_TOKEN': env.get('OUTBOX_API_TOKEN'),
        'ALLOWED_ORIGINS': [x.strip().rstrip('/') for x in env.get('ALLOWED_ORIGINS', '').split(',') if x.strip()],
        'TRUST_PROXY': env.get('TRUST_PROXY') == '1',
        'MAX_CONTENT_LENGTH': 64 * 1024,
        'TESTING': False,
        'DEBUG': False,
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'SESSION_REFRESH_EACH_REQUEST': False,
        'PERMANENT_SESSION_LIFETIME': 8 * 3600,
    }
    config.update(overrides or {})
    mode = config['APP_ENV']
    if mode not in ('production', 'development', 'test'):
        raise RuntimeError('APP_ENV must be production, development, or test')
    if mode == 'production' and (config['TESTING'] or config['DEBUG']):
        raise RuntimeError('Production cannot enable TESTING or DEBUG')
    for key in ('SECRET_KEY', 'DATABASE_URL', 'OUTBOX_ENCRYPTION_KEY', 'IDEMPOTENCY_HASH_KEYS'):
        if not config.get(key):
            raise RuntimeError(f'{key} is required')
    keys = config['IDEMPOTENCY_HASH_KEYS']
    if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or len(k) < 32 for k in keys):
        raise RuntimeError('IDEMPOTENCY_HASH_KEYS must contain generated keys of at least 32 characters')
    secret = config['SECRET_KEY']
    if len(secret) < 32 or any(x in secret.lower() for x in ('replace_', 'change_this', 'your_super', 'example')):
        raise RuntimeError('SECRET_KEY must be a generated secret of at least 32 characters')
    if mode == 'production' and parse_qs(urlsplit(config['DATABASE_URL']).query).get('sslmode') != ['verify-full']:
        raise RuntimeError('Production DATABASE_URL must explicitly set sslmode=verify-full')
    if urlsplit(config['DATABASE_URL']).scheme not in ('postgresql', 'postgres'):
        raise RuntimeError('PostgreSQL DATABASE_URL is required; SQLite is not supported')
    try:
        Fernet(config['OUTBOX_ENCRYPTION_KEY'])
    except (ValueError, TypeError):
        raise RuntimeError('OUTBOX_ENCRYPTION_KEY must be a Fernet key') from None
    origins = config['ALLOWED_ORIGINS']
    if not origins:
        raise RuntimeError('ALLOWED_ORIGINS is required')
    for origin in origins:
        parsed = urlsplit(origin)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query
                or parsed.fragment or parsed.scheme not in ('http', 'https') or '*' in origin):
            raise RuntimeError('ALLOWED_ORIGINS must contain exact HTTP(S) origins')
        if mode == 'production' and parsed.scheme != 'https':
            raise RuntimeError('Production origins must use HTTPS')
    if config.get('OUTBOX_DELIVERY_URL'):
        p = urlsplit(config['OUTBOX_DELIVERY_URL'])
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment:
            raise RuntimeError('OUTBOX_DELIVERY_URL must be an HTTPS endpoint without credentials')
        if not config.get('OUTBOX_API_TOKEN'):
            raise RuntimeError('OUTBOX_API_TOKEN is required when delivery is configured')
    config['SESSION_COOKIE_SECURE'] = mode == 'production'
    return config
