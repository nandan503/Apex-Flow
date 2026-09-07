import secrets
import uuid
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash

from backend.database import db_session
from backend.models import row_to_dict, rows_to_list
from backend.logger import log_otp_attempt, log_otp_locked
from backend.authz import (
    Caller, deny, require_staff, require_admin,
    assert_shipment_visible, shipment_visible,
    can_create_shipment, can_assign_shipment,
    assert_status_transition, delivery_confirmable,
    vehicle_visible, driver_visible,
)
from backend.errors import ValidationError
from backend.validation import (
    bound_text, parse_weight, parse_quantity, parse_year, parse_capacity, parse_experience,
)
OTP_TTL_MINUTES = 30
OTP_MAX_ATTEMPTS = 5

SHIPMENT_COLUMNS = (
    "shipment_id, customer_id, customer_name, pickup_location, destination, "
    "goods_type, description, weight_kg, quantity, vehicle_id, vehicle_reg, "
    "driver_id, driver_name, status, booking_date, expected_delivery, "
    "shipping_cost, payment_method, payment_status, special_instructions, created_at"
)

DELIVERY_PUBLIC_COLUMNS = (
    "delivery_id, shipment_id, customer_name, driver_id, vehicle_reg, "
    "pickup, destination, expected_delivery, actual_delivery, "
    "status, receiver_name, delivery_notes"
)

_SHIPMENT_ALLOWED_FIELDS = {
    'customer_name', 'customer', 'pickup_location', 'pickup',
    'destination', 'goods_type', 'description',
    'weight_kg', 'weight', 'quantity',
    'payment_method', 'special_instructions',
    'booking_date', 'expected_delivery', 'customer_id',
}


def _generate_otp() -> str:
    return str(secrets.randbelow(900000) + 100000)


def _new_uuid_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _hash_otp(otp: str) -> str:
    return generate_password_hash(otp)


def _shipment_scope_sql(caller: Caller, table_alias=None):
    prefix = f"{table_alias}." if table_alias else ""
    if caller.is_staff:
        return "", []
    if caller.is_customer:
        if not caller.customer_id:
            return " AND 1=0", []
        return f" AND {prefix}customer_id = ?", [caller.customer_id]
    if caller.is_driver:
        if not caller.driver_id:
            return " AND 1=0", []
        return f" AND {prefix}driver_id = ?", [caller.driver_id]
    return " AND 1=0", []


def get_all_shipments(status_filter=None, search=None, caller: Caller = None):
    if caller is None:
        deny()
    conn_clause, params = _shipment_scope_sql(caller)
    query = f"SELECT {SHIPMENT_COLUMNS} FROM shipments WHERE 1=1{conn_clause}"
    if status_filter:
        query += " AND status = ?"
        params.append(bound_text(status_filter, 'status', max_len=40))
    if search:
        term = bound_text(search, 'search', max_len=80)
        # Strip LIKE wildcards so callers cannot widen a scoped query
        term = term.replace('%', '').replace('_', '')
        if not term:
            search = None
    if search:
        query += (
            " AND (shipment_id LIKE ? OR customer_name LIKE ? OR pickup_location LIKE ? "
            "OR destination LIKE ? OR vehicle_reg LIKE ? OR driver_name LIKE ?)"
        )
        like = f"%{term}%"
        params.extend([like, like, like, like, like, like])
    query += " ORDER BY created_at DESC"
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return rows_to_list(cursor.fetchall())


def get_shipment_by_id(shipment_id, caller: Caller = None):
    if caller is None:
        deny()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {SHIPMENT_COLUMNS} FROM shipments WHERE shipment_id = ?",
            (shipment_id,),
        )
        shipment = row_to_dict(cursor.fetchone())
        if not shipment:
            return None
        assert_shipment_visible(caller, shipment)
        cursor.execute(
            "SELECT history_id, shipment_id, status, timestamp, location, updated_by, notes "
            "FROM shipment_status_history WHERE shipment_id = ? ORDER BY timestamp ASC",
            (shipment_id,),
        )
        shipment['history'] = rows_to_list(cursor.fetchall())
        return shipment


def create_shipment(data, caller: Caller):
    if not can_create_shipment(caller):
        deny('Forbidden — cannot create shipments')

    safe = {k: v for k, v in (data or {}).items() if k in _SHIPMENT_ALLOWED_FIELDS}
    pickup_location = bound_text(
        safe.get('pickup_location') or safe.get('pickup'), 'pickup', required=True
    )
    destination = bound_text(safe.get('destination'), 'destination', required=True)
    goods_type = bound_text(safe.get('goods_type'), 'goods_type', default='General Freight')
    description = bound_text(safe.get('description'), 'description', max_len=500, default='')
    instructions = bound_text(safe.get('special_instructions'), 'special_instructions',
                              max_len=500, default='')
    payment_method = bound_text(safe.get('payment_method'), 'payment_method',
                                default='Credit Invoice')
    weight_kg = parse_weight(safe.get('weight_kg') or safe.get('weight'))
    quantity = parse_quantity(safe.get('quantity'))

    with db_session() as conn:
        cursor = conn.cursor()
        if caller.is_customer:
            if not caller.customer_id:
                deny('Customer account is not linked to a customer record')
            customer_id = caller.customer_id
            cursor.execute(
                "SELECT name, company FROM customers WHERE customer_id = ?",
                (customer_id,),
            )
            cust = row_to_dict(cursor.fetchone())
            customer_name = (cust or {}).get('company') or caller.name
        else:
            customer_id = bound_text(safe.get('customer_id'), 'customer_id', default='')
            name_in = bound_text(
                safe.get('customer_name') or safe.get('customer'),
                'customer_name',
                default='',
            )
            cust = None
            if customer_id:
                cursor.execute(
                    "SELECT customer_id, name, company FROM customers WHERE customer_id = ?",
                    (customer_id,),
                )
                cust = row_to_dict(cursor.fetchone())
            elif name_in:
                cursor.execute(
                    "SELECT customer_id, name, company FROM customers "
                    "WHERE company = ? OR name = ?",
                    (name_in, name_in),
                )
                cust = row_to_dict(cursor.fetchone())
            if not cust:
                raise ValidationError('A valid customer_id is required')
            customer_id = cust['customer_id']
            customer_name = name_in or cust['company']

        shipment_id = _new_uuid_id('SHP')
        booking_date = datetime.now().strftime('%Y-%m-%d')
        expected_delivery = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
        status = 'Booked'
        payment_status = 'Pending'
        shipping_cost = _calculate_shipping_cost(weight_kg, pickup_location, destination)

        cursor.execute('''
            INSERT INTO shipments (
                shipment_id, customer_id, customer_name, pickup_location, destination,
                goods_type, description, weight_kg, quantity, vehicle_id, vehicle_reg,
                driver_id, driver_name, status, booking_date, expected_delivery,
                shipping_cost, payment_method, payment_status, special_instructions
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            shipment_id, customer_id, customer_name,
            pickup_location, destination, goods_type,
            description, weight_kg, quantity,
            None, None, None, None,
            status, booking_date, expected_delivery,
            shipping_cost, payment_method, payment_status, instructions,
        ))

        cursor.execute('''
            INSERT INTO shipment_status_history (shipment_id, status, location, updated_by, notes)
            VALUES (?,?,?,?,?)
        ''', (shipment_id, status, pickup_location, caller.role, 'Shipment created successfully.'))

        delivery_id = _new_uuid_id('DEL')
        otp_code = _generate_otp()
        otp_expires_at = (datetime.now() + timedelta(minutes=OTP_TTL_MINUTES)).strftime(
            '%Y-%m-%d %H:%M:%S'
        )
        cursor.execute('''
            INSERT INTO deliveries (
                delivery_id, shipment_id, customer_name, driver_id, vehicle_reg,
                pickup, destination, expected_delivery, status, otp_code,
                otp_attempts, otp_expires_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            delivery_id, shipment_id, customer_name, None, None,
            pickup_location, destination, expected_delivery,
            'Pending', _hash_otp(otp_code), 0, otp_expires_at,
        ))

        invoice_id = _new_uuid_id('INV')
        tax = round(shipping_cost * 0.18, 2)
        cursor.execute('''
            INSERT INTO payments (
                invoice_id, shipment_id, customer_name, amount, tax_amount,
                total_amount, payment_method, payment_status, invoice_date
            ) VALUES (?,?,?,?,?,?,?,?,?)
        ''', (
            invoice_id, shipment_id, customer_name,
            shipping_cost, tax, shipping_cost + tax,
            payment_method, payment_status, booking_date,
        ))

        cursor.execute('''
            INSERT INTO notifications (notification_id, user_role, title, message, type)
            VALUES (?,?,?,?,?)
        ''', (
            f"NOTIF_{uuid.uuid4().hex[:8]}", 'ADMIN',
            f"New Shipment {shipment_id}",
            f"Shipment {shipment_id} from {pickup_location} to {destination} created.",
            'success',
        ))

    created = get_shipment_by_id(shipment_id, caller)
    return created


def _calculate_shipping_cost(weight_kg: float, pickup: str, destination: str) -> float:
    base = 1500.0
    per_kg = weight_kg * 2.5
    # Stable (not PYTHONHASHSEED-randomized) distance stand-in.
    seed = abs(int.from_bytes((pickup + '|' + destination).encode(), 'little'))
    distance_factor = seed % 3000 + 500
    return round(base + per_kg + distance_factor * 0.5, 2)


def assign_shipment(shipment_id, driver_id, vehicle_id, caller: Caller):
    if not can_assign_shipment(caller):
        deny()
    driver_id = bound_text(driver_id, 'driver_id', required=True)
    vehicle_id = bound_text(vehicle_id, 'vehicle_id', required=True)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT {SHIPMENT_COLUMNS} FROM shipments WHERE shipment_id = ?",
                       (shipment_id,))
        shipment = row_to_dict(cursor.fetchone())
        if not shipment:
            return None
        cursor.execute(
            "SELECT driver_id, name FROM drivers WHERE driver_id = ?", (driver_id,)
        )
        driver = row_to_dict(cursor.fetchone())
        cursor.execute(
            "SELECT vehicle_id, registration_number FROM vehicles WHERE vehicle_id = ?",
            (vehicle_id,),
        )
        vehicle = row_to_dict(cursor.fetchone())
        if not driver or not vehicle:
            raise ValidationError('Unknown driver_id or vehicle_id')
        cursor.execute(
            "UPDATE shipments SET driver_id = ?, driver_name = ?, vehicle_id = ?, vehicle_reg = ? "
            "WHERE shipment_id = ?",
            (driver['driver_id'], driver['name'], vehicle['vehicle_id'],
             vehicle['registration_number'], shipment_id),
        )
        cursor.execute(
            "UPDATE deliveries SET driver_id = ?, vehicle_reg = ? WHERE shipment_id = ?",
            (driver['driver_id'], vehicle['registration_number'], shipment_id),
        )
    return get_shipment_by_id(shipment_id, caller)


def update_shipment_status(shipment_id, new_status, location=None, notes=None,
                           caller: Caller = None, via_otp=False, conn=None):
    if caller is None:
        deny()
    notes = bound_text(notes, 'notes', max_len=500, default='')
    location = bound_text(location, 'location', max_len=200, default='') if location else None

    def _run(cursor):
        cursor.execute(
            f"SELECT {SHIPMENT_COLUMNS} FROM shipments WHERE shipment_id = ?",
            (shipment_id,),
        )
        shipment = row_to_dict(cursor.fetchone())
        if not shipment:
            return None
        assert_shipment_visible(caller, shipment)
        if caller.is_driver and not via_otp:
            if shipment.get('driver_id') != caller.driver_id:
                deny()
        assert_status_transition(caller, shipment['status'], new_status, via_otp=via_otp)

        loc = location or (
            shipment['destination'] if new_status == 'Delivered' else shipment['pickup_location']
        )
        cursor.execute(
            "UPDATE shipments SET status = ? WHERE shipment_id = ?",
            (new_status, shipment_id),
        )
        cursor.execute('''
            INSERT INTO shipment_status_history (shipment_id, status, location, updated_by, notes)
            VALUES (?,?,?,?,?)
        ''', (shipment_id, new_status, loc, caller.role,
              notes or f"Status updated to {new_status}"))

        if new_status == 'Delivered':
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute(
                "UPDATE deliveries SET status = 'Delivered', actual_delivery = ? WHERE shipment_id = ?",
                (now_str, shipment_id),
            )
            # Payments stay Pending until a dedicated collection workflow — never auto-Paid.
        elif new_status in ['In Transit', 'Out for Delivery']:
            cursor.execute(
                "UPDATE deliveries SET status = 'In Progress' WHERE shipment_id = ?",
                (shipment_id,),
            )
        return True

    if conn is not None:
        ok = _run(conn.cursor())
        return ok
    with db_session() as owned:
        ok = _run(owned.cursor())
        if not ok:
            return None
    return get_shipment_by_id(shipment_id, caller)


def delete_shipment(shipment_id, caller: Caller):
    require_admin(caller)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT shipment_id FROM shipments WHERE shipment_id = ?", (shipment_id,))
        if not cursor.fetchone():
            return False
        cursor.execute("DELETE FROM payments WHERE shipment_id = ?", (shipment_id,))
        cursor.execute("DELETE FROM deliveries WHERE shipment_id = ?", (shipment_id,))
        cursor.execute("DELETE FROM shipment_status_history WHERE shipment_id = ?", (shipment_id,))
        cursor.execute("DELETE FROM shipments WHERE shipment_id = ?", (shipment_id,))
        return True


def get_all_vehicles(caller: Caller):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT vehicle_id, registration_number, vehicle_type, make, model, year,
                   capacity_mt, fuel_type, current_location, assigned_driver_id,
                   insurance_expiry, permit_expiry, fitness_expiry,
                   service_due_date, status, created_at
            FROM vehicles ORDER BY created_at DESC
        """)
        vehicles = rows_to_list(cursor.fetchall())
    return [v for v in vehicles if vehicle_visible(caller, v)]


def create_vehicle(data, caller: Caller):
    require_staff(caller)
    data = data or {}
    reg = bound_text(data.get('registration_number'), 'registration_number', required=True)
    with db_session() as conn:
        cursor = conn.cursor()
        vehicle_id = _new_uuid_id('VEH')
        cursor.execute('''
            INSERT INTO vehicles (
                vehicle_id, registration_number, vehicle_type, make, model, year,
                capacity_mt, fuel_type, current_location, assigned_driver_id,
                insurance_expiry, permit_expiry, fitness_expiry, service_due_date, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            vehicle_id, reg,
            bound_text(data.get('vehicle_type'), 'vehicle_type', default='Truck'),
            bound_text(data.get('make'), 'make', default='Tata'),
            bound_text(data.get('model'), 'model', default='Model X'),
            parse_year(data.get('year')),
            parse_capacity(data.get('capacity_mt')),
            bound_text(data.get('fuel_type'), 'fuel_type', default='Diesel'),
            bound_text(data.get('current_location'), 'current_location', default='Delhi'),
            bound_text(data.get('assigned_driver_id'), 'assigned_driver_id', default='') or None,
            bound_text(data.get('insurance_expiry'), 'insurance_expiry', default='2027-12-31'),
            bound_text(data.get('permit_expiry'), 'permit_expiry', default='2027-12-31'),
            bound_text(data.get('fitness_expiry'), 'fitness_expiry', default='2027-12-31'),
            bound_text(data.get('service_due_date'), 'service_due_date', default='2026-12-31'),
            'Available',
        ))
    return get_vehicle_by_id(vehicle_id, caller)


def get_vehicle_by_id(vehicle_id, caller: Caller):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT vehicle_id, registration_number, vehicle_type, make, model, year, "
            "capacity_mt, fuel_type, current_location, assigned_driver_id, "
            "insurance_expiry, permit_expiry, fitness_expiry, service_due_date, status, created_at "
            "FROM vehicles WHERE vehicle_id = ?",
            (vehicle_id,),
        )
        v = row_to_dict(cursor.fetchone())
    if v and not vehicle_visible(caller, v):
        return None
    return v


def get_all_drivers(caller: Caller):
    with db_session() as conn:
        cursor = conn.cursor()
        if caller.is_staff:
            cursor.execute("""
                SELECT driver_id, name, phone, email, license_number, license_expiry,
                       experience_years, assigned_vehicle_id, status,
                       total_trips, completed_trips, rating, created_at
                FROM drivers ORDER BY created_at DESC
            """)
        elif caller.is_driver and caller.driver_id:
            cursor.execute("""
                SELECT driver_id, name, phone, email, license_number, license_expiry,
                       experience_years, assigned_vehicle_id, status,
                       total_trips, completed_trips, rating, created_at
                FROM drivers WHERE driver_id = ?
            """, (caller.driver_id,))
        else:
            deny('Forbidden — cannot list drivers')
        drivers = rows_to_list(cursor.fetchall())
    return [d for d in drivers if driver_visible(caller, d)]


def create_driver(data, caller: Caller):
    require_staff(caller)
    data = data or {}
    name = bound_text(data.get('name'), 'name', required=True)
    license_number = bound_text(data.get('license_number'), 'license_number', required=True)
    with db_session() as conn:
        cursor = conn.cursor()
        driver_id = _new_uuid_id('DRV')
        cursor.execute('''
            INSERT INTO drivers (
                driver_id, name, phone, email, license_number, license_expiry,
                address, experience_years, assigned_vehicle_id, status,
                total_trips, completed_trips, rating
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            driver_id, name,
            bound_text(data.get('phone'), 'phone', default=''),
            bound_text(data.get('email'), 'email', default=''),
            license_number,
            bound_text(data.get('license_expiry'), 'license_expiry', default='2028-12-31'),
            bound_text(data.get('address'), 'address', default='City Hub'),
            parse_experience(data.get('experience_years')),
            bound_text(data.get('assigned_vehicle_id'), 'assigned_vehicle_id', default='') or None,
            'Available', 0, 0, 5.0,
        ))
    return get_driver_by_id(driver_id, caller)


def get_driver_by_id(driver_id, caller: Caller):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT driver_id, name, phone, email, license_number, license_expiry, "
            "experience_years, assigned_vehicle_id, status, total_trips, completed_trips, "
            "rating, created_at FROM drivers WHERE driver_id = ?",
            (driver_id,),
        )
        d = row_to_dict(cursor.fetchone())
    if d and not driver_visible(caller, d):
        return None
    return d


def get_all_customers(caller: Caller):
    require_staff(caller)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT customer_id, name, company, phone, email, total_shipments, total_spent, created_at
            FROM customers ORDER BY created_at DESC
        """)
        return rows_to_list(cursor.fetchall())


def create_customer(data, caller: Caller):
    require_staff(caller)
    data = data or {}
    name = bound_text(data.get('name'), 'name', required=True)
    company = bound_text(data.get('company'), 'company', required=True)
    with db_session() as conn:
        cursor = conn.cursor()
        cust_id = _new_uuid_id('CUST')
        cursor.execute('''
            INSERT INTO customers (customer_id, name, company, phone, email, address, total_shipments, total_spent)
            VALUES (?,?,?,?,?,?,?,?)
        ''', (
            cust_id, name, company,
            bound_text(data.get('phone'), 'phone', default=''),
            bound_text(data.get('email'), 'email', default=''),
            bound_text(data.get('address'), 'address', default=''),
            0, 0.0,
        ))
    return get_customer_by_id(cust_id, caller)


def get_customer_by_id(customer_id, caller: Caller):
    require_staff(caller)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT customer_id, name, company, phone, email, address, "
            "total_shipments, total_spent, created_at FROM customers WHERE customer_id = ?",
            (customer_id,),
        )
        return row_to_dict(cursor.fetchone())


def get_all_warehouses(caller: Caller):
    require_staff(caller)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT warehouse_id, name, location, manager_name, contact_phone, "
            "capacity_tons, current_occupancy_tons, status, created_at "
            "FROM warehouses ORDER BY created_at DESC"
        )
        return rows_to_list(cursor.fetchall())


def get_all_routes(caller: Caller):
    if caller.is_customer:
        deny()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT route_id, pickup, destination, distance_km, estimated_time, "
            "fuel_cost_est, recommended_route, stops_json, created_at "
            "FROM routes ORDER BY created_at DESC"
        )
        return rows_to_list(cursor.fetchall())


def optimize_route(pickup, destination, caller: Caller):
    if caller.is_customer:
        deny()
    pickup = bound_text(pickup, 'pickup', max_len=100, required=True)
    destination = bound_text(destination, 'destination', max_len=100, required=True)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT route_id, pickup, destination, distance_km, estimated_time, "
            "fuel_cost_est, recommended_route, stops_json "
            "FROM routes WHERE LOWER(pickup) = LOWER(?) AND LOWER(destination) = LOWER(?)",
            (pickup, destination),
        )
        found = row_to_dict(cursor.fetchone())
    if found:
        return found
    dist = 250 + (len(pickup) + len(destination)) * 12
    time_hrs = round(dist / 65.0, 1)
    hrs = int(time_hrs)
    mins = int((time_hrs - hrs) * 60)
    fuel_cost = int(dist * 8.6)
    return {
        'route_id': f"RTE_OPT_{int(dist)}",
        'pickup': pickup,
        'destination': destination,
        'distance_km': dist,
        'estimated_time': f"{hrs}h {mins}m",
        'fuel_cost_est': fuel_cost,
        'recommended_route': f"Via National Highway Corridor ({pickup}-{destination} Expressway)",
        'stops': [
            {'stop': f"{pickup} Exit Toll Plaza", 'eta': '30m'},
            {'stop': "Midway Logistics Rest Stop", 'eta': f"{hrs // 2}h 15m"},
            {'stop': f"{destination} City Hub", 'eta': f"{hrs}h {mins}m"},
        ],
    }


def get_all_deliveries(caller: Caller):
    scope, params = _shipment_scope_sql(caller, table_alias='s')
    query = (
        "SELECT d.delivery_id, d.shipment_id, d.customer_name, d.driver_id, d.vehicle_reg, "
        "d.pickup, d.destination, d.expected_delivery, d.actual_delivery, "
        "d.status, d.receiver_name, d.delivery_notes "
        "FROM deliveries d JOIN shipments s ON s.shipment_id = d.shipment_id "
        f"WHERE 1=1{scope} "
        "ORDER BY d.expected_delivery DESC"
    )
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return rows_to_list(cursor.fetchall())


def confirm_delivery(shipment_id, otp_entered, caller: Caller, receiver_name=None, ip='unknown'):
    if not caller.is_driver:
        deny('Forbidden — only the assigned driver can confirm a delivery')

    shipment_id = bound_text(shipment_id, 'shipment_id', required=True)
    otp_entered = bound_text(otp_entered, 'otp_code', max_len=12, required=True)
    receiver_name = bound_text(receiver_name, 'receiver_name', max_len=120, default='')

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT delivery_id, shipment_id, driver_id, destination, status, "
            "otp_code, otp_attempts, otp_expires_at FROM deliveries WHERE shipment_id = ?",
            (shipment_id,),
        )
        d = row_to_dict(cursor.fetchone())
        if not d:
            return False, "Delivery record not found for shipment"
        if not delivery_confirmable(caller, d):
            deny('Forbidden — not assigned to this delivery')
        if d.get('status') == 'Delivered':
            return False, "Delivery already confirmed"

        attempt_count = int(d.get('otp_attempts') or 0)
        if attempt_count >= OTP_MAX_ATTEMPTS:
            log_otp_locked(shipment_id=shipment_id, ip=ip)
            return False, "OTP locked after too many failed attempts. Contact support."

        expires_at_str = d.get('otp_expires_at')
        if not expires_at_str:
            return False, "OTP has expired. Please request a new one."
        try:
            expires_at = datetime.strptime(str(expires_at_str)[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return False, "OTP has expired. Please request a new one."
        if datetime.now() > expires_at:
            return False, "OTP has expired. Please request a new one."

        stored = d.get('otp_code') or ''
        if stored == 'USED' or ':' not in stored or not check_password_hash(stored, otp_entered):
            new_attempt = attempt_count + 1
            cursor.execute(
                "UPDATE deliveries SET otp_attempts = ? WHERE shipment_id = ?",
                (new_attempt, shipment_id),
            )
            log_otp_attempt(shipment_id=shipment_id, success=False,
                            attempt_count=new_attempt, ip=ip)
            remaining = OTP_MAX_ATTEMPTS - new_attempt
            if remaining <= 0:
                log_otp_locked(shipment_id=shipment_id, ip=ip)
                return False, "OTP locked after too many failed attempts. Contact support."
            return False, f"Invalid OTP Code. {remaining} attempt(s) remaining."

        cursor.execute(
            "UPDATE deliveries SET otp_code = 'USED', otp_attempts = ?, receiver_name = ? "
            "WHERE shipment_id = ? AND status != 'Delivered'",
            (attempt_count + 1, receiver_name or 'Receiver', shipment_id),
        )
        if cursor.rowcount == 0:
            return False, "Delivery already confirmed"
        log_otp_attempt(shipment_id=shipment_id, success=True,
                        attempt_count=attempt_count + 1, ip=ip)
        update_shipment_status(
            shipment_id, 'Delivered',
            location=d['destination'],
            notes=f"Delivery confirmed by {receiver_name or 'Receiver'}. OTP verified.",
            caller=caller,
            via_otp=True,
            conn=conn,
        )
    return True, "Delivery confirmed successfully!"


def get_all_payments(caller: Caller):
    with db_session() as conn:
        cursor = conn.cursor()
        if caller.is_staff:
            cursor.execute("""
                SELECT invoice_id, shipment_id, customer_name, amount, tax_amount,
                       total_amount, payment_method, payment_status, invoice_date, paid_date
                FROM payments ORDER BY invoice_date DESC
            """)
        elif caller.is_customer and caller.customer_id:
            cursor.execute("""
                SELECT p.invoice_id, p.shipment_id, p.customer_name, p.amount, p.tax_amount,
                       p.total_amount, p.payment_method, p.payment_status, p.invoice_date, p.paid_date
                FROM payments p
                JOIN shipments s ON s.shipment_id = p.shipment_id
                WHERE s.customer_id = ?
                ORDER BY p.invoice_date DESC
            """, (caller.customer_id,))
        else:
            deny()
        return rows_to_list(cursor.fetchall())


def record_payment(invoice_id, caller: Caller):
    """Staff-only explicit collection — not a side effect of delivery."""
    require_staff(caller)
    invoice_id = bound_text(invoice_id, 'invoice_id', required=True)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT invoice_id, shipment_id, payment_status FROM payments WHERE invoice_id = ?",
            (invoice_id,),
        )
        payment = row_to_dict(cursor.fetchone())
        if not payment:
            return None
        paid_date = datetime.now().strftime('%Y-%m-%d')
        cursor.execute(
            "UPDATE payments SET payment_status = 'Paid', paid_date = ? WHERE invoice_id = ?",
            (paid_date, invoice_id),
        )
        cursor.execute(
            "UPDATE shipments SET payment_status = 'Paid' WHERE shipment_id = ?",
            (payment['shipment_id'],),
        )
    return True


def get_all_notifications(caller: Caller):
    with db_session() as conn:
        cursor = conn.cursor()
        if caller.is_admin:
            cursor.execute("""
                SELECT notification_id, user_role, title, message, type, is_read, timestamp
                FROM notifications ORDER BY timestamp DESC
            """)
        else:
            cursor.execute("""
                SELECT notification_id, user_role, title, message, type, is_read, timestamp
                FROM notifications
                WHERE user_role = ? OR user_role IS NULL
                ORDER BY timestamp DESC
            """, (caller.role,))
        return rows_to_list(cursor.fetchall())


def mark_notification_read(notif_id, caller: Caller):
    notif_id = bound_text(notif_id, 'notification_id', required=True)
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT notification_id, user_role FROM notifications WHERE notification_id = ?",
            (notif_id,),
        )
        row = row_to_dict(cursor.fetchone())
        if not row:
            return False
        if not caller.is_admin and row.get('user_role') not in (caller.role, None):
            deny()
        cursor.execute(
            "UPDATE notifications SET is_read = 1 WHERE notification_id = ?",
            (notif_id,),
        )
        return True


def set_delivery_otp_for_tests(shipment_id, otp_code, driver_id=None):
    """Test helper — never expose over HTTP."""
    expires = (datetime.now() + timedelta(minutes=OTP_TTL_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
    with db_session() as conn:
        cursor = conn.cursor()
        if driver_id:
            cursor.execute(
                "UPDATE deliveries SET otp_code = ?, otp_attempts = 0, otp_expires_at = ?, "
                "driver_id = ?, status = 'In Progress' WHERE shipment_id = ?",
                (_hash_otp(otp_code), expires, driver_id, shipment_id),
            )
        else:
            cursor.execute(
                "UPDATE deliveries SET otp_code = ?, otp_attempts = 0, otp_expires_at = ?, "
                "status = 'In Progress' WHERE shipment_id = ?",
                (_hash_otp(otp_code), expires, shipment_id),
            )
    return True
