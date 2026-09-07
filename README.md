# 🚀 APEX FLOW — Smart Transport & Logistics System

> *Smarter Logistics. Faster Tomorrow.*

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-2.3+-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![SQLite](https://img.shields.io/badge/SQLite-dev-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-prod-336791?logo=postgresql&logoColor=white)](https://postgresql.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Architecture](#-architecture)
- [Quick Start (Local)](#-quick-start-local)
- [Environment Variables](#-environment-variables)
- [Deployment](#-deployment-render--railway)
- [API Reference](#-api-reference)
- [User Roles](#-user-roles)
- [Security](#-security)
- [Project Structure](#-project-structure)
- [Contributing](#-contributing)

---

## 🌐 Overview

APEX FLOW is a full-stack **Transport & Logistics Management System** built with:

- **Frontend**: HTML5 · CSS3 · Vanilla JavaScript (responsive, mobile-first)
- **Backend**: Python Flask REST API
- **Database**: SQLite (development) / PostgreSQL (production)
- **Deployment**: Render / Railway (Procfile-ready)

The system manages the complete logistics lifecycle — from shipment booking and vehicle fleet management to live GPS tracking, OTP-verified delivery confirmation, and analytics dashboards — all accessible from **any device** via a public HTTPS URL.

---

## ✨ Features

| Module | Description |
|---|---|
| 🔐 **Authentication** | Session-based login with role-based access control (RBAC) |
| 📦 **Shipments** | Book, track, update, and cancel shipments end-to-end |
| 🚛 **Fleet Management** | Manage vehicles with status, compliance, and assignment tracking |
| 👨‍✈️ **Drivers** | Driver profiles, license tracking, trip history, and ratings |
| 👥 **Customers** | Customer accounts with shipment history and spend tracking |
| 🗺️ **Live Tracking** | Real-time GPS simulation with ETA and speed telemetry |
| 📍 **Route Optimization** | Calculate optimal routes with fuel cost estimation |
| 🏭 **Warehouses** | Warehouse locations and capacity management |
| 📬 **Delivery OTP** | CSPRNG 6-digit OTP with 5-attempt lockout and 30-min expiry |
| 💳 **Payments** | Invoice and payment record management |
| 📊 **Reports & KPIs** | Dashboard KPIs — revenue, fleet utilization, delivery rates |
| 🔔 **Notifications** | System-wide notification feed with read/unread state |

---

## 🏗️ Architecture

```
apex-flow/
├── backend/               # Flask REST API
│   ├── app.py             # WSGI entry point, CORS, rate-limiter, session config
│   ├── routes.py          # All API endpoints (Blueprint)
│   ├── auth.py            # @login_required / @require_role decorators
│   ├── services.py        # Business logic layer
│   ├── database.py        # SQLite & PostgreSQL dual-engine with schema
│   ├── tracking.py        # Live GPS simulation engine
│   ├── reports.py         # KPI aggregations and analytics queries
│   ├── logger.py          # Structured security event logger (PII-redacted)
│   ├── models.py          # Row-to-dict helpers
│   ├── utils.py           # JSON response helpers
│   └── config.py          # Environment-driven configuration
├── frontend/              # Static HTML5/CSS3/JS pages
│   ├── login.html         # Authentication page
│   ├── dashboard.html     # KPI overview
│   ├── shipments.html     # Shipment management
│   ├── tracking.html      # Live GPS tracking map
│   ├── vehicles.html      # Fleet management
│   ├── drivers.html       # Driver management
│   ├── customers.html     # Customer management
│   ├── routes.html        # Route optimization
│   ├── deliveries.html    # OTP delivery confirmation
│   ├── payments.html      # Payment records
│   ├── reports.html       # Analytics reports
│   ├── notifications.html # Notification center
│   ├── warehouses.html    # Warehouse directory
│   └── settings.html      # App settings
├── data/                  # Seed JSON data files
├── .env.example           # Environment variable template
├── requirements.txt       # Python dependencies
└── Procfile               # Gunicorn deployment config
```

---

## ⚡ Quick Start (Local)

### Prerequisites

- Python 3.10+
- pip

### 1. Clone the repository

```bash
git clone https://github.com/nandan503/Apex-Flow.git
cd Apex-Flow
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
# Edit .env and set your values — see Environment Variables section below
```

### 5. Run the development server

```bash
python3 backend/app.py
```

The app will be available at **http://localhost:5050**

### Default Login Credentials (Development Only)

> ⚠️ Change these immediately in any non-local environment via the `SEED_*_PASSWORD` env vars.

| Role | Email | Password (env var) |
|---|---|---|
| Admin | admin@apexflow.com | `SEED_ADMIN_PASSWORD` |
| Manager | manager@apexflow.com | `SEED_MANAGER_PASSWORD` |
| Driver | driver@apexflow.com | `SEED_DRIVER_PASSWORD` |
| Customer | customer@apexflow.com | `SEED_CUSTOMER_PASSWORD` |

---

## 🔧 Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✅ Production | Flask session signing key. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_ENV` | ✅ Production | Set to `production` to enable all security hardening |
| `DATABASE_URL` | ⬜ Optional | PostgreSQL URL. Omit to use SQLite locally |
| `ALLOWED_ORIGINS` | ✅ Production | Comma-separated list of allowed frontend origins |
| `REDIS_URL` | ⬜ Optional | Redis URL for distributed rate limiting. Omit for in-memory |
| `HOST` | ⬜ Optional | Server bind host (default: `0.0.0.0`) |
| `PORT` | ⬜ Optional | Server bind port (default: `5050`) |
| `SEED_ADMIN_PASSWORD` | ✅ First run | Admin seed password for DB initialization |
| `SEED_MANAGER_PASSWORD` | ✅ First run | Manager seed password |
| `SEED_DRIVER_PASSWORD` | ✅ First run | Driver seed password |
| `SEED_CUSTOMER_PASSWORD` | ✅ First run | Customer seed password |

---

## ☁️ Deployment (Render / Railway)

### Deploy to Render (Recommended — Free tier available)

1. Push your code to GitHub.
2. Go to [render.com](https://render.com) → **New Web Service**.
3. Connect your `nandan503/Apex-Flow` repository.
4. Configure:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn backend.app:app` *(auto-read from Procfile)*
   - **Environment**: Python 3
5. Add environment variables in the Render dashboard (see table above).
6. Click **Deploy**.

Your app will be live at `https://apex-flow-xxxx.onrender.com` — accessible from **any device worldwide**.

### Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli
railway login
railway init
railway up
```

Set env vars in Railway dashboard or via `railway variables set KEY=value`.

### Access from Any Device

Once deployed to Render/Railway, open the public HTTPS URL on:
- 📱 Android phone
- 📱 iPhone
- 💻 Windows PC
- 🍎 MacBook
- 🐧 Linux PC
- 📟 Tablet

No VPN, no port forwarding, no localhost required.

---

## 📡 API Reference

All endpoints are prefixed with `/api`. Full API documentation: [`docs/API.md`](docs/API.md)

### Authentication

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/login` | Public | Login — returns session cookie |
| `POST` | `/api/auth/logout` | Session | Logout — clears session |
| `GET` | `/api/auth/me` | Session | Get current user profile |

### Shipments

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/shipments` | Session | List shipments (role-filtered) |
| `GET` | `/api/shipments/:id` | Session | Get shipment + status history |
| `POST` | `/api/shipments` | Session | Create new shipment |
| `PUT` | `/api/shipments/:id/status` | ADMIN/MANAGER/DRIVER | Update shipment status |
| `DELETE` | `/api/shipments/:id` | ADMIN only | Delete shipment |

### Fleet, Drivers, Customers

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/vehicles` | Session | List all vehicles |
| `POST` | `/api/vehicles` | ADMIN/MANAGER | Add vehicle |
| `GET` | `/api/drivers` | Session | List all drivers |
| `POST` | `/api/drivers` | ADMIN/MANAGER | Add driver |
| `GET` | `/api/customers` | ADMIN/MANAGER | List all customers |
| `POST` | `/api/customers` | ADMIN/MANAGER | Add customer |

### Tracking, Routes, Deliveries

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/tracking` | Session | All active shipment positions |
| `GET` | `/api/tracking/:vehicle_id` | Session | Single vehicle tracking |
| `GET` | `/api/routes` | Session | List all routes |
| `POST` | `/api/routes/optimize` | Session | Calculate optimal route |
| `GET` | `/api/deliveries` | Session | List deliveries |
| `POST` | `/api/deliveries/confirm` | Session | Confirm delivery with OTP |

### Reports & Notifications

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/api/reports/dashboard` | Session | KPI summary for dashboard |
| `GET` | `/api/reports/:type` | ADMIN/MANAGER | Analytics report (shipments/revenue/fleet/drivers) |
| `GET` | `/api/payments` | ADMIN/MANAGER | Payment records |
| `GET` | `/api/notifications` | Session | All notifications |
| `PUT` | `/api/notifications/:id/read` | Session | Mark notification as read |

---

## 👥 User Roles

APEX FLOW uses Role-Based Access Control (RBAC) with four roles:

| Role | Access Level |
|---|---|
| **ADMIN** | Full access — all endpoints including delete, payments, customers, reports |
| **MANAGER** | Operational access — shipments, fleet, drivers, customers, reports (no delete) |
| **DRIVER** | Limited — view shipments assigned to them, update delivery status |
| **CUSTOMER** | Self-service — book and track their own shipments only (IDOR-protected) |

---

## 🔒 Security

APEX FLOW has undergone a full security remediation. Key measures:

| Control | Implementation |
|---|---|
| **Authentication** | Server-side Flask sessions with HttpOnly + SameSite=Lax + Secure cookies |
| **Authorization** | `@login_required` and `@require_role` decorators on every protected route |
| **IDOR Protection** | Customers can only access their own shipment records |
| **Rate Limiting** | Global 200 req/min; Login 10/min; OTP confirmation 5/min |
| **OTP Security** | 6-digit CSPRNG OTP, 30-min expiry, 5-attempt lockout |
| **Secret Key** | Mandatory via env var; raises `RuntimeError` if missing in production |
| **CORS** | Locked to explicit origin allowlist (`ALLOWED_ORIGINS` env var) |
| **Mass Assignment** | Shipment creation uses an explicit field allowlist |
| **Audit Logging** | Structured JSON security logs for all auth events (PII-redacted) |
| **SAST** | Custom Semgrep rules in `.semgrep.yml` |
| **Secret Scanning** | `gitleaks` pre-commit hook in `.pre-commit-config.yaml` |

See the full security audit report: [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md)

---

## 📁 Project Structure

```
Apex-Flow/
├── .env.example              # Environment variable template
├── .gitignore                # Excludes bytecode, .env, databases, IDE files
├── .pre-commit-config.yaml   # gitleaks + semgrep pre-commit hooks
├── .semgrep.yml              # Custom SAST rules
├── Procfile                  # gunicorn start command for Render/Railway
├── README.md                 # This file
├── requirements.txt          # Python dependencies
├── backend/
│   ├── app.py                # Flask app factory, CORS, rate limiting, session
│   ├── auth.py               # Auth decorators and authentication service
│   ├── config.py             # Environment-driven config
│   ├── database.py           # SQLite/PostgreSQL dual-engine + schema init
│   ├── logger.py             # Structured security event logger
│   ├── models.py             # DB row serialization helpers
│   ├── reports.py            # KPI and analytics queries
│   ├── routes.py             # All API route handlers
│   ├── services.py           # Business logic (shipments, vehicles, drivers, etc.)
│   ├── tracking.py           # Live GPS simulation engine
│   └── utils.py              # JSON response helpers
├── frontend/
│   ├── css/                  # Stylesheets
│   ├── js/                   # JavaScript modules
│   ├── login.html            # Login page
│   ├── dashboard.html        # KPI dashboard
│   ├── shipments.html        # Shipment management
│   ├── tracking.html         # Live map tracking
│   ├── vehicles.html         # Fleet management
│   ├── drivers.html          # Driver management
│   ├── customers.html        # Customer management
│   ├── routes.html           # Route optimization
│   ├── deliveries.html       # OTP delivery confirmation
│   ├── payments.html         # Payment records
│   ├── reports.html          # Analytics reports
│   ├── notifications.html    # Notification center
│   ├── warehouses.html       # Warehouse directory
│   └── settings.html         # Settings page
├── data/
│   ├── customers.json        # Seed customer data
│   ├── drivers.json          # Seed driver data
│   ├── routes.json           # Seed route data
│   ├── shipments.json        # Seed shipment data
│   ├── users.json            # Seed user data
│   └── vehicles.json         # Seed vehicle data
└── docs/
    ├── API.md                # Full API reference
    ├── SECURITY_AUDIT.md     # Security audit report
    ├── DEPLOYMENT.md         # Detailed deployment guide
    └── CONTRIBUTING.md       # Contribution guidelines
```

---

## 🤝 Contributing

See [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) for contribution guidelines, branch policy, and commit message standards.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

*Built with ❤️ by the APEX FLOW team.*
