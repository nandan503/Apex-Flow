"""HTTP unit of work and transactional replay contract, shared by all providers."""
import hashlib
import hmac
import json
import re
import uuid
from functools import wraps

from flask import request, g, current_app, make_response
from psycopg2 import IntegrityError, DataError

from backend.database import db_session
from backend.errors import AppError
from backend.utils import error_response

SAFE = {'GET', 'HEAD', 'OPTIONS'}


def tenant_endpoint(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        caller = g.caller
        mutation = request.method not in SAFE
        if mutation:
            key = request.headers.get('Idempotency-Key', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', key):
                return error_response('Idempotency-Key (16–128 opaque characters) required', 400, code='IDEMPOTENCY_REQUIRED')
            # Bind exact bytes, method, path and query; no body/password/OTP in logs or storage.
            material = b'\0'.join([request.method.encode(), request.path.encode(), request.query_string, request.get_data()])
            fingerprints = [hashlib.sha256(k.encode()).hexdigest()[:16] + ':' + hmac.new(k.encode(), material, hashlib.sha256).hexdigest()
                            for k in current_app.config['IDEMPOTENCY_HASH_KEYS']]
            fingerprint = fingerprints[0]
            authority = json.dumps([caller.role, caller.customer_id, caller.driver_id])
        with db_session(caller) as conn, conn.cursor() as cur:
            if mutation:
                cur.execute('SELECT fingerprint, authorization_context, state, status_code, response_body FROM idempotency_records WHERE tenant_id=%s AND user_id=%s AND key=%s',
                            (caller.tenant_id, caller.user_id, key))
                old = cur.fetchone()
                if old:
                    if old['authorization_context'] != authority:
                        return error_response('Authorization changed since this operation', 403)
                    if not any(hmac.compare_digest(old['fingerprint'], f) for f in fingerprints):
                        return error_response('Idempotency key reused with different request', 409, code='IDEMPOTENCY_CONFLICT')
                    if old['state'] == 'PENDING':
                        return error_response('Operation pending; retry with the same key', 409, code='IDEMPOTENCY_PENDING')
                    if caller.is_driver and request.endpoint in ('api.update_status', 'api.confirm_delivery_api'):
                        from backend.services import get_shipment_by_id
                        resource = (request.view_args or {}).get('shipment_id') or (request.get_json(silent=True) or {}).get('shipment_id')
                        if not get_shipment_by_id(resource, caller):
                            return error_response('Shipment not found', 404)
                    cur.execute('UPDATE idempotency_records SET fingerprint=%s WHERE tenant_id=%s AND user_id=%s AND key=%s',
                                (fingerprint, caller.tenant_id, caller.user_id, key))
                    response = current_app.response_class(old['response_body'], status=old['status_code'], mimetype='application/json')
                    response.headers['Idempotency-Replayed'] = 'true'
                    return response
                cur.execute("INSERT INTO idempotency_records(tenant_id,user_id,key,fingerprint,authorization_context,state) VALUES (%s,%s,%s,%s,%s,'PENDING')",
                            (caller.tenant_id, caller.user_id, key, fingerprint, authority))
            cur.execute('SAVEPOINT business')
            try:
                response = make_response(view(*args, **kwargs))
                cur.execute('SET CONSTRAINTS ALL IMMEDIATE')
            except (AppError, IntegrityError, DataError) as exc:
                cur.execute('ROLLBACK TO SAVEPOINT business')
                if isinstance(exc, AppError):
                    response = make_response(error_response(exc.message, exc.status, code=exc.code))
                else:
                    # Constraint errors can contain foreign IDs or sensitive values.
                    response = make_response(error_response('Invalid or conflicting resource data', 409, code='CONSTRAINT'))
            if mutation and response.status_code >= 500:
                # Never persist ambiguous/infrastructure failure as a completed operation.
                raise RuntimeError('Business endpoint returned a server error')
            if mutation:
                state = 'COMPLETED' if response.status_code < 400 else 'FAILED'
                cur.execute('''UPDATE idempotency_records SET state=%s, status_code=%s, response_body=%s
                               WHERE tenant_id=%s AND user_id=%s AND key=%s''',
                            (state, response.status_code, response.get_data(as_text=True), caller.tenant_id, caller.user_id, key))
                selectors = request.view_args or {}
                resource_id = next(iter(selectors.values()), None)
                if resource_id is None and request.is_json:
                    resource_id = (request.get_json(silent=True) or {}).get('shipment_id')
                cur.execute('INSERT INTO audit_events(event_id,user_id,operation,status_code,request_key,resource_id) VALUES (%s,%s,%s,%s,%s,%s)',
                            (str(uuid.uuid4()), caller.user_id, f'{request.method} {request.url_rule.rule}', response.status_code, key,
                             str(resource_id)[:200] if resource_id is not None else None))
        # Connection commits BEFORE returning any response; loss now is replay-safe.
        return response
    return wrapped
