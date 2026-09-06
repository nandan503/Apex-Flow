from flask import Blueprint, request, session, jsonify
from backend.auth import authenticate_user, get_user_by_id
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

api_bp = Blueprint('api', __name__, url_prefix='/api')

# AUTHENTICATION ENDPOINTS
@api_bp.route('/auth/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    if not email or not password:
        return error_response('Email and password are required', 400)

    user, err = authenticate_user(email, password)
    if err:
        return error_response(err, 401)

    session['user_id'] = user['user_id']
    session['role'] = user['role']

    return json_response(data=user, message='Login successful')

@api_bp.route('/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return json_response(message='Logged out successfully')

@api_bp.route('/auth/me', methods=['GET'])
def get_current_user():
    user_id = session.get('user_id')
    if not user_id:
        return error_response('Unauthenticated', 401)
    user = get_user_by_id(user_id)
    if not user:
        return error_response('User not found', 444)
    return json_response(data=user)

# SHIPMENTS ENDPOINTS
@api_bp.route('/shipments', methods=['GET'])
def list_shipments():
    status = request.args.get('status')
    search = request.args.get('search')
    shipments = get_all_shipments(status_filter=status, search=search)
    return json_response(data=shipments)

@api_bp.route('/shipments/<shipment_id>', methods=['GET'])
def get_shipment(shipment_id):
    shipment = get_shipment_by_id(shipment_id)
    if not shipment:
        return error_response('Shipment not found', 404)
    return json_response(data=shipment)

@api_bp.route('/shipments', methods=['POST'])
def add_shipment():
    data = request.get_json() or {}
    pickup = data.get('pickup_location') or data.get('pickup')
    destination = data.get('destination')
    if not pickup or not destination:
        return error_response('Pickup location and destination are required', 400)

    shipment = create_shipment(data)
    shipment_id = shipment['shipment_id']
    return json_response(data=shipment, shipment_id=shipment_id, message="Shipment created successfully", status_code=201)


@api_bp.route('/shipments/<shipment_id>/status', methods=['PUT'])
def update_status(shipment_id):
    data = request.get_json() or {}
    new_status = data.get('status')
    location = data.get('location')
    notes = data.get('notes')
    updated_by = data.get('updated_by', session.get('role', 'Admin'))

    if not new_status:
        return error_response('Status is required', 400)

    shipment = update_shipment_status(shipment_id, new_status, location=location, notes=notes, updated_by=updated_by)
    if not shipment:
        return error_response('Shipment not found', 404)

    return json_response(data=shipment, message=f"Shipment status updated to {new_status}")

@api_bp.route('/shipments/<shipment_id>', methods=['DELETE'])
def remove_shipment(shipment_id):
    success = delete_shipment(shipment_id)
    if not success:
        return error_response('Shipment not found', 404)
    return json_response(message=f"Shipment {shipment_id} deleted successfully")

# VEHICLES ENDPOINTS
@api_bp.route('/vehicles', methods=['GET'])
def list_vehicles():
    vehicles = get_all_vehicles()
    return json_response(data=vehicles)

@api_bp.route('/vehicles', methods=['POST'])
def add_vehicle():
    data = request.get_json() or {}
    if not data.get('registration_number'):
        return error_response('Registration number is required', 400)

    vehicle = create_vehicle(data)
    return json_response(data=vehicle, message=f"Vehicle {vehicle['registration_number']} added successfully", status_code=201)

# DRIVERS ENDPOINTS
@api_bp.route('/drivers', methods=['GET'])
def list_drivers():
    drivers = get_all_drivers()
    return json_response(data=drivers)

@api_bp.route('/drivers', methods=['POST'])
def add_driver():
    data = request.get_json() or {}
    if not data.get('name') or not data.get('license_number'):
        return error_response('Name and license number are required', 400)

    driver = create_driver(data)
    return json_response(data=driver, message=f"Driver {driver['name']} added successfully", status_code=201)

# CUSTOMERS ENDPOINTS
@api_bp.route('/customers', methods=['GET'])
def list_customers():
    customers = get_all_customers()
    return json_response(data=customers)

@api_bp.route('/customers', methods=['POST'])
def add_customer():
    data = request.get_json() or {}
    if not data.get('name') or not data.get('company'):
        return error_response('Name and company are required', 400)

    cust = create_customer(data)
    return json_response(data=cust, message=f"Customer {cust['company']} created successfully", status_code=201)

# WAREHOUSES ENDPOINTS
@api_bp.route('/warehouses', methods=['GET'])
def list_warehouses():
    whs = get_all_warehouses()
    return json_response(data=whs)

# ROUTES & OPTIMIZATION ENDPOINTS
@api_bp.route('/routes', methods=['GET'])
def list_routes():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM routes ORDER BY created_at DESC")
    routes = rows_to_list(cursor.fetchall())
    conn.close()
    return json_response(data=routes)

@api_bp.route('/routes/optimize', methods=['POST'])
def optimize_route():
    data = request.get_json() or {}
    pickup = data.get('pickup', 'Delhi').strip()
    destination = data.get('destination', 'Jaipur').strip()

    # Route optimization logic
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM routes WHERE LOWER(pickup) = LOWER(?) AND LOWER(destination) = LOWER(?)", (pickup, destination))
    found = row_to_dict(cursor.fetchone())
    conn.close()

    if found:
        return json_response(data=found, message="Optimized route calculated!")

    # Calculate dynamic estimation if route is new
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
            {'stop': f"Midway Logistics Rest Stop", 'eta': f"{hrs//2}h 15m"},
            {'stop': f"{destination} City Hub", 'eta': f"{hrs}h {mins}m"}
        ]
    }
    return json_response(data=optimized_data, message="Optimized route calculated!")

# LIVE TRACKING ENDPOINTS
@api_bp.route('/tracking', methods=['GET'])
def list_tracking():
    data = get_live_tracking_data()
    return json_response(data=data)

@api_bp.route('/tracking/<vehicle_id>', methods=['GET'])
def get_tracking_by_vehicle(vehicle_id):
    data = get_vehicle_tracking(vehicle_id)
    if not data:
        return error_response('Vehicle tracking data unavailable', 404)
    return json_response(data=data)

# DELIVERIES ENDPOINTS
@api_bp.route('/deliveries', methods=['GET'])
def list_deliveries():
    deliveries = get_all_deliveries()
    return json_response(data=deliveries)

@api_bp.route('/deliveries/confirm', methods=['POST'])
def confirm_delivery_api():
    data = request.get_json() or {}
    shipment_id = data.get('shipment_id')
    otp_code = data.get('otp_code')
    receiver_name = data.get('receiver_name')

    if not shipment_id or not otp_code:
        return error_response('Shipment ID and OTP Code are required', 400)

    success, msg = confirm_delivery(shipment_id, otp_code, receiver_name=receiver_name)
    if not success:
        return error_response(msg, 400)

    return json_response(message=msg)

# PAYMENTS ENDPOINTS
@api_bp.route('/payments', methods=['GET'])
def list_payments():
    payments = get_all_payments()
    return json_response(data=payments)

# REPORTS ENDPOINTS
@api_bp.route('/reports/dashboard', methods=['GET'])
def dashboard_kpis():
    kpis = get_dashboard_kpis()
    return json_response(data=kpis)

@api_bp.route('/reports/<report_type>', methods=['GET'])
def get_report(report_type):
    data = generate_analytics_report(report_type=report_type)
    return json_response(data=data)

# NOTIFICATIONS ENDPOINTS
@api_bp.route('/notifications', methods=['GET'])
def list_notifications():
    notifications = get_all_notifications()
    return json_response(data=notifications)

@api_bp.route('/notifications/<notif_id>/read', methods=['PUT'])
def mark_read(notif_id):
    mark_notification_read(notif_id)
    return json_response(message="Notification marked as read")
