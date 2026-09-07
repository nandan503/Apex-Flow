import secrets
import uuid
from datetime import datetime, timedelta
from backend.database import get_db_connection
from backend.models import row_to_dict, rows_to_list
from backend.logger import log_otp_attempt, log_otp_locked

# ── OTP Configuration ──────────────────────────────────────────────────────────
OTP_DIGITS = 6          # 6-digit = 900,000 possibilities (vs 9,000 before)
OTP_TTL_MINUTES = 30    # OTP expires after 30 minutes
OTP_MAX_ATTEMPTS = 5    # Lock after 5 wrong attempts

def _generate_otp() -> str:
    """Generate a cryptographically secure 6-digit OTP using secrets module."""
    return str(secrets.randbelow(900000) + 100000)

def _new_uuid_id(prefix: str) -> str:
    """Generate a short non-sequential ID: e.g. SHP-a3f2b1c0"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ── Allowed fields for shipment creation (mass-assignment protection) ──────────
_SHIPMENT_ALLOWED_FIELDS = {
    'customer_name', 'customer', 'pickup_location', 'pickup',
    'destination', 'goods_type', 'description',
    'weight_kg', 'weight', 'quantity',
    'payment_method', 'special_instructions',
    'booking_date', 'expected_delivery'
}

# ── SHIPMENTS SERVICE ──────────────────────────────────────────────────────────

def get_all_shipments(status_filter=None, search=None,
                       caller_role=None, caller_user_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM shipments WHERE 1=1"
    params = []

    # IDOR: CUSTOMER role can only see their own shipments
    if caller_role == 'CUSTOMER' and caller_user_id:
        query += " AND customer_id = ?"
        params.append(caller_user_id)

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if search:
        query += " AND (shipment_id LIKE ? OR customer_name LIKE ? OR pickup_location LIKE ? OR destination LIKE ? OR vehicle_reg LIKE ? OR driver_name LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term, term, term, term])

    query += " ORDER BY created_at DESC"
    cursor.execute(query, params)
    shipments = rows_to_list(cursor.fetchall())
    conn.close()
    return shipments


def get_shipment_by_id(shipment_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM shipments WHERE shipment_id = ?", (shipment_id,))
    shipment = row_to_dict(cursor.fetchone())

    if shipment:
        cursor.execute(
            "SELECT * FROM shipment_status_history WHERE shipment_id = ? ORDER BY timestamp ASC",
            (shipment_id,)
        )
        shipment['history'] = rows_to_list(cursor.fetchall())

    conn.close()
    return shipment


def create_shipment(data, caller_user_id=None, caller_role=None):
    """
    Create a new shipment.

    SECURITY: Status, payment_status, and shipping_cost are ALWAYS set by
    the server — never taken from client input (F-02 mass-assignment fix).
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Non-sequential UUID-based ID (F-13 fix)
    shipment_id = _new_uuid_id('SHP')

    booking_date = datetime.now().strftime('%Y-%m-%d')
    expected_delivery = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')

    # Sanitize — only accept fields in the allowlist
    safe = {k: v for k, v in data.items() if k in _SHIPMENT_ALLOWED_FIELDS}

    customer_name = safe.get('customer_name') or safe.get('customer') or 'Default Customer'
    pickup_location = safe.get('pickup_location') or safe.get('pickup') or 'Ludhiana'
    destination = safe.get('destination') or 'Delhi'
    goods_type = safe.get('goods_type') or 'General Freight'
    weight_kg = float(safe.get('weight_kg') or safe.get('weight') or 1000)

    # For CUSTOMER callers: bind the shipment to their account
    customer_id = caller_user_id if caller_role == 'CUSTOMER' else data.get('customer_id', 'CUST001')

    # Server-side enforced fields — NEVER from client (mass-assignment fix)
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
        safe.get('description', ''), weight_kg, int(safe.get('quantity', 1)),
        None, None, None, None,
        status, booking_date, expected_delivery,
        shipping_cost,
        safe.get('payment_method', 'Credit Invoice'),
        payment_status,
        safe.get('special_instructions', '')
    ))

    # Initial status history
    cursor.execute('''
        INSERT INTO shipment_status_history (shipment_id, status, location, updated_by, notes)
        VALUES (?,?,?,?,?)
    ''', (shipment_id, status, pickup_location, 'System', 'Shipment created successfully.'))

    # Delivery record with secure 6-digit CSPRNG OTP + expiry
    delivery_id = _new_uuid_id('DEL')
    otp_code = _generate_otp()
    otp_expires_at = (datetime.now() + timedelta(minutes=OTP_TTL_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''
        INSERT INTO deliveries (
            delivery_id, shipment_id, customer_name, driver_id, vehicle_reg,
            pickup, destination, expected_delivery, status, otp_code,
            otp_attempts, otp_expires_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        delivery_id, shipment_id, customer_name,
        None, None,
        pickup_location, destination, expected_delivery,
        'Pending', otp_code, 0, otp_expires_at
    ))

    # Invoice record — server-calculated amounts
    invoice_id = _new_uuid_id('INV')
    tax = round(shipping_cost * 0.18, 2)
    cursor.execute('''
        INSERT INTO payments (
            invoice_id, shipment_id, customer_name, amount, tax_amount,
            total_amount, payment_method, payment_status, invoice_date
        )
        VALUES (?,?,?,?,?,?,?,?,?)
    ''', (
        invoice_id, shipment_id, customer_name,
        shipping_cost, tax, shipping_cost + tax,
        safe.get('payment_method', 'Credit Invoice'),
        payment_status, booking_date
    ))

    # Notification
    cursor.execute('''
        INSERT INTO notifications (notification_id, user_role, title, message, type)
        VALUES (?,?,?,?,?)
    ''', (
        f"NOTIF_{uuid.uuid4().hex[:8]}", 'ADMIN',
        f"New Shipment {shipment_id}",
        f"Shipment {shipment_id} from {pickup_location} to {destination} created.",
        'success'
    ))

    conn.commit()
    conn.close()
    return get_shipment_by_id(shipment_id)


def _calculate_shipping_cost(weight_kg: float, pickup: str, destination: str) -> float:
    """Server-side shipping cost calculation — clients cannot override this."""
    base = 1500.0
    per_kg = weight_kg * 2.5
    distance_factor = abs(hash(pickup + destination)) % 3000 + 500
    return round(base + per_kg + distance_factor * 0.5, 2)


def update_shipment_status(shipment_id, new_status, location=None,
                            notes=None, updated_by="System"):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM shipments WHERE shipment_id = ?", (shipment_id,))
    shipment = row_to_dict(cursor.fetchone())
    if not shipment:
        conn.close()
        return None

    loc = (location or shipment['destination']) if new_status == 'Delivered' \
          else (location or shipment['pickup_location'])

    cursor.execute("UPDATE shipments SET status = ? WHERE shipment_id = ?",
                   (new_status, shipment_id))

    cursor.execute('''
        INSERT INTO shipment_status_history (shipment_id, status, location, updated_by, notes)
        VALUES (?,?,?,?,?)
    ''', (shipment_id, new_status, loc, updated_by,
          notes or f"Status updated to {new_status}"))

    if new_status == 'Delivered':
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            "UPDATE deliveries SET status = 'Delivered', actual_delivery = ? WHERE shipment_id = ?",
            (now_str, shipment_id)
        )
        cursor.execute(
            "UPDATE payments SET payment_status = 'Paid', paid_date = ? WHERE shipment_id = ?",
            (datetime.now().strftime('%Y-%m-%d'), shipment_id)
        )
    elif new_status in ['In Transit', 'Out for Delivery']:
        cursor.execute(
            "UPDATE deliveries SET status = 'In Progress' WHERE shipment_id = ?",
            (shipment_id,)
        )

    conn.commit()
    conn.close()
    return get_shipment_by_id(shipment_id)


def delete_shipment(shipment_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shipments WHERE shipment_id = ?", (shipment_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ── VEHICLES SERVICE ───────────────────────────────────────────────────────────

def get_all_vehicles():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT vehicle_id, registration_number, vehicle_type, make, model, year,
               capacity_mt, fuel_type, current_location, assigned_driver_id,
               insurance_expiry, permit_expiry, fitness_expiry,
               service_due_date, status, created_at
        FROM vehicles ORDER BY created_at DESC
    """)
    vehicles = rows_to_list(cursor.fetchall())
    conn.close()
    return vehicles


def create_vehicle(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    vehicle_id = _new_uuid_id('VEH')

    cursor.execute('''
        INSERT INTO vehicles (
            vehicle_id, registration_number, vehicle_type, make, model, year,
            capacity_mt, fuel_type, current_location, assigned_driver_id,
            insurance_expiry, permit_expiry, fitness_expiry, service_due_date, status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        vehicle_id, data['registration_number'], data.get('vehicle_type', 'Truck'),
        data.get('make', 'Tata'), data.get('model', 'Model X'), int(data.get('year', 2023)),
        float(data.get('capacity_mt', 15.0)), data.get('fuel_type', 'Diesel'),
        data.get('current_location', 'Delhi'), data.get('assigned_driver_id'),
        data.get('insurance_expiry', '2027-12-31'), data.get('permit_expiry', '2027-12-31'),
        data.get('fitness_expiry', '2027-12-31'), data.get('service_due_date', '2026-12-31'),
        data.get('status', 'Available')
    ))
    conn.commit()
    conn.close()
    return get_vehicle_by_id(vehicle_id)


def get_vehicle_by_id(vehicle_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vehicles WHERE vehicle_id = ?", (vehicle_id,))
    v = row_to_dict(cursor.fetchone())
    conn.close()
    return v


# ── DRIVERS SERVICE ────────────────────────────────────────────────────────────

def get_all_drivers():
    """Return driver list with PII scoped to role (caller must check role before exposing)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # Explicit column list — excludes sensitive PII like home address for list view
    cursor.execute("""
        SELECT driver_id, name, phone, license_number, license_expiry,
               experience_years, assigned_vehicle_id, status,
               total_trips, completed_trips, rating, created_at
        FROM drivers ORDER BY created_at DESC
    """)
    drivers = rows_to_list(cursor.fetchall())
    conn.close()
    return drivers


def create_driver(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    driver_id = _new_uuid_id('DRV')

    cursor.execute('''
        INSERT INTO drivers (
            driver_id, name, phone, email, license_number, license_expiry,
            address, experience_years, assigned_vehicle_id, status,
            total_trips, completed_trips, rating
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        driver_id, data['name'], data.get('phone', ''), data.get('email', ''),
        data['license_number'],
        data.get('license_expiry', '2028-12-31'), data.get('address', 'City Hub'),
        int(data.get('experience_years', 5)), data.get('assigned_vehicle_id'),
        data.get('status', 'Available'), 0, 0, 5.0
    ))
    conn.commit()
    conn.close()
    return get_driver_by_id(driver_id)


def get_driver_by_id(driver_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM drivers WHERE driver_id = ?", (driver_id,))
    d = row_to_dict(cursor.fetchone())
    conn.close()
    return d


# ── CUSTOMERS SERVICE ──────────────────────────────────────────────────────────

def get_all_customers():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT customer_id, name, company, phone, email, total_shipments, total_spent, created_at
        FROM customers ORDER BY created_at DESC
    """)
    custs = rows_to_list(cursor.fetchall())
    conn.close()
    return custs


def create_customer(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    cust_id = _new_uuid_id('CUST')

    cursor.execute('''
        INSERT INTO customers (customer_id, name, company, phone, email, address, total_shipments, total_spent)
        VALUES (?,?,?,?,?,?,?,?)
    ''', (
        cust_id, data['name'], data['company'],
        data.get('phone', ''), data.get('email', ''),
        data.get('address', ''), 0, 0.0
    ))
    conn.commit()
    conn.close()
    return get_customer_by_id(cust_id)


def get_customer_by_id(customer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,))
    c = row_to_dict(cursor.fetchone())
    conn.close()
    return c


# ── WAREHOUSES SERVICE ─────────────────────────────────────────────────────────

def get_all_warehouses():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM warehouses ORDER BY created_at DESC")
    whs = rows_to_list(cursor.fetchall())
    conn.close()
    return whs


# ── DELIVERIES SERVICE ─────────────────────────────────────────────────────────

def get_all_deliveries():
    conn = get_db_connection()
    cursor = conn.cursor()
    # SECURITY F-05: otp_code, otp_attempts, otp_expires_at are EXCLUDED from this query
    cursor.execute("""
        SELECT delivery_id, shipment_id, customer_name, driver_id, vehicle_reg,
               pickup, destination, expected_delivery, actual_delivery,
               status, receiver_name, delivery_notes
        FROM deliveries ORDER BY expected_delivery DESC
    """)
    dels = rows_to_list(cursor.fetchall())
    conn.close()
    return dels


def confirm_delivery(shipment_id, otp_entered, receiver_name=None,
                     notes=None, ip='unknown'):
    """
    Confirm delivery with OTP verification.

    Security controls:
    - OTP is 6-digit CSPRNG (not brute-forceable in reasonable time)
    - Max 5 attempts before lockout (F-04)
    - OTP expires after 30 minutes (F-04)
    - otp_code never returned to caller (F-05)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch full delivery record including OTP fields (server-side only)
    cursor.execute(
        "SELECT * FROM deliveries WHERE shipment_id = ?",
        (shipment_id,)
    )
    d = row_to_dict(cursor.fetchone())

    if not d:
        conn.close()
        return False, "Delivery record not found for shipment"

    attempt_count = int(d.get('otp_attempts') or 0)

    # Lockout check
    if attempt_count >= OTP_MAX_ATTEMPTS:
        log_otp_locked(shipment_id=shipment_id, ip=ip)
        conn.close()
        return False, "OTP locked after too many failed attempts. Contact support."

    # Expiry check
    expires_at_str = d.get('otp_expires_at')
    if expires_at_str:
        expires_at = datetime.strptime(expires_at_str, '%Y-%m-%d %H:%M:%S')
        if datetime.now() > expires_at:
            conn.close()
            return False, "OTP has expired. Please request a new one."

    # OTP comparison
    if d['otp_code'] != str(otp_entered).strip():
        new_attempt = attempt_count + 1
        cursor.execute(
            "UPDATE deliveries SET otp_attempts = ? WHERE shipment_id = ?",
            (new_attempt, shipment_id)
        )
        conn.commit()
        conn.close()
        log_otp_attempt(shipment_id=shipment_id, success=False,
                        attempt_count=new_attempt, ip=ip)
        remaining = OTP_MAX_ATTEMPTS - new_attempt
        if remaining <= 0:
            return False, "OTP locked after too many failed attempts. Contact support."
        return False, f"Invalid OTP Code. {remaining} attempt(s) remaining."

    conn.close()
    log_otp_attempt(shipment_id=shipment_id, success=True,
                    attempt_count=attempt_count + 1, ip=ip)

    update_shipment_status(
        shipment_id, 'Delivered',
        location=d['destination'],
        notes=f"Delivery confirmed by {receiver_name or 'Receiver'}. OTP verified.",
        updated_by="Driver"
    )
    return True, "Delivery confirmed successfully!"


# ── PAYMENTS SERVICE ───────────────────────────────────────────────────────────

def get_all_payments():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT invoice_id, shipment_id, customer_name, amount, tax_amount,
               total_amount, payment_method, payment_status, invoice_date, paid_date
        FROM payments ORDER BY invoice_date DESC
    """)
    pymts = rows_to_list(cursor.fetchall())
    conn.close()
    return pymts


# ── NOTIFICATIONS SERVICE ──────────────────────────────────────────────────────

def get_all_notifications():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT notification_id, user_role, title, message, type, is_read, timestamp
        FROM notifications ORDER BY timestamp DESC
    """)
    notifs = rows_to_list(cursor.fetchall())
    conn.close()
    return notifs


def mark_notification_read(notif_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE notifications SET is_read = 1 WHERE notification_id = ?",
        (notif_id,)
    )
    conn.commit()
    conn.close()
    return True
