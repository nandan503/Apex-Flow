"""
backend/logger.py — Centralized structured security logger.

All security-relevant events go through this module.
PII fields (email, phone, receiver_name) are partially redacted in logs.
"""

import logging
import json
import re
from datetime import datetime, timezone

from flask import has_request_context, g

# Configure root app logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

security_logger = logging.getLogger('apexflow.security')
app_logger = logging.getLogger('apexflow.app')


def _redact_email(email: str) -> str:
    """Partially redact email: user@domain.com → us**@domain.com"""
    if not email or '@' not in email:
        return '***'
    local, domain = email.split('@', 1)
    return local[:2] + '**@' + domain


def _redact_phone(phone: str) -> str:
    """Redact all but last 4 digits of phone number."""
    digits = re.sub(r'\D', '', str(phone or ''))
    if len(digits) < 4:
        return '****'
    return '****' + digits[-4:]


def _log_event(logger, level: str, event: str, **fields):
    """Emit a structured JSON log line for a security event."""
    allowed = {'user_id', 'role', 'email', 'ip', 'reason', 'endpoint', 'resource',
               'resource_id', 'shipment_id', 'attempt', 'receiver', 'context', 'error_type'}
    fields = {key: value for key, value in fields.items() if key in allowed}
    if has_request_context():
        fields['request_id'] = getattr(g, 'request_id', None)
        fields['tenant_id'] = getattr(getattr(g, 'caller', None), 'tenant_id', None)
    record = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'event': event,
        **fields
    }
    msg = json.dumps(record, default=str)
    getattr(logger, level)(msg)


# ── Public logging functions ────────────────────────────────────────────────

def log_login_success(email: str, user_id: str, role: str, ip: str):
    _log_event(security_logger, 'info', 'LOGIN_SUCCESS',
               user_id=user_id, role=role,
               email=_redact_email(email), ip=ip)


def log_login_failure(email: str, ip: str, reason: str = 'bad_credentials'):
    _log_event(security_logger, 'warning', 'LOGIN_FAILURE',
               email=_redact_email(email), ip=ip, reason=reason)


def log_auth_required(endpoint: str, ip: str):
    _log_event(security_logger, 'warning', 'AUTH_REQUIRED',
               endpoint=endpoint, ip=ip)


def log_forbidden(user_id: str, role: str, endpoint: str, ip: str):
    _log_event(security_logger, 'warning', 'FORBIDDEN',
               user_id=user_id, role=role, endpoint=endpoint, ip=ip)


def log_delete(user_id: str, role: str, resource: str, resource_id: str, ip: str):
    _log_event(security_logger, 'warning', 'RESOURCE_DELETE',
               user_id=user_id, role=role,
               resource=resource, resource_id=resource_id, ip=ip)


def log_otp_attempt(shipment_id: str, success: bool, attempt_count: int, ip: str):
    event = 'OTP_SUCCESS' if success else 'OTP_FAILURE'
    level = 'info' if success else 'warning'
    _log_event(security_logger, level, event,
               shipment_id=shipment_id,
               attempt=attempt_count, ip=ip)


def log_otp_locked(shipment_id: str, ip: str):
    _log_event(security_logger, 'error', 'OTP_LOCKED',
               shipment_id=shipment_id, ip=ip)


def log_delivery_confirmed(shipment_id: str, receiver_name: str, ip: str):
    safe_name = (receiver_name or 'Unknown')[:3] + '***'
    _log_event(security_logger, 'info', 'DELIVERY_CONFIRMED',
               shipment_id=shipment_id, receiver=safe_name, ip=ip)


def log_rate_limit_hit(endpoint: str, ip: str):
    _log_event(security_logger, 'warning', 'RATE_LIMIT_HIT',
               endpoint=endpoint, ip=ip)


def log_app_error(context: str, error: Exception):
    _log_event(app_logger, 'error', 'APP_ERROR',
               context=context, error_type=type(error).__name__)
