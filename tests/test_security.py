"""Regression tests for the production security audit gates."""

import os

from tests.conftest import login, TEST_PASSWORD, set_delivery_otp_for_tests


def test_unauthenticated_shipments_rejected(client):
    r = client.get('/api/shipments')
    assert r.status_code == 401


def test_customer_cannot_get_foreign_shipment(client):
    assert login(client, 'customer@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.get('/api/shipments/SHP003')
    assert r.status_code == 404
    r = client.get('/api/shipments/SHP001')
    assert r.status_code == 200
    assert r.get_json()['data']['customer_id'] == 'CUST001'


def test_customer_list_is_scoped(client):
    assert login(client, 'customer@apexflow.com', TEST_PASSWORD).status_code == 200
    data = client.get('/api/shipments').get_json()['data']
    assert data
    assert all(s['customer_id'] == 'CUST001' for s in data)
    assert 'SHP003' not in [s['shipment_id'] for s in data]


def test_customer_cannot_list_drivers_or_fleet(client):
    assert login(client, 'customer@apexflow.com', TEST_PASSWORD).status_code == 200
    assert client.get('/api/drivers').status_code == 403
    assert client.get('/api/vehicles').status_code == 403
    assert client.get('/api/warehouses').status_code == 403


def test_customer_dashboard_is_own_only(client):
    assert login(client, 'customer@apexflow.com', TEST_PASSWORD).status_code == 200
    kpis = client.get('/api/reports/dashboard').get_json()['data']
    ids = [s['shipment_id'] for s in kpis['recent_shipments']]
    assert 'SHP003' not in ids
    assert 'SHP001' in ids
    assert kpis['total_revenue'] == '8500.00'
    assert kpis['estimated_fuel_cost'] == '0.00'


def test_customer_cannot_confirm_foreign_otp(client):
    assert login(client, 'customer@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/deliveries/confirm', json={
        'shipment_id': 'SHP001', 'otp_code': '4912', 'receiver_name': 'Attacker',
    })
    assert r.status_code == 403


def test_customer_notifications_are_role_scoped(client):
    assert login(client, 'customer@apexflow.com', TEST_PASSWORD).status_code == 200
    notes = client.get('/api/notifications').get_json()['data']
    assert all(n['user_role'] in ('CUSTOMER', None) for n in notes)
    r = client.put('/api/notifications/NOTIF001/read')
    assert r.status_code == 404


def test_driver_cannot_read_unassigned_shipment(client):
    assert login(client, 'driver@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.get('/api/shipments/SHP003')
    assert r.status_code == 404
    r = client.get('/api/shipments/SHP001')
    assert r.status_code == 200


def test_driver_cannot_mark_unassigned_delivered(client):
    assert login(client, 'driver@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.put('/api/shipments/SHP002/status', json={'status': 'Delivered'})
    assert r.status_code in (403, 404)


def test_driver_cannot_skip_otp_on_assigned_shipment(client):
    assert login(client, 'driver@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.put('/api/shipments/SHP001/status', json={'status': 'Delivered'})
    assert r.status_code == 403
    assert 'OTP' in r.get_json()['message']


def test_driver_create_shipment_forbidden(client):
    assert login(client, 'driver@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/shipments', json={
        'pickup': 'Delhi', 'destination': 'Mumbai', 'customer_id': 'CUST005',
    })
    assert r.status_code == 403


def test_otp_not_in_deliveries_payload(client):
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    data = client.get('/api/deliveries').get_json()['data']
    assert data
    for row in data:
        assert 'otp_code' not in row
        assert 'otp_attempts' not in row


def test_create_shipment_ignores_client_payment_status(client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/shipments', json={
        'customer_id': 'CUST002',
        'pickup': 'Delhi',
        'destination': 'Jaipur',
        'weight': 100,
        'status': 'Delivered',
        'payment_status': 'Paid',
        'shipping_cost': 1,
    })
    assert r.status_code == 201
    body = r.get_json()['data']
    assert body['status'] == 'Booked'
    assert body['payment_status'] == 'Pending'
    assert body['shipping_cost'] != 1
    assert body['customer_id'] == 'CUST002'


def test_negative_weight_rejected(client):
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/shipments', json={
        'customer_id': 'CUST001', 'pickup': 'A', 'destination': 'B', 'weight': -1,
    })
    assert r.status_code == 400


def test_vehicle_status_not_mass_assignable(client):
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/vehicles', json={
        'registration_number': 'TEST-HYGIENE-1',
        'status': 'On Trip',
    })
    assert r.status_code == 201
    assert r.get_json()['data']['status'] == 'Available'


def test_tracking_unknown_vehicle_is_404(client):
    assert login(client, 'customer@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.get('/api/tracking/DOESNOTEXIST')
    assert r.status_code == 404


def test_otp_confirm_and_replay(client):
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    set_delivery_otp_for_tests('SHP001', '847291', driver_id='DRV001')
    client.post('/api/auth/logout')

    assert login(client, 'driver@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/deliveries/confirm', json={
        'shipment_id': 'SHP001', 'otp_code': '847291', 'receiver_name': 'Receiver',
    })
    assert r.status_code == 200
    replay = client.post('/api/deliveries/confirm', json={
        'shipment_id': 'SHP001', 'otp_code': '847291', 'receiver_name': 'Receiver',
    })
    assert replay.status_code == 400

    client.post('/api/auth/logout')
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    payments = client.get('/api/payments').get_json()['data']
    shp002 = [p for p in payments if p['shipment_id'] == 'SHP002'][0]
    assert shp002['payment_status'] == 'Pending'


def test_delivery_does_not_auto_pay_invoice(client):
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    assert client.put('/api/shipments/SHP004/assign', json={
        'driver_id': 'DRV001', 'vehicle_id': 'VEH001',
    }).status_code == 200
    set_delivery_otp_for_tests('SHP004', '654321', driver_id='DRV001')
    client.post('/api/auth/logout')
    assert login(client, 'driver@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/deliveries/confirm', json={
        'shipment_id': 'SHP004', 'otp_code': '654321', 'receiver_name': 'Dock',
    })
    assert r.status_code == 200, r.get_json()
    client.post('/api/auth/logout')
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    payments = client.get('/api/payments').get_json()['data']
    shp004 = [p for p in payments if p['shipment_id'] == 'SHP004'][0]
    assert shp004['payment_status'] == 'Pending'


def test_manager_cannot_confirm_otp(client):
    set_delivery_otp_for_tests('SHP001', '111111', driver_id='DRV001')
    assert login(client, 'manager@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/deliveries/confirm', json={
        'shipment_id': 'SHP001', 'otp_code': '111111', 'receiver_name': 'Mgr',
    })
    assert r.status_code == 403


def test_password_must_be_string(client):
    r = client.post('/api/auth/login', json={
        'email': 'admin@apexflow.com', 'password': [TEST_PASSWORD],
    })
    assert r.status_code == 400


def test_csrf_rejects_foreign_origin(client):
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.delete('/api/shipments/SHP005', headers={'Origin': 'https://evil.com'})
    assert r.status_code == 403
    assert r.get_json()['error'] == 'CSRF'


def test_evil_host_and_origin_request_is_unauthenticated(client):
    """A request whose Origin and Host BOTH claim attacker control is the
    attacker's own origin at best: the same-origin rule classifies it as
    same-origin-with-itself, but session cookies are bound to the real site
    and never follow a forged Host header — so the request arrives
    unauthenticated and is denied (401). The browser-relevant cross-site
    attack (victim Host, evil Origin) is rejected at the origin gate with
    403 CSRF — see test_csrf_rejects_foreign_origin. Replaced 2026-09-08:
    the previous assertion (403 at the origin gate for this exact request)
    encoded the old gate that also rejected legitimate same-origin logins;
    the denial now happens at the equally-strict authentication layer."""
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.delete('/api/shipments/SHP005', headers={
        'Origin': 'http://evil.com',
        'Host': 'evil.com',
    })
    assert r.status_code == 401


def test_seed_otp_4912_rejected(client):
    assert login(client, 'driver@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.post('/api/deliveries/confirm', json={
        'shipment_id': 'SHP001', 'otp_code': '4912', 'receiver_name': 'Leak',
    })
    assert r.status_code == 400


def test_admin_delete_shipment_with_payments(client):
    assert login(client, 'admin@apexflow.com', TEST_PASSWORD).status_code == 200
    r = client.delete('/api/shipments/SHP003')
    assert r.status_code == 200
    assert client.get('/api/shipments/SHP003').status_code == 404


def test_login_page_has_no_demo_passwords():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    html = open(os.path.join(root, 'frontend', 'login.html'), encoding='utf-8').read()
    assert TEST_PASSWORD not in html
    assert TEST_PASSWORD not in html
    assert 'selectDemoRole' not in html


def test_security_headers_present(client):
    r = client.get('/login.html')
    assert r.headers.get('X-Frame-Options') == 'DENY'
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'
    assert 'Content-Security-Policy' in r.headers


def test_login_rate_limit(client):
    codes = [login(client, 'admin@apexflow.com', 'incorrect').status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
