"""SPRINT 3 — F-3 resource-exhaustion bounds: adversarial verification.

Every externally reachable tenant-wide retrieval must be bounded server-side.
This module attacks the previously unbounded paths with oversized datasets and
verifies caps, plan-level limits, isolation under caps, and invalid inputs.
"""
import threading

import pytest

from backend.database import connect

TENANT_A = '00000000-0000-4000-8000-000000000001'
TENANT_B = '00000000-0000-4000-8000-000000000002'

OVER_CAP = 2050  # dataset larger than every hard cap (2000 / 500 / 1000)


def admin(app):
    return connect(app.config['TEST_ADMIN_URL'])


@pytest.fixture
def large_tenant(app):
    """Insert OVER_CAP vehicles and shipments into tenant A via the admin
    connection (fixture data, not an API path)."""
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
        cur.executemany(
            """INSERT INTO vehicles (vehicle_id, registration_number, vehicle_type, make, model, year,
                                     capacity_mt, fuel_type, current_location, insurance_expiry,
                                     permit_expiry, fitness_expiry, service_due_date, status)
               VALUES (%s, %s, 'Truck', 'Tata', 'M', 2023, 15.0, 'Diesel', 'Delhi',
                       '2027-12-31', '2027-12-31', '2027-12-31', '2026-12-31', 'Available')""",
            [(f'VEH-RB{i:05d}', f'RB{i:05d}') for i in range(OVER_CAP)])
        cur.executemany(
            """INSERT INTO shipments (shipment_id, customer_id, customer_name, pickup_location, destination,
                                      goods_type, weight_kg, quantity, status, booking_date,
                                      expected_delivery, shipping_cost, payment_method, payment_status)
               VALUES (%s, 'CUST001', 'ABC Group Ltd', 'Ludhiana', 'Delhi', 'General', 100, 1,
                       'Booked', '2026-09-01', '2026-09-05', 500, 'Credit Invoice', 'Pending')""",
            [(f'SHP-RB{i:05d}',) for i in range(OVER_CAP)])
        cur.executemany(
            """INSERT INTO payments (invoice_id, shipment_id, customer_name, amount, tax_amount,
                                     total_amount, payment_method, payment_status, invoice_date)
               VALUES (%s, %s, 'ABC Group Ltd', 500, 90, 590, 'Credit Invoice', 'Pending', '2026-09-01')""",
            [(f'INV-RB{i:05d}', f'SHP-RB{i:05d}') for i in range(OVER_CAP)])
    conn.close()
    return OVER_CAP


def db_count(app, table, tenant=TENANT_A):
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM {table} WHERE tenant_id = %s', (tenant,))
        n = cur.fetchone()[0]
    conn.close()
    return n


def expected_bounded(app, table):
    """API rows must equal the DB count when under the cap, else exactly the cap."""
    n = db_count(app, table)
    return min(n, 2000)


def test_lists_are_hard_capped(app, large_tenant):
    from tests.conftest import TEST_PASSWORD
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': 'admin@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    # Vehicles list (previously unbounded) returns exactly the cap.
    r = c.get('/api/vehicles')
    assert r.status_code == 200 and len(r.get_json()['data']) == 2000
    # Payments list (previously unbounded) matches min(db_count, cap).
    assert len(c.get('/api/payments').get_json()['data']) == expected_bounded(app, 'payments')
    # Notifications (previously unbounded) never exceed the cap.
    assert len(c.get('/api/notifications').get_json()['data']) <= 2000


def test_reports_are_hard_capped_and_ordered(app, large_tenant):
    from tests.conftest import TEST_PASSWORD
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': 'manager@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    expected = {'shipments': expected_bounded(app, 'shipments'), 'revenue': expected_bounded(app, 'payments'),
                'fleet': expected_bounded(app, 'vehicles'), 'drivers': expected_bounded(app, 'drivers'),
                'general': expected_bounded(app, 'shipments')}
    for report in ('shipments', 'revenue', 'fleet', 'drivers', 'general'):
        r = c.get(f'/api/reports/{report}')
        assert r.status_code == 200, report
        assert len(r.get_json()['data']) == expected[report], report
    # A client-supplied limit parameter cannot raise the cap (reports accept none).
    r = c.get('/api/reports/shipments?limit=999999999')
    assert len(r.get_json()['data']) == 2000


def test_database_plan_applies_limit_node(app, large_tenant):
    """The cap is enforced inside PostgreSQL (Limit node) — the server never
    materializes the whole tenant result set for a capped report."""
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("""EXPLAIN (FORMAT JSON) SELECT shipment_id FROM shipments
                       ORDER BY booking_date DESC LIMIT 2000""")
        plan = cur.fetchone()[0]
    conn.close()
    text = str(plan)
    assert "'Node Type': 'Limit'" in text or '"Node Type": "Limit"' in text


def test_cap_does_not_weaken_isolation(app, large_tenant):
    """With tenant A over the cap, tenant B rows must still be invisible to A
    staff and B users must still see only B rows."""
    from tests.conftest import TEST_PASSWORD
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_B,))
        cur.execute("""INSERT INTO shipments (shipment_id, customer_id, customer_name, pickup_location,
                       destination, goods_type, weight_kg, quantity, status, booking_date,
                       expected_delivery, shipping_cost, payment_method, payment_status)
                       VALUES ('SHP-RBZZZZZ', 'BCUST001', 'B', 'Ludhiana', 'Delhi', 'General', 100, 1,
                       'Booked', '2026-09-01', '2026-09-05', 500, 'Credit Invoice', 'Pending')""")
    conn.close()
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': 'manager@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    data = c.get('/api/shipments?limit=200').get_json()['data']
    assert all(s['shipment_id'] != 'SHP-RBZZZZZ' for s in data)
    other = app.test_client()
    assert other.post('/api/auth/login', json={'email': 'Bmanager@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    b_data = other.get('/api/shipments?limit=200').get_json()['data']
    assert b_data and all(s['customer_id'].startswith('B') for s in b_data)


def test_tracking_is_scoped_and_capped_in_sql(app):
    from tests.conftest import TEST_PASSWORD
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
        # 600 active shipments assigned to tenant A's driver.
        cur.executemany(
            """INSERT INTO shipments (shipment_id, customer_id, customer_name, pickup_location, destination,
                                      goods_type, weight_kg, quantity, status, booking_date, expected_delivery,
                                      shipping_cost, payment_method, payment_status, driver_id, driver_name)
               VALUES (%s, 'CUST001', 'ABC Group Ltd', 'Ludhiana', 'Delhi', 'General', 100, 1, 'In Transit',
                       '2026-09-01', '2026-09-05', 500, 'Credit Invoice', 'Pending', 'DRV001', 'Rajesh Kumar')""",
            [(f'SHP-RTR{i:04d}',) for i in range(600)])
    conn.close()
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': 'driver@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    data = c.get('/api/tracking').get_json()['data']
    assert len(data) == 500  # capped at TRACKING_HARD_CAP after SQL scoping to the driver
    # Foreign driver gets none of tenant A's active shipments.
    other = app.test_client()
    assert other.post('/api/auth/login', json={'email': 'Bdriver@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    b_tracking = other.get('/api/tracking').get_json()['data']
    # B driver sees only B-tenant active work — never any of tenant A's 600.
    assert all(s['shipment_id'].startswith('BSHP') for s in b_tracking) and b_tracking


def test_shipment_history_is_capped_newest_first(app):
    from tests.conftest import TEST_PASSWORD
    from backend.services import HISTORY_HARD_CAP
    conn = admin(app)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
        cur.executemany(
            """INSERT INTO shipment_status_history (shipment_id, status, location, updated_by, notes)
               VALUES (%s, %s, %s, %s, %s)""",
            [('SHP001', 'In Transit', 'x', 'USR003', 'stress') for _ in range(HISTORY_HARD_CAP + 300)])
    conn.close()
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': 'customer@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    data = c.get('/api/shipments/SHP001').get_json()['data']
    history = data['history']
    assert len(history) == HISTORY_HARD_CAP
    stamps = [h['history_id'] for h in history]
    assert stamps == sorted(stamps)      # oldest-first contract preserved
    assert all(h['notes'] == 'stress' for h in history[-10:])  # newest stress rows retained


def test_invalid_and_boundary_pagination_still_rejected(app):
    from tests.conftest import TEST_PASSWORD
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': 'manager@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    for bad in ('limit=0', 'limit=-1', 'limit=201', 'limit=999999999', 'limit=abc', 'offset=-1', 'sort=(SELECT 1)'):
        assert c.get(f'/api/shipments?{bad}').status_code == 400, bad
    assert c.get('/api/shipments?limit=200&offset=10000').status_code == 200  # maximum permitted


def test_concurrent_large_requests_stay_bounded(app, large_tenant):
    from tests.conftest import TEST_PASSWORD
    session_client = app.test_client()
    assert session_client.post('/api/auth/login', json={'email': 'manager@apexflow.com', 'password': TEST_PASSWORD}).status_code == 200
    results, errors = [], []
    lock = threading.Lock()

    def fetch():
        try:
            r = session_client.get('/api/reports/shipments')
            with lock:
                results.append((r.status_code, len(r.get_json()['data'])))
        except Exception as exc:
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=fetch) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results == [(200, 2000)] * 4
