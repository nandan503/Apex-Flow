# APEX FLOW — Full API Reference

Base URL: `https://your-app.onrender.com/api`  
Authentication: Session cookie (`session` — set on login, sent automatically by the browser)

---

## Table of Contents

- [Authentication](#authentication)
- [Shipments](#shipments)
- [Vehicles](#vehicles)
- [Drivers](#drivers)
- [Customers](#customers)
- [Warehouses](#warehouses)
- [Routes & Optimization](#routes--optimization)
- [Live Tracking](#live-tracking)
- [Deliveries & OTP](#deliveries--otp)
- [Payments](#payments)
- [Reports & KPIs](#reports--kpis)
- [Notifications](#notifications)
- [Error Responses](#error-responses)
- [Rate Limits](#rate-limits)

---

## Standard Response Format

All successful responses follow this envelope:

```json
{
  "success": true,
  "message": "Human-readable message",
  "data": { ... }
}
```

All error responses:

```json
{
  "success": false,
  "message": "Error description",
  "error": "ERROR_CODE"
}
```

---

## Authentication

### POST `/api/auth/login`

Login with email and password. Sets a session cookie.

**Rate limit:** 10 requests/minute per IP.

**Request body:**
```json
{
  "email": "admin@apexflow.com",
  "password": "your_password"
}
```

**Success response (200):**
```json
{
  "success": true,
  "message": "Login successful",
  "data": {
    "user_id": "USR-a1b2c3d4",
    "name": "Admin User",
    "email": "admin@apexflow.com",
    "role": "ADMIN",
    "phone": "+91-9876543210"
  }
}
```

**Error responses:**
- `400` — Email or password missing
- `401` — Invalid credentials

---

### POST `/api/auth/logout`

Clears the server-side session.

**Auth:** Session required  
**Request body:** None

**Success (200):**
```json
{ "success": true, "message": "Logged out successfully" }
```

---

### GET `/api/auth/me`

Returns the currently authenticated user's profile.

**Auth:** Session required

**Success (200):**
```json
{
  "success": true,
  "data": {
    "user_id": "USR-a1b2c3d4",
    "name": "Admin User",
    "email": "admin@apexflow.com",
    "role": "ADMIN",
    "phone": "+91-9876543210",
    "created_at": "2026-09-07T00:00:00"
  }
}
```

---

## Shipments

### GET `/api/shipments`

List shipments. CUSTOMER role returns only their own shipments (IDOR protection).

**Auth:** Session required  
**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `status` | string | Filter by status: `Booked`, `Confirmed`, `Picked Up`, `At Warehouse`, `In Transit`, `Out for Delivery`, `Delivered`, `Delayed`, `Cancelled`, `Returned` |
| `search` | string | Search across shipment_id, customer_name, pickup, destination, vehicle_reg, driver_name |

**Success (200):**
```json
{
  "success": true,
  "data": [
    {
      "shipment_id": "SHP-a1b2c3d4",
      "customer_name": "Acme Corp",
      "pickup_location": "Delhi",
      "destination": "Jaipur",
      "goods_type": "Electronics",
      "weight_kg": 120.5,
      "quantity": 10,
      "status": "In Transit",
      "booking_date": "2026-09-01",
      "expected_delivery": "2026-09-05",
      "shipping_cost": 8500.0,
      "payment_method": "Credit Card",
      "payment_status": "Paid",
      "driver_name": "Rajesh Kumar",
      "vehicle_reg": "HR26BX4587",
      "created_at": "2026-09-01T10:00:00"
    }
  ]
}
```

---

### GET `/api/shipments/:shipment_id`

Get a single shipment including its full status history.

**Auth:** Session required  
**CUSTOMER role:** Only permitted to access their own shipments.

**Success (200):**
```json
{
  "success": true,
  "data": {
    "shipment_id": "SHP-a1b2c3d4",
    "...": "...",
    "history": [
      {
        "history_id": 1,
        "status": "Booked",
        "timestamp": "2026-09-01T10:00:00",
        "location": "Delhi Hub",
        "updated_by": "ADMIN",
        "notes": "Booking confirmed"
      }
    ]
  }
}
```

**Error responses:**
- `403` — Customer accessing another customer's shipment
- `404` — Shipment not found

---

### POST `/api/shipments`

Create a new shipment booking.

**Auth:** Session required

**Request body:**
```json
{
  "customer_name": "Acme Corp",
  "pickup_location": "Delhi",
  "destination": "Jaipur",
  "goods_type": "Electronics",
  "description": "Fragile electronic components",
  "weight_kg": 120.5,
  "quantity": 10,
  "payment_method": "Credit Card",
  "special_instructions": "Handle with care",
  "booking_date": "2026-09-01",
  "expected_delivery": "2026-09-05"
}
```

> Fields outside the allowlist are silently dropped (mass-assignment protection).

**Success (201):**
```json
{
  "success": true,
  "message": "Shipment created successfully",
  "shipment_id": "SHP-a1b2c3d4",
  "data": { "...": "..." }
}
```

---

### PUT `/api/shipments/:shipment_id/status`

Update a shipment's operational status. `updated_by` is always sourced from the server-side session, never the request body.

**Auth:** ADMIN, MANAGER, or DRIVER

**Request body:**
```json
{
  "status": "In Transit",
  "location": "Gurugram Toll Plaza",
  "notes": "Cleared customs checkpoint"
}
```

**Valid status values:** `Booked` → `Confirmed` → `Picked Up` → `At Warehouse` → `In Transit` → `Out for Delivery` → `Delivered` | `Delayed` | `Cancelled` | `Returned`

---

### DELETE `/api/shipments/:shipment_id`

Permanently delete a shipment.

**Auth:** ADMIN only  
**Audit:** Deletion is logged with user ID, role, and IP address.

**Success (200):**
```json
{ "success": true, "message": "Shipment SHP-a1b2c3d4 deleted successfully" }
```

---

## Vehicles

### GET `/api/vehicles`

List all vehicles.

**Auth:** Session required

**Response data fields:**
`vehicle_id`, `registration_number`, `vehicle_type`, `make`, `model`, `year`, `capacity_mt`, `fuel_type`, `current_location`, `status` (`Available` | `On Trip` | `Maintenance` | `Inactive`), `insurance_expiry`, `permit_expiry`, `fitness_expiry`, `service_due_date`

---

### POST `/api/vehicles`

Add a new vehicle to the fleet.

**Auth:** ADMIN or MANAGER

**Request body:**
```json
{
  "registration_number": "HR26BX4587",
  "vehicle_type": "Truck",
  "make": "Tata",
  "model": "Prima 4028",
  "year": 2022,
  "capacity_mt": 25.0,
  "fuel_type": "Diesel",
  "current_location": "Delhi Hub",
  "status": "Available",
  "insurance_expiry": "2027-03-31",
  "permit_expiry": "2027-06-30",
  "fitness_expiry": "2027-01-15",
  "service_due_date": "2026-12-01"
}
```

---

## Drivers

### GET `/api/drivers`

List all drivers.

**Auth:** Session required

**Response data fields:**
`driver_id`, `name`, `phone`, `email`, `license_number`, `license_expiry`, `experience_years`, `status` (`Available` | `On Trip` | `Off Duty` | `Suspended`), `total_trips`, `completed_trips`, `rating`

---

### POST `/api/drivers`

Add a new driver.

**Auth:** ADMIN or MANAGER

**Required fields:** `name`, `license_number`

```json
{
  "name": "Rajesh Kumar",
  "phone": "+91-9876543210",
  "email": "rajesh@example.com",
  "license_number": "DL-0420110012345",
  "license_expiry": "2028-05-20",
  "address": "123 Main St, Delhi",
  "experience_years": 8,
  "status": "Available"
}
```

---

## Customers

### GET `/api/customers`

List all customers.

**Auth:** ADMIN or MANAGER only

---

### POST `/api/customers`

Create a new customer account.

**Auth:** ADMIN or MANAGER  
**Required fields:** `name`, `company`

```json
{
  "name": "Priya Sharma",
  "company": "Acme Corp",
  "phone": "+91-9988776655",
  "email": "priya@acme.com",
  "address": "456 Business Park, Mumbai"
}
```

---

## Warehouses

### GET `/api/warehouses`

List all warehouse locations.

**Auth:** Session required

---

## Routes & Optimization

### GET `/api/routes`

List all pre-defined routes from the database.

**Auth:** Session required

---

### POST `/api/routes/optimize`

Calculate an optimized route between two locations.

**Auth:** Session required

**Request body:**
```json
{
  "pickup": "Delhi",
  "destination": "Jaipur"
}
```

**Success (200):**
```json
{
  "success": true,
  "message": "Optimized route calculated!",
  "data": {
    "route_id": "RTE_OPT_428",
    "pickup": "Delhi",
    "destination": "Jaipur",
    "distance_km": 281,
    "estimated_time": "4h 19m",
    "fuel_cost_est": 2416,
    "recommended_route": "Via National Highway Corridor (Delhi-Jaipur Expressway)",
    "stops": [
      { "stop": "Delhi Exit Toll Plaza", "eta": "30m" },
      { "stop": "Midway Logistics Rest Stop", "eta": "2h 15m" },
      { "stop": "Jaipur City Hub", "eta": "4h 19m" }
    ]
  }
}
```

---

## Live Tracking

### GET `/api/tracking`

Returns real-time GPS position data for all active shipments (status: In Transit, Picked Up, Out for Delivery).

**Auth:** Session required

**Response data fields per vehicle:**
`shipment_id`, `vehicle_id`, `vehicle_reg`, `driver_name`, `pickup`, `destination`, `latitude`, `longitude`, `speed_kmh`, `distance_remaining_km`, `eta`, `progress_percent`, `status`

---

### GET `/api/tracking/:vehicle_id`

Get tracking data for a specific vehicle.

**Auth:** Session required  
**404** if vehicle not found in active tracking.

---

## Deliveries & OTP

### GET `/api/deliveries`

List all delivery records.

**Auth:** Session required

---

### POST `/api/deliveries/confirm`

Confirm a delivery using a 6-digit OTP code.

**Rate limit:** 5 requests/minute per IP (brute-force protection)  
**Auth:** Session required

**Request body:**
```json
{
  "shipment_id": "SHP-a1b2c3d4",
  "otp_code": "834921",
  "receiver_name": "Priya Sharma"
}
```

**Security properties:**
- OTP is 6 digits, cryptographically secure (CSPRNG)
- Expires after **30 minutes**
- Locked after **5 failed attempts**
- Delivery confirmation is audit-logged

**Success (200):**
```json
{ "success": true, "message": "Delivery confirmed successfully" }
```

**Error (400):**
```json
{ "success": false, "message": "Invalid OTP code" }
```
or
```json
{ "success": false, "message": "OTP locked — too many failed attempts" }
```

---

## Payments

### GET `/api/payments`

List all payment/invoice records.

**Auth:** ADMIN or MANAGER only

**Response data fields:**
`invoice_id`, `shipment_id`, `customer_name`, `amount`, `tax_amount`, `total_amount`, `payment_status`, `invoice_date`

---

## Reports & KPIs

### GET `/api/reports/dashboard`

Returns aggregated KPIs for the admin dashboard.

**Auth:** Session required

**Response (200):**
```json
{
  "success": true,
  "data": {
    "total_shipments": 142,
    "booked": 12,
    "confirmed": 8,
    "picked_up": 5,
    "in_transit": 23,
    "delivered": 88,
    "delayed": 4,
    "cancelled": 2,
    "available_vehicles": 6,
    "total_vehicles": 10,
    "active_drivers": 8,
    "total_drivers": 12,
    "total_revenue": 1250000.0,
    "estimated_fuel_cost": 87500.0,
    "recent_shipments": [ { "...": "..." } ],
    "recent_vehicles": [ { "...": "..." } ]
  }
}
```

---

### GET `/api/reports/:report_type`

Generate a detailed analytics report.

**Auth:** ADMIN or MANAGER only  
**Valid `report_type` values:**

| report_type | Returns |
|---|---|
| `shipments` | All shipments with booking date and cost |
| `revenue` | All payment invoices |
| `fleet` | Vehicle fleet status and compliance |
| `drivers` | Driver performance ranked by rating |
| `general` (default) | All shipments |

---

## Notifications

### GET `/api/notifications`

List all system notifications.

**Auth:** Session required

---

### PUT `/api/notifications/:notif_id/read`

Mark a notification as read.

**Auth:** Session required  
**Success (200):**
```json
{ "success": true, "message": "Notification marked as read" }
```

---

## Error Responses

| HTTP Status | Meaning |
|---|---|
| `400` | Bad Request — missing or invalid parameters |
| `401` | Unauthorized — no valid session |
| `403` | Forbidden — insufficient role |
| `404` | Not Found — resource does not exist |
| `429` | Too Many Requests — rate limit exceeded |
| `500` | Internal Server Error |

---

## Rate Limits

| Endpoint | Limit |
|---|---|
| All endpoints (global) | 200 requests/minute |
| `POST /api/auth/login` | 10 requests/minute |
| `POST /api/deliveries/confirm` | 5 requests/minute |

Rate limit headers returned on every response:
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`

When rate-limited (`429`):
```json
{
  "success": false,
  "message": "Too many requests. Please slow down.",
  "error": "RATE_LIMITED"
}
```
