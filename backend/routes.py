from flask import Blueprint, request, session, g

from backend.auth import authenticate_user, get_user_by_id, login_required, require_role
from backend.utils import json_response, error_response
from backend.limiter import limiter
from backend.errors import AuthzError, ValidationError, AppError
from backend.services import (
    get_all_shipments, get_shipment_by_id, create_shipment, update_shipment_status,
    delete_shipment, assign_shipment,
    get_all_vehicles, create_vehicle,
    get_all_drivers, create_driver,
    get_all_customers, create_customer,
    get_all_warehouses, get_all_deliveries, confirm_delivery,
    get_all_payments, get_all_notifications, mark_notification_read,
    get_all_routes, optimize_route, record_payment,
)
from backend.tracking import get_live_tracking_data, get_vehicle_tracking
from backend.reports import get_dashboard_kpis, generate_analytics_report
from backend.logger import log_delete, log_delivery_confirmed
from backend.validation import bound_text

api_bp = Blueprint('api', __name__, url_prefix='/api')


def _caller():
    return g.caller


# ── AUTHENTICATION ─────────────────────────────────────────────────────────────

@api_bp.route('/auth/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    data = request.get_json(silent=True) or {}
    email = bound_text(data.get('email'), 'email', max_len=120, required=True)
    password = data.get('password') or ''
    if not str(password):
        return error_response('Email and password are required', 400)
    # Do not strip interior password characters; only reject empty.
    if isinstance(password, str):
        password = password.strip('\n\r')

    user, err = authenticate_user(email, password, ip=request.remote_addr)
    if err:
        return error_response(err, 401)

    session.clear()
    session['user_id'] = user['user_id']
    session['role'] = user['role']
    session.permanent = True
    return json_response(data=user, message='Login successful')


@api_bp.route('/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return json_response(message='Logged out successfully')


@api_bp.route('/auth/me', methods=['GET'])
@login_required
def get_current_user():
    user = get_user_by_id(_caller().user_id)
    if not user:
        session.clear()
        return error_response('User not found', 404)
    return json_response(data=user)


# ── SHIPMENTS ──────────────────────────────────────────────────────────────────

@api_bp.route('/shipments', methods=['GET'])
@login_required
def list_shipments():
    status = request.args.get('status')
    search = request.args.get('search')
    shipments = get_all_shipments(
        status_filter=status, search=search, caller=_caller()
    )
    return json_response(data=shipments)


@api_bp.route('/shipments/<shipment_id>', methods=['GET'])
@login_required
def get_shipment(shipment_id):
    shipment = get_shipment_by_id(shipment_id, caller=_caller())
    if not shipment:
        return error_response('Shipment not found', 404)
    return json_response(data=shipment)


@api_bp.route('/shipments', methods=['POST'])
@login_required
def add_shipment():
    data = request.get_json(silent=True) or {}
    shipment = create_shipment(data, caller=_caller())
    return json_response(
        data=shipment, shipment_id=shipment['shipment_id'],
        message="Shipment created successfully", status_code=201,
    )


@api_bp.route('/shipments/<shipment_id>/status', methods=['PUT'])
@require_role('ADMIN', 'MANAGER', 'DRIVER')
def update_status(shipment_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get('status')
    if not new_status:
        return error_response('Status is required', 400)
    shipment = update_shipment_status(
        shipment_id, new_status,
        location=data.get('location'),
        notes=data.get('notes'),
        caller=_caller(),
    )
    if not shipment:
        return error_response('Shipment not found', 404)
    return json_response(data=shipment, message=f"Shipment status updated to {new_status}")


@api_bp.route('/shipments/<shipment_id>/assign', methods=['PUT'])
@require_role('ADMIN', 'MANAGER')
def assign_shipment_api(shipment_id):
    data = request.get_json(silent=True) or {}
    shipment = assign_shipment(
        shipment_id,
        driver_id=data.get('driver_id'),
        vehicle_id=data.get('vehicle_id'),
        caller=_caller(),
    )
    if not shipment:
        return error_response('Shipment not found', 404)
    return json_response(data=shipment, message='Shipment assigned')


@api_bp.route('/shipments/<shipment_id>', methods=['DELETE'])
@require_role('ADMIN')
def remove_shipment(shipment_id):
    log_delete(
        user_id=_caller().user_id,
        role=_caller().role,
        resource='shipment',
        resource_id=shipment_id,
        ip=request.remote_addr
    )
    success = delete_shipment(shipment_id, caller=_caller())
    if not success:
        return error_response('Shipment not found', 404)
    return json_response(message=f"Shipment {shipment_id} deleted successfully")


# ── VEHICLES ───────────────────────────────────────────────────────────────────

@api_bp.route('/vehicles', methods=['GET'])
@require_role('ADMIN', 'MANAGER', 'DRIVER')
def list_vehicles():
    return json_response(data=get_all_vehicles(_caller()))


@api_bp.route('/vehicles', methods=['POST'])
@require_role('ADMIN', 'MANAGER')
def add_vehicle():
    data = request.get_json(silent=True) or {}
    if not data.get('registration_number'):
        return error_response('Registration number is required', 400)
    vehicle = create_vehicle(data, caller=_caller())
    return json_response(
        data=vehicle,
        message=f"Vehicle {vehicle['registration_number']} added successfully",
        status_code=201,
    )


# ── DRIVERS ────────────────────────────────────────────────────────────────────

@api_bp.route('/drivers', methods=['GET'])
@require_role('ADMIN', 'MANAGER', 'DRIVER')
def list_drivers():
    return json_response(data=get_all_drivers(_caller()))


@api_bp.route('/drivers', methods=['POST'])
@require_role('ADMIN', 'MANAGER')
def add_driver():
    data = request.get_json(silent=True) or {}
    if not data.get('name') or not data.get('license_number'):
        return error_response('Name and license number are required', 400)
    driver = create_driver(data, caller=_caller())
    return json_response(
        data=driver,
        message=f"Driver {driver['name']} added successfully",
        status_code=201,
    )


# ── CUSTOMERS ──────────────────────────────────────────────────────────────────

@api_bp.route('/customers', methods=['GET'])
@require_role('ADMIN', 'MANAGER')
def list_customers():
    return json_response(data=get_all_customers(_caller()))


@api_bp.route('/customers', methods=['POST'])
@require_role('ADMIN', 'MANAGER')
def add_customer():
    data = request.get_json(silent=True) or {}
    if not data.get('name') or not data.get('company'):
        return error_response('Name and company are required', 400)
    cust = create_customer(data, caller=_caller())
    return json_response(
        data=cust,
        message=f"Customer {cust['company']} created successfully",
        status_code=201,
    )


# ── WAREHOUSES / ROUTES ────────────────────────────────────────────────────────

@api_bp.route('/warehouses', methods=['GET'])
@require_role('ADMIN', 'MANAGER')
def list_warehouses():
    return json_response(data=get_all_warehouses(_caller()))


@api_bp.route('/routes', methods=['GET'])
@require_role('ADMIN', 'MANAGER', 'DRIVER')
def list_routes():
    return json_response(data=get_all_routes(_caller()))


@api_bp.route('/routes/optimize', methods=['POST'])
@require_role('ADMIN', 'MANAGER', 'DRIVER')
def optimize_route_api():
    data = request.get_json(silent=True) or {}
    result = optimize_route(data.get('pickup'), data.get('destination'), caller=_caller())
    return json_response(data=result, message="Optimized route calculated!")


# ── TRACKING ───────────────────────────────────────────────────────────────────

@api_bp.route('/tracking', methods=['GET'])
@login_required
def list_tracking():
    return json_response(data=get_live_tracking_data(_caller()))


@api_bp.route('/tracking/<vehicle_id>', methods=['GET'])
@login_required
def get_tracking_by_vehicle(vehicle_id):
    data = get_vehicle_tracking(vehicle_id, caller=_caller())
    if not data:
        return error_response('Vehicle tracking data unavailable', 404)
    return json_response(data=data)


# ── DELIVERIES ─────────────────────────────────────────────────────────────────

@api_bp.route('/deliveries', methods=['GET'])
@login_required
def list_deliveries():
    return json_response(data=get_all_deliveries(_caller()))


@api_bp.route('/deliveries/confirm', methods=['POST'])
@limiter.limit("5 per minute")
@require_role('DRIVER', 'ADMIN', 'MANAGER')
def confirm_delivery_api():
    data = request.get_json(silent=True) or {}
    shipment_id = data.get('shipment_id')
    otp_code = data.get('otp_code')
    receiver_name = data.get('receiver_name')
    if not shipment_id or not otp_code:
        return error_response('Shipment ID and OTP Code are required', 400)
    success, msg = confirm_delivery(
        shipment_id, otp_code,
        caller=_caller(),
        receiver_name=receiver_name,
        ip=request.remote_addr,
    )
    if not success:
        return error_response(msg, 400)
    log_delivery_confirmed(
        shipment_id=shipment_id, receiver_name=receiver_name, ip=request.remote_addr
    )
    return json_response(message=msg)


# ── PAYMENTS / REPORTS / NOTIFICATIONS ─────────────────────────────────────────

@api_bp.route('/payments', methods=['GET'])
@require_role('ADMIN', 'MANAGER', 'CUSTOMER')
def list_payments():
    return json_response(data=get_all_payments(_caller()))


@api_bp.route('/payments/<invoice_id>/collect', methods=['POST'])
@require_role('ADMIN', 'MANAGER')
def collect_payment(invoice_id):
    ok = record_payment(invoice_id, caller=_caller())
    if not ok:
        return error_response('Invoice not found', 404)
    return json_response(message='Payment recorded')


@api_bp.route('/reports/dashboard', methods=['GET'])
@login_required
def dashboard_kpis():
    return json_response(data=get_dashboard_kpis(_caller()))


@api_bp.route('/reports/<report_type>', methods=['GET'])
@require_role('ADMIN', 'MANAGER')
def get_report(report_type):
    data = generate_analytics_report(caller=_caller(), report_type=report_type)
    return json_response(data=data)


@api_bp.route('/notifications', methods=['GET'])
@login_required
def list_notifications():
    return json_response(data=get_all_notifications(_caller()))


@api_bp.route('/notifications/<notif_id>/read', methods=['PUT'])
@login_required
def mark_read(notif_id):
    mark_notification_read(notif_id, caller=_caller())
    return json_response(message="Notification marked as read")


@api_bp.errorhandler(AuthzError)
@api_bp.errorhandler(ValidationError)
@api_bp.errorhandler(AppError)
def _handle_app_error(err):
    return error_response(err.message, err.status, code=getattr(err, 'code', None))
