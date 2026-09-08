"""RED TEAM — outbox delivery-duplicate reality, worker tenant scope, config fail-close."""

import pytest

from backend.database import connect

TENANT_A = '00000000-0000-4000-8000-000000000001'
TENANT_B = '00000000-0000-4000-8000-000000000002'


def admin(app):
    return connect(app.config['TEST_ADMIN_URL'])


def test_outbox_is_at_least_once_consumer_may_receive_duplicates(app):
    """DOCUMENTED FINDING (by design): crash after the consumer side effect but
    before the acknowledgement leads to a SECOND delivery of the same event.
    Exactly-once is impossible here; the HTTPS consumer MUST deduplicate on the
    stable Idempotency-Key (event_id)."""
    from backend.app import create_app  # noqa: F401 — ensure app context active
    from backend.authz import Caller
    from backend.database import db_session
    from backend.outbox import deliver_one

    caller = Caller(user_id='USR001', tenant_id=TENANT_A, role='ADMIN',
                    name='a', email='admin@apexflow.com')
    with app.app_context():
        with db_session(caller) as conn:
            from backend.outbox import enqueue as _e
            event_id = _e(conn, 'redteam.test', {'hello': 'world'})

    deliveries = []

    class CrashAfterEffect(Exception):
        pass

    def crashing_sender(event_id, kind, payload):
        deliveries.append(event_id)      # consumer-side effect happened
        raise CrashAfterEffect('worker died before acknowledgement')

    def healthy_sender(event_id, kind, payload):
        deliveries.append(event_id)

    with app.app_context():
        # 'Crash' (any sender exception) is swallowed into a retry, never lost:
        assert deliver_one(caller, sender=crashing_sender) is True
        conn0 = admin(app)
        with conn0, conn0.cursor() as cur:
            cur.execute("SELECT state, attempts, available_at > now() FROM outbox_events WHERE event_id=%s",
                        (event_id,))
            state, attempts, backed_off = cur.fetchone()
        conn0.close()
        assert (state, attempts, backed_off) == ('PENDING', 1, True)  # retry scheduled with backoff
        conn0 = admin(app)
        with conn0, conn0.cursor() as cur:  # simulate backoff elapsing (stale-lease recovery)
            cur.execute("UPDATE outbox_events SET available_at = now() - interval '1 second' WHERE event_id=%s",
                        (event_id,))
        conn0.close()
        assert deliver_one(caller, sender=healthy_sender) is True    # retry happens later
        assert deliver_one(caller, sender=healthy_sender) is False   # queue drained

    assert deliveries == [event_id, event_id]  # DUPLICATE delivered to consumer
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT state, attempts FROM outbox_events WHERE event_id=%s", (event_id,))
        state, attempts = cur.fetchone()
        cur.execute("SELECT count(*) FROM outbox_events WHERE tenant_id=%s AND state='PENDING'", (TENANT_A,))
        assert cur.fetchone()[0] == 0
    conn.close()
    assert state == 'COMPLETED' and attempts == 2


def test_outbox_worker_is_tenant_scoped(app):
    """An ADMIN worker of tenant A cannot claim or even see tenant B events."""
    from backend.authz import Caller
    from backend.database import db_session
    from backend.outbox import deliver_one, enqueue

    ca = Caller(user_id='USR001', tenant_id=TENANT_A, role='ADMIN', name='a', email='a@x')
    cb = Caller(user_id='BUSR001', tenant_id=TENANT_B, role='ADMIN', name='b', email='b@x')
    sent = []
    with app.app_context():
        with db_session(cb) as conn:
            event_b = enqueue(conn, 'redteam.b', {'tenant': 'B'})
        assert deliver_one(ca, sender=lambda *a: sent.append(a)) is False  # nothing visible
        assert deliver_one(cb, sender=lambda *a: sent.append(a)) is True
        assert deliver_one(cb, sender=lambda *a: sent.append(a)) is False
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT state, attempts FROM outbox_events WHERE event_id=%s", (event_b,))
        state, attempts = cur.fetchone()
    conn.close()
    assert (state, attempts) == ('COMPLETED', 1)
    assert len(sent) == 1


def test_worker_admin_role_revalidated_not_trusted(app):
    """A CLI-created context claiming ADMIN without a real ADMIN membership fails."""
    from backend.authz import Caller
    from backend.database import db_session
    from backend.errors import AuthzError

    impostor = Caller(user_id='USR004', tenant_id=TENANT_A, role='ADMIN', name='d', email='x')
    with app.app_context():
        with pytest.raises(AuthzError):
            with db_session(impostor):
                pass


def test_demo_fixture_uses_generated_credentials():
    """No static default password may appear in test seeding."""
    import inspect
    from tests import demo_data
    src = inspect.getsource(demo_data)
    for banned in ('Admin@123', 'Manager@123', 'Driver@123', 'Customer@123'):
        assert banned not in src
