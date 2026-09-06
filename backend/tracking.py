import math
import time
from backend.database import get_db_connection
from backend.models import rows_to_list, row_to_dict

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

def get_live_tracking_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM shipments WHERE status IN ('In Transit', 'Picked Up', 'Out for Delivery')")
    shipments = rows_to_list(cursor.fetchall())
    conn.close()

    tracking_list = []
    current_time_factor = (int(time.time()) % 100) / 100.0  # Smooth simulation factor

    for s in shipments:
        pickup_name = s['pickup_location']
        dest_name = s['destination']
        
        start_coords = CITY_COORDINATES.get(pickup_name, (28.6139, 77.2090))
        end_coords = CITY_COORDINATES.get(dest_name, (26.9124, 75.7873))
        
        # Interpolate location based on time factor
        lat = start_coords[0] + (end_coords[0] - start_coords[0]) * current_time_factor
        lng = start_coords[1] + (end_coords[1] - start_coords[1]) * current_time_factor
        
        # Distance calculation approximation
        d_lat = end_coords[0] - start_coords[0]
        d_lng = end_coords[1] - start_coords[1]
        dist_total = math.sqrt(d_lat*d_lat + d_lng*d_lng) * 111.0 # rough km
        dist_remaining = round(dist_total * (1.0 - current_time_factor), 1)
        
        speed = 58 + int(math.sin(time.time()) * 12)  # 46 - 70 km/h simulation
        eta_minutes = int((dist_remaining / max(speed, 30)) * 60)
        hours = eta_minutes // 60
        mins = eta_minutes % 60
        eta_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} mins"

        tracking_list.append({
            'shipment_id': s['shipment_id'],
            'vehicle_id': s['vehicle_id'] or 'VEH001',
            'vehicle_reg': s['vehicle_reg'] or 'HR26BX4587',
            'driver_name': s['driver_name'] or 'Rajesh Kumar',
            'pickup': pickup_name,
            'destination': dest_name,
            'goods_type': s['goods_type'],
            'latitude': round(lat, 5),
            'longitude': round(lng, 5),
            'speed_kmh': speed,
            'distance_remaining_km': dist_remaining,
            'eta': eta_str,
            'progress_percent': int(current_time_factor * 100),
            'status': s['status']
        })

    # If no active shipments in transit, provide primary vehicle telemetry
    if not tracking_list:
        tracking_list.append({
            'shipment_id': 'SHP001',
            'vehicle_id': 'VEH001',
            'vehicle_reg': 'HR26BX4587',
            'driver_name': 'Rajesh Kumar',
            'pickup': 'Delhi',
            'destination': 'Jaipur',
            'goods_type': 'Electronics',
            'latitude': 27.8,
            'longitude': 76.5,
            'speed_kmh': 62,
            'distance_remaining_km': 142.0,
            'eta': '2h 15m',
            'progress_percent': 50,
            'status': 'In Transit'
        })

    return tracking_list

def get_vehicle_tracking(vehicle_id):
    all_tracking = get_live_tracking_data()
    for t in all_tracking:
        if t['vehicle_id'] == vehicle_id:
            return t
    return all_tracking[0] if all_tracking else None
