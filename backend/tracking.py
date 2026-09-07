import math
import time

from backend.database import db_session
from backend.models import rows_to_list
from backend.authz import Caller, shipment_visible
from backend.services import SHIPMENT_COLUMNS

CITY_COORDINATES = {
    'Delhi': (28.6139, 77.2090),
    'Jaipur': (26.9124, 75.7873),
    'Ludhiana': (30.9010, 75.8573),
    'Chandigarh': (30.7333, 76.7794),
    'Mumbai': (19.0760, 72.8777),
    'Kanpur': (26.4499, 80.3319),
    'Patna': (25.5941, 85.1376),
    'Amritsar': (31.6340, 74.8723),
    'Bangalore': (12.9716, 77.5946),
    'Ahmedabad': (23.0225, 72.5714),
    'Lucknow': (26.8467, 80.9462)
}


def _tracking_payload(s):
    pickup_name = s['pickup_location']
    dest_name = s['destination']
    start_coords = CITY_COORDINATES.get(pickup_name, (28.6139, 77.2090))
    end_coords = CITY_COORDINATES.get(dest_name, (26.9124, 75.7873))
    current_time_factor = (int(time.time()) % 100) / 100.0
    lat = start_coords[0] + (end_coords[0] - start_coords[0]) * current_time_factor
    lng = start_coords[1] + (end_coords[1] - start_coords[1]) * current_time_factor
    d_lat = end_coords[0] - start_coords[0]
    d_lng = end_coords[1] - start_coords[1]
    dist_total = math.sqrt(d_lat * d_lat + d_lng * d_lng) * 111.0
    dist_remaining = round(dist_total * (1.0 - current_time_factor), 1)
    speed = 58 + int(math.sin(time.time()) * 12)
    eta_minutes = int((dist_remaining / max(speed, 30)) * 60)
    hours = eta_minutes // 60
    mins = eta_minutes % 60
    eta_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} mins"
    return {
        'shipment_id': s['shipment_id'],
        'vehicle_id': s['vehicle_id'],
        'vehicle_reg': s['vehicle_reg'],
        'driver_name': s['driver_name'],
        'pickup': pickup_name,
        'destination': dest_name,
        'goods_type': s['goods_type'],
        'latitude': round(lat, 5),
        'longitude': round(lng, 5),
        'speed_kmh': speed,
        'distance_remaining_km': dist_remaining,
        'eta': eta_str,
        'progress_percent': int(current_time_factor * 100),
        'status': s['status'],
    }


def get_live_tracking_data(caller: Caller):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {SHIPMENT_COLUMNS} FROM shipments "
            "WHERE status IN ('In Transit', 'Picked Up', 'Out for Delivery')"
        )
        shipments = rows_to_list(cursor.fetchall())
    visible = [s for s in shipments if shipment_visible(caller, s)]
    return [_tracking_payload(s) for s in visible]


def get_vehicle_tracking(vehicle_id, caller: Caller):
    all_tracking = get_live_tracking_data(caller)
    for t in all_tracking:
        if t.get('vehicle_id') == vehicle_id:
            return t
    return None
