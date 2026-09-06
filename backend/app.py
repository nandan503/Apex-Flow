import os
import sys

# Ensure root directory is in python path
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from flask import Flask, send_from_directory, redirect
from flask_cors import CORS

from backend.config import SECRET_KEY, HOST, PORT, BASE_DIR, DEBUG

from backend.database import initialize_database
from backend.routes import api_bp

# Set static_folder to 'frontend' directory
frontend_dir = os.path.join(BASE_DIR, 'frontend')

app = Flask(__name__, static_folder=frontend_dir, static_url_path='')
app.secret_key = SECRET_KEY
CORS(app)

# Register API blueprint
app.register_blueprint(api_bp)

# Auto-initialize database schema and seed data on startup if needed
try:
    initialize_database()
except Exception as e:
    print(f"[Database Init Warning] {e}")

# Frontend Static Page Routing
@app.route('/')
def index():
    return send_from_directory(frontend_dir, 'login.html')

@app.route('/<path:path>')
def serve_static(path):
    if os.path.exists(os.path.join(frontend_dir, path)):
        return send_from_directory(frontend_dir, path)
    elif os.path.exists(os.path.join(frontend_dir, f"{path}.html")):
        return send_from_directory(frontend_dir, f"{path}.html")
    return send_from_directory(frontend_dir, 'login.html')

if __name__ == '__main__':
    print("=" * 60)
    print("  🚀 APEX FLOW - Smart Transport & Logistics System")
    print("  Tagline: 'Smarter Logistics. Faster Tomorrow.'")
    print("=" * 60)
    print(f"  Server Running on host {HOST} port {PORT}")
    print("=" * 60)
    app.run(host=HOST, port=PORT, debug=DEBUG)
