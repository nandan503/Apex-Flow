"""Adversarial and failure tests: real PostgreSQL, runtime is NOT table owner."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import psycopg2
import pytest
from cryptography.fernet import Fernet

from backend.app import create_app
from backend.authz import Caller
from backend.config import load_config
from backend.database import db_session, identity_session, connect
from backend.errors import AuthzError
from backend.migrate import migrate
from backend.outbox import deliver_one
from backend.services import get_all_shipments
from tests.conftest import login, TEST_PASSWORD, TENANT_A, TENANT_B, APIClient, set_delivery_otp_for_tests


def caller(role='ADMIN', tenant=TENANT_A, prefix=''):
    i = {'ADMIN': 1, 'MANAGER': 2, 'DRIVER': 3, 'CUSTOMER': 4}[role]
    return Caller(user_id=f'{prefix}USR00{i}', tenant_id=tenant, role=role, name=role,
                  email=f'{prefix}{role.lower()}@apexflow.com',
                  customer_id=f'{prefix}CUST001' if role=='CUSTOMER' else None,
                  driver_id=f'{prefix}DRV001' if role=='DRIVER' else None)


def admin_sql(app, sql, params=None):
    conn = connect(app.config['TEST_ADMIN_URL'])
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []
    finally:
        conn.close()


def payload(**extra):
    return {'customer_id': 'CUST001', 'pickup': 'Delhi', 'destination': 'Jaipur', 'weight': 100, **extra}


def clone_provider(app):
    other = create_app({k: app.config[k] for k in ('APP_ENV','TESTING','DATABASE_URL','SECRET_KEY','IDEMPOTENCY_HASH_KEYS','OUTBOX_ENCRYPTION_KEY','ALLOWED_ORIGINS')})
    other.test_client_class = APIClient
    return other


@pytest.mark.parametrize('role', ['ADMIN', 'MANAGER', 'DRIVER', 'CUSTOMER'])
def test_every_read_endpoint_role_and_tenant_matrix(client, role):
    login(client, f'{role.lower()}@apexflow.com')
    reads = {
        '/auth/me': set(['ADMIN','MANAGER','DRIVER','CUSTOMER']),
        '/shipments': set(['ADMIN','MANAGER','DRIVER','CUSTOMER']),
        '/shipments/SHP001': set(['ADMIN','MANAGER','DRIVER','CUSTOMER']),
        '/vehicles': set(['ADMIN','MANAGER','DRIVER']),
        '/drivers': set(['ADMIN','MANAGER','DRIVER']),
        '/customers': set(['ADMIN','MANAGER']),
        '/warehouses': set(['ADMIN','MANAGER']),
        '/routes': set(['ADMIN','MANAGER','DRIVER']),
        '/tracking': set(['ADMIN','MANAGER','DRIVER','CUSTOMER']),
        '/tracking/VEH001': set(['ADMIN','MANAGER','DRIVER','CUSTOMER']),
        '/deliveries': set(['ADMIN','MANAGER','DRIVER','CUSTOMER']),
        '/payments': set(['ADMIN','MANAGER','CUSTOMER']),
        '/reports/dashboard': set(['ADMIN','MANAGER','DRIVER','CUSTOMER']),
        '/notifications': set(['ADMIN','MANAGER','DRIVER','CUSTOMER']),
        **{f'/reports/{r}': set(['ADMIN','MANAGER']) for r in ['shipments','revenue','fleet','drivers','general']},
    }
    for endpoint, allowed in reads.items():
        r = client.get('/api'+endpoint)
        assert r.status_code == (200 if role in allowed else 403), (role, endpoint, r.json)
        text = r.get_data(as_text=True)
        assert not any(f'B{x}' in text for x in ('SHP001','VEH001','DRV001','CUST001','INV-2026-001','NOTIF001','RTE001','WH001'))
    for path in ['/shipments/BSHP001', '/shipments/'+str(uuid.uuid4()), '/tracking/BVEH001']:
        assert client.get('/api'+path).status_code == 404


@pytest.mark.parametrize('role', ['ADMIN', 'MANAGER', 'DRIVER', 'CUSTOMER'])
def test_every_mutation_role_matrix(client, role):
    login(client, f'{role.lower()}@apexflow.com')
    set_delivery_otp_for_tests('SHP001', '123456')
    staff = {'ADMIN','MANAGER'}
    all_roles = staff | {'DRIVER','CUSTOMER'}
    ops = [
        ('POST', '/shipments', payload(), staff|{'CUSTOMER'}, 201),
        ('POST', '/vehicles', {'registration_number': str(uuid.uuid4())}, staff, 201),
        ('POST', '/drivers', {'name': 'Test','license_number': str(uuid.uuid4())}, staff, 201),
        ('POST', '/customers', {'name': 'Test','company': 'Test','email': 'new@example.invalid'}, staff, 201),
        ('PUT', '/shipments/SHP004/assign', {'driver_id':'DRV001','vehicle_id':'VEH001'}, staff, 200),
        ('PUT', '/shipments/SHP001/status', {'status': 'Out for Delivery'}, staff|{'DRIVER'}, 200),
        ('POST', '/routes/optimize', {'pickup':'Delhi','destination':'Jaipur'}, staff|{'DRIVER'}, 200),
        ('POST', '/deliveries/confirm', {'shipment_id':'SHP001','otp_code':'123456'}, {'DRIVER'}, 200),
        ('POST', '/payments/INV-2026-002/collect', {}, staff, 200),
        ('PUT', '/notifications/NOTIF005/read', {}, {'ADMIN','CUSTOMER'}, 200),
        ('DELETE', '/shipments/SHP005', {}, {'ADMIN'}, 200),
    ]
    for method, endpoint, body, allowed, success in ops:
        r = client.open('/api'+endpoint, method=method, json=body)
        expected = success if role in allowed else (404 if endpoint.startswith('/notifications/') else 403)
        assert r.status_code == expected, (role, endpoint, r.json)
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/auth/me').status_code == 401


def test_all_protected_routes_require_authentication(app, client):
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith('api.') or rule.endpoint == 'api.login':
            continue
        path = rule.rule
        for arg in rule.arguments:
            path = path.replace('<'+arg+'>', 'known-id')
        for method in rule.methods - {'HEAD','OPTIONS'}:
            assert client.open(path, method=method, json={}).status_code == 401, (path, method)


def test_tenant_selector_and_client_privilege_fields_cannot_authorize(client):
    login(client, 'admin@apexflow.com')
    assert client.get('/api/shipments', headers={'X-Tenant-ID':TENANT_B}).status_code == 403
    r = client.post('/api/shipments', json=payload(tenant_id=TENANT_B,user_id='BUSR001',role='SYSTEM_ADMIN'))
    assert r.status_code == 201
    assert r.json['data']['shipment_id']
    assert client.get('/api/shipments?tenant_id='+TENANT_B).status_code == 200
    assert all(not s['shipment_id'].startswith('B') for s in client.get('/api/shipments').json['data'])
    r = client.post('/api/shipments', json=payload(customer_id='BCUST001'))
    assert r.status_code == 400
    assert 'BCUST001' not in r.get_data(as_text=True)
    assert client.put('/api/shipments/SHP001/assign', json={'driver_id':'BDRV001','vehicle_id':'BVEH001'}).status_code == 400
    for method, path, data in [('DELETE','/shipments/BSHP001',{}), ('PUT','/shipments/BSHP001/status',{'status':'Cancelled'}), ('POST','/payments/BINV-2026-001/collect',{})]:
        assert client.open('/api'+path, method=method, json=data).status_code == 404


def test_search_pagination_sort_and_exports_do_not_widen_tenant(client):
    login(client, 'admin@apexflow.com')
    paths = ['/shipments?search=%25&tenant_id='+TENANT_B, '/shipments?search=BSHP',
             '/shipments?limit=1&offset=1&sort=status&direction=asc', '/shipments?offset=100&sort=booking_date',
             '/shipments?status=In%20Transit', '/reports/shipments', '/reports/revenue']
    for path in paths:
        r = client.get('/api'+path)
        assert r.status_code == 200
        assert 'BSHP' not in r.get_data(as_text=True)
    for query in ['limit=100000', 'offset=-1', 'sort=tenant_id', 'sort=status;DROP TABLE users', 'direction=or 1=1']:
        assert client.get('/api/shipments?'+query).status_code == 400
    assert client.get('/api/shipments?limit=2&sort=created_at').json == client.get('/api/shipments?limit=2&sort=created_at').json
    # These capabilities do not exist. No catch-all bulk/import/file/admin route may appear.
    for path in ['/shipments/bulk', '/imports', '/exports', '/files/BSHP001/download', '/admin/users']:
        assert client.post('/api'+path, json={'tenant_id':TENANT_B,'ids':['BSHP001']}).status_code in (404,405)


def test_rls_unscoped_sql_and_connection_cleanup(app):
    tables = ['shipments','customers','drivers','vehicles','deliveries','payments','shipment_status_history',
              'routes','warehouses','notifications','maintenance_records','outbox_events','idempotency_records','audit_events','notification_reads','tenants','memberships']
    for _ in range(2):
        with identity_session() as conn, conn.cursor() as cur:
            for table in tables:
                cur.execute(f'SELECT count(*) FROM {table}')
                assert cur.fetchone()[0] == 0, table
        with db_session(caller()) as conn, conn.cursor() as cur:
            cur.execute('SELECT shipment_id FROM shipments')  # deliberately NO tenant filter
            assert {r[0] for r in cur.fetchall()} == {'SHP001','SHP002','SHP003','SHP004','SHP005'}
            cur.execute("UPDATE shipments SET description='attack' WHERE shipment_id='BSHP001'")
            assert cur.rowcount == 0
            cur.execute("DELETE FROM shipments WHERE shipment_id='BSHP001'")
            assert cur.rowcount == 0
    with pytest.raises(RuntimeError):
        with db_session(caller()):
            raise RuntimeError('simulate process failure')
    with identity_session() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM shipments')
        assert cur.fetchone()[0] == 0


def test_cross_tenant_fk_and_rls_insert_rejected(app):
    with pytest.raises(psycopg2.Error):
        with db_session(caller()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE shipments SET customer_id='BCUST001' WHERE shipment_id='SHP001'")
    with pytest.raises(psycopg2.Error):
        with db_session(caller()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE shipments SET tenant_id=%s WHERE shipment_id='SHP001'", (TENANT_B,))
    for sql in ['UPDATE audit_events SET operation=\'tamper\'', 'DELETE FROM audit_events', 'UPDATE memberships SET role=\'ADMIN\'', 'ALTER TABLE shipments DISABLE ROW LEVEL SECURITY']:
        with pytest.raises(psycopg2.Error):
            with db_session(caller()) as conn, conn.cursor() as cur:
                cur.execute(sql)


def test_background_service_requires_revalidated_context(app):
    with pytest.raises(TypeError):
        db_session().__enter__()
    for forged in [replace(caller(),tenant_id=TENANT_B), replace(caller('CUSTOMER'),role='ADMIN')]:
        with pytest.raises(AuthzError):
            get_all_shipments(caller=forged)
    with db_session(caller()):
        with pytest.raises(AuthzError):
            with db_session(caller('MANAGER')):
                pass
    assert not any(s['shipment_id'].startswith('B') for s in get_all_shipments(caller=caller()))


def test_lost_response_replayed_on_second_provider(app, client):
    login(client, 'admin@apexflow.com')
    key = str(uuid.uuid4())
    first = client.post('/api/shipments', json=payload(), headers={'Idempotency-Key':key})
    assert first.status_code == 201
    provider_b = clone_provider(app).test_client()
    provider_b.set_cookie('session', client.get_cookie('session').value)
    replay = provider_b.post('/api/shipments', json=payload(), headers={'Idempotency-Key':key})
    assert replay.status_code == 201
    assert replay.data == first.data
    assert replay.headers['Idempotency-Replayed'] == 'true'
    assert admin_sql(app,"SELECT count(*) FROM shipments WHERE tenant_id=%s",(TENANT_A,))[0][0] == 6
    assert admin_sql(app,'SELECT count(*) FROM outbox_events')[0][0] == 1
    assert admin_sql(app,'SELECT count(*) FROM audit_events')[0][0] == 1
    assert provider_b.post('/api/shipments', json=payload(weight=101), headers={'Idempotency-Key':key}).status_code == 409


def test_concurrent_duplicate_creation_commits_one_operation(app, client):
    login(client,'admin@apexflow.com')
    cookie = client.get_cookie('session').value
    key = str(uuid.uuid4())
    provider_b = clone_provider(app)
    def create(provider):
        c = provider.test_client()
        c.set_cookie('session', cookie)
        return c.post('/api/shipments', json=payload(), headers={'Idempotency-Key':key})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create, [app,provider_b]))
    assert [r.status_code for r in responses] == [201,201]
    assert responses[0].data == responses[1].data
    assert admin_sql(app,'SELECT count(*) FROM idempotency_records')[0][0] == 1
    assert admin_sql(app,'SELECT count(*) FROM payments WHERE tenant_id=%s',(TENANT_A,))[0][0] == 5


def test_idempotency_scope_and_failed_state(app, client):
    login(client,'admin@apexflow.com')
    key = str(uuid.uuid4())
    first = client.post('/api/shipments',json=payload(weight=-1),headers={'Idempotency-Key':key})
    second = client.post('/api/shipments',json=payload(weight=-1),headers={'Idempotency-Key':key})
    assert first.status_code == 400 and second.data == first.data
    assert second.headers['Idempotency-Replayed'] == 'true'
    assert admin_sql(app,'SELECT state FROM idempotency_records')[0][0] == 'FAILED'
    c2 = app.test_client(); login(c2,'manager@apexflow.com')
    assert c2.post('/api/shipments',json=payload(),headers={'Idempotency-Key':key}).status_code == 201
    c3 = app.test_client(); login(c3,'Badmin@apexflow.com')
    assert c3.post('/api/shipments',json=payload(customer_id='BCUST001'),headers={'Idempotency-Key':key}).status_code == 201
    assert client.post('/api/shipments',json=payload(),headers={'Idempotency-Key':''}).status_code == 400


def test_worker_failure_before_commit_rolls_back_business_outbox_and_replay(app,client,monkeypatch):
    login(client,'admin@apexflow.com')
    from backend import outbox
    original = outbox.enqueue
    def fail_after_enqueue(*args):
        original(*args)
        raise RuntimeError('worker terminated before commit')
    monkeypatch.setattr(outbox,'enqueue',fail_after_enqueue)
    key = str(uuid.uuid4())
    assert client.post('/api/shipments',json=payload(),headers={'Idempotency-Key':key}).status_code == 500
    for table in ['outbox_events','idempotency_records','audit_events']:
        assert admin_sql(app,f'SELECT count(*) FROM {table}')[0][0] == 0
    monkeypatch.setattr(outbox,'enqueue',original)
    assert client.post('/api/shipments',json=payload(),headers={'Idempotency-Key':key}).status_code == 201


def test_concurrent_otp_failures_cannot_lose_attempts(app,client):
    login(client,'driver@apexflow.com'); set_delivery_otp_for_tests('SHP001','123456')
    cookie = client.get_cookie('session').value
    def wrong(_):
        c = app.test_client(); c.set_cookie('session',cookie)
        return c.post('/api/deliveries/confirm',json={'shipment_id':'SHP001','otp_code':'999999'}).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(wrong,range(6))) == [400]*6
    assert admin_sql(app,"SELECT otp_attempts FROM deliveries WHERE shipment_id='SHP001'")[0][0] == 5


def test_otp_success_replay_and_payment_date_stability(app,client):
    login(client,'driver@apexflow.com'); set_delivery_otp_for_tests('SHP001','123456')
    key = str(uuid.uuid4()); data={'shipment_id':'SHP001','otp_code':'123456'}
    a=client.post('/api/deliveries/confirm',json=data,headers={'Idempotency-Key':key})
    b=client.post('/api/deliveries/confirm',json=data,headers={'Idempotency-Key':key})
    assert a.status_code == b.status_code == 200 and a.data == b.data
    assert admin_sql(app,"SELECT count(*) FROM shipment_status_history WHERE shipment_id='SHP001' AND status='Delivered'")[0][0] == 1
    c=app.test_client(); login(c,'admin@apexflow.com')
    admin_sql(app,"UPDATE payments SET paid_date='2020-01-01' WHERE invoice_id='INV-2026-001'")
    assert c.post('/api/payments/INV-2026-001/collect',json={}).status_code == 200
    assert str(admin_sql(app,"SELECT paid_date FROM payments WHERE invoice_id='INV-2026-001'")[0][0]) == '2020-01-01'


def test_csrf_requires_token_even_without_origin_and_rotates(client):
    token = client.get('/api/auth/csrf').json['data']['csrf_token']
    assert client.post('/api/auth/login',json={'email':'admin@apexflow.com','password':TEST_PASSWORD},auto_security=False).status_code == 403
    assert login(client,'admin@apexflow.com').status_code == 200
    assert client.post('/api/shipments',json=payload(),auto_security=False).status_code == 403
    assert client.post('/api/shipments',json=payload(),headers={'X-CSRF-Token':token}).status_code == 403
    assert client.post('/api/shipments',json=[],headers={}).status_code == 400


def test_logout_revoke_expiry_disable_and_membership_change(app,client):
    login(client,'admin@apexflow.com'); cookie=client.get_cookie('session').value
    clone=app.test_client(); clone.set_cookie('session',cookie)
    assert clone.get('/api/auth/me').status_code == 200
    assert client.post('/api/auth/logout').status_code == 200
    assert clone.get('/api/auth/me').status_code == 401
    login(client,'admin@apexflow.com')
    admin_sql(app,"UPDATE auth_sessions SET expires_at=now()-interval '1 second'")
    assert client.get('/api/auth/me').status_code == 401
    login(client,'admin@apexflow.com')
    admin_sql(app,"UPDATE users SET active=false WHERE user_id='USR001'")
    assert client.get('/api/auth/me').status_code == 401
    login(client,'manager@apexflow.com')
    admin_sql(app,"DELETE FROM memberships WHERE user_id='USR002'")
    assert client.get('/api/shipments').status_code == 403


def test_shared_login_budget_across_providers(app,client):
    b=clone_provider(app).test_client()
    results=[login(c,'admin@apexflow.com','incorrect').status_code for c in [client,b,client,b,client,b]]
    assert results == [401]*5+[429]


def test_db_outage_is_503_no_secrets_or_sql_in_response(app,client,monkeypatch,caplog):
    from backend import database
    def unavailable(*args,**kwargs):
        raise psycopg2.OperationalError('sensitive-dsn-password')
    monkeypatch.setattr(database,'connect',unavailable)
    r=client.get('/health/ready')
    assert r.status_code == 503 and r.headers['Retry-After'] == '2'
    assert 'sensitive-dsn-password' not in r.get_data(as_text=True)+caplog.text
    assert client.get('/health/live').status_code == 200


def test_outbox_encryption_retry_and_receiver_dedup(app,client):
    login(client,'admin@apexflow.com'); assert client.post('/api/shipments',json=payload()).status_code == 201
    encrypted=admin_sql(app,'SELECT payload FROM outbox_events')[0][0]
    assert 'recipient' not in encrypted and 'otp' not in encrypted
    delivered={}; side_effects=[]
    def consumer(event_id,kind,data):
        assert kind == 'delivery.otp' and data['tenant_id'] == TENANT_A
        if event_id not in delivered:
            delivered[event_id]=True; side_effects.append(data['shipment_id'])
            raise TimeoutError('ack lost after remote side effect')
    assert deliver_one(caller(),sender=consumer)
    assert admin_sql(app,'SELECT state FROM outbox_events')[0][0] == 'PENDING'
    admin_sql(app,"UPDATE outbox_events SET available_at=now()")
    assert deliver_one(caller(),sender=consumer)
    assert admin_sql(app,'SELECT state FROM outbox_events')[0][0] == 'COMPLETED'
    assert not deliver_one(caller(),sender=consumer)
    assert len(side_effects) == 1
    assert not deliver_one(caller(tenant=TENANT_B,prefix='B'),sender=consumer)
    with pytest.raises(AuthzError):
        deliver_one(replace(caller(),tenant_id=TENANT_B),sender=consumer)


def test_notification_read_is_per_principal(app,client):
    login(client,'admin@apexflow.com'); client.put('/api/notifications/NOTIF005/read')
    customer=app.test_client();login(customer,'customer@apexflow.com')
    note=next(x for x in customer.get('/api/notifications').json['data'] if x['notification_id']=='NOTIF005')
    assert note['is_read'] == 0
    assert customer.put('/api/notifications/BNOTIF005/read').status_code == 404


def test_startup_validation_is_read_only_and_rejects_privileged_role(app):
    before=admin_sql(app,"SELECT count(*) FROM users")[0][0]
    clone_provider(app); clone_provider(app)
    assert admin_sql(app,"SELECT count(*) FROM users")[0][0] == before
    config={k:app.config[k] for k in ('APP_ENV','TESTING','DATABASE_URL','SECRET_KEY','IDEMPOTENCY_HASH_KEYS','OUTBOX_ENCRYPTION_KEY','ALLOWED_ORIGINS')}
    config['DATABASE_URL']=app.config['TEST_ADMIN_URL']
    with pytest.raises(RuntimeError,match='startup validation failed'):
        create_app(config)
    migrate(app.config['TEST_ADMIN_URL'])  # repeat command is a checksum-verified no-op


def test_fail_closed_config_and_numeric_validation(app,client):
    config={k:app.config[k] for k in ('APP_ENV','TESTING','DATABASE_URL','SECRET_KEY','IDEMPOTENCY_HASH_KEYS','OUTBOX_ENCRYPTION_KEY','ALLOWED_ORIGINS')}
    for key in ['SECRET_KEY','DATABASE_URL','IDEMPOTENCY_HASH_KEYS','OUTBOX_ENCRYPTION_KEY']:
        with pytest.raises(RuntimeError):
            load_config({**config,key:None})
    for extra in [{'APP_ENV':'prod'}, {'DATABASE_URL':'sqlite:///demo.db'}, {'SECRET_KEY':'short'}, {'ALLOWED_ORIGINS':['*']}]:
        with pytest.raises(RuntimeError):
            load_config({**config,**extra})
    production=load_config({**config,'APP_ENV':'production','TESTING':False,'DATABASE_URL':'postgresql://runtime@db.invalid/apex?sslmode=verify-full','ALLOWED_ORIGINS':['https://app.example.invalid']})
    assert production['SESSION_COOKIE_SECURE'] and production['SESSION_COOKIE_HTTPONLY']
    login(client,'admin@apexflow.com')
    assert client.post('/api/shipments', json=payload(weight_kg=0)).status_code == 400
    for invalid in ['NaN','Infinity','-Infinity',True]:
        assert client.post('/api/shipments',json=payload(weight=invalid)).status_code == 400
    for path in ['/version','/api/version','/.env']:
        assert client.get(path).status_code == 404


def test_real_process_death_rolls_back_uncommitted_unit(app):
    import os
    import subprocess
    import sys
    import select
    config = {k:app.config[k] for k in ('APP_ENV','TESTING','DATABASE_URL','SECRET_KEY','IDEMPOTENCY_HASH_KEYS','OUTBOX_ENCRYPTION_KEY','ALLOWED_ORIGINS')}
    script = '''
import json, os, time
from backend.app import create_app
from backend.database import db_session
from tests.test_contracts import caller, payload
from backend.services import create_shipment
app=create_app(json.loads(os.environ['APEX_CHILD_CONFIG']))
with app.app_context(), db_session(caller()) as conn:
    create_shipment(payload(), caller())
    with conn.cursor() as cur:
        cur.execute("INSERT INTO idempotency_records(user_id,key,fingerprint,authorization_context,state) VALUES ('USR001','process-death-key','test','test','PENDING')")
    print('uncommitted', flush=True)
    time.sleep(30)
'''
    env = dict(os.environ, APEX_CHILD_CONFIG=json.dumps(config))
    process = subprocess.Popen([sys.executable,'-c',script],env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        assert select.select([process.stdout],[],[],15)[0], 'child transaction did not start'
        assert process.stdout.readline().strip() == 'uncommitted'
    finally:
        process.kill(); process.wait(timeout=10); process.stdout.close()
    # Acquiring the same tenant lock proves the backend transaction has released it.
    with db_session(caller()) as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM shipments'); assert cur.fetchone()[0] == 5
        for table in ('outbox_events','idempotency_records'):
            cur.execute(f'SELECT count(*) FROM {table}'); assert cur.fetchone()[0] == 0


def test_replay_cannot_restore_revoked_role(app,client):
    login(client,'admin@apexflow.com'); key=str(uuid.uuid4())
    assert client.post('/api/shipments',json=payload(),headers={'Idempotency-Key':key}).status_code == 201
    admin_sql(app,"UPDATE memberships SET role='CUSTOMER', customer_id='CUST002' WHERE user_id='USR001'")
    assert client.post('/api/shipments',json=payload(),headers={'Idempotency-Key':key}).status_code == 403


def test_keyring_rotation_preserves_replay(app,client):
    import secrets
    login(client,'admin@apexflow.com'); key=str(uuid.uuid4())
    first=client.post('/api/shipments',json=payload(),headers={'Idempotency-Key':key})
    old_fingerprint=admin_sql(app,'SELECT fingerprint FROM idempotency_records')[0][0]
    app.config['IDEMPOTENCY_HASH_KEYS'].insert(0,secrets.token_hex(32))
    replay=client.post('/api/shipments',json=payload(),headers={'Idempotency-Key':key})
    assert first.data == replay.data and replay.headers['Idempotency-Replayed'] == 'true'
    assert admin_sql(app,'SELECT fingerprint FROM idempotency_records')[0][0] != old_fingerprint
    app.config['IDEMPOTENCY_HASH_KEYS'].pop()
    assert client.post('/api/shipments',json=payload(),headers={'Idempotency-Key':key}).data == first.data


def test_password_rotation_revokes_all_provider_sessions(app,client,monkeypatch):
    import secrets
    from backend.cli import reset_password
    login(client,'admin@apexflow.com'); b=clone_provider(app).test_client()
    b.set_cookie('session',client.get_cookie('session').value)
    password=secrets.token_urlsafe(24)
    monkeypatch.setattr('backend.cli.getpass.getpass',lambda _:password)
    reset_password(app.config['TEST_ADMIN_URL'],'USR001')
    assert client.get('/api/auth/me').status_code == b.get('/api/auth/me').status_code == 401
    assert login(client,'admin@apexflow.com',TEST_PASSWORD).status_code == 401
    assert login(client,'admin@apexflow.com',password).status_code == 200


def test_multi_membership_requires_authorized_selector(app,client):
    admin_sql(app,"INSERT INTO memberships(tenant_id,user_id,role) VALUES (%s,'USR001','MANAGER')",(TENANT_B,))
    login(client,'admin@apexflow.com')
    assert client.get('/api/shipments').status_code == 403
    rows=client.get('/api/shipments',headers={'X-Tenant-ID':TENANT_B}).json['data']
    assert rows and all(r['shipment_id'].startswith('B') for r in rows)
    assert client.get('/api/shipments/SHP001',headers={'X-Tenant-ID':TENANT_B}).status_code == 404
    assert client.delete('/api/shipments/BSHP005',headers={'X-Tenant-ID':TENANT_B}).status_code == 403


def test_outbox_expiry_and_dead_letter_are_explicit(app,client):
    from datetime import datetime, timezone, timedelta
    login(client,'admin@apexflow.com'); client.post('/api/shipments',json=payload())
    key=Fernet(app.config['OUTBOX_ENCRYPTION_KEY'])
    stored=admin_sql(app,'SELECT payload FROM outbox_events')[0][0]
    data=json.loads(key.decrypt(stored.encode()))
    data['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    admin_sql(app,'UPDATE outbox_events SET payload=%s',(key.encrypt(json.dumps(data).encode()).decode(),))
    sent=[]
    assert deliver_one(caller(),sender=lambda *args:sent.append(args))
    assert not sent and admin_sql(app,'SELECT state FROM outbox_events')[0][0] == 'FAILED'
    # A non-expired event reaches a bounded dead-letter state after repeated failure.
    client.post('/api/shipments',json=payload())
    admin_sql(app,"UPDATE outbox_events SET attempts=9 WHERE state='PENDING'")
    def fail(*args): raise TimeoutError()
    assert deliver_one(caller(),sender=fail)
    assert all(r[0]=='FAILED' for r in admin_sql(app,'SELECT state FROM outbox_events'))


def test_production_tracking_never_fabricates_live_position(app,client):
    login(client,'admin@apexflow.com')
    app.config['APP_ENV']='production'
    response=client.get('/api/tracking')
    assert response.status_code == 503 and response.json['error']=='TELEMETRY_UNAVAILABLE'


def test_rls_rejects_unauthorized_raw_tenant_selector(app):
    with identity_session() as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.principal_id','USR001',true),set_config('app.tenant_id',%s,true)",(TENANT_B,))
        cur.execute('SELECT count(*) FROM shipments');assert cur.fetchone()[0]==0
        cur.execute('SELECT count(*) FROM tenants');assert cur.fetchone()[0]==0


def test_known_full_uuid_resource_and_file_selector_cannot_cross_tenants(app,client):
    b=app.test_client();login(b,'Badmin@apexflow.com')
    created=b.post('/api/shipments',json=payload(customer_id='BCUST001')).json['data']['shipment_id']
    assert len(created.split('-',1)[1])==32
    login(client,'admin@apexflow.com')
    assert client.get('/api/shipments/'+created).status_code==404
    for path in ['/api/files/'+created+'/download','/api/exports/'+created,'/api/imports/'+created]:
        assert client.get(path).status_code==404
    assert client.get('/api/shipments?search='+created).json['data']==[]


def test_concurrent_duplicate_outbox_job_has_one_delivery(app,client):
    login(client,'admin@apexflow.com'); client.post('/api/shipments',json=payload())
    sent=[]
    def work(_):
        with app.app_context():
            return deliver_one(caller(),sender=lambda *args:sent.append(args[0]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(work,[1,2]))==[False,True]
    assert len(sent)==1


def test_migration_checksum_and_transactional_failure(app,tmp_path,monkeypatch):
    from backend import migrate as migration_module
    original_files=migration_module.migration_files()
    bad=tmp_path/'999_invalid.sql';bad.write_text('CREATE TABLE migration_sentinel(id int); SELECT no_such_function();')
    monkeypatch.setattr(migration_module,'migration_files',lambda:original_files+[(bad,'test-checksum')])
    with pytest.raises(psycopg2.Error):
        migration_module.migrate(app.config['TEST_ADMIN_URL'])
    assert admin_sql(app,"SELECT to_regclass('public.migration_sentinel')")[0][0] is None
    # Exactly the shipped migration files are recorded (updated for 002 in Sprint 3).
    assert admin_sql(app,'SELECT count(*) FROM schema_migrations')[0][0]==len(original_files)
    monkeypatch.setattr(migration_module,'migration_files',lambda:original_files)
    # Tamper with ONE specific version row (multiple migrations exist since 002).
    checksum=admin_sql(app,"SELECT checksum FROM schema_migrations WHERE version='001_baseline.sql'")[0][0]
    admin_sql(app,"UPDATE schema_migrations SET checksum='tampered' WHERE version='001_baseline.sql'")
    try:
        with pytest.raises(RuntimeError,match='checksum mismatch'):
            migration_module.migrate(app.config['TEST_ADMIN_URL'])
        with pytest.raises(RuntimeError,match='startup validation failed'):
            clone_provider(app)
    finally:
        admin_sql(app,"UPDATE schema_migrations SET checksum=%s WHERE version='001_baseline.sql'",(checksum,))


def test_legacy_database_refused_without_modification(app):
    admin_sql(app,'ALTER TABLE schema_migrations RENAME TO saved_migration_history')
    try:
        with pytest.raises(RuntimeError,match='legacy database'):
            migrate(app.config['TEST_ADMIN_URL'])
        assert admin_sql(app,'SELECT count(*) FROM users')[0][0]==8
        assert admin_sql(app,"SELECT to_regclass('public.schema_migrations')")[0][0] is None
    finally:
        admin_sql(app,'ALTER TABLE saved_migration_history RENAME TO schema_migrations')


def test_import_has_no_database_or_configuration_side_effects():
    import subprocess
    import sys
    code='''import psycopg2
psycopg2.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError("import opened database"))
import backend.app
assert not hasattr(backend.app, 'app')
'''
    subprocess.run([sys.executable,'-c',code],check=True,timeout=15,capture_output=True)


def test_membership_removal_does_not_prevent_logout(app,client):
    login(client,'customer@apexflow.com')
    admin_sql(app,"DELETE FROM memberships WHERE user_id='USR004'")
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/auth/me').status_code==401


def test_db_rejects_nan_money_and_cross_tenant_recipient(app):
    with pytest.raises(psycopg2.Error):
        with db_session(caller()) as conn, conn.cursor() as cur:
            cur.execute("UPDATE payments SET amount='NaN', tax_amount='NaN', total_amount='NaN' WHERE invoice_id='INV-2026-001'")
    with pytest.raises(psycopg2.Error):
        with db_session(caller()) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO notifications(notification_id,title,message,type,recipient_user_id) VALUES ('test','test','test','info','BUSR001')")


def test_malformed_status_is_safe_validation_error(client):
    login(client,'admin@apexflow.com')
    for value in [True, {'value':'Cancelled'}, ['Cancelled']]:
        assert client.put('/api/shipments/SHP001/status',json={'status':value}).status_code==400
