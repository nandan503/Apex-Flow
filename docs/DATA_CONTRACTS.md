# Data, replay and external effects

## PostgreSQL unit of work

Every protected endpoint (except authentication lifecycle and /me) is wrapped in a
tenant unit of work. Business services require a Caller and reuse the transaction
through a ContextVar. Reads, state checks, writes, response serialization, durable
replay result and audit insertion occur **before a single commit**. Startup never
creates, alters, seeds, links identities or rewrites records.

Initial concurrency control is a PostgreSQL transaction-scoped advisory lock keyed
by tenant (64-bit server hash). All HTTP tenant operations and direct tenant service
transactions acquire it. Hash collisions only over-serialize; RLS remains independent.
This prevents stale transitions, double confirmation, lost OTP attempt increments
and competing duplicate creates across workers/providers. It deliberately serializes
reads too. READ COMMITTED is sufficient **for participating application transactions**;
manual privileged maintenance must quiesce writers or take the same lock. Statement
and lock timeouts bound waits. No automatic retry of a partially executed transaction.

This is not the desired ultimate throughput architecture. Finer resource locks and
repeatable-read report snapshots must replace it only with concurrency tests and
observed capacity requirements. A direct multi-service batch/CLI caller must use an
outer db_session; independently invoked services are independent operations, not an
implicit distributed transaction or idempotent job API.

## Domain invariants

- Membership unique by tenant/user; checked role-to-owner link shapes and composite
  tenant/customer or tenant/driver foreign keys.
- All business relationships use composite tenant foreign keys. Some fleet cycles
  are deferrable; the HTTP wrapper forces constraints before saving a replay result.
- Shipment creation creates a delivery, unique invoice, initial history, tenant-admin
  notification and encrypted OTP outbox event in one transaction. Client status,
  price, paid flag, assigned driver and tenant fields are not authoritative.
- Status transitions use the existing explicit graph. Terminal assignment is rejected.
  Custody Delivered is assigned-driver + verified-OTP only, with a private internal
  capability for the status service. Delivery never marks an invoice paid.
- Wrong OTP increments are committed up to five, not rolled back as generic validation.
  Exact request replays do not consume another attempt. OTP expiry uses UTC.
- Collection records a pending invoice as paid and updates its shipment. It is **not
  a gateway charge**. Already-paid invoices keep the original paid date. Future
  real payment gateways need a separate provider operation key and outbox/reconciliation.
- NUMERIC storage and Decimal arithmetic replace binary floating point currency;
  API decimal values are strings, including monetary zero. Weight is normalized to
  0.01 kg with ROUND_HALF_UP before pricing, matching its stored precision. Current pricing is a deterministic heuristic,
  not an approved tariff/routing contract. Legacy aggregate customer/fleet counters
  are not automatically maintained and must not be treated as accounting truth.
- Notification visibility is recipient-specific for normal users; marking read is
  per principal, not a global flag for an entire role.
- Audit events have random UUIDs, tenant, actor, operation, requested resource selector,
  response status and request key. Runtime has SELECT/INSERT only. The business history
  table can be deleted with a shipment; the audit record survives that deletion.

## HTTP idempotency

All protected POST/PUT/PATCH/DELETE operations require `Idempotency-Key`, including
read-only route optimization for a uniform client contract. No PATCH endpoint
currently exists. Login uses session rotation, logout revokes a handle, and neither
is in the business mutation replay namespace.

Scope: **tenant + authenticated principal + key**, unique in PostgreSQL. The hash
binds method, exact path, query bytes and body bytes; JSON with different whitespace
is a different request. HMAC avoids making the short OTP space cheaply enumerable
from a stolen replay table. Replay keyring is independent of the cookie-signing key.

| Situation | Behavior |
|---|---|
| New request | Insert PENDING under tenant lock; execute inside savepoint |
| Concurrent same key | Wait for lock, then replay committed result; timeout = retryable 503 |
| Completed 2xx | Save exact JSON response/status, COMPLETED, and audit in business commit |
| Handled validation/authorization/constraint error | Roll back business savepoint; save deterministic FAILED 4xx and audit |
| Invalid OTP response | Preserve attempt increment, save FAILED response; retry same key does not increment again |
| Same key, different bytes/endpoint | 409 IDEMPOTENCY_CONFLICT, no mutation |
| Worker death before commit | PostgreSQL rolls back business, PENDING, audit and outbox together |
| DB/connection failure or 5xx | Do not commit response as success; 503 for DB failures; retry exact request/key |
| Commit succeeds, response lost, other provider retries | Same stored response, no second logical operation |
| Persisted PENDING (manual/future asynchronous path) | 409 IDEMPOTENCY_PENDING; never steal or run it twice blindly |
| Membership/role/owner links changed since original | Reject replay with 403 rather than restoring old authorization |
| Driver reassigned away from resource | Recheck resource visibility before replay; deny inaccessible resource |

No PENDING lease timeout is required for synchronous requests: PENDING never commits
on the normal path. FAILED is terminal **for that key**. Correcting invalid input
requires a new logical operation/key. New key after an ambiguous response is unsafe.
Records are not automatically deleted; expiry would remove the dedup guarantee.
Retention/pruning must explicitly define a replay window and tombstone policy first.

A response replay is historical, not a fresh representation of the resource. Headers
are regenerated (security headers/request ID); only JSON bytes and HTTP status replay.
Authentication, membership and CSRF checks always happen again before replay.

The browser stores only a request digest + random key in sessionStorage across
ambiguous failures, never bodies/OTPs. Confirmed responses clear that pending intent.
This protects retries, not an intentional second submission after successful completion.

## Transactional outbox

Existing real external side effects: none in the baseline. New `delivery.otp` events
are now durably recorded; they are **not** claimed to be delivered until the configured
consumer accepts them. In-app notifications already reside in the business transaction.
Browser exports do not involve a backend external side effect. No object upload,
email/SMS SDK, payment gateway or webhook integration was present to preserve.

Encrypted payload: tenant, shipment, receiver email, OTP, UTC expiry. Business verification
stores only an scrypt OTP hash. Consumer credentials/payload are never logged or returned
by the API. Operators supply one shared Fernet key outside database backups.

`deliver_one(caller)` revalidates an ADMIN membership, claims a due event under the
same tenant transaction plus FOR UPDATE SKIP LOCKED, sends HTTPS with a five-second
timeout and stable event UUID in `Idempotency-Key`, and commits COMPLETED on 2xx.
No redirects (prevents forwarding authorization credentials to a redirected origin).
Failures use capped exponential retry delay; ten failures or expired OTP → FAILED.
FAILED is a dead-letter state requiring reviewed operator action, not silent deletion.
Outbox attempt/result audit is in the same transaction.

**At least once, not exactly once:** a consumer may act then lose its response, or
PostgreSQL may fail after it acts. The worker will retry the same event ID. The
consumer MUST persist its dedup result and downstream operation identity. A 2xx
means durable acceptance by that consumer, not proof the receiver read an email.
If an email/SMS provider lacks idempotency, duplicate visible messages remain possible;
this repository cannot promise otherwise. Tests model a deduplicating consumer and
lost acknowledgement; no real provider has been certified.

No independent lease or polling daemon is hidden in Flask. Schedule bounded CLI jobs
outside the application. A killed worker loses its transaction; the event is available
again. Holding a tenant lock during a network call is a conscious baseline trade-off,
not a scalable queue architecture. Backpressure, queue-age alerts and OTP reissuance
are explicit outstanding operational/product work.

## Retrieval and export bounds (F-3, Sprint 3)

Every externally reachable tenant-wide retrieval carries a server-side hard cap that client
input can never raise. Caps are constants enforced in the SQL `LIMIT` clause (PostgreSQL plans
show a Limit node, so the server never materializes a whole oversized tenant result):

| Path | Bound |
|---|---|
| `GET /api/shipments` | validated pagination: limit ≤ 200, offset ≤ 10 000 |
| `vehicles`, `drivers`, `customers`, `warehouses`, `routes`, `deliveries`, `payments`, `notifications` lists | `LIST_HARD_CAP` = 2000 rows |
| `GET /api/reports/{type}` (shipments, revenue, fleet, drivers, general) | `REPORT_HARD_CAP` = 2000 rows, stable ORDER BY |
| `GET /api/tracking` | SQL-level role scoping plus `TRACKING_HARD_CAP` = 500 active shipments |
| shipment status history | newest `HISTORY_HARD_CAP` = 1000 rows, deterministic `(timestamp, history_id)` order, returned oldest-first |
| dashboard aggregates | bounded cardinalities (COUNT/GROUP BY over 10 statuses; recent lists LIMIT 5) |
| browser CSV export | serializes the same capped JSON payload — exports are bounded by the report cap |

This is a bounded-by-construction contract (row count × validated field lengths) with the
10 s statement timeout as a backstop — it is not a hard per-request memory guarantee. Deeper
cursor pagination and streaming export jobs remain future work; tenants whose datasets exceed
the caps must not onboard until the bounded-export-job release gate is closed.
