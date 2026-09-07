# APEX FLOW — Security & Technical Debt Audit

**Date:** 2026-09-07  
**Auditor:** Senior Staff Security Engineer  
**Scope:** Full repository (`backend/`, `frontend/`, `data/`, deploy config)  
**Method:** Source review + live exploit against Flask test client (same code paths as production)  
**Build reviewed:** branch `arena/01a07d2b-apex-flow` @ `5c72254`

---

## Verdict: **NO-GO for production**

Do not expose this application to real customers, real invoices, or a public URL. Authentication exists; **authorization does not**. A logged-in Customer or Driver can read fleet PII, live tracking, other customers' cargo, and company revenue; can confirm other parties' deliveries with OTPs that are committed to git; and a Driver can mark any invoice **Paid** by setting shipment status to `Delivered`. A previous AI security pass (comments `F-02`…`F-13`, Semgrep, pre-commit) patched several *look-correct* issues and left the control that actually matters — object-level access control — broken.

Re-evaluate only after Findings **F1–F4** are fixed and retested.

---

## Executive summary

- Any authenticated user (Customer or Driver) can read **all in-flight cargo, GPS tracks, driver licenses/phones, warehouse contacts, and total revenue**. The UI hides some buttons; the API does not.
- A Customer can **confirm someone else's delivery** using OTP `4912` copied from this repo; a Driver can **skip OTP entirely** and force `Delivered`, which the server treats as **invoice Paid**.
- The login rate limit advertised as “10 per minute” **does not fire**. 15 sequential bad passwords all returned `401`. Demo admin credentials are hardcoded in `frontend/login.html`.
- Customer “IDOR protection” compares `users.user_id` (`USR004`) to `shipments.customer_id` (`CUST001`) — different ID spaces — so the one check that exists both **blocks the customer from their own seeded data** and **does not implement tenancy**.
- SQL is parameterized and passwords are hashed. That is necessary and nowhere near sufficient. Treat the Semgrep/pre-commit setup as **security theater** until authorization is centralized and tested.

---

## 1. Reconnaissance

### What this is

A Flask monolith serving a vanilla-JS SPA from `frontend/`. Persistence is SQLite (`data/apexflow.db`) or PostgreSQL via a homegrown compatibility wrapper. Four hard-coded roles: `ADMIN`, `MANAGER`, `DRIVER`, `CUSTOMER`. No tenant/org table. No user-management, password-change, or registration APIs.

### Entry points

| Surface | Auth enforced? | Notes |
|---|---|---|
| `POST /api/auth/login` | Public | Session cookie issued |
| `POST /api/auth/logout` | Public | `session.clear()` |
| `GET /api/auth/me` | `login_required` | |
| REST under `/api/*` (shipments, vehicles, drivers, customers, warehouses, routes, tracking, deliveries, payments, reports, notifications) | Mix of `login_required` / `require_role` | **Almost no object-level checks** |
| `GET /`, `GET /<path>` static | **None** | HTML/JS served to the world; API is the trust boundary |
| CLI / queues / webhooks | None present | |
| `data/*.json` | Unused leftovers | Not loaded at runtime |

### AuthN / AuthZ model

- **AuthN:** Flask server-side signed cookie session. On login, `session['user_id']` and `session['role']` are copied from the DB **once** and never re-read.
- **AuthZ:** Decorators in `backend/auth.py` (`login_required`, `require_role`). Enforced only at the route layer. The service/repo layer accepts `caller_role` on **one** function (`get_all_shipments`) and ignores caller identity everywhere else.
- **Role in the cookie is the authorization source of truth.** Cookie payload (decoded): `{"_permanent": true, "role": "ADMIN", "user_id": "USR001"}`. Tamper-proof without `SECRET_KEY`; fully attacker-controlled if `SECRET_KEY` leaks. No server-side session store ⇒ logout on one browser does not revoke a stolen cookie.

### Trust boundaries

```
Browser  --cookie-->  Flask (routes.py)  -->  services.py  -->  SQLite/Postgres
                         ^
                         |  role taken from session, not DB
                         |
              no gateway, no WAF, no CSP, no MAX_CONTENT_LENGTH
```

Static pages are unauthenticated by design. That is acceptable **only if** every API is correctly authorized. It is not.

### AI-generation fingerprints (relevant because they predict the bug classes)

- Prior-pass comments `F-02`, `F-04`, `F-05`, `F-06`, `F-08`, `F-12`, `F-13` next to incomplete fixes.
- `get_all_drivers()` docstring: *“PII scoped to role (caller must check role before exposing)”* — the caller never checks.
- Semgrep rule `apexflow-unauthenticated-route` matches `/api/auth/login`; the real route is `/auth/login` on a blueprint. The rule cannot see what it claims to guard.
- Leftovers from other prompts: `html.h` (Arduino `R"rawliteral("` embed), empty `HImanshu` / `index.h`, unused `data/*.json`, root `index.html` (1,269-line monolith not served by Flask).
- Frontend still renders `d.otp_code` after the backend stopped returning it; deliveries UI still says “4-Digit OTP” after the backend moved to 6 digits.
- `set_limiter()` in `routes.py` is never called; limiter is applied in `app.py` incorrectly (F3).

---

## 2. Threat model (attacker playbooks)

Assume the public URL is live with seed data (the documented demo).

| Attacker | First move | Result in this codebase |
|---|---|---|
| Unauthenticated | Open `/login.html`, click **ADMIN**, or spray passwords | Demo passwords in HTML; login rate limit does not apply (F3). Static dashboard HTML is public; data is not, until they have a cookie. |
| Authenticated Customer (`USR004`) | `GET /api/reports/dashboard`, `/api/drivers`, `/api/tracking`, `/api/deliveries` | Full company revenue, all recent shipments, every driver license/phone, live GPS, all deliveries (F1). Then `POST /api/deliveries/confirm` with OTP `4912` from git (F2). |
| Authenticated Driver | `GET /api/shipments/SHP003`; `PUT /api/shipments/SHP002/status {"status":"Delivered"}` | Reads Globe Pharma cold-chain instructions; marks Zenith invoice **Paid** with no OTP and no payment (F1, F2). |
| Authenticated high-priv (Manager/Admin) | Stored XSS in `notes` / `customer_name` | Any user who opens the shipment timeline executes HTML in the admin browser (F4). Cookie is HttpOnly so theft is via CSRF-from-XSS (`fetch('/api/...')`), not `document.cookie`. |
| Insider / stolen cookie | Use cookie for up to 8 hours, **sliding** on each request | No revocation list. `SESSION_REFRESH_EACH_REQUEST` is Flask default `True`. |

### IDOR traces (three endpoints, as required)

**A. `GET /api/shipments/<shipment_id>`** — existence check only, plus a broken customer clause.

```94:107:backend/routes.py
@api_bp.route('/shipments/<shipment_id>', methods=['GET'])
@login_required
def get_shipment(shipment_id):
    shipment = get_shipment_by_id(shipment_id)
    if not shipment:
        return error_response('Shipment not found', 404)

    # IDOR: customers can only view their own shipments
    if session.get('role') == 'CUSTOMER':
        if shipment.get('customer_id') != session.get('user_id'):
            return error_response('Forbidden', 403)

    return json_response(data=shipment)
```

- Live: Customer `USR004` → `GET /api/shipments/SHP001` → **403** (because `customer_id` is `CUST001`, not `USR004`).
- Live: Driver `USR003` → `GET /api/shipments/SHP003` → **200** with `goods_type=Pharmaceuticals`, `special_instructions=Maintain 2-8°C temperature throughout.`, `shipping_cost=24500`.
- `DRIVER` / `MANAGER` / `ADMIN` have **zero** ownership check. Drivers are not restricted to `driver_id == me`.

**B. `GET /api/tracking/<vehicle_id>`** — no ownership; on miss it returns **someone else's** vehicle.

```93:97:backend/tracking.py
def get_vehicle_tracking(vehicle_id):
    all_tracking = get_live_tracking_data()
    for t in all_tracking:
        if t['vehicle_id'] == vehicle_id:
            return t
    return all_tracking[0] if all_tracking else None
```

Live: Customer `GET /api/tracking/DOESNOTEXIST` → **200** `vehicle_id=VEH002`.

**C. `PUT /api/notifications/<notif_id>/read`** — any authenticated user can mark any role's notification read.

Live: Customer `PUT /api/notifications/NOTIF001/read` → **200** (that notification is `user_role=ADMIN`).

### Privilege escalation via parameter tampering

| Client field | Accepted? | Effect |
|---|---|---|
| `role` / `is_admin` on login | No | Role taken from DB into session. Good. |
| `status` / `payment_status` / `shipping_cost` on `POST /api/shipments` | No | Allowlisted; server computes cost (F-02). Good. |
| `status` on `PUT /api/shipments/<id>/status` | Yes, any of the CHECK enum | Driver/Manager can jump `Booked` → `Delivered`, which **sets `payments.payment_status='Paid'`**. |
| `customer_id` on create (non-CUSTOMER) | Yes, from raw `data`, not the allowlist | Driver created a shipment for `CUST005`. |
| `status` on `POST /api/vehicles` | Yes | Live: `{"registration_number":"HACK999","status":"On Trip"}` → stored `On Trip`. |
| `weight` negative | Yes | Live: Driver `weight: -1` → `201`, `shipping_cost=1917`. |

---

## 3. Findings

### F1. Broken object-level authorization (IDOR) across the API

- **Severity:** Critical  
- **Location:** `backend/routes.py` (most handlers); `backend/services.py` (`get_all_*`, `get_dashboard_kpis`); `backend/reports.py`; `backend/tracking.py`  
- **CVSS-informed:** 8.6 (Authenticated, low-priv, scope change: cross-customer data). Business-adjusted to **Critical** because cargo contents, GPS, and revenue are the product.

**Evidence.** `list_drivers` is `@login_required` only. The service even documents a check that does not exist:

```181:184:backend/routes.py
@api_bp.route('/drivers', methods=['GET'])
@login_required
def list_drivers():
    drivers = get_all_drivers()
```

```332:345:backend/services.py
def get_all_drivers():
    """Return driver list with PII scoped to role (caller must check role before exposing)."""
    ...
        SELECT driver_id, name, phone, license_number, license_expiry,
               experience_years, assigned_vehicle_id, status,
               ...
        FROM drivers ORDER BY created_at DESC
```

Dashboard KPIs are global, with **no tenant filter**, and allowed to every logged-in role:

```36:38:backend/reports.py
    cursor.execute("SELECT * FROM shipments ORDER BY created_at DESC LIMIT 5")
    recent_shipments = rows_to_list(cursor.fetchall())
```

**Live results (Customer `customer@apexflow.com`):**

| Request | Result |
|---|---|
| `GET /api/shipments` | `[]` (identity-mapping bug, F5) |
| `GET /api/shipments/SHP001` | 403 |
| `GET /api/drivers` | 5 drivers, `license_number=DL-1420110098`, `phone=+91 9876543212` |
| `GET /api/vehicles` | 6 vehicles |
| `GET /api/deliveries` | all 3 deliveries |
| `GET /api/reports/dashboard` | `total_revenue=99200`, `recent_shipments=['SHP001'…'SHP005']` |
| `GET /api/tracking` | 3 live tracks |
| `GET /api/notifications` | ADMIN + MANAGER + DRIVER notifications |
| `GET /api/reports/shipments` | 403 (this one is actually role-gated) |
| `GET /api/payments` | 403 |

**Live results (Driver):** `GET /api/shipments` returned **all 6** shipments including ones they are not assigned to.

**Attack scenario**

1. Register nothing — use the documented Customer or Driver demo account (or any future low-priv account).
2. `POST /api/auth/login` with that account.
3. `GET /api/reports/dashboard` — competitor/customer volume and revenue.
4. `GET /api/tracking` — live interpolated GPS of every in-transit load.
5. `GET /api/drivers` — harvest license numbers and phones.
6. `GET /api/shipments/SHP003` (as Driver) — read pharmaceutical handling instructions for another customer.

**Root cause:** Systemic. Routes use `login_required` as if it were authorization. Service layer does not receive caller identity except for one list filter. **[AI-PATTERN]** “add `@login_required` to every route” without RBAC matrices or object ownership. The comment on `get_all_drivers` is the tell: the model *knew* PII needed scoping and left it to a caller that does not exist.

**Remediation**

- Write a single authorization module, e.g. `assert_can(user, action, resource)`, and call it from the **service** layer (not only the route).
- Query-level scoping:

```python
def get_all_shipments(..., caller_role, caller_user_id):
    if caller_role == 'CUSTOMER':
        query += " AND customer_id = ?"
        params.append(customer_id_for_user(caller_user_id))  # mapped, not user_id
    elif caller_role == 'DRIVER':
        query += " AND driver_id = ?"
        params.append(driver_id_for_user(caller_user_id))
```

- Default-deny matrix:

| Resource | CUSTOMER | DRIVER | MANAGER | ADMIN |
|---|---|---|---|---|
| Own shipments | R | R (assigned) | RW | RW |
| Other shipments | — | — | RW | RW |
| Drivers PII | — | self | RW | RW |
| Vehicles / warehouses | — | assigned | RW | RW |
| Dashboard revenue | own | — | R | R |
| Payments | own invoices | — | R | RW |
| Tracking | own shipment | assigned vehicle | R | R |
| Notifications | own role + user | own | own | all |

- Hide-by-CSS (`.admin-only` in `frontend/js/app.js`) is not a control. Remove it from the threat model.

**Effort:** L **Regression risk:** Every frontend page that currently assumes global lists will show empty tables for Customer/Driver until UIs are role-split. That is the desired break.

---

### F2. Delivery OTP and payment integrity are bypassable

- **Severity:** Critical  
- **Location:** `backend/routes.py:117-140`, `311-337`; `backend/services.py:199-226`, `418-496`; `backend/database.py` seed rows; `frontend/login.html` / `README.md` OTPs

Three independent bugs that chain.

**2a. Status update is a payment switch.** Any `ADMIN`/`MANAGER`/`DRIVER` can `PUT` status `Delivered`. That path marks the invoice Paid with no payment processor and no OTP:

```214:219:backend/services.py
    if new_status == 'Delivered':
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ...
        cursor.execute(
            "UPDATE payments SET payment_status = 'Paid', paid_date = ? WHERE shipment_id = ?",
            (datetime.now().strftime('%Y-%m-%d'), shipment_id)
        )
```

No transition table: `Booked` → `Delivered` is legal.

**Live:** Driver `PUT /api/shipments/SHP002/status {"status":"Delivered","notes":"<img src=x onerror=alert(1)>"}` → 200. Admin `GET /api/payments` showed SHP002 `payment_status=Paid`, `paid_date=2026-09-07`.

**2b. Confirm-delivery has no ownership or role check.** `@login_required` only. A Customer can confirm **any** shipment's OTP.

**2c. Seed OTPs are public, 4-digit, never expire, never consumed.**

```426:436:backend/database.py
        ('DEL001', 'SHP001', ..., 'In Progress', '4912', ...),
        ('DEL002', 'SHP003', ..., 'Delivered', '8492', ...),
        ('DEL003', 'SHP004', ..., 'Pending', '3381', ...),
```

The INSERT does not set `otp_expires_at`, so it is NULL. The expiry check is skipped:

```448:453:backend/services.py
    expires_at_str = d.get('otp_expires_at')
    if expires_at_str:
        expires_at = datetime.strptime(expires_at_str, '%Y-%m-%d %H:%M:%S')
        if datetime.now() > expires_at:
            ...
```

On success the OTP is **not** flipped, hashed, or deleted — the connection is closed and `update_shipment_status` is called on a new connection:

```475:490:backend/services.py
    conn.close()
    log_otp_attempt(...)
    update_shipment_status(
        shipment_id, 'Delivered',
        ...
    )
    return True, "Delivery confirmed successfully!"
```

**Live:** Customer `POST /api/deliveries/confirm {"shipment_id":"SHP001","otp_code":"4912"}` → **200 Delivery confirmed successfully!** Replay of the same OTP → **200** again.

Placeholder in the UI advertises the seed OTP (`frontend/deliveries.html`: `placeholder="e.g. 4912"`). New shipments generate a 6-digit CSPRNG OTP that is **never returned, emailed, or SMS'd** — so the “secure OTP” path is unusable for real deliveries and trivial for seed ones.

**Attack scenario**

1. Log in as Customer (or anything with a cookie).
2. `POST /api/deliveries/confirm` with `shipment_id=SHP001`, `otp_code=4912` from GitHub.
3. Shipment is Delivered; invoice is Paid; receiver_name is attacker-controlled.
4. Alternative, as Driver: skip OTP, `PUT /api/shipments/<id>/status {"status":"Delivered"}`.

**Root cause:** Systemic business-logic gap. **[AI-PATTERN]** Security features (6-digit CSPRNG, attempt cap, TTL, “never return otp_code”) bolted onto seed data and a status machine that already grants the same privilege. Frontend still displays `${d.otp_code}` (`frontend/js/deliveries.js:24`) after the field was removed from the SELECT.

**Remediation**

- Status machine: explicit transitions. Only `Out for Delivery` → `Delivered`, and **only** via `confirm_delivery`.
- `confirm_delivery`: require `DRIVER` (or receiver token); require `deliveries.driver_id == caller`; reject if already Delivered; consume OTP (`otp_code = NULL` or rotate) in the **same transaction** as the status update; use `hmac.compare_digest`.
- Stop marking payments Paid as a side effect of delivery. Payments need their own authenticated, preferably idempotent, workflow.
- Hash OTPs at rest (`generate_password_hash`); never seed static OTPs; never commit them.
- If `otp_expires_at` is NULL, treat as expired, not immortal.
- Deliver the OTP out of band (SMS/email) to the **customer**, not the driver UI.

**Effort:** M **Regression risk:** Drivers who currently “complete” jobs from the status dropdown will fail until they use the OTP flow. Payments that auto-flip to Paid will stay Pending — finance must get a real collection path.

---

### F3. Login (and OTP) rate limits are dead code

- **Severity:** High  
- **Location:** `backend/app.py:69-76`

```69:76:backend/app.py
with app.app_context():
    login_view = app.view_functions.get('api.login')
    confirm_view = app.view_functions.get('api.confirm_delivery_api')
    if login_view:
        limiter.limit("10 per minute")(login_view)
    if confirm_view:
        limiter.limit("5 per minute")(confirm_view)
```

`limiter.limit(...)(view)` returns a **wrapper that is discarded**. Flask-Limiter also adds the function’s qualified name to `_marked_for_limiting`, which causes the before-request middleware to **skip the global 200/min default**, expecting the wrapper to enforce the decorated limit. The wrapper is not in `app.view_functions`, so **neither limit runs**.

**Live:**

- `app.view_functions['api.login']` is the raw function; `__wrapped__` is `None`.
- `_marked_for_limiting == {'backend.routes.login.login', 'backend.routes.confirm_delivery_api.confirm_delivery_api'}`.
- 15 sequential `POST /api/auth/login` with a wrong password: **`[401] × 15`**, never `429`.

Combined with F6 (passwords in HTML) this is unauthenticated admin takeover on any deploy that used seed defaults.

`set_limiter()` in `routes.py` is unused. Storage defaults to `memory://` (`backend/config.py`); README’s `gunicorn --workers 4` would split counters even if the decorator worked.

**Root cause:** One-off misuse of a decorator. **[AI-PATTERN]** “apply limiter after registration” without assigning the wrapper back, plus in-memory store on a multi-worker WSGI server.

**Remediation**

```python
@api_bp.route('/auth/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    ...
```

Pass the limiter into the blueprint (`limiter.limit` needs the app context, or use `Limiter.limit` as a decorator at import via `limiter.init_app`). Use Redis in production (`REDIS_URL`). Add `ProxyFix` **only** if the reverse proxy strips client-supplied `X-Forwarded-For` and sets it; otherwise keep `get_remote_address` on the socket peer.

**Effort:** S **Regression risk:** Low. Legitimate users behind a shared NAT may hit 5/min — tune, don’t skip.

---

### F4. Stored XSS via unsanitized `innerHTML` (no CSP)

- **Severity:** High  
- **Location:** every `frontend/js/*.js` table renderer; write path `PUT /api/shipments/<id>/status` `notes`; `POST /api/shipments` text fields; toast in `frontend/js/app.js:44`

**Evidence.** Driver-supplied notes are stored verbatim and later injected:

```100:107:frontend/js/shipments.js
        historyContainer.innerHTML = (s.history || []).map(h => `
          ...
            <div ...>${h.notes || ''}</div>
```

```44:47:frontend/js/app.js
  toast.innerHTML = `
    <span ...>${icon}</span>
    <span>${message}</span>
  `;
```

No `Content-Security-Policy`, no `X-Frame-Options`, no output encoding helper.

**Live:** Driver status notes `<img src=x onerror=alert(1)>` persisted; `GET /api/shipments/SHP002` returned that string in `history[-1].notes`. Whoever opens the timeline executes it in their origin (admin session included). Session cookie is HttpOnly, so the payload’s value is **authenticated API calls**, e.g. `fetch('/api/shipments/'+id,{method:'DELETE'})`.

**Root cause:** Systemic frontend pattern. **[AI-PATTERN]** template literals into `innerHTML` for every table.

**Remediation:** `textContent` / `document.createElement`; a one-line `escapeHtml()`. Add CSP `default-src 'self'; script-src 'self'` (will require removing inline `onclick=` / `onsubmit=` — good). Validate/limit `notes` length server-side (`[:500]`, strip tags).

**Effort:** M **Regression risk:** Inline handlers on every HTML page will break under a strict CSP until they are converted to `addEventListener`. Do encoding first, CSP second.

---

### F5. Customer identity is not mapped to `customers.customer_id`

- **Severity:** High (integrity + false sense of IDOR coverage)  
- **Location:** `backend/services.py:99`; `backend/routes.py:101-105`; `backend/database.py` seed (`USR004` vs `CUST001`)

```99:99:backend/services.py
    customer_id = caller_user_id if caller_role == 'CUSTOMER' else data.get('customer_id', 'CUST001')
```

Users live in `users.user_id` (`USR004`). Shipments live in `customers.customer_id` (`CUST001`). There is no join table. The IDOR check compares the two.

**Live:** Customer list = **0 rows** despite ABC Industries owning SHP001. Newly created customer shipments are tagged `customer_id=USR004`, which **violates** `FOREIGN KEY(customer_id) REFERENCES customers(customer_id)` in theory; SQLite accepted it because the INSERT ran (FK to customers may fail on Postgres — **NEEDS VERIFICATION** on PostgreSQL: `POST /api/shipments` as CUSTOMER should 500/IntegrityError).

**Attack / impact:** Tenancy cannot be implemented until this is fixed; the existing check is a placebo. Also lets a Driver set arbitrary `customer_id` (not in the field allowlist — read from raw `data`).

**Remediation:** `users.customer_id` FK, or `users.driver_id` FK, populated at seed. Never use `user_id` as `customer_id`. Put `customer_id` in `_SHIPMENT_ALLOWED_FIELDS` only for staff, and verify it exists.

**Effort:** M **Regression risk:** Existing rows with `customer_id=USR004` (any customer-created shipments) need a data migration.

---

### F6. Default credentials shipped in the production frontend

- **Severity:** High  
- **Location:** `frontend/login.html:38-41`; `README.md:80-84`; `backend/database.py:347-356`

```38:41:frontend/login.html
        <button class="demo-role-btn" onclick="selectDemoRole('admin@apexflow.com', 'Admin@123')">ADMIN</button>
        <button class="demo-role-btn" onclick="selectDemoRole('manager@apexflow.com', 'Manager@123')">MANAGER</button>
        <button class="demo-role-btn" onclick="selectDemoRole('driver@apexflow.com', 'Driver@123')">DRIVER</button>
        <button class="demo-role-btn" onclick="selectDemoRole('customer@apexflow.com', 'Customer@123')">CUSTOMER</button>
```

Production seed *refuses* to start without `SEED_*_PASSWORD` — good — but:

1. A DB first initialized in development (defaults) and then copied / reused in production keeps `Admin@123`.
2. README documents those passwords next to Render/Railway deploy instructions.
3. The login page always offers them, including on a hardened production DB (buttons just fail — still a targeting gift).
4. Combined with F3, online guessing of the documented password is unlimited per IP.

**Remediation:** Delete demo buttons from any build that is not `FLASK_ENV=development`. Refuse to serve the app if seed hashes match known defaults (`check_password_hash` against `Admin@123` at boot). Rotate the documented demo entirely.

**Effort:** S **Regression risk:** Demo reviewers lose one-click login — provide a local-only fixture.

---

### F7. PostgreSQL failure silently falls back to a different database

- **Severity:** High (integrity / availability; split-brain)  
- **Location:** `backend/database.py:68-79`

```68:79:backend/database.py
        except Exception as e:
            print(f"[DB Warning] Could not connect to PostgreSQL ({e}). Falling back to SQLite.")

    # SQLite fallback for local development
    conn = sqlite3.connect(DATABASE_PATH)
```

If `DATABASE_URL` is set and the network blips, **writes go to a local SQLite file** that Gunicorn workers do not share and that Render’s ephemeral disk will throw away. Exception text is printed and can include the connection URI (password).

**Remediation:** If `DATABASE_URL` is set, fail hard. Never fall back. Do not print the exception containing the URI; log `type(e).__name__` only.

**Effort:** S **Regression risk:** Dev setups that relied on a broken `DATABASE_URL` accidentally using SQLite will now crash — that is the point.

---

### F8. Unhandled DB errors leak connections and lock SQLite; delete is broken

- **Severity:** Medium (availability) — raised because a single failed DELETE wedged the DB  
- **Location:** `backend/services.py:237-244` and every `get_db_connection()` caller; `payments` FK has no `ON DELETE`

```237:244:backend/services.py
def delete_shipment(shipment_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shipments WHERE shipment_id = ?", (shipment_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
```

`payments.shipment_id` references `shipments` **without** `ON DELETE CASCADE` (unlike `deliveries`). Live: `DELETE /api/shipments/SHP003` as Admin → `IntegrityError: FOREIGN KEY constraint failed` → connection **not closed**. Immediate next DELETE → `OperationalError: database is locked` (observed).

No `try/finally`, no connection pool, no Flask `g`/teardown. Invalid `quantity: "not-a-number"` similarly 500s on `int(...)`.

**Remediation:** Context manager; `teardown_appcontext`; delete children first or add `ON DELETE CASCADE` **after** deciding retention policy; convert `IntegrityError`/`ValueError` to 400. Set `app.config['MAX_CONTENT_LENGTH'] = 64 * 1024`.

**Effort:** M **Regression risk:** Clients that treated 500 as “deleted” will see 409/400. Good.

---

### F9. Remaining mass-assignment and missing server-side bounds

- **Severity:** Medium  
- **Location:** `backend/services.py:277-293` (`create_vehicle`), `355-370` (`create_driver`); `create_shipment` weight/quantity

Semgrep rule `apexflow-mass-assignment-status` flags `data.get('status'` in `services.py` — and these calls still exist. Live vehicle create with `status=On Trip` succeeded. Weight `-1` succeeded. `quantity: "not-a-number"` → 500.

**Remediation:** Server-set `status='Available'` on create. `weight_kg` in `(0, 100_000]`. Catch `ValueError`. Expand the Semgrep rule to `create_vehicle`/`create_driver` **or actually run it in CI** — `.pre-commit-config.yaml` is not a GitHub Action.

**Effort:** S **Regression risk:** UI that posts `status` on create will be ignored.

---

### F10. Session lifecycle, cookies, and missing edge defenses

- **Severity:** Medium (combined)  
- **Location:** `backend/app.py:28-32`; `backend/config.py`; Flask defaults

What is good: `session.clear()` before login (fixation), `HttpOnly`, `SameSite=Lax`, `SECRET_KEY` required in production, 8h `PERMANENT_SESSION_LIFETIME`.

What is not:

- Flask default `SESSION_REFRESH_EACH_REQUEST=True` ⇒ 8h **sliding**. An active stolen cookie never dies.
- Role is not re-checked against the DB.
- No server-side session store ⇒ logout is local.
- `SESSION_COOKIE_SECURE=not DEBUG`. `DEBUG = (FLASK_ENV != 'production')`. `HOST` defaults to `0.0.0.0`. Documented start is `python backend/app.py`. If `FLASK_ENV` is unset on a VPS, you get the Werkzeug debugger on all interfaces. Gunicorn (Procfile) does not enable that debugger — this is a **run-mode** footgun, not the Render path.
- No `MAX_CONTENT_LENGTH` (`None` observed).
- No security headers (`CSP`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `HSTS`).
- Rate-limit key is `request.remote_addr` with no `ProxyFix` — behind Render, every client may share one IP (**NEEDS VERIFICATION** on a live Render deploy: confirm `X-Forwarded-For` handling). Do not blindly trust `X-Forwarded-For` from the client.

**Effort:** S–M **Regression risk:** Setting `Secure` in production requires HTTPS (Render provides it). Idle-timeout will log users out of long-lived tabs.

---

### F11. Dependency and deploy hygiene

- **Severity:** Medium  
- **Location:** `requirements.txt`; `Procfile`; `.semgrep.yml`; `.pre-commit-config.yaml`

```
Flask>=2.3.0
flask-cors>=4.0.0
flask-limiter>=3.5.0
...
```

Unpinned ranges. This audit ran against **Flask 3.1.3 / flask-limiter 4.1.1** pulled today; F3’s decorator behavior is version-sensitive. No CI, no lockfile, no `pip-audit`/`osv-scanner`.

Semgrep `apexflow-unauthenticated-route` `pattern-not` uses `"/api/auth/login"`; the code has `@api_bp.route('/auth/login')`. The rule will not do what the comment says. Pre-commit is optional and not enforced.

Root junk (`html.h`, `index.html`, `HImanshu`, `data/*.json`) increases attack surface for “what did we accidentally serve?” review, even though Flask’s `send_from_directory` blocked `../backend/config.py` (404 observed).

`settings.html` embeds `AIzaSyA_APEX_FLOW_DEMO_KEY_2026` in a password field. Looks fake (`APEX_FLOW_DEMO`); **NEEDS VERIFICATION:** search Google Cloud for that key. Remove it regardless.

**Remediation:** Pin hashes (`pip-compile`); add CI: `pip-audit`, Semgrep with **fixed** patterns, gitleaks. Delete leftover files. Fail the build if `login.html` contains `Admin@123`.

**Effort:** S

---

### F12. OTP comparison is not constant-time; new OTPs have no delivery channel

- **Severity:** Low (OTP) / Medium (product-security design)  
- **Location:** `backend/services.py:460`

`d['otp_code'] != str(otp_entered).strip()` plus 5 attempts on a 6-digit space is not practically brute-forceable **if** F3 is fixed and the OTP is not in git. Still use `hmac.compare_digest`. More importantly: after F-05 removed `otp_code` from `GET /api/deliveries`, **nobody can complete a newly created delivery** through the intended UI (`undefined` in the OTP column). Security control vs. product mismatch.

**Effort:** S to hash/compare; M to actually deliver OTPs.

---

## 4. What is already in good shape (do not regress)

These are real controls. Keep them while fixing F1–F4.

- Passwords hashed with `werkzeug.security.generate_password_hash` / `check_password_hash`; hash stripped before JSON (`backend/auth.py:81`).
- Parameterized queries (`?` / wrapper `%s`) on every user-data path traced. No string-concat SQL in handlers. (Migration `ALTER TABLE {table}` uses constants only.)
- Shipment `status`, `payment_status`, `shipping_cost` are **not** taken from the client on create (`_SHIPMENT_ALLOWED_FIELDS`).
- `updated_by` comes from the session, not the body.
- New IDs are `uuid4` (`SHP-a3f2b1c0`), not sequential (seed rows still are).
- `otp_code` is excluded from `GET /api/deliveries` (the frontend just was not updated).
- `SECRET_KEY` and `SEED_*_PASSWORD` required when `FLASK_ENV=production`.
- Session fixation: `session.clear()` before setting a new session.
- Cookie `HttpOnly` + `SameSite=Lax`. CORS allowlist, not `*`.
- Login API message does not distinguish unknown user vs bad password (logs still do — fine).
- `send_from_directory` rejected path traversal in test.

Do **not** treat the above as a green light. They are why this looks “almost production” in review.

---

## 5. Prioritized remediation backlog

Sorted by **severity × exploitability × effort** (do first = highest).

| # | Finding | Sev | Exploitability now | Effort | Sprint note |
|---|---|---|---|---|---|
| 1 | F1 object-level authz + query scoping | Critical | Trivial with any cookie | L | Blocker. Write tests per role × resource. |
| 2 | F2 status machine + OTP consume + stop auto-Paid | Critical | Trivial (Driver PUT; Customer OTP 4912) | M | Blocker. |
| 3 | F3 actually attach limiter; Redis in prod | High | 15/15 logins unthrottled | S | Do in the same PR as F6. |
| 4 | F6 remove demo credentials from shipped UI | High | Click ADMIN | S | |
| 5 | F5 map `user_id` ↔ `customer_id`/`driver_id` | High | Breaks tenancy + F1 fix | M | Prerequisite for F1 tests. |
| 6 | F4 HTML-encode all renders; kill innerHTML | High | Stored XSS as Driver | M | |
| 7 | F7 fail closed on Postgres errors | High | Needs DB outage | S | |
| 8 | F8 connection context manager + FK-safe delete | Medium | One failed DELETE locks DB | M | |
| 9 | F9 mass-assignment leftovers + bounds | Medium | Live `status=On Trip` | S | |
| 10 | F10 Secure cookie / idle timeout / headers / `MAX_CONTENT_LENGTH` | Medium | Defense in depth | S | |
| 11 | F11 pin deps, CI audit, delete leftovers, fix Semgrep | Medium | Supply chain / theater | S | |
| 12 | F12 hash OTPs, `compare_digest`, out-of-band delivery | Low/Med | After F2 | M | |

**Suggested two-sprint plan**

- **Sprint 1 (go/no-go blockers):** #1–#7. No production traffic until an integration test file proves Customer cannot read SHP001, Driver cannot `PUT Delivered` on an unassigned shipment, and 11th login fails with 429.
- **Sprint 2:** #8–#12, CSP, server-side sessions (Redis), password change, lockout.

---

## 6. Systemic recommendations

1. **Authorization is a layer, not a decorator.** Today `require_role` is copied per route and object checks exist on one GET. Next bug is guaranteed the first time someone adds `GET /api/invoices/<id>`. Put checks in `services.py` (or a domain policy object) so a missing decorator cannot leak data.
2. **Identity model first.** `users` ⟂ `customers` ⟂ `drivers` with FKs. Until `USR004` is bound to `CUST001`, every “own records” filter is wrong.
3. **State machines for money and custody.** Shipment status and payment status must not be writable as free strings. Table-driven transitions; payments never change as a side effect of GPS/status.
4. **One DB connection protocol.** `with get_db() as conn:` + `teardown_appcontext`. Ban ad-hoc `get_db_connection()` at review.
5. **Stop dual-DB string replace.** The `query.replace('?', '%s')` wrapper is a future SQL-injection/semantic landmine. Pick Postgres in prod, SQLite in tests, via a real driver (`SQLAlchemy` or `psycopg` + a test container).
6. **Frontend encoding helper + CSP.** Ban `innerHTML` with a Semgrep/ESLint rule. Convert inline handlers.
7. **Threat model the demo.** If the product needs a public demo, give it a disposable dataset and a non-admin role. Never the same build as production.

Least-privilege debt: one DB user for DDL+DML+seed (acceptable for a first service, **not** if you later add reporting replicas or a worker). More urgent is the **application** role model, which currently treats Customer ≈ Driver ≈ Manager for reads.

---

## 7. Guardrails so AI-assisted PRs cannot resurrect this

CI (required checks):

- `gitleaks` (already in `.pre-commit-config.yaml` — **run it in CI**, not only on laptops).
- Semgrep: fix the route pattern (`'/auth/login'` not `"/api/auth/login"`); add rules:
  - `innerHTML` in `frontend/js`
  - `data.get('status'` / `data.get('payment_status'` / `data.get('shipping_cost'` in services
  - `get_db_connection()` without `close`/`finally` (heuristic)
  - `@api_bp.route` not followed by `@login_required` or `@require_role` (except login/logout)
- `pip-audit` on a **pinned** `requirements.txt`.
- Pytest matrix (this repo has **zero tests**):

```text
test_customer_cannot_get_foreign_shipment
test_driver_cannot_mark_unassigned_delivered
test_customer_cannot_confirm_foreign_otp
test_login_rate_limited_11th
test_otp_not_in_deliveries_payload
test_create_shipment_ignores_client_payment_status
test_demo_passwords_rejected_when_FLASK_ENV_production
```

PR checklist (AI-assisted contributions):

- [ ] New endpoint: who is the owner column, and is it in the `WHERE`?
- [ ] New status/money field: is it computed server-side?
- [ ] New HTML render: `textContent` only?
- [ ] Secrets: env-only, no fallback default in production?
- [ ] Rate limit decorator assigned to the view that Flask will actually call?

Do not add more `F-xx` comments. Add tests named after the bug.

---

## 8. Go / no-go criteria

| Gate | Status now |
|---|---|
| Unauthenticated attacker cannot obtain an admin session with documented secrets + unlimited guesses | **FAIL** (F3, F6) |
| Customer cannot read or mutate another customer’s shipment, tracking, invoice, or PII | **FAIL** (F1, F2, F5) |
| Driver cannot mark arbitrary invoices Paid | **FAIL** (F2) |
| OTPs are unguessable, expiring, single-use, not in git | **FAIL** (F2) |
| XSS in user fields cannot run as admin | **FAIL** (F4) |
| DB outage cannot silently write to another engine | **FAIL** (F7) |
| Parameterized SQL, hashed passwords, HttpOnly session | PASS |

**Production decision: NO-GO.**

After Sprint 1, run this audit’s live script again (Customer dashboard revenue must be own-only or 403; Driver `PUT Delivered` on SHP002 must be 403; 11th login 429; seed OTP 4912 rejected or rotated). Only then consider a limited internal beta — still not a public Render URL with real shippers.
