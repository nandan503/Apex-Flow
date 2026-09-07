"""Central authorization policy. Enforce in the service layer, not only on routes."""

from dataclasses import dataclass
from typing import Optional

from backend.errors import AuthzError


STAFF_ROLES = frozenset({'ADMIN', 'MANAGER'})
ALL_ROLES = frozenset({'ADMIN', 'MANAGER', 'DRIVER', 'CUSTOMER'})

SHIPMENT_STATUSES = frozenset({
    'Booked', 'Confirmed', 'Picked Up', 'At Warehouse',
    'In Transit', 'Out for Delivery', 'Delivered', 'Delayed', 'Cancelled', 'Returned',
})

# Delivered is intentionally absent — custody transfer is OTP-only.
STATUS_TRANSITIONS = {
    'Booked': frozenset({'Confirmed', 'Cancelled'}),
    'Confirmed': frozenset({'Picked Up', 'Cancelled'}),
    'Picked Up': frozenset({'At Warehouse', 'In Transit', 'Delayed', 'Cancelled'}),
    'At Warehouse': frozenset({'In Transit', 'Delayed', 'Cancelled'}),
    'In Transit': frozenset({'Out for Delivery', 'At Warehouse', 'Delayed'}),
    'Out for Delivery': frozenset({'Delayed', 'Returned'}),
    'Delayed': frozenset({'In Transit', 'Out for Delivery', 'Returned', 'Cancelled'}),
    'Delivered': frozenset(),
    'Cancelled': frozenset(),
    'Returned': frozenset(),
}

DRIVER_ALLOWED_STATUSES = frozenset({
    'Picked Up', 'At Warehouse', 'In Transit', 'Out for Delivery', 'Delayed',
})


@dataclass(frozen=True)
class Caller:
    user_id: str
    role: str
    name: str
    email: str
    customer_id: Optional[str] = None
    driver_id: Optional[str] = None

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def is_admin(self) -> bool:
        return self.role == 'ADMIN'

    @property
    def is_driver(self) -> bool:
        return self.role == 'DRIVER'

    @property
    def is_customer(self) -> bool:
        return self.role == 'CUSTOMER'


def deny(message='Forbidden'):
    raise AuthzError(message, 403)


def require_staff(caller: Caller):
    if not caller.is_staff:
        deny('Forbidden — staff only')


def require_admin(caller: Caller):
    if not caller.is_admin:
        deny('Forbidden — admin only')


def shipment_visible(caller: Caller, shipment: dict) -> bool:
    if not shipment:
        return False
    if caller.is_staff:
        return True
    if caller.is_customer:
        return bool(caller.customer_id) and shipment.get('customer_id') == caller.customer_id
    if caller.is_driver:
        return bool(caller.driver_id) and shipment.get('driver_id') == caller.driver_id
    return False


def assert_shipment_visible(caller: Caller, shipment: dict):
    if not shipment_visible(caller, shipment):
        # 404 rather than 403 to avoid IDOR existence leaks for foreign IDs
        raise AuthzError('Shipment not found', 404)


def can_create_shipment(caller: Caller) -> bool:
    return caller.role in ('ADMIN', 'MANAGER', 'CUSTOMER')


def can_assign_shipment(caller: Caller) -> bool:
    return caller.is_staff


def allowed_status_targets(caller: Caller, current: str):
    nxt = set(STATUS_TRANSITIONS.get(current, frozenset()))
    if caller.is_driver:
        nxt &= DRIVER_ALLOWED_STATUSES
    return nxt


def assert_status_transition(caller: Caller, current: str, new_status: str, via_otp=False):
    if via_otp:
        if new_status != 'Delivered':
            deny('OTP confirmation can only mark a shipment Delivered')
        if current == 'Delivered':
            deny('Shipment already delivered')
        return
    if new_status == 'Delivered':
        deny('Deliveries must be confirmed with the receiver OTP')
    if new_status not in SHIPMENT_STATUSES:
        raise AuthzError('Invalid status', 400)
    allowed = allowed_status_targets(caller, current)
    if new_status not in allowed:
        deny(f'Cannot transition from {current} to {new_status}')


def delivery_confirmable(caller: Caller, delivery: dict) -> bool:
    if not delivery:
        return False
    if caller.is_staff:
        return True
    if caller.is_driver:
        return bool(caller.driver_id) and delivery.get('driver_id') == caller.driver_id
    return False


def vehicle_visible(caller: Caller, vehicle: dict) -> bool:
    if caller.is_staff:
        return True
    if caller.is_driver:
        return bool(caller.driver_id) and vehicle.get('assigned_driver_id') == caller.driver_id
    return False


def driver_visible(caller: Caller, driver: dict) -> bool:
    if caller.is_staff:
        return True
    if caller.is_driver:
        return driver.get('driver_id') == caller.driver_id
    return False
