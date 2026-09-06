import uuid
from datetime import datetime, timedelta
from backend.database import get_db_connection
from backend.models import row_to_dict, rows_to_list

# SHIPMENTS SERVICE
def get_all_shipments(status_filter=None, search=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM shipments WHERE 1=1"
    params = []
    
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
        cursor.execute("SELECT * FROM shipment_status_history WHERE shipment_id = ? ORDER BY timestamp ASC", (shipment_id,))
        shipment['history'] = rows_to_list(cursor.fetchall())
        
    conn.close()
    return shipment

def create_shipment(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Auto-generate Shipment ID if not provided
    cursor.execute("SELECT COUNT(*) FROM shipments")
    count = cursor.fetchone()[0] + 1
    shipment_id = data.get('shipment_id') or f"SHP{count:03d}"
    
    booking_date = data.get('booking_date') or datetime.now().strftime('%Y-%m-%d')
    expected_delivery = data.get('expected_delivery') or (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
    
    customer_name = data.get('customer_name') or data.get('customer') or 'Default Customer'
    pickup_location = data.get('pickup_location') or data.get('pickup') or 'Ludhiana'
    destination = data.get('destination') or 'Delhi'
    goods_type = data.get('goods_type') or 'General Freight'
    weight_kg = float(data.get('weight_kg') or data.get('weight') or 1000)
    
    cursor.execute('''
        INSERT INTO shipments (
            shipment_id, customer_id, customer_name, pickup_location, destination,
            goods_type, description, weight_kg, quantity, vehicle_id, vehicle_reg,
            driver_id, driver_name, status, booking_date, expected_delivery,
            shipping_cost, payment_method, payment_status, special_instructions
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        shipment_id, data.get('customer_id', 'CUST001'), customer_name,
        pickup_location, destination, goods_type,
        data.get('description', ''), weight_kg, int(data.get('quantity', 1)),
        data.get('vehicle_id'), data.get('vehicle_reg'), data.get('driver_id'),
        data.get('driver_name'), data.get('status', 'Booked'), booking_date,
        expected_delivery, float(data.get('shipping_cost', 5000)),
        data.get('payment_method', 'Credit Invoice'), data.get('payment_status', 'Pending'),
        data.get('special_instructions', '')
    ))
    
    # Add initial status history
    cursor.execute('''
        INSERT INTO shipment_status_history (shipment_id, status, location, updated_by, notes)
        VALUES (?,?,?,?,?)
    ''', (shipment_id, data.get('status', 'Booked'), pickup_location, 'System', 'Shipment created successfully.'))
    
    # Add initial delivery record
    delivery_id = f"DEL{count:03d}"
    cursor.execute('''
        INSERT INTO deliveries (delivery_id, shipment_id, customer_name, driver_id, vehicle_reg, pickup, destination, expected_delivery, status, otp_code)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    ''', (delivery_id, shipment_id, customer_name, data.get('driver_id'), data.get('vehicle_reg'), pickup_location, destination, expected_delivery, 'Pending', f"{int(uuid.uuid4().int % 9000 + 1000)}"))
    
    # Add invoice record
    invoice_id = f"INV-2026-{count:03d}"
    amount = float(data.get('shipping_cost', 5000))
    tax = round(amount * 0.18, 2)
    cursor.execute('''
        INSERT INTO payments (invoice_id, shipment_id, customer_name, amount, tax_amount, total_amount, payment_method, payment_status, invoice_date)
        VALUES (?,?,?,?,?,?,?,?,?)
    ''', (invoice_id, shipment_id, customer_name, amount, tax, amount + tax, data.get('payment_method', 'Credit Invoice'), data.get('payment_status', 'Pending'), booking_date))

    # Add Notification
    cursor.execute('''
        INSERT INTO notifications (notification_id, user_role, title, message, type)
        VALUES (?,?,?,?,?)
    ''', (f"NOTIF_{uuid.uuid4().hex[:6]}", 'ADMIN', f"New Shipment {shipment_id}", f"Shipment {shipment_id} from {pickup_location} to {destination} created.", 'success'))

    conn.commit()
    conn.close()
    return get_shipment_by_id(shipment_id)

def update_shipment_status(shipment_id, new_status, location=None, notes=None, updated_by="Admin"):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM shipments WHERE shipment_id = ?", (shipment_id,))
    shipment = row_to_dict(cursor.fetchone())
    if not shipment:
        conn.close()
        return None
        
    loc = location or shipment['destination'] if new_status == 'Delivered' else (location or shipment['pickup_location'])
    
    cursor.execute("UPDATE shipments SET status = ? WHERE shipment_id = ?", (new_status, shipment_id))
    
    cursor.execute('''
        INSERT INTO shipment_status_history (shipment_id, status, location, updated_by, notes)
        VALUES (?,?,?,?,?)
    ''', (shipment_id, new_status, loc, updated_by, notes or f"Status updated to {new_status}"))
    
    if new_status == 'Delivered':
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("UPDATE deliveries SET status = 'Delivered', actual_delivery = ? WHERE shipment_id = ?", (now_str, shipment_id))
        cursor.execute("UPDATE payments SET payment_status = 'Paid', paid_date = ? WHERE shipment_id = ?", (datetime.now().strftime('%Y-%m-%d'), shipment_id))
    elif new_status in ['In Transit', 'Out for Delivery']:
        cursor.execute("UPDATE deliveries SET status = 'In Progress' WHERE shipment_id = ?", (shipment_id,))

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

# VEHICLES SERVICE
def get_all_vehicles():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vehicles ORDER BY created_at DESC")
    vehicles = rows_to_list(cursor.fetchall())
    conn.close()
    return vehicles

def create_vehicle(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM vehicles")
    count = cursor.fetchone()[0] + 1
    vehicle_id = f"VEH{count:03d}"
    
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

# DRIVERS SERVICE
def get_all_drivers():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM drivers ORDER BY created_at DESC")
    drivers = rows_to_list(cursor.fetchall())
    conn.close()
    return drivers

def create_driver(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM drivers")
    count = cursor.fetchone()[0] + 1
    driver_id = f"DRV{count:03d}"
    
    cursor.execute('''
        INSERT INTO drivers (
            driver_id, name, phone, email, license_number, license_expiry,
            address, experience_years, assigned_vehicle_id, status, total_trips, completed_trips, rating
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        driver_id, data['name'], data['phone'], data['email'], data['license_number'],
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

# CUSTOMERS SERVICE
def get_all_customers():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM customers ORDER BY created_at DESC")
    custs = rows_to_list(cursor.fetchall())
    conn.close()
    return custs

def create_customer(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM customers")
    count = cursor.fetchone()[0] + 1
    cust_id = f"CUST{count:03d}"
    
    cursor.execute('''
        INSERT INTO customers (customer_id, name, company, phone, email, address, total_shipments, total_spent)
        VALUES (?,?,?,?,?,?,?,?)
    ''', (cust_id, data['name'], data['company'], data['phone'], data['email'], data['address'], 0, 0.0))
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

# WAREHOUSES SERVICE
def get_all_warehouses():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM warehouses ORDER BY created_at DESC")
    whs = rows_to_list(cursor.fetchall())
    conn.close()
    return whs

# DELIVERIES SERVICE
def get_all_deliveries():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM deliveries ORDER BY expected_delivery DESC")
    dels = rows_to_list(cursor.fetchall())
    conn.close()
    return dels

def confirm_delivery(shipment_id, otp_entered, receiver_name=None, notes=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM deliveries WHERE shipment_id = ?", (shipment_id,))
    d = row_to_dict(cursor.fetchone())
    
    if not d:
        conn.close()
        return False, "Delivery record not found for shipment"
        
    if d['otp_code'] != str(otp_entered).strip():
        conn.close()
        return False, "Invalid OTP Code. Please verify with receiver."
        
    conn.close()
    # Update shipment status
    update_shipment_status(shipment_id, 'Delivered', location=d['destination'], notes=f"Delivery confirmed by {receiver_name or 'Receiver'}. OTP verified.", updated_by="Driver")
    return True, "Delivery confirmed successfully!"

# PAYMENTS SERVICE
def get_all_payments():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM payments ORDER BY invoice_date DESC")
    pymts = rows_to_list(cursor.fetchall())
    conn.close()
    return pymts

# NOTIFICATIONS SERVICE
def get_all_notifications():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notifications ORDER BY timestamp DESC")
    notifs = rows_to_list(cursor.fetchall())
    conn.close()
    return notifs

def mark_notification_read(notif_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE notification_id = ?", (notif_id,))
    conn.commit()
    conn.close()
    return True
