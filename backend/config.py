import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')


def get_database_path():
    """Resolved at call time so tests can point at a temp file."""
    return os.environ.get('DATABASE_PATH') or os.path.join(DATA_DIR, 'apexflow.db')


DATABASE_PATH = get_database_path()

FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
IS_PRODUCTION = FLASK_ENV == 'production'
DEBUG = not IS_PRODUCTION

_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    if IS_PRODUCTION:
        raise RuntimeError(
            "[SECURITY] SECRET_KEY environment variable is not set. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    else:
        import secrets as _secrets
        _secret_key = _secrets.token_hex(32)
        print("[WARNING] SECRET_KEY not set. Using a random ephemeral key (dev only). "
              "Sessions will not persist across restarts.")
SECRET_KEY = _secret_key

DATABASE_URL = os.environ.get('DATABASE_URL', '')
# Bind all interfaces in production (containers). Local default is loopback.
HOST = os.environ.get('HOST', '0.0.0.0' if IS_PRODUCTION else '127.0.0.1')
PORT = int(os.environ.get('PORT', 5050))

RATELIMIT_STORAGE_URI = os.environ.get('REDIS_URL', 'memory://')
TRUST_PROXY = os.environ.get('TRUST_PROXY', '0') == '1'
MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 64 * 1024))

ALLOWED_ORIGINS = [
    o.strip() for o in
    os.environ.get('ALLOWED_ORIGINS', 'http://localhost:5050,http://127.0.0.1:5050').split(',')
    if o.strip()
]

os.makedirs(DATA_DIR, exist_ok=True)

KNOWN_DEFAULT_PASSWORDS = {
    'admin@apexflow.com': 'Admin@123',
    'manager@apexflow.com': 'Manager@123',
    'driver@apexflow.com': 'Driver@123',
    'customer@apexflow.com': 'Customer@123',
}
