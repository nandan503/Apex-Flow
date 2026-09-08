import hmac
import os
import secrets
import time
import uuid
from urllib.parse import urlsplit

from flask import Flask, send_from_directory, request, session, g
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.middleware.proxy_fix import ProxyFix
import psycopg2

from backend.config import BASE_DIR, load_config
from backend.database import validate_database, identity_session
from backend.routes import api_bp
from backend.errors import AppError
from backend.utils import error_response, json_response
from backend.auth import load_session_caller, session_authenticated
from backend.contracts import tenant_endpoint, SAFE
from backend.logger import app_logger


def _origin_matches_request(origin):
    """True only when a well-formed Origin IS this request's own origin.

    Browsers attach Origin to same-origin POSTs; such a request is by
    definition not cross-site and must not depend on the cross-origin
    ALLOWED_ORIGINS list. Scheme, host and port must all match the request;
    userinfo, path/query/fragment or a malformed port fail closed to the
    allowlist. Host is used only to recognize the request's own origin —
    never to grant a foreign Origin cross-origin access.
    """
    try:
        parts = urlsplit(origin)
        origin_port = parts.port  # ValueError on malformed ports
    except ValueError:
        return False
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return False
    if parts.username or parts.password or parts.path not in ('', '/'):
        return False
    if parts.query or parts.fragment:
        return False
    try:
        host_parts = urlsplit(f'//{request.host.lower()}')
        request_port = host_parts.port
    except ValueError:
        return False
    # Omitted ports mean the scheme default on BOTH sides.
    request_port = request_port or (443 if request.scheme == 'https' else 80)
    origin_port = origin_port or (443 if parts.scheme == 'https' else 80)
    return (parts.scheme == request.scheme
            and parts.hostname.lower() == (host_parts.hostname or '')
            and origin_port == request_port)


def create_app(config_overrides=None):
    frontend_dir = os.path.join(BASE_DIR, 'frontend')
    app = Flask(__name__, static_folder=frontend_dir, static_url_path='')
    app.config.update(load_config(config_overrides))
    if app.config['TRUST_PROXY']:
        # Only enable behind an ingress that overwrites forwarded headers and blocks direct access.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    CORS(app, origins=app.config['ALLOWED_ORIGINS'], supports_credentials=True,
         allow_headers=['Content-Type', 'X-CSRF-Token', 'X-Tenant-ID', 'Idempotency-Key'],
         methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
    app.register_blueprint(api_bp)
    for endpoint, view in list(app.view_functions.items()):
        if endpoint.startswith('api.') and endpoint not in {'api.login', 'api.logout', 'api.get_current_user'}:
            app.view_functions[endpoint] = tenant_endpoint(view)

    @app.before_request
    def security_context():
        g.caller = None
        g.request_id = str(uuid.uuid4())  # never trust a client log identifier
        g.started_at = time.monotonic()
        if not request.path.startswith('/api/') or request.method == 'OPTIONS':
            return None
        origin = request.headers.get('Origin')
        if request.method not in SAFE and origin:
            # Same-origin mutations (Origin == this request's own origin) are
            # not cross-site and never need the cross-origin allowlist.
            # Genuine cross-origin mutations require the exact allowlist entry.
            if origin.rstrip('/') not in app.config['ALLOWED_ORIGINS'] and not _origin_matches_request(origin):
                return error_response('Cross-origin request blocked', 403, code='CSRF')
        public = request.endpoint in {'api.login', 'csrf_token'}
        # Unknown routes stay 404; never grant an implicit capability.
        if request.url_rule is None:
            return error_response('Not found', 404)
        authenticated = session_authenticated() if request.endpoint == 'api.logout' else (True if public else load_session_caller())
        if not authenticated:
            return error_response('Authentication required', 401)
        if request.method not in SAFE:
            expected = session.get('csrf_token', '')
            supplied = request.headers.get('X-CSRF-Token', '')
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                return error_response('Valid CSRF token required', 403, code='CSRF')
            if request.content_length and (not request.is_json or not isinstance(request.get_json(silent=True), dict)):
                return error_response('A JSON object is required', 400)

    @app.route('/api/auth/csrf')
    def csrf_token():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_urlsafe(32)
        return json_response(data={'csrf_token': session['csrf_token']})

    @app.errorhandler(AppError)
    def handle_app_error(err):
        return error_response(err.message, err.status, code=err.code)

    @app.errorhandler(psycopg2.Error)
    def database_error(err):
        app_logger.warning('database_failure request_id=%s type=%s', getattr(g, 'request_id', ''), type(err).__name__)
        response, code = error_response('Database unavailable; retry with the same idempotency key', 503, code='DATABASE_UNAVAILABLE')
        response.headers['Retry-After'] = '2'
        return response, code

    @app.errorhandler(HTTPException)
    def handle_http(err):
        return error_response('Not found' if err.code == 404 else err.name, err.code)

    @app.errorhandler(Exception)
    def handle_unexpected(err):
        app_logger.error('request_failure request_id=%s type=%s', getattr(g, 'request_id', ''), type(err).__name__)
        return error_response('Internal server error', 500, code='INTERNAL')

    @app.after_request
    def security_headers(response):
        response.headers.update({
            'X-Request-ID': getattr(g, 'request_id', ''),
            'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'strict-origin-when-cross-origin',
            'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
            'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        })
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if app.config['APP_ENV'] == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        app_logger.info('request_complete request_id=%s endpoint=%s method=%s status=%s duration_ms=%s',
                        getattr(g, 'request_id', ''), request.endpoint or 'unmatched', request.method,
                        response.status_code, round((time.monotonic()-getattr(g, 'started_at', time.monotonic()))*1000))
        return response

    @app.route('/health/live')
    def live():
        return {'status': 'live'}

    @app.route('/health/ready')
    def ready():
        with identity_session() as conn, conn.cursor() as cur:
            cur.execute('SELECT 1')
        return {'status': 'ready'}

    @app.route('/')
    def index():
        return send_from_directory(frontend_dir, 'login.html')

    @app.route('/<path:path>')
    def serve_static(path):
        if path.startswith('api/'):
            return error_response('Not found', 404)
        try:
            return send_from_directory(frontend_dir, path)
        except NotFound:
            if not path.endswith('.html'):
                return send_from_directory(frontend_dir, f'{path}.html')
            raise

    with app.app_context():
        try:
            validate_database()
        except Exception as exc:
            # Do not print DSNs, SQL values, or driver exception messages.
            raise RuntimeError(f'Database startup validation failed ({type(exc).__name__})') from None
    return app
