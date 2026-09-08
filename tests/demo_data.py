import os
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

def seed_demo_data(conn):
    cursor = conn.cursor()

    customers_data = [
        ('CUST001', 'ABC Industries', 'ABC Group Ltd', '+91 9811223344', 'contact@abcind.com', 'GT Road, Ludhiana, Punjab', 12, 142000.0),
        ('CUST002', 'Zenith Electronics', 'Zenith Tech Corp', '+91 9822334455', 'logistics@zenith.com', 'Sector 18, Gurgaon, Haryana', 8, 98500.0),
        ('CUST003', 'Globe Pharma Ltd', 'Globe Global', '+91 9833445566', 'supply@globepharma.com', 'Industrial Area, Baddi, HP', 15, 215000.0),
        ('CUST004', 'Apex Retail Solutions', 'Apex Retail', '+91 9844556677', 'orders@apexretail.in', 'Connaught Place, New Delhi', 5, 45000.0),
        ('CUST005', 'Royal Textile Mills', 'Royal Fabrics', '+91 9855667788', 'info@royaltextiles.com', 'Textile Hub, Ahmedabad, Gujarat', 9, 118000.0)
    ]
    cursor.executemany(
        "INSERT INTO customers (customer_id, name, company, phone, email, address, total_shipments, total_spent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        customers_data,
    )

    vehicles_data = [
        ('VEH001', 'HR26BX4587', 'Truck', 'Tata', 'Prima 2830.K', 2023, 15.0, 'Diesel', 'Delhi', 'DRV001', '2027-05-15', '2027-06-30', '2027-04-10', '2026-11-15', 'Available'),
        ('VEH002', 'PB10CD1234', 'Container', 'Ashok Leyland', 'AVTR 3520', 2022, 25.0, 'Diesel', 'Jaipur', 'DRV002', '2027-02-10', '2027-03-20', '2027-01-15', '2026-10-05', 'On Trip'),
        ('VEH003', 'UP32KL5678', 'Tata Ace', 'Tata Motors', 'Ace Gold', 2024, 5.0, 'CNG', 'Ludhiana', 'DRV003', '2027-08-01', '2027-09-12', '2027-07-20', '2026-12-01', 'Available'),
        ('VEH004', 'RJ14GH9012', 'Truck', 'BharatBenz', '1617R', 2021, 12.0, 'Diesel', 'Jaipur', 'DRV004', '2026-10-15', '2026-11-20', '2026-09-30', '2026-09-10', 'Maintenance'),
        ('VEH005', 'DL8CAB6789', 'Container', 'Mahindra', 'Blazo X 28', 2023, 20.0, 'Diesel', 'Mumbai', 'DRV005', '2027-04-05', '2027-05-10', '2027-03-15', '2026-10-25', 'On Trip')
    ]
    cursor.executemany(
        "INSERT INTO vehicles (vehicle_id, registration_number, vehicle_type, make, model, year, capacity_mt, fuel_type, current_location, assigned_driver_id, insurance_expiry, permit_expiry, fitness_expiry, service_due_date, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        vehicles_data,
    )

    drivers_data = [
        ('DRV001', 'Rajesh Kumar', '+91 9876543212', 'driver@apexflow.com', 'DL-1420110098', '2028-09-15', 'Flat 402, Rohini, Delhi', 8, 'VEH001', 'Available', 142, 140, 4.9),
        ('DRV002', 'Sukhwinder Singh', '+91 9876543222', 'sukhwinder@apexflow.com', 'PB-0820150042', '2027-11-20', 'Model Town, Ludhiana, Punjab', 11, 'VEH002', 'On Trip', 210, 205, 4.8),
        ('DRV003', 'Vikram Yadav', '+91 9876543233', 'vikram@apexflow.com', 'UP-3220180076', '2029-01-10', 'Gomti Nagar, Lucknow, UP', 5, 'VEH003', 'Available', 85, 84, 4.7),
        ('DRV004', 'Ramesh Patel', '+91 9876543244', 'ramesh@apexflow.com', 'GJ-0120140089', '2027-06-05', 'Ashram Road, Ahmedabad', 14, 'VEH004', 'Off Duty', 315, 308, 4.9),
        ('DRV005', 'Amit Sharma', '+91 9876543255', 'amit@apexflow.com', 'DL-0420190033', '2028-04-18', 'Dwarka Sector 7, New Delhi', 6, 'VEH005', 'On Trip', 98, 96, 4.6)
    ]
    cursor.executemany(
        "INSERT INTO drivers (driver_id, name, phone, email, license_number, license_expiry, address, experience_years, assigned_vehicle_id, status, total_trips, completed_trips, rating) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        drivers_data,
    )

    shipments_data = [
        ('SHP001', 'CUST001', 'ABC Industries', 'Ludhiana', 'Delhi', 'Electronics', '50 Crates of LED Displays', 8000.0, 50, 'VEH001', 'HR26BX4587', 'DRV001', 'Rajesh Kumar', 'In Transit', '2026-09-05', '2026-09-08', 8500.0, 'Online Bank Transfer', 'Paid', 'Handle with extreme care, fragile goods.'),
        ('SHP002', 'CUST002', 'Zenith Electronics', 'Chandigarh', 'Jaipur', 'Consumer Tech', '20 Pallets of Smart TVs', 4500.0, 20, 'VEH002', 'PB10CD1234', 'DRV002', 'Sukhwinder Singh', 'Picked Up', '2026-09-06', '2026-09-09', 12400.0, 'Credit Invoice', 'Pending', 'Keep dry, priority shipment.'),
        ('SHP003', 'CUST003', 'Globe Pharma Ltd', 'Delhi', 'Mumbai', 'Pharmaceuticals', '100 Boxes Cold Storage Vaccines', 3200.0, 100, 'VEH005', 'DL8CAB6789', 'DRV005', 'Amit Sharma', 'Delivered', '2026-09-02', '2026-09-05', 24500.0, 'UPI / Instant', 'Paid', 'Maintain 2-8°C temperature throughout.'),
        ('SHP004', 'CUST004', 'Apex Retail Solutions', 'Kanpur', 'Patna', 'Garments & Retail', '80 Cartons Apparel', 6000.0, 80, 'VEH004', 'RJ14GH9012', 'DRV004', 'Ramesh Patel', 'Delayed', '2026-09-01', '2026-09-04', 15800.0, 'Credit Invoice', 'Pending', 'Weather delay near Kanpur highway.'),
        ('SHP005', 'CUST005', 'Royal Textile Mills', 'Amritsar', 'Bangalore', 'Raw Cotton Bales', '35 Bales of Raw Cotton', 14000.0, 35, 'VEH002', 'PB10CD1234', 'DRV002', 'Sukhwinder Singh', 'In Transit', '2026-09-04', '2026-09-10', 38000.0, 'Bank Transfer', 'Paid', 'Long distance express freight.')
    ]
    cursor.executemany(
        "INSERT INTO shipments (shipment_id, customer_id, customer_name, pickup_location, destination, goods_type, description, weight_kg, quantity, vehicle_id, vehicle_reg, driver_id, driver_name, status, booking_date, expected_delivery, shipping_cost, payment_method, payment_status, special_instructions) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        shipments_data,
    )

    status_history_data = [
        (1, 'SHP001', 'Booked', '2026-09-05 09:00:00', 'Ludhiana Warehouse', 'System Admin', 'Shipment booked by ABC Industries'),
        (2, 'SHP001', 'Confirmed', '2026-09-05 10:30:00', 'Ludhiana Dispatch', 'Logistics Manager', 'Vehicle HR26BX4587 assigned'),
        (3, 'SHP001', 'Picked Up', '2026-09-05 14:15:00', 'Ludhiana Cargo Hub', 'Rajesh Kumar', 'Goods loaded and verified'),
        (4, 'SHP001', 'In Transit', '2026-09-06 08:00:00', 'Ambala Highway NH44', 'GPS Automated Tracker', 'En route to Delhi destination'),
        (5, 'SHP003', 'Delivered', '2026-09-05 17:45:00', 'Mumbai Central Logistics Hub', 'Amit Sharma', 'Delivered and OTP verified by Receiver')
    ]
    cursor.executemany(
        "INSERT INTO shipment_status_history (history_id, shipment_id, status, timestamp, location, updated_by, notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        status_history_data,
    )

    routes_data = [
        ('RTE001', 'Delhi', 'Jaipur', 286.0, '3h 20m', 2480.0, 'Via NH48 (Optimized Expressway)', '[{"stop":"Gurgaon Toll","eta":"45m"},{"stop":"Neemrana","eta":"1h 45m"}]'),
        ('RTE002', 'Ludhiana', 'Delhi', 315.0, '4h 45m', 3100.0, 'Via NH44 Express Highway', '[{"stop":"Ambala Cantt","eta":"1h 30m"},{"stop":"Panipat","eta":"3h 10m"}]'),
        ('RTE003', 'Delhi', 'Mumbai', 1415.0, '22h 30m', 15800.0, 'Via NE2 / NH48 Golden Quadrilateral', '[{"stop":"Udaipur","eta":"9h 00m"},{"stop":"Ahmedabad","eta":"14h 00m"}]'),
        ('RTE004', 'Amritsar', 'Bangalore', 2480.0, '38h 00m', 29500.0, 'Via NH44 North-South Corridor', '[{"stop":"Nagpur Hub","eta":"18h 00m"},{"stop":"Hyderabad","eta":"28h 00m"}]')
    ]
    cursor.executemany(
        "INSERT INTO routes (route_id, pickup, destination, distance_km, estimated_time, fuel_cost_est, recommended_route, stops_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        routes_data,
    )

    warehouses_data = [
        ('WH001', 'North Hub Freight Terminal', 'Ludhiana, Punjab', 'Harpreet Singh', '+91 9811122233', 5000.0, 3200.0, 'Active'),
        ('WH002', 'NCR Central Logistics Park', 'Gurgaon, NCR', 'Sanjay Verma', '+91 9822233344', 12000.0, 8900.0, 'Active'),
        ('WH003', 'Pink City Distribution Center', 'Jaipur, Rajasthan', 'Mohan Lal', '+91 9833344455', 4000.0, 3950.0, 'Full'),
        ('WH004', 'West Coast Gateway Depot', 'Bhiwandi, Mumbai', 'Pravin Shinde', '+91 9844455566', 15000.0, 7400.0, 'Active')
    ]
    cursor.executemany(
        "INSERT INTO warehouses (warehouse_id, name, location, manager_name, contact_phone, capacity_tons, current_occupancy_tons, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        warehouses_data,
    )

    # OTPs are hashed at rest. Seed codes are random and immediately unusable from git.
    used_hash = generate_password_hash('USED')
    live_hash = generate_password_hash(os.urandom(16).hex())
    otp_expiry = (datetime.now() + timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
    deliveries_data = [
        ('DEL001', 'SHP001', 'ABC Industries', 'DRV001', 'HR26BX4587', 'Ludhiana', 'Delhi', '2026-09-08', None, 'In Progress', live_hash, 0, otp_expiry, 'Vikram Malhotra', 'On schedule to Delhi West Hub', None),
        ('DEL002', 'SHP003', 'Globe Pharma Ltd', 'DRV005', 'DL8CAB6789', 'Delhi', 'Mumbai', '2026-09-05', '2026-09-05 17:45:00', 'Delivered', used_hash, 0, otp_expiry, 'Dr. Alok Nath', 'Vaccines delivered at 4C verified.', 'SIG_OK_DIGITAL_VERIFIED'),
        ('DEL003', 'SHP004', 'Apex Retail Solutions', 'DRV004', 'RJ14GH9012', 'Kanpur', 'Patna', '2026-09-04', None, 'Pending', live_hash, 0, otp_expiry, 'Rohan Gupta', 'Delayed due to heavy rainfall.', None)
    ]
    cursor.executemany(
        "INSERT INTO deliveries (delivery_id, shipment_id, customer_name, driver_id, vehicle_reg, pickup, destination, expected_delivery, actual_delivery, status, otp_code, otp_attempts, otp_expires_at, receiver_name, delivery_notes, signature_data) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        deliveries_data,
    )

    payments_data = [
        ('INV-2026-001', 'SHP001', 'ABC Industries', 8500.0, 1530.0, 10030.0, 'Online Bank Transfer', 'Paid', '2026-09-05', '2026-09-05'),
        ('INV-2026-002', 'SHP002', 'Zenith Electronics', 12400.0, 2232.0, 14632.0, 'Credit Invoice', 'Pending', '2026-09-06', None),
        ('INV-2026-003', 'SHP003', 'Globe Pharma Ltd', 24500.0, 4410.0, 28910.0, 'UPI / Instant', 'Paid', '2026-09-02', '2026-09-02'),
        ('INV-2026-004', 'SHP004', 'Apex Retail Solutions', 15800.0, 2844.0, 18644.0, 'Credit Invoice', 'Pending', '2026-09-01', None)
    ]
    cursor.executemany(
        "INSERT INTO payments (invoice_id, shipment_id, customer_name, amount, tax_amount, total_amount, payment_method, payment_status, invoice_date, paid_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        payments_data,
    )

    notifications_data = [
        ('NOTIF001', 'ADMIN', 'Shipment In Transit', 'Shipment SHP001 (Ludhiana to Delhi) is currently in transit on NH44.', 'info', None, '2026-09-06 08:05:00'),
        ('NOTIF002', 'MANAGER', 'Shipment Delayed', 'Shipment SHP004 (Kanpur to Patna) reported a weather delay.', 'warning', None, '2026-09-06 09:12:00'),
        ('NOTIF003', 'DRIVER', 'Trip Assigned', 'Driver Rajesh Kumar assigned to Vehicle HR26BX4587 for SHP001.', 'success', None, '2026-09-05 10:30:00'),
        ('NOTIF004', 'ADMIN', 'Vehicle Maintenance Due', 'Vehicle RJ14GH9012 requires scheduled engine service.', 'danger', None, '2026-09-06 11:00:00'),
        ('NOTIF005', 'CUSTOMER', 'Shipment Update', 'Your shipment SHP001 is in transit to Delhi.', 'info', None, '2026-09-06 08:06:00')
    ]
    cursor.executemany(
        "INSERT INTO notifications (notification_id, user_role, title, message, type, recipient_user_id, timestamp) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        notifications_data,
    )




