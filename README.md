# 🚀 APEX FLOW - Smart Transport & Logistics System
> **Tagline:** *"Smarter Logistics. Faster Tomorrow."*

APEX FLOW is an enterprise-grade, cross-device accessible transport and fleet management web application. Designed for seamless operation on **Android phones, iPhones, iPads, Tablets, Windows PCs, MacBooks, and Linux PCs**, APEX FLOW adapts dynamically to any screen resolution without depending on a personal computer's `localhost`.

---

## 🌟 Key Features & Cross-Device Compatibility

- **Universal Accessibility**: Accessible via standard web browsers across mobile phones, tablets, laptops, and desktop computers.
- **Mobile Responsive UI**:
  - Auto-collapsing slide-out drawer navigation (Hamburger menu) for touch screens (`< 850px`).
  - Adaptive 1 to 4-column dashboard KPI cards grid.
  - Horizontally scrollable data tables (`.table-responsive`) with zero page overflow.
  - 44px+ touch-friendly tap targets for buttons, inputs, and modals.
- **Production Dual Database Engine**:
  - Zero-config **SQLite** for instant local development.
  - Cloud-ready **PostgreSQL** compatibility for online multi-device synchronization.
- **RESTful API Backend**: Built with Python Flask, providing structured JSON REST endpoints for auth, shipments, vehicles, drivers, tracking, routes, reports, and notifications.

---

## 🛠️ Tech Stack

- **Frontend**: HTML5, Vanilla CSS3 (CSS Variables, Flexbox, Grid, Media Queries), Vanilla JavaScript (ES6+ fetch API).
- **Backend**: Python 3.9+ Flask, Gunicorn WSGI Server, Flask-CORS, Werkzeug.
- **Database Engine**: Dual SQLite / PostgreSQL abstraction layer (`backend/database.py`).

---

## 💻 1. Local Installation

Follow these steps to set up and run APEX FLOW on your local computer:

```bash
# 1. Clone the repository or navigate to project folder
cd /path/to/HImanshu

# 2. Create a virtual environment
python3 -m venv venv

# 3. Activate the virtual environment
# On macOS / Linux:
source venv/bin/activate
# On Windows (Command Prompt):
# venv\Scripts\activate.bat
# On Windows (PowerShell):
# .\venv\Scripts\Activate.ps1

# 4. Install production dependencies
pip install -r requirements.txt
```

---

## 🧪 2. Local Testing

To run the application locally for testing:

```bash
python backend/app.py
```

Output:
```
============================================================
  🚀 APEX FLOW - Smart Transport & Logistics System
  Tagline: 'Smarter Logistics. Faster Tomorrow.'
============================================================
  Server Running on host 0.0.0.0 port 5050
============================================================
```

Open your browser and navigate to: `http://localhost:5050` or `http://127.0.0.1:5050`.

### 🔑 Local development credentials (never shipped in the login UI)

These exist only when `FLASK_ENV` is not `production` **and** the corresponding `SEED_*_PASSWORD` env vars are unset. They are rejected at boot in production. Do not use them on a public URL.

| Role | Email | Password (dev default) |
| :--- | :--- | :--- |
| **System Admin** | `admin@apexflow.com` | `Admin@123` |
| **Logistics Manager** | `manager@apexflow.com` | `Manager@123` |
| **Driver** | `driver@apexflow.com` | `Driver@123` |
| **Customer** | `customer@apexflow.com` | `Customer@123` |

---

## ⚙️ 3. Environment Variables Configuration

APEX FLOW utilizes environment variables for production security and cloud deployment settings.

Create a `.env` file in the root directory (refer to `.env.example`):

```env
# Server Binding
PORT=5050
HOST=0.0.0.0
FLASK_ENV=production
SECRET_KEY=your_super_secret_production_key_here

# Database URL
# SQLite Local (Default when empty):
DATABASE_URL=
# PostgreSQL Production (e.g. Render / Railway / Supabase / Neon / ElephantSQL):
# DATABASE_URL=postgresql://username:password@ep-cloud-db.render.com:5432/apexflow_db
```

---

## 🗄️ 4. Database Configuration (SQLite vs PostgreSQL)

APEX FLOW features an automatic dual-database engine in [backend/database.py](file:///Users/nandankumar/HImanshu/backend/database.py):

- **Local SQLite**: Used automatically if `DATABASE_URL` is omitted. Stored in `data/apexflow.db`.
- **Cloud PostgreSQL**: When deploying to production platforms (Render, Railway, Supabase, Neon, AWS RDS), set `DATABASE_URL` to your PostgreSQL URI. The application will automatically connect to PostgreSQL and create all required tables (`users`, `customers`, `vehicles`, `drivers`, `shipments`, `shipment_status_history`, `routes`, `warehouses`, `deliveries`, `payments`, `notifications`).

Because the database resides on the cloud server, any record created on one device (e.g. creating shipment `SHP006` from an Android/iPhone) is immediately visible when opening APEX FLOW on any other device (PC, Laptop, Tablet).

---

## 🌐 5. Production Deployment & Starting WSGI Server

APEX FLOW includes a production-grade `Procfile` and `requirements.txt` ready for instant deployment to cloud platforms such as **Render**, **Railway**, **Fly.io**, **Koyeb**, **Heroku**, or a **VPS / Docker container**.

### Option A: Deploying on Render (Free Public HTTPS URL)

1. Push your repository to **GitHub / GitLab**.
2. Log into [Render Dashboard](https://dashboard.render.com/) and click **New +** → **Web Service**.
3. Connect your GitHub repository.
4. Configure service settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn backend.app:app`
5. Add Environment Variables under **Environment**:
   - `FLASK_ENV` = `production`
   - `SECRET_KEY` = *(your generated secret)*
   - `DATABASE_URL` = *(Optional: PostgreSQL URL provided by Render Postgres or Supabase)*
6. Click **Create Web Service**.
7. Render will build and launch your application, providing a public HTTPS URL (e.g., `https://apex-flow.onrender.com`).

### Option B: Deploying on Railway

1. Go to [Railway.app](https://railway.app) and create a **New Project**.
2. Select **Deploy from GitHub repo**.
3. Railway automatically detects `requirements.txt` and `Procfile`.
4. Add environment variables (`PORT`, `SECRET_KEY`, `DATABASE_URL`).
5. Generate a public domain under **Settings** → **Networking** (e.g., `https://apex-flow.up.railway.app`).

### Option C: Manual Production Launch via Gunicorn (Linux / VPS)

To start the Flask production server manually on a VPS (e.g., DigitalOcean, AWS EC2, Linode):

```bash
gunicorn --bind 0.0.0.0:5050 backend.app:app --workers 4
```

---

## 🔌 6. REST API Endpoints

APEX FLOW exposes full RESTful JSON APIs:

| Method | Endpoint | Description | Sample Payload |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/auth/login` | User login session | `{"email": "admin@apexflow.com", "password": "..."}` |
| `POST` | `/api/auth/logout` | Clear session | N/A |
| `GET` | `/api/shipments` | List all shipments | N/A |
| `POST` | `/api/shipments` | Create new shipment | `{"customer": "ABC Ind", "pickup": "Ludhiana", "destination": "Delhi", "weight": 8000}` |
| `GET` | `/api/vehicles` | Fleet vehicles list | N/A |
| `POST` | `/api/vehicles` | Add new vehicle | `{"registration_number": "HR26BX4587", ...}` |
| `GET` | `/api/drivers` | Drivers roster | N/A |
| `POST` | `/api/drivers` | Add new driver | `{"name": "Rajesh Kumar", "license_number": "..."}` |
| `GET` | `/api/customers` | Customer directory | N/A |
| `GET` | `/api/routes` | Routes list | N/A |
| `POST` | `/api/routes/optimize` | Calculate route ETA & fuel | `{"pickup": "Delhi", "destination": "Jaipur"}` |
| `GET` | `/api/tracking` | Live GPS tracking data | N/A |
| `GET` | `/api/reports/dashboard` | Dashboard KPIs summary | N/A |
| `GET` | `/api/notifications` | User notifications | N/A |

### Example API Request & Response (`POST /api/shipments`):

**Request**:
```json
POST /api/shipments
Content-Type: application/json

{
  "customer": "ABC Industries",
  "pickup": "Ludhiana",
  "destination": "Delhi",
  "goods_type": "Electronics",
  "weight": 8000
}
```

**Response**:
```json
{
  "success": true,
  "shipment_id": "SHP006",
  "message": "Shipment created successfully",
  "data": {
    "shipment_id": "SHP006",
    "customer_name": "ABC Industries",
    "pickup_location": "Ludhiana",
    "destination": "Delhi",
    "status": "Booked",
    "shipping_cost": 5000.0
  }
}
```

---

## 📱 7. Responsive Breakpoints Verification Matrix

APEX FLOW has been engineered and tested across standard mobile, tablet, laptop, and desktop resolutions:

| Device Category | Breakpoint Width | Responsive Design Behavior |
| :--- | :--- | :--- |
| **Small Mobile** | `320px` | 1-col card grid, drawer navigation, scrollable tables, full-width forms |
| **Standard Mobile** | `375px` | iPhone layout, touch-optimized tap targets, hamburger menu toggle |
| **Large Mobile** | `414px` | Plus/Max mobile optimization, touch modals, 100% responsive header |
| **Tablet Portrait** | `768px` | 2-col dashboard grid, slide-out drawer navigation overlay |
| **Tablet Landscape**| `1024px` | Expanded stats layout, adaptive chart and map views |
| **Standard Laptop** | `1366px` | Full fixed sidebar navigation, 4-col KPI metrics grid |
| **Full HD Desktop** | `1920px` | Max-width content boundary, multi-column analytics grid |

---

## 🔒 8. Security & Best Practices

- **Authorization is enforced in the service layer**, not only with `@login_required`. Customers see their `customer_id` rows; drivers see assigned jobs; staff see the fleet.
- **Delivery ≠ payment.** Marking a shipment delivered (OTP) does not flip invoices to Paid. Collect via `POST /api/payments/<invoice_id>/collect`.
- **OTPs** are 6-digit CSPRNG values, hashed at rest, single-use, TTL 30 minutes, 5-attempt lockout. Seed OTPs from git (`4912`, …) are invalid.
- **Login rate limit** is 5/minute, applied with `@limiter.limit` on the view Flask actually calls. Set `REDIS_URL` in production so workers share counters.
- **Demo passwords are not on the login page.** Production boot refuses documented defaults (`Admin@123`, …).
- **Passwords hashed** with `werkzeug.security`. **SQL** is parameterized. Session cookies are `HttpOnly`, `SameSite=Lax`, `Secure` in production.
- **PostgreSQL fail-closed:** if `DATABASE_URL` is set and the connection fails, the app does not fall back to SQLite.
- **Security headers:** `CSP`, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `HSTS` (production).
- Run `pytest` before every release. Gates live in `tests/test_security.py`.

---

## 📄 License & Credits

Built for enterprise logistics and fleet operations.
Designed & Developed for **APEX FLOW**. *"Smarter Logistics. Faster Tomorrow."*
