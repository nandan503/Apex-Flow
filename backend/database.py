import sqlite3
import os
from werkzeug.security import generate_password_hash
from backend.config import DATABASE_PATH, DATABASE_URL

# PostgreSQL Helper Wrapper for SQLite API Compatibility
class PostgresRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class PostgresCursorWrapper:
    def __init__(self, pg_cursor):
        self.cursor = pg_cursor

    def execute(self, query, params=None):
        # Translate SQLite AUTOINCREMENT and ? placeholders for PostgreSQL
        pg_query = query.replace('AUTOINCREMENT', '').replace('?', '%s')
        if params is not None:
            self.cursor.execute(pg_query, params)
        else:
            self.cursor.execute(pg_query)
        return self

    def executemany(self, query, params_list):
        pg_query = query.replace('AUTOINCREMENT', '').replace('?', '%s')
        self.cursor.executemany(pg_query, params_list)
        return self

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        colnames = [desc[0] for desc in self.cursor.description]
        return PostgresRow(zip(colnames, row))

    def fetchall(self):
        rows = self.cursor.fetchall()
        if not rows:
            return []
        colnames = [desc[0] for desc in self.cursor.description]
        return [PostgresRow(zip(colnames, r)) for r in rows]

class PostgresConnWrapper:
    def __init__(self, pg_conn):
        self.conn = pg_conn

    def cursor(self):
        return PostgresCursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

def get_db_connection():
    db_url = DATABASE_URL or os.environ.get('DATABASE_URL', '')
    if db_url and (db_url.startswith('postgres://') or db_url.startswith('postgresql://')):
        try:
            import psycopg2
            if db_url.startswith('postgres://'):
                db_url = db_url.replace('postgres://', 'postgresql://', 1)
            pg_conn = psycopg2.connect(db_url)
            pg_conn.autocommit = False
            return PostgresConnWrapper(pg_conn)
        except Exception as e:
            print(f"[DB Warning] Could not connect to PostgreSQL ({e}). Falling back to SQLite.")

    # SQLite fallback for local development
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def initialize_database():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('ADMIN', 'MANAGER', 'DRIVER', 'CUSTOMER')),
        phone TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 2. Customers table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        company TEXT NOT NULL,
        phone TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        address TEXT NOT NULL,
        total_shipments INTEGER DEFAULT 0,
        total_spent REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 3. Vehicles table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS vehicles (
        vehicle_id TEXT PRIMARY KEY,
        registration_number TEXT UNIQUE NOT NULL,
        vehicle_type TEXT NOT NULL,
        make TEXT NOT NULL,
        model TEXT NOT NULL,
        year INTEGER NOT NULL,
        capacity_mt REAL NOT NULL,
        fuel_type TEXT NOT NULL,
        current_location TEXT NOT NULL,
        assigned_driver_id TEXT,
        insurance_expiry DATE NOT NULL,
        permit_expiry DATE NOT NULL,
        fitness_expiry DATE NOT NULL,
        service_due_date DATE NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('Available', 'On Trip', 'Maintenance', 'Inactive')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 4. Drivers table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS drivers (
        driver_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        license_number TEXT UNIQUE NOT NULL,
        license_expiry DATE NOT NULL,
        address TEXT NOT NULL,
        experience_years INTEGER NOT NULL,
        assigned_vehicle_id TEXT,
        status TEXT NOT NULL CHECK(status IN ('Available', 'On Trip', 'Off Duty', 'Suspended')),
        total_trips INTEGER DEFAULT 0,
        completed_trips INTEGER DEFAULT 0,
        rating REAL DEFAULT 5.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 5. Shipments table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS shipments (
        shipment_id TEXT PRIMARY KEY,
        customer_id TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        pickup_location TEXT NOT NULL,
        destination TEXT NOT NULL,
        goods_type TEXT NOT NULL,
        description TEXT,
        weight_kg REAL NOT NULL,
        quantity INTEGER NOT NULL,
        vehicle_id TEXT,
        vehicle_reg TEXT,
        driver_id TEXT,
        driver_name TEXT,
        status TEXT NOT NULL CHECK(status IN (
            'Booked', 'Confirmed', 'Picked Up', 'At Warehouse',
            'In Transit', 'Out for Delivery', 'Delivered', 'Delayed', 'Cancelled', 'Returned'
        )),
        booking_date DATE NOT NULL,
        expected_delivery DATE NOT NULL,
        shipping_cost REAL NOT NULL,
        payment_method TEXT NOT NULL,
        payment_status TEXT NOT NULL CHECK(payment_status IN ('Pending', 'Paid', 'Failed', 'Refunded')),
        special_instructions TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
    );
    ''')

    # 6. Shipment status history table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS shipment_status_history (
        history_id INTEGER PRIMARY KEY AUTOINCREMENT,
        shipment_id TEXT NOT NULL,
        status TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        location TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        notes TEXT,
        FOREIGN KEY(shipment_id) REFERENCES shipments(shipment_id) ON DELETE CASCADE
    );
    ''')

    # 7. Routes table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS routes (
        route_id TEXT PRIMARY KEY,
        pickup TEXT NOT NULL,
        destination TEXT NOT NULL,
        distance_km REAL NOT NULL,
        estimated_time TEXT NOT NULL,
        fuel_cost_est REAL NOT NULL,
        recommended_route TEXT NOT NULL,
        stops_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 8. Warehouses table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS warehouses (
        warehouse_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        location TEXT NOT NULL,
        manager_name TEXT NOT NULL,
        contact_phone TEXT NOT NULL,
        capacity_tons REAL NOT NULL,
        current_occupancy_tons REAL NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('Active', 'Full', 'Maintenance')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 9. Deliveries table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS deliveries (
        delivery_id TEXT PRIMARY KEY,
        shipment_id TEXT UNIQUE NOT NULL,
        customer_name TEXT NOT NULL,
        driver_id TEXT,
        vehicle_reg TEXT,
        pickup TEXT NOT NULL,
        destination TEXT NOT NULL,
        expected_delivery DATE NOT NULL,
        actual_delivery TIMESTAMP,
        status TEXT NOT NULL CHECK(status IN ('Pending', 'In Progress', 'Delivered', 'Failed')),
        otp_code TEXT NOT NULL,
        receiver_name TEXT,
        delivery_notes TEXT,
        signature_data TEXT,
        FOREIGN KEY(shipment_id) REFERENCES shipments(shipment_id) ON DELETE CASCADE
    );
    ''')

    # 10. Payments table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        invoice_id TEXT PRIMARY KEY,
        shipment_id TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        amount REAL NOT NULL,
        tax_amount REAL NOT NULL,
        total_amount REAL NOT NULL,
        payment_method TEXT NOT NULL,
        payment_status TEXT NOT NULL CHECK(payment_status IN ('Pending', 'Paid', 'Failed', 'Refunded')),
        invoice_date DATE NOT NULL,
        paid_date DATE,
        FOREIGN KEY(shipment_id) REFERENCES shipments(shipment_id)
    );
    ''')

    # 11. Notifications table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
        notification_id TEXT PRIMARY KEY,
        user_role TEXT,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('info', 'success', 'warning', 'danger')),
        is_read INTEGER DEFAULT 0,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 12. Maintenance Records table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS maintenance_records (
        record_id TEXT PRIMARY KEY,
        vehicle_id TEXT NOT NULL,
        service_type TEXT NOT NULL,
        cost REAL NOT NULL,
        service_date DATE NOT NULL,
        next_due_date DATE NOT NULL,
        status TEXT NOT NULL,
        notes TEXT,
        FOREIGN KEY(vehicle_id) REFERENCES vehicles(vehicle_id)
    );
    ''')

    conn.commit()
    seed_demo_data(conn)
    conn.close()

def seed_demo_data(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] > 0:
        return  # Database already seeded

    # Seed Users
    users_data = [
        ('USR001', 'System Admin', 'admin@apexflow.com', generate_password_hash('Admin@123'), 'ADMIN', '+91 9876543210'),
        ('USR002', 'Logistics Manager', 'manager@apexflow.com', generate_password_hash('Manager@123'), 'MANAGER', '+91 9876543211'),
        ('USR003', 'Rajesh Kumar', 'driver@apexflow.com', generate_password_hash('Driver@123'), 'DRIVER', '+91 9876543212'),
        ('USR004', 'ABC Industries', 'customer@apexflow.com', generate_password_hash('Customer@123'), 'CUSTOMER', '+91 9876543213'),
    ]
    cursor.executemany("INSERT INTO users (user_id, name, email, password_hash, role, phone) VALUES (?,?,?,?,?,?)", users_data)

    # Seed Customers
    customers_data = [
        ('CUST001', 'ABC Industries', 'ABC Group Ltd', '+91 9811223344', 'contact@abcind.com', 'GT Road, Ludhiana, Punjab', 12, 142000.0),
        ('CUST002', 'Zenith Electronics', 'Zenith Tech Corp', '+91 9822334455', 'logistics@zenith.com', 'Sector 18, Gurgaon, Haryana', 8, 98500.0),
        ('CUST003', 'Globe Pharma Ltd', 'Globe Global', '+91 9833445566', 'supply@globepharma.com', 'Industrial Area, Baddi, HP', 15, 215000.0),
        ('CUST004', 'Apex Retail Solutions', 'Apex Retail', '+91 9844556677', 'orders@apexretail.in', 'Connaught Place, New Delhi', 5, 45000.0),
        ('CUST005', 'Royal Textile Mills', 'Royal Fabrics', '+91 9855667788', 'info@royaltextiles.com', 'Textile Hub, Ahmedabad, Gujarat', 9, 118000.0)
    ]
    cursor.executemany("INSERT INTO customers (customer_id, name, company, phone, email, address, total_shipments, total_spent) VALUES (?,?,?,?,?,?,?,?)", customers_data)

    # Seed Vehicles
    vehicles_data = [
        ('VEH001', 'HR26BX4587', 'Truck', 'Tata', 'Prima 2830.K', 2023, 15.0, 'Diesel', 'Delhi', 'DRV001', '2027-05-15', '2027-06-30', '2027-04-10', '2026-11-15', 'Available'),
        ('VEH002', 'PB10CD1234', 'Container', 'Ashok Leyland', 'AVTR 3520', 2022, 25.0, 'Diesel', 'Jaipur', 'DRV002', '2027-02-10', '2027-03-20', '2027-01-15', '2026-10-05', 'On Trip'),
        ('VEH003', 'UP32KL5678', 'Tata Ace', 'Tata Motors', 'Ace Gold', 2024, 5.0, 'CNG', 'Ludhiana', 'DRV003', '2027-08-01', '2027-09-12', '2027-07-20', '2026-12-01', 'Available'),
        ('VEH004', 'RJ14GH9012', 'Truck', 'BharatBenz', '1617R', 2021, 12.0, 'Diesel', 'Jaipur', 'DRV004', '2026-10-15', '2026-11-20', '2026-09-30', '2026-09-10', 'Maintenance'),
        ('VEH005', 'DL8CAB6789', 'Container', 'Mahindra', 'Blazo X 28', 2023, 20.0, 'Diesel', 'Mumbai', 'DRV005', '2027-04-05', '2027-05-10', '2027-03-15', '2026-10-25', 'On Trip')
    ]
    cursor.executemany("INSERT INTO vehicles (vehicle_id, registration_number, vehicle_type, make, model, year, capacity_mt, fuel_type, current_location, assigned_driver_id, insurance_expiry, permit_expiry, fitness_expiry, service_due_date, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vehicles_data)

    # Seed Drivers
    drivers_data = [
        ('DRV001', 'Rajesh Kumar', '+91 9876543212', 'driver@apexflow.com', 'DL-1420110098', '2028-09-15', 'Flat 402, Rohini, Delhi', 8, 'VEH001', 'Available', 142, 140, 4.9),
        ('DRV002', 'Sukhwinder Singh', '+91 9876543222', 'sukhwinder@apexflow.com', 'PB-0820150042', '2027-11-20', 'Model Town, Ludhiana, Punjab', 11, 'VEH002', 'On Trip', 210, 205, 4.8),
        ('DRV003', 'Vikram Yadav', '+91 9876543233', 'vikram@apexflow.com', 'UP-3220180076', '2029-01-10', 'Gomti Nagar, Lucknow, UP', 5, 'VEH003', 'Available', 85, 84, 4.7),
        ('DRV004', 'Ramesh Patel', '+91 9876543244', 'ramesh@apexflow.com', 'GJ-0120140089', '2027-06-05', 'Ashram Road, Ahmedabad', 14, 'VEH004', 'Off Duty', 315, 308, 4.9),
        ('DRV005', 'Amit Sharma', '+91 9876543255', 'amit@apexflow.com', 'DL-0420190033', '2028-04-18', 'Dwarka Sector 7, New Delhi', 6, 'VEH005', 'On Trip', 98, 96, 4.6)
    ]
    cursor.executemany("INSERT INTO drivers (driver_id, name, phone, email, license_number, license_expiry, address, experience_years, assigned_vehicle_id, status, total_trips, completed_trips, rating) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", drivers_data)

    # Seed Shipments
    shipments_data = [
        ('SHP001', 'CUST001', 'ABC Industries', 'Ludhiana', 'Delhi', 'Electronics', '50 Crates of LED Displays', 8000.0, 50, 'VEH001', 'HR26BX4587', 'DRV001', 'Rajesh Kumar', 'In Transit', '2026-09-05', '2026-09-08', 8500.0, 'Online Bank Transfer', 'Paid', 'Handle with extreme care, fragile goods.'),
        ('SHP002', 'CUST002', 'Zenith Electronics', 'Chandigarh', 'Jaipur', 'Consumer Tech', '20 Pallets of Smart TVs', 4500.0, 20, 'VEH002', 'PB10CD1234', 'DRV002', 'Sukhwinder Singh', 'Picked Up', '2026-09-06', '2026-09-09', 12400.0, 'Credit Invoice', 'Pending', 'Keep dry, priority shipment.'),
        ('SHP003', 'CUST003', 'Globe Pharma Ltd', 'Delhi', 'Mumbai', 'Pharmaceuticals', '100 Boxes Cold Storage Vaccines', 3200.0, 100, 'VEH005', 'DL8CAB6789', 'DRV005', 'Amit Sharma', 'Delivered', '2026-09-02', '2026-09-05', 24500.0, 'UPI / Instant', 'Paid', 'Maintain 2-8°C temperature throughout.'),
        ('SHP004', 'CUST004', 'Apex Retail Solutions', 'Kanpur', 'Patna', 'Garments & Retail', '80 Cartons Apparel', 6000.0, 80, 'VEH004', 'RJ14GH9012', 'DRV004', 'Ramesh Patel', 'Delayed', '2026-09-01', '2026-09-04', 15800.0, 'Credit Invoice', 'Pending', 'Weather delay near Kanpur highway.'),
        ('SHP005', 'CUST005', 'Royal Textile Mills', 'Amritsar', 'Bangalore', 'Raw Cotton Bales', '35 Bales of Raw Cotton', 14000.0, 35, 'VEH002', 'PB10CD1234', 'DRV002', 'Sukhwinder Singh', 'In Transit', '2026-09-04', '2026-09-10', 38000.0, 'Bank Transfer', 'Paid', 'Long distance express freight.')
    ]
    cursor.executemany("INSERT INTO shipments (shipment_id, customer_id, customer_name, pickup_location, destination, goods_type, description, weight_kg, quantity, vehicle_id, vehicle_reg, driver_id, driver_name, status, booking_date, expected_delivery, shipping_cost, payment_method, payment_status, special_instructions) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", shipments_data)

    # Seed Status History
    status_history_data = [
        (1, 'SHP001', 'Booked', '2026-09-05 09:00:00', 'Ludhiana Warehouse', 'System Admin', 'Shipment booked by ABC Industries'),
        (2, 'SHP001', 'Confirmed', '2026-09-05 10:30:00', 'Ludhiana Dispatch', 'Logistics Manager', 'Vehicle HR26BX4587 assigned'),
        (3, 'SHP001', 'Picked Up', '2026-09-05 14:15:00', 'Ludhiana Cargo Hub', 'Rajesh Kumar', 'Goods loaded and verified'),
        (4, 'SHP001', 'In Transit', '2026-09-06 08:00:00', 'Ambala Highway NH44', 'GPS Automated Tracker', 'En route to Delhi destination'),
        (5, 'SHP003', 'Delivered', '2026-09-05 17:45:00', 'Mumbai Central Logistics Hub', 'Amit Sharma', 'Delivered & OTP 8492 verified by Receiver')
    ]
    cursor.executemany("INSERT INTO shipment_status_history (history_id, shipment_id, status, timestamp, location, updated_by, notes) VALUES (?,?,?,?,?,?,?)", status_history_data)

    # Seed Routes
    routes_data = [
        ('RTE001', 'Delhi', 'Jaipur', 286.0, '3h 20m', 2480.0, 'Via NH48 (Optimized Expressway)', '[{"stop":"Gurgaon Toll","eta":"45m"},{"stop":"Neemrana","eta":"1h 45m"}]'),
        ('RTE002', 'Ludhiana', 'Delhi', 315.0, '4h 45m', 3100.0, 'Via NH44 Express Highway', '[{"stop":"Ambala Cantt","eta":"1h 30m"},{"stop":"Panipat","eta":"3h 10m"}]'),
        ('RTE003', 'Delhi', 'Mumbai', 1415.0, '22h 30m', 15800.0, 'Via NE2 / NH48 Golden Quadrilateral', '[{"stop":"Udaipur","eta":"9h 00m"},{"stop":"Ahmedabad","eta":"14h 00m"}]'),
        ('RTE004', 'Amritsar', 'Bangalore', 2480.0, '38h 00m', 29500.0, 'Via NH44 North-South Corridor', '[{"stop":"Nagpur Hub","eta":"18h 00m"},{"stop":"Hyderabad","eta":"28h 00m"}]')
    ]
    cursor.executemany("INSERT INTO routes (route_id, pickup, destination, distance_km, estimated_time, fuel_cost_est, recommended_route, stops_json) VALUES (?,?,?,?,?,?,?,?)", routes_data)

    # Seed Warehouses
    warehouses_data = [
        ('WH001', 'North Hub Freight Terminal', 'Ludhiana, Punjab', 'Harpreet Singh', '+91 9811122233', 5000.0, 3200.0, 'Active'),
        ('WH002', 'NCR Central Logistics Park', 'Gurgaon, NCR', 'Sanjay Verma', '+91 9822233344', 12000.0, 8900.0, 'Active'),
        ('WH003', 'Pink City Distribution Center', 'Jaipur, Rajasthan', 'Mohan Lal', '+91 9833344455', 4000.0, 3950.0, 'Full'),
        ('WH004', 'West Coast Gateway Depot', 'Bhiwandi, Mumbai', 'Pravin Shinde', '+91 9844455566', 15000.0, 7400.0, 'Active')
    ]
    cursor.executemany("INSERT INTO warehouses (warehouse_id, name, location, manager_name, contact_phone, capacity_tons, current_occupancy_tons, status) VALUES (?,?,?,?,?,?,?,?)", warehouses_data)

    # Seed Deliveries
    deliveries_data = [
        ('DEL001', 'SHP001', 'ABC Industries', 'DRV001', 'HR26BX4587', 'Ludhiana', 'Delhi', '2026-09-08', None, 'In Progress', '4912', 'Vikram Malhotra', 'On schedule to Delhi West Hub', None),
        ('DEL002', 'SHP003', 'Globe Pharma Ltd', 'DRV005', 'DL8CAB6789', 'Delhi', 'Mumbai', '2026-09-05', '2026-09-05 17:45:00', 'Delivered', '8492', 'Dr. Alok Nath', 'Vaccines delivered at 4°C verified.', 'SIG_OK_DIGITAL_VERIFIED'),
        ('DEL003', 'SHP004', 'Apex Retail Solutions', 'DRV004', 'RJ14GH9012', 'Kanpur', 'Patna', '2026-09-04', None, 'Pending', '3381', 'Rohan Gupta', 'Delayed due to heavy rainfall.', None)
    ]
    cursor.executemany("INSERT INTO deliveries (delivery_id, shipment_id, customer_name, driver_id, vehicle_reg, pickup, destination, expected_delivery, actual_delivery, status, otp_code, receiver_name, delivery_notes, signature_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", deliveries_data)

    # Seed Payments
    payments_data = [
        ('INV-2026-001', 'SHP001', 'ABC Industries', 8500.0, 1530.0, 10030.0, 'Online Bank Transfer', 'Paid', '2026-09-05', '2026-09-05'),
        ('INV-2026-002', 'SHP002', 'Zenith Electronics', 12400.0, 2232.0, 14632.0, 'Credit Invoice', 'Pending', '2026-09-06', None),
        ('INV-2026-003', 'SHP003', 'Globe Pharma Ltd', 24500.0, 4410.0, 28910.0, 'UPI / Instant', 'Paid', '2026-09-02', '2026-09-02'),
        ('INV-2026-004', 'SHP004', 'Apex Retail Solutions', 15800.0, 2844.0, 18644.0, 'Credit Invoice', 'Pending', '2026-09-01', None)
    ]
    cursor.executemany("INSERT INTO payments (invoice_id, shipment_id, customer_name, amount, tax_amount, total_amount, payment_method, payment_status, invoice_date, paid_date) VALUES (?,?,?,?,?,?,?,?,?,?)", payments_data)

    # Seed Notifications
    notifications_data = [
        ('NOTIF001', 'ADMIN', 'Shipment In Transit', 'Shipment SHP001 (Ludhiana → Delhi) is currently in transit on NH44.', 'info', 0, '2026-09-06 08:05:00'),
        ('NOTIF002', 'MANAGER', 'Shipment Delayed', 'Shipment SHP004 (Kanpur → Patna) reported a weather delay.', 'warning', 0, '2026-09-06 09:12:00'),
        ('NOTIF003', 'DRIVER', 'Trip Assigned', 'Driver Rajesh Kumar assigned to Vehicle HR26BX4587 for SHP001.', 'success', 1, '2026-09-05 10:30:00'),
        ('NOTIF004', 'ADMIN', 'Vehicle Maintenance Due', 'Vehicle RJ14GH9012 requires scheduled engine service.', 'danger', 0, '2026-09-06 11:00:00')
    ]
    cursor.executemany("INSERT INTO notifications (notification_id, user_role, title, message, type, is_read, timestamp) VALUES (?,?,?,?,?,?,?)", notifications_data)

    conn.commit()

if __name__ == '__main__':
    initialize_database()
    print("APEX FLOW database initialized successfully!")
