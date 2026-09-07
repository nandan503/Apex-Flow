from functools import wraps

from flask import session, request, g
from werkzeug.security import check_password_hash

from backend.database import db_session
from backend.models import row_to_dict
from backend.utils import error_response
from backend.authz import Caller
from backend.logger import (
    log_login_success, log_login_failure,
    log_auth_required, log_forbidden
)

USER_SAFE_COLUMNS = (
    "user_id, name, email, role, phone, customer_id, driver_id, created_at"
)


def _row_to_caller(user: dict) -> Caller:
    return Caller(
        user_id=user['user_id'],
        role=user['role'],
        name=user.get('name') or '',
        email=user.get('email') or '',
        customer_id=user.get('customer_id') or None,
        driver_id=user.get('driver_id') or None,
    )


def load_session_caller():
    """Re-read role and identity links from the DB on every request."""
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user:
        session.clear()
        return None
    session['role'] = user['role']
    return _row_to_caller(user)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        caller = load_session_caller()
        if not caller:
            log_auth_required(
                endpoint=request.endpoint or request.path,
                ip=request.remote_addr
            )
            return error_response('Authentication required', 401)
        g.caller = caller
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            caller = load_session_caller()
            if not caller:
                log_auth_required(
                    endpoint=request.endpoint or request.path,
                    ip=request.remote_addr
                )
                return error_response('Authentication required', 401)
            if caller.role not in roles:
                log_forbidden(
                    user_id=caller.user_id,
                    role=caller.role,
                    endpoint=request.endpoint or request.path,
                    ip=request.remote_addr
                )
                return error_response(
                    f"Forbidden — requires role: {' or '.join(roles)}", 403
                )
            g.caller = caller
            return f(*args, **kwargs)
        return decorated
    return decorator


def authenticate_user(email, password, ip='unknown'):
    email = (email or '').strip().lower()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, name, email, role, phone, customer_id, driver_id, "
            "created_at, password_hash FROM users WHERE LOWER(email) = ?",
            (email,),
        )
        user_row = cursor.fetchone()

    if not user_row:
        log_login_failure(email=email, ip=ip, reason='user_not_found')
        return None, "Invalid email or password"

    user = row_to_dict(user_row)
    if not check_password_hash(user['password_hash'], password):
        log_login_failure(email=email, ip=ip, reason='bad_password')
        return None, "Invalid email or password"

    log_login_success(
        email=email,
        user_id=user['user_id'],
        role=user['role'],
        ip=ip
    )
    user.pop('password_hash', None)
    return user, None


def get_user_by_id(user_id):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {USER_SAFE_COLUMNS} FROM users WHERE user_id = ?",
            (user_id,),
        )
        return row_to_dict(cursor.fetchone())
