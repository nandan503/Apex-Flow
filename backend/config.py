import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATABASE_PATH = os.path.join(DATA_DIR, 'apexflow.db')

# Environment & Cloud Configuration
SECRET_KEY = os.environ.get('SECRET_KEY', 'apexflow_super_secret_logistics_key_2026')
DATABASE_URL = os.environ.get('DATABASE_URL', '')
FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
DEBUG = FLASK_ENV != 'production'
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', 5050))

os.makedirs(DATA_DIR, exist_ok=True)

