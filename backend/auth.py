from werkzeug.security import check_password_hash
from backend.database import get_db_connection
from backend.models import row_to_dict
from backend.utils import json_response, error_response

def authenticate_user(email, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user_row = cursor.fetchone()
    conn.close()

    if not user_row:
        return None, "Invalid email or password"

    user = row_to_dict(user_row)
    if not check_password_hash(user['password_hash'], password):
        return None, "Invalid email or password"

    # Remove password hash before returning
    user.pop('password_hash', None)
    return user, None

def get_user_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, name, email, role, phone, created_at FROM users WHERE user_id = ?", (user_id,))
    user = row_to_dict(cursor.fetchone())
    conn.close()
    return user
