from backend.database import get_db_connection
from backend.models import rows_to_list, row_to_dict

def get_dashboard_kpis():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Total shipments count & status breakdown
    cursor.execute("SELECT status, COUNT(*) as count FROM shipments GROUP BY status")
    status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
    
    cursor.execute("SELECT COUNT(*) FROM shipments")
    total_shipments = cursor.fetchone()[0]

    # Vehicles status breakdown
    cursor.execute("SELECT COUNT(*) FROM vehicles WHERE status = 'Available'")
    avail_vehicles = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM vehicles")
    total_vehicles = cursor.fetchone()[0]

    # Drivers count
    cursor.execute("SELECT COUNT(*) FROM drivers WHERE status = 'Available' OR status = 'On Trip'")
    active_drivers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM drivers")
    total_drivers = cursor.fetchone()[0]

    # Total revenue & costs
    cursor.execute("SELECT SUM(shipping_cost) FROM shipments")
    total_revenue = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT SUM(fuel_cost_est) FROM routes")
    est_fuel_cost = cursor.fetchone()[0] or 0.0

    # Recent shipments list
    cursor.execute("SELECT * FROM shipments ORDER BY created_at DESC LIMIT 5")
    recent_shipments = rows_to_list(cursor.fetchall())

    # Recent vehicles list
    cursor.execute("SELECT * FROM vehicles ORDER BY created_at DESC LIMIT 5")
    recent_vehicles = rows_to_list(cursor.fetchall())

    conn.close()

    return {
        'total_shipments': total_shipments,
        'booked': status_counts.get('Booked', 0),
        'confirmed': status_counts.get('Confirmed', 0),
        'picked_up': status_counts.get('Picked Up', 0),
        'in_transit': status_counts.get('In Transit', 0),
        'delivered': status_counts.get('Delivered', 0),
        'delayed': status_counts.get('Delayed', 0),
        'cancelled': status_counts.get('Cancelled', 0),
        'available_vehicles': avail_vehicles,
        'total_vehicles': total_vehicles,
        'active_drivers': active_drivers,
        'total_drivers': total_drivers,
        'total_revenue': total_revenue,
        'estimated_fuel_cost': est_fuel_cost,
        'recent_shipments': recent_shipments,
        'recent_vehicles': recent_vehicles
    }

def generate_analytics_report(report_type="general", date_range="all"):
    conn = get_db_connection()
    cursor = conn.cursor()

    if report_type == "shipments":
        cursor.execute("SELECT shipment_id, customer_name, pickup_location, destination, goods_type, weight_kg, status, booking_date, shipping_cost FROM shipments ORDER BY booking_date DESC")
        data = rows_to_list(cursor.fetchall())
    elif report_type == "revenue":
        cursor.execute("SELECT invoice_id, shipment_id, customer_name, amount, tax_amount, total_amount, payment_status, invoice_date FROM payments ORDER BY invoice_date DESC")
        data = rows_to_list(cursor.fetchall())
    elif report_type == "fleet":
        cursor.execute("SELECT vehicle_id, registration_number, vehicle_type, make, capacity_mt, status, service_due_date FROM vehicles ORDER BY registration_number ASC")
        data = rows_to_list(cursor.fetchall())
    elif report_type == "drivers":
        cursor.execute("SELECT driver_id, name, phone, license_number, total_trips, completed_trips, rating, status FROM drivers ORDER BY rating DESC")
        data = rows_to_list(cursor.fetchall())
    else:
        cursor.execute("SELECT * FROM shipments ORDER BY created_at DESC")
        data = rows_to_list(cursor.fetchall())

    conn.close()
    return data
