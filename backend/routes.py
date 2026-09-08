from flask import Blueprint, request, session
from backend.auth import authenticate_user, get_user_by_id, login_required, require_role
from backend.utils import json_response, error_response
from backend.services import (
    get_all_shipments, get_shipment_by_id, create_shipment, update_shipment_status, delete_shipment,
    get_all_vehicles, create_vehicle, get_vehicle_by_id,
    get_all_drivers, create_driver, get_driver_by_id,
    get_all_customers, create_customer, get_customer_by_id,
    get_all_warehouses, get_all_deliveries, confirm_delivery,
    get_all_payments, get_all_notifications, mark_notification_read
)
from backend.tracking import get_live_tracking_data, get_vehicle_tracking
from backend.reports import get_dashboard_kpis, generate_analytics_report
from backend.database import get_db_connection
from backend.models import rows_to_list, row_to_dict
from backend.logger import log_delete, log_delivery_confirmed

api_bp = Blueprint('api', __name__, url_prefix='/api')

# Limiter is attached in app.py and passed here via the blueprint
# We use a lazy import so routes.py doesn't need to know the limiter instance at import time
_limiter = None

def set_limiter(limiter):
    global _limiter
    _limiter = limiter


# ── AUTHENTICATION ENDPOINTS ───────────────────────────────────────────────────

@api_bp.route('/auth/login', methods=['POST'])
def login():
    # Rate limiting applied via app.py limit_handler
    data = request.get_json() or {}
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    if not email or not password:
        return error_response('Email and password are required', 400)

    user, err = authenticate_user(email, password, ip=request.remote_addr)
    if err:
        return error_response(err, 401)

    session.clear()  # Prevent session fixation
    session['user_id'] = user['user_id']
    session['role'] = user['role']
    session.permanent = True

    return json_response(data=user, message='Login successful')


@api_bp.route('/auth/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return json_response(message='Logged out successfully')


@api_bp.route('/auth/me', methods=['GET'])
@login_required
def get_current_user():
    user_id = session.get('user_id')
    user = get_user_by_id(user_id)
    if not user:
        session.clear()
        return error_response('User not found', 404)
    return json_response(data=user)


# ── SHIPMENTS ENDPOINTS ────────────────────────────────────────────────────────

@api_bp.route('/shipments', methods=['GET'])
@login_required
def list_shipments():
    caller_role = session.get('role')
    caller_user_id = session.get('user_id')
    status = request.args.get('status')
    search = request.args.get('search')
    shipments = get_all_shipments(
        status_filter=status,
        search=search,
        caller_role=caller_role,
        caller_user_id=caller_user_id
    )
    return json_response(data=shipments)


@api_bp.route('/shipments/<shipment_id>', methods=['GET'])
@login_required
def get_shipment(shipment_id):
    shipment = get_shipment_by_id(shipment_id)
    if not shipment:
        return error_response('Shipment not found', 404)

    # IDOR: customers can only view their own shipments
    if session.get('role') == 'CUSTOMER':
        if shipment.get('customer_id') != session.get('user_id'):
            return error_response('Forbidden', 403)

    return json_response(data=shipment)


@api_bp.route('/shipments', methods=['POST'])
@login_required
def add_shipment():
    data = request.get_json() or {}
    pickup = data.get('pickup_location') or data.get('pickup')
    destination = data.get('destination')
    if not pickup or not destination:
        return error_response('Pickup location and destination are required', 400)

    # Pass caller context so service can enforce customer_id ownership
    shipment = create_shipment(data, caller_user_id=session.get('user_id'),
                               caller_role=session.get('role'))
    shipment_id = shipment['shipment_id']
    return json_response(data=shipment, shipment_id=shipment_id,
                         message="Shipment created successfully", status_code=201)


@api_bp.route('/shipments/<shipment_id>/status', methods=['PUT'])
@require_role('ADMIN', 'MANAGER', 'DRIVER')
def update_status(shipment_id):
    data = request.get_json() or {}
    new_status = data.get('status')
    location = data.get('location')
    notes = data.get('notes')

    # SECURITY FIX F-08: updated_by is ALWAYS from the server-side session, never the request body
    updated_by = session.get('role', 'System')

    if not new_status:
        return error_response('Status is required', 400)

    shipment = update_shipment_status(shipment_id, new_status, location=location,
                                      notes=notes, updated_by=updated_by)
    if not shipment:
        return error_response('Shipment not found', 404)

    return json_response(data=shipment, message=f"Shipment status updated to {new_status}")


@api_bp.route('/shipments/<shipment_id>', methods=['DELETE'])
@require_role('ADMIN')
def remove_shipment(shipment_id):
    log_delete(
        user_id=session.get('user_id', '?'),
        role=session.get('role', '?'),
        resource='shipment',
        resource_id=shipment_id,
        ip=request.remote_addr
    )
    success = delete_shipment(shipment_id)
    if not success:
        return error_response('Shipment not found', 404)
    return json_response(message=f"Shipment {shipment_id} deleted successfully")


# ── VEHICLES ENDPOINTS ─────────────────────────────────────────────────────────

@api_bp.route('/vehicles', methods=['GET'])
@login_required
def list_vehicles():
    vehicles = get_all_vehicles()
    return json_response(data=vehicles)


@api_bp.route('/vehicles', methods=['POST'])
@require_role('ADMIN', 'MANAGER')
def add_vehicle():
    data = request.get_json() or {}
    if not data.get('registration_number'):
        return error_response('Registration number is required', 400)

    vehicle = create_vehicle(data)
    return json_response(data=vehicle,
                         message=f"Vehicle {vehicle['registration_number']} added successfully",
                         status_code=201)


# ── DRIVERS ENDPOINTS ──────────────────────────────────────────────────────────

@api_bp.route('/drivers', methods=['GET'])
@login_required
def list_drivers():
    drivers = get_all_drivers()
    return json_response(data=drivers)


@api_bp.route('/drivers', methods=['POST'])
@require_role('ADMIN', 'MANAGER')
def add_driver():
    data = request.get_json() or {}
    if not data.get('name') or not data.get('license_number'):
        return error_response('Name and license number are required', 400)

    driver = create_driver(data)
    return json_response(data=driver,
                         message=f"Driver {driver['name']} added successfully",
                         status_code=201)


# ── CUSTOMERS ENDPOINTS ────────────────────────────────────────────────────────

@api_bp.route('/customers', methods=['GET'])
@require_role('ADMIN', 'MANAGER')
def list_customers():
    customers = get_all_customers()
    return json_response(data=customers)


@api_bp.route('/customers', methods=['POST'])
@require_role('ADMIN', 'MANAGER')
def add_customer():
    data = request.get_json() or {}
    if not data.get('name') or not data.get('company'):
        return error_response('Name and company are required', 400)

    cust = create_customer(data)
    return json_response(data=cust,
                         message=f"Customer {cust['company']} created successfully",
                         status_code=201)


# ── WAREHOUSES ENDPOINTS ───────────────────────────────────────────────────────

@api_bp.route('/warehouses', methods=['GET'])
@login_required
def list_warehouses():
    whs = get_all_warehouses()
    return json_response(data=whs)


# ── ROUTES & OPTIMIZATION ENDPOINTS ───────────────────────────────────────────

@api_bp.route('/routes', methods=['GET'])
@login_required
def list_routes():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM routes ORDER BY created_at DESC")
    routes = rows_to_list(cursor.fetchall())
    conn.close()
    return json_response(data=routes)


@api_bp.route('/routes/optimize', methods=['POST'])
@login_required
def optimize_route():
    data = request.get_json() or {}
    pickup = (data.get('pickup', 'Delhi') or '').strip()[:100]
    destination = (data.get('destination', 'Jaipur') or '').strip()[:100]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM routes WHERE LOWER(pickup) = LOWER(?) AND LOWER(destination) = LOWER(?)",
        (pickup, destination)
    )
    found = row_to_dict(cursor.fetchone())
    conn.close()

    if found:
        return json_response(data=found, message="Optimized route calculated!")

    dist = 250 + (len(pickup) + len(destination)) * 12
    time_hrs = round(dist / 65.0, 1)
    hrs = int(time_hrs)
    mins = int((time_hrs - hrs) * 60)
    fuel_cost = int(dist * 8.6)

    optimized_data = {
        'route_id': f"RTE_OPT_{int(dist)}",
        'pickup': pickup,
        'destination': destination,
        'distance_km': dist,
        'estimated_time': f"{hrs}h {mins}m",
        'fuel_cost_est': fuel_cost,
        'recommended_route': f"Via National Highway Corridor ({pickup}-{destination} Expressway)",
        'stops': [
            {'stop': f"{pickup} Exit Toll Plaza", 'eta': '30m'},
            {'stop': "Midway Logistics Rest Stop", 'eta': f"{hrs//2}h 15m"},
            {'stop': f"{destination} City Hub", 'eta': f"{hrs}h {mins}m"}
        ]
    }
    return json_response(data=optimized_data, message="Optimized route calculated!")


# ── LIVE TRACKING ENDPOINTS ────────────────────────────────────────────────────

@api_bp.route('/tracking', methods=['GET'])
@login_required
def list_tracking():
    data = get_live_tracking_data()
    return json_response(data=data)


@api_bp.route('/tracking/<vehicle_id>', methods=['GET'])
@login_required
def get_tracking_by_vehicle(vehicle_id):
    data = get_vehicle_tracking(vehicle_id)
    if not data:
        return error_response('Vehicle tracking data unavailable', 404)
    return json_response(data=data)


# ── DELIVERIES ENDPOINTS ───────────────────────────────────────────────────────

@api_bp.route('/deliveries', methods=['GET'])
@login_required
def list_deliveries():
    deliveries = get_all_deliveries()
    return json_response(data=deliveries)


@api_bp.route('/deliveries/confirm', methods=['POST'])
@login_required
def confirm_delivery_api():
    # Rate limiting applied via app.py limiter on this endpoint
    data = request.get_json() or {}
    shipment_id = data.get('shipment_id')
    otp_code = data.get('otp_code')
    receiver_name = data.get('receiver_name')

    if not shipment_id or not otp_code:
        return error_response('Shipment ID and OTP Code are required', 400)

    success, msg = confirm_delivery(shipment_id, otp_code,
                                    receiver_name=receiver_name,
                                    ip=request.remote_addr)
    if not success:
        return error_response(msg, 400)

    log_delivery_confirmed(shipment_id=shipment_id,
                           receiver_name=receiver_name,
                           ip=request.remote_addr)
    return json_response(message=msg)


# ── PAYMENTS ENDPOINTS ─────────────────────────────────────────────────────────

@api_bp.route('/payments', methods=['GET'])
@require_role('ADMIN', 'MANAGER')
def list_payments():
    payments = get_all_payments()
    return json_response(data=payments)


# ── REPORTS ENDPOINTS ──────────────────────────────────────────────────────────

@api_bp.route('/reports/dashboard', methods=['GET'])
@login_required
def dashboard_kpis():
    kpis = get_dashboard_kpis()
    return json_response(data=kpis)


@api_bp.route('/reports/<report_type>', methods=['GET'])
@require_role('ADMIN', 'MANAGER')
def get_report(report_type):
    # report_type is used only for if/elif branching in generate_analytics_report — no SQL injection risk
    data = generate_analytics_report(report_type=report_type)
    return json_response(data=data)


# ── NOTIFICATIONS ENDPOINTS ────────────────────────────────────────────────────

@api_bp.route('/notifications', methods=['GET'])
@login_required
def list_notifications():
    notifications = get_all_notifications()
    return json_response(data=notifications)


@api_bp.route('/notifications/<notif_id>/read', methods=['PUT'])
@login_required
def mark_read(notif_id):
    mark_notification_read(notif_id)
    return json_response(message="Notification marked as read")
