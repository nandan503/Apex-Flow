from decimal import Decimal

from backend.database import db_session
from backend.models import rows_to_list
from backend.authz import Caller, deny, require_staff
from backend.services import SHIPMENT_COLUMNS, _shipment_scope_sql

# F-3: analytics reports are hard-capped server-side; the browser CSV export
# serializes this same bounded payload (docs/DATA_CONTRACTS.md).
REPORT_HARD_CAP = 2000


def get_dashboard_kpis(caller: Caller):
    scope, params = _shipment_scope_sql(caller)
    with db_session(caller) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT status, COUNT(*) as count FROM shipments WHERE 1=1{scope} GROUP BY status",
            params,
        )
        status_counts = {row['status']: row['count'] for row in cursor.fetchall()}

        cursor.execute(f"SELECT COUNT(*) FROM shipments WHERE 1=1{scope}", params)
        total_shipments = cursor.fetchone()[0]

        cursor.execute(
            f"SELECT COALESCE(SUM(shipping_cost), 0) FROM shipments WHERE 1=1{scope}",
            params,
        )
        total_revenue = cursor.fetchone()[0] or Decimal('0.00')

        cursor.execute(
            f"SELECT {SHIPMENT_COLUMNS} FROM shipments WHERE 1=1{scope} "
            "ORDER BY created_at DESC LIMIT 5",
            params,
        )
        recent_shipments = rows_to_list(cursor.fetchall())

        avail_vehicles = total_vehicles = active_drivers = total_drivers = 0
        est_fuel_cost = Decimal('0.00')
        recent_vehicles = []

        if caller.is_staff:
            cursor.execute("SELECT COUNT(*) FROM vehicles WHERE status = 'Available'")
            avail_vehicles = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM vehicles")
            total_vehicles = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM drivers WHERE status = 'Available' OR status = 'On Trip'"
            )
            active_drivers = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM drivers")
            total_drivers = cursor.fetchone()[0]
            cursor.execute("SELECT COALESCE(SUM(fuel_cost_est), 0) FROM routes")
            est_fuel_cost = cursor.fetchone()[0] or Decimal('0.00')
            cursor.execute(
                "SELECT vehicle_id, registration_number, vehicle_type, capacity_mt, status "
                "FROM vehicles ORDER BY created_at DESC LIMIT 5"
            )
            recent_vehicles = rows_to_list(cursor.fetchall())
        elif caller.is_driver and caller.driver_id:
            cursor.execute(
                "SELECT COUNT(*) FROM vehicles WHERE assigned_driver_id = %s",
                (caller.driver_id,),
            )
            total_vehicles = cursor.fetchone()[0]
            avail_vehicles = total_vehicles
            active_drivers = 1
            total_drivers = 1

        if caller.is_customer:
            # Customers must not see company-wide fuel or fleet figures.
            total_revenue = total_revenue  # own shipments only (already scoped)
            est_fuel_cost = Decimal('0.00')

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
        'recent_vehicles': recent_vehicles,
    }


def generate_analytics_report(caller: Caller, report_type="general", date_range="all"):
    require_staff(caller)
    allowed = {'shipments', 'revenue', 'fleet', 'drivers', 'general'}
    if report_type not in allowed:
        deny('Unknown report type')
    with db_session(caller) as conn:
        cursor = conn.cursor()
        if report_type == "shipments":
            cursor.execute(
                "SELECT shipment_id, customer_name, pickup_location, destination, goods_type, "
                "weight_kg, status, booking_date, shipping_cost FROM shipments ORDER BY booking_date DESC LIMIT %s"
            , (REPORT_HARD_CAP,))
        elif report_type == "revenue":
            cursor.execute(
                "SELECT invoice_id, shipment_id, customer_name, amount, tax_amount, "
                "total_amount, payment_status, invoice_date FROM payments ORDER BY invoice_date DESC LIMIT %s"
            , (REPORT_HARD_CAP,))
        elif report_type == "fleet":
            cursor.execute(
                "SELECT vehicle_id, registration_number, vehicle_type, make, capacity_mt, "
                "status, service_due_date FROM vehicles ORDER BY registration_number ASC LIMIT %s"
            , (REPORT_HARD_CAP,))
        elif report_type == "drivers":
            cursor.execute(
                "SELECT driver_id, name, phone, license_number, total_trips, "
                "completed_trips, rating, status FROM drivers ORDER BY rating DESC LIMIT %s"
            , (REPORT_HARD_CAP,))
        else:
            cursor.execute(
                f"SELECT {SHIPMENT_COLUMNS} FROM shipments ORDER BY created_at DESC LIMIT %s"
            , (REPORT_HARD_CAP,))
        return rows_to_list(cursor.fetchall())
