"""Encrypted transactional outbox. Delivery is at-least-once, not exactly-once.

The HTTPS consumer MUST durably deduplicate Idempotency-Key before producing a
receiver-visible side effect. No redirects (avoid credential forwarding).
"""
import json
import uuid
from datetime import datetime, timezone
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

from cryptography.fernet import Fernet
from flask import current_app

from backend.database import db_session
from backend.authz import require_admin


def enqueue(conn, kind, payload):
    event_id = str(uuid.uuid4())
    envelope = Fernet(current_app.config['OUTBOX_ENCRYPTION_KEY']).encrypt(json.dumps(payload).encode()).decode()
    with conn.cursor() as cur:
        cur.execute('INSERT INTO outbox_events(event_id,kind,payload) VALUES (%s,%s,%s)', (event_id, kind, envelope))
    return event_id


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_event(event_id, kind, payload):
    url = current_app.config.get('OUTBOX_DELIVERY_URL')
    token = current_app.config.get('OUTBOX_API_TOKEN')
    if not url or not token:
        raise RuntimeError('Outbox consumer is not configured')
    req = Request(url, data=json.dumps({'event_id': event_id, 'kind': kind, 'payload': payload}).encode(),
                  headers={'Content-Type': 'application/json', 'Idempotency-Key': event_id,
                           'Authorization': f'Bearer {token}'}, method='POST')
    with build_opener(NoRedirect()).open(req, timeout=5) as response:
        if not 200 <= response.status < 300:
            raise HTTPError(url, response.status, 'Consumer rejected event', {}, None)


def deliver_one(caller, sender=None):
    require_admin(caller)
    sender = sender or send_event
    with db_session(caller) as conn, conn.cursor() as cur:
        cur.execute("""SELECT event_id, kind, payload, attempts FROM outbox_events
                       WHERE state='PENDING' AND available_at <= now()
                       ORDER BY available_at, event_id LIMIT 1 FOR UPDATE SKIP LOCKED""")
        row = cur.fetchone()
        if not row:
            return False
        event_id = str(row['event_id'])
        attempts = row['attempts'] + 1
        state = 'PENDING'
        try:
            payload = json.loads(Fernet(current_app.config['OUTBOX_ENCRYPTION_KEY']).decrypt(row['payload'].encode()))
            if payload.get('expires_at') and datetime.fromisoformat(payload['expires_at']) <= datetime.now(timezone.utc):
                state = 'FAILED'
            else:
                sender(event_id, row['kind'], payload)
                state = 'COMPLETED'
        except Exception:
            # Never log encrypted/plaintext payload, token, recipient or remote response.
            if attempts >= 10:
                state = 'FAILED'
        cur.execute("""UPDATE outbox_events SET state=%s, attempts=%s,
                       available_at=now()+(%s * interval '1 second'),
                       delivered_at=CASE WHEN %s='COMPLETED' THEN now() ELSE NULL END
                       WHERE event_id=%s""",
                    (state, attempts, min(3600, 2**attempts), state, event_id))
        cur.execute('INSERT INTO audit_events(event_id,user_id,operation,status_code,request_key,resource_id) VALUES (%s,%s,%s,%s,%s,%s)',
                    (str(uuid.uuid4()), caller.user_id, 'OUTBOX ' + state, 200 if state == 'COMPLETED' else 202, event_id, event_id))
    return True
