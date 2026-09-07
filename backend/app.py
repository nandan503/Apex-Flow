import os
import sys
from datetime import timedelta

# Ensure root directory is in python path
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from flask import Flask, send_from_directory, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from backend.config import (
    SECRET_KEY, HOST, PORT, BASE_DIR, DEBUG,
    RATELIMIT_STORAGE_URI, ALLOWED_ORIGINS
)
from backend.database import initialize_database
from backend.routes import api_bp
from backend.logger import log_rate_limit_hit

# ── App Initialization ─────────────────────────────────────────────────────────
frontend_dir = os.path.join(BASE_DIR, 'frontend')
app = Flask(__name__, static_folder=frontend_dir, static_url_path='')
app.secret_key = SECRET_KEY

# ── Session Cookie Security (F-12) ────────────────────────────────────────────
app.config.update(
    SESSION_COOKIE_SECURE=not DEBUG,    # HTTPS-only in production
    SESSION_COOKIE_HTTPONLY=True,       # No JS access to session cookie
    SESSION_COOKIE_SAMESITE='Lax',      # CSRF protection
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

# ── CORS — Locked to explicit origin allowlist (F-06) ─────────────────────────
CORS(app,
     origins=ALLOWED_ORIGINS,
     supports_credentials=True,
     allow_headers=['Content-Type', 'X-Requested-With'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])

# ── Rate Limiter (F-04) ────────────────────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=RATELIMIT_STORAGE_URI,
    default_limits=["200 per minute"],   # Global fallback limit
    headers_enabled=True                 # Return X-RateLimit-* headers
)


@app.errorhandler(429)
def ratelimit_handler(e):
    log_rate_limit_hit(endpoint=request.endpoint or request.path,
                       ip=request.remote_addr)
    return jsonify({
        'success': False,
        'message': 'Too many requests. Please slow down.',
        'error': 'RATE_LIMITED'
    }), 429


# ── Register Blueprint ─────────────────────────────────────────────────────────
app.register_blueprint(api_bp)

# Apply per-endpoint rate limits after blueprint is registered
with app.app_context():
    login_view = app.view_functions.get('api.login')
    confirm_view = app.view_functions.get('api.confirm_delivery_api')
    if login_view:
        limiter.limit("10 per minute")(login_view)
    if confirm_view:
        limiter.limit("5 per minute")(confirm_view)

# ── Database Initialization ────────────────────────────────────────────────────
try:
    initialize_database()
except Exception as e:
    print(f"[Database Init Warning] {e}")

# ── Frontend Static Page Routing ───────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(frontend_dir, 'login.html')


@app.route('/<path:path>')
def serve_static(path):
    if os.path.exists(os.path.join(frontend_dir, path)):
        return send_from_directory(frontend_dir, path)
    elif os.path.exists(os.path.join(frontend_dir, f"{path}.html")):
        return send_from_directory(frontend_dir, f"{path}.html")
    return send_from_directory(frontend_dir, 'login.html')


if __name__ == '__main__':
    print("=" * 60)
    print("  🚀 APEX FLOW - Smart Transport & Logistics System")
    print("  Tagline: 'Smarter Logistics. Faster Tomorrow.'")
    print("=" * 60)
    print(f"  Server Running on host {HOST} port {PORT}")
    print("=" * 60)
    app.run(host=HOST, port=PORT, debug=DEBUG)
