import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATABASE_PATH = os.path.join(DATA_DIR, 'apexflow.db')

# Environment & Cloud Configuration
FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
IS_PRODUCTION = FLASK_ENV == 'production'
DEBUG = not IS_PRODUCTION

# --- SECRET_KEY: Must be set explicitly. No insecure fallback in production. ---
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
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', 5050))

# Rate limiter storage (in-memory by default; set REDIS_URL for production)
RATELIMIT_STORAGE_URI = os.environ.get('REDIS_URL', 'memory://')

# CORS: comma-separated list of allowed origins
ALLOWED_ORIGINS = [
    o.strip() for o in
    os.environ.get('ALLOWED_ORIGINS', 'http://localhost:5050').split(',')
    if o.strip()
]

os.makedirs(DATA_DIR, exist_ok=True)
