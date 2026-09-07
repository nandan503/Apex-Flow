import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from flask import Flask, send_from_directory, request, jsonify, abort
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.middleware.proxy_fix import ProxyFix

from backend.config import (
    SECRET_KEY, HOST, PORT, BASE_DIR, DEBUG,
    ALLOWED_ORIGINS, TRUST_PROXY, MAX_CONTENT_LENGTH, IS_PRODUCTION,
    RATELIMIT_STORAGE_URI,
)
from backend.database import initialize_database
from backend.routes import api_bp
from backend.limiter import limiter
from backend.logger import log_rate_limit_hit
from backend.errors import AppError
from backend.utils import error_response


def create_app(config_overrides=None):
    frontend_dir = os.path.join(BASE_DIR, 'frontend')
    app = Flask(__name__, static_folder=frontend_dir, static_url_path='')
    app.secret_key = SECRET_KEY
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
    app.config['SESSION_REFRESH_EACH_REQUEST'] = False
    app.config.update(
        SESSION_COOKIE_SECURE=IS_PRODUCTION,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        TESTING=False,
    )
    if config_overrides:
        app.config.update(config_overrides)
        db_path = config_overrides.get('DATABASE_PATH')
        if db_path:
            os.environ['DATABASE_PATH'] = db_path

    if TRUST_PROXY:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    CORS(
        app,
        origins=ALLOWED_ORIGINS,
        supports_credentials=True,
        allow_headers=['Content-Type', 'X-Requested-With'],
        methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    )

    if app.config.get('TESTING') and not app.config.get('TESTING_RATE_LIMIT'):
        app.config['RATELIMIT_ENABLED'] = False
    limiter.init_app(app)

    if IS_PRODUCTION and str(RATELIMIT_STORAGE_URI).startswith('memory://'):
        print("[WARNING] REDIS_URL is not set. Rate limits are in-memory and "
              "will not be shared across Gunicorn workers.")

    app.register_blueprint(api_bp)

    @app.errorhandler(429)
    def ratelimit_handler(e):
        log_rate_limit_hit(endpoint=request.endpoint or request.path,
                           ip=request.remote_addr)
        return jsonify({
            'success': False,
            'message': 'Too many requests. Please slow down.',
            'error': 'RATE_LIMITED',
            'data': None,
        }), 429

    @app.errorhandler(AppError)
    def handle_app_error(err):
        return error_response(err.message, err.status, code=getattr(err, 'code', None))

    @app.errorhandler(HTTPException)
    def handle_http(err):
        if err.code == 404 and request.path.startswith('/api/'):
            return error_response('Not found', 404)
        if err.code and err.code >= 400 and request.path.startswith('/api/'):
            return error_response(err.description or 'Error', err.code)
        return err

    @app.errorhandler(Exception)
    def handle_unexpected(err):
        if isinstance(err, HTTPException):
            return err
        print(f"[ERROR] {type(err).__name__}")
        return error_response('Internal server error', 500, code='INTERNAL')

    @app.before_request
    def csrf_origin_check():
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return None
        if not request.path.startswith('/api/'):
            return None
        origin = request.headers.get('Origin')
        if not origin:
            return None
        allowed = {o.rstrip('/') for o in ALLOWED_ORIGINS}
        # Do not trust Host / X-Forwarded-Host — only the explicit allowlist.
        if origin.rstrip('/') not in allowed:
            return error_response('Cross-origin request blocked', 403, code='CSRF')
        return None

    @app.after_request
    def security_headers(response):
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if IS_PRODUCTION:
            response.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains'
            )
        return response

    @app.route('/')
    def index():
        return send_from_directory(frontend_dir, 'login.html')

    @app.route('/<path:path>')
    def serve_static(path):
        # send_from_directory already rejects path traversal.
        try:
            return send_from_directory(frontend_dir, path)
        except NotFound:
            if not path.endswith('.html'):
                try:
                    return send_from_directory(frontend_dir, f'{path}.html')
                except NotFound:
                    abort(404)
            abort(404)

    try:
        initialize_database()
    except Exception as e:
        if app.config.get('TESTING'):
            raise
        print(f"[Database Init Error] {type(e).__name__}: {e}")
        if IS_PRODUCTION:
            raise

    return app


app = create_app()


if __name__ == '__main__':
    print("=" * 60)
    print("  APEX FLOW - Smart Transport & Logistics System")
    print("  Tagline: 'Smarter Logistics. Faster Tomorrow.'")
    print("=" * 60)
    print(f"  Server Running on host {HOST} port {PORT}")
    print("=" * 60)
    app.run(host=HOST, port=PORT, debug=DEBUG)
