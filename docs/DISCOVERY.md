# System discovery — baseline 861e8b1, 2026-09-07

This describes inspected code BEFORE reengineering, not security guarantees.

## Actual inventory
- `backend/app.py`: factory **and import-time app creation**; static HTML/JS (no Jinja templates); one `/api` blueprint. Factory mutates environment and calls initialize_database.
- `config.py`: import-time config, ephemeral development signing key, optional DATABASE_URL with local SQLite fallback, CORS origin list, ProxyFix opt-in, Redis URI used only by Flask-Limiter.
- `database.py`: hand-written SQLite DDL translated with string replacement for PostgreSQL; boot-time CREATE/ALTER (swallowed exceptions), seeding, identity linking by known email, OTP invalidation. No versioned migrations. Global users with role/customer/driver links. No tenant column anywhere.
- `auth.py`: signed cookie stores user_id/role, DB reload per endpoint. Logout only clears cookie: copied cookies remain valid. No password reset/account-management routes. Werkzeug password hashing. Origin check allows missing Origin; no CSRF token.
- `authz.py`: service policies for four roles, customer ownership and assigned-driver access. ADMIN is global; no separate system administrator or membership.
- `services.py`: raw parameterized SQL, each db_session opens/commits/closes independently; creation followed by a second transaction to read response. UUIDs truncated to 32 bits. Floating point currency. Validation misses NaN. No idempotency records or durable audit.
- `reports.py`: raw aggregate/list SQL, staff has global visibility. Browser `reports.js` downloads the fetched JSON/CSV via Blob; no server-side export job or stored file.
- `tracking.py`: time-based **synthetic** coordinates/speed, not a GPS integration. Route optimization is a deterministic heuristic, not a routing provider.
- `logger.py`: structured event strings with partial PII redaction, stdout; some success/delete events logged before commit. Not authoritative audit.
- `limiter.py`: optional Redis, otherwise per-process memory; security limits reset/disagree across providers. Redis stores no business data.
- No background worker, scheduler, CLI provisioning, upload/download API, object-storage adapter, email/SMS/webhook/payment gateway, Dockerfile, or file persistence beyond SQLite found. `signature_data` is a DB text field. JSON files in data/ are unused examples, not authoritative inputs.
- `Procfile`: Gunicorn imports app. CI: Python 3.11, SQLite pytest plus one UI-password grep. Dependencies exactly pinned (Flask 3.1.3, Werkzeug 3.1.8, psycopg2 2.9.10 etc.); exact pins are not evidence of a vulnerability scan. Existing SECURITY_AUDIT overstates PostgreSQL/production assurances.

## End-to-end operation/failure traces
Common entry: browser fetch('/api/...') with cookie → route decorator reloads global user → service role/ownership policy → db_session. No tenant is ever established. No file/job/bulk/import endpoint exists; unsupported paths must remain unavailable, not silently become privileged features.

| Operation | Input, authorization, reads and writes | Transaction / repetition / crash / malicious input |
|---|---|---|
| Login/logout | JSON email/password → user hash lookup; cookie cleared/recreated. Logout clears browser cookie only | Read-only login; per-process brute-force control; copied cookie survives logout. Retry reissues cookie. DB failure returns generic 500. No tenant selector/membership. |
| Shipment creation | JSON allowlist → staff or customer; customer chosen by own identity or staff selector/name → customers read; shipment+history+delivery+invoice+notification insert | Inserts atomic in one connection; response read is another transaction. OTP hash stored, plaintext **discarded**. Lost response / worker death after commit / another provider retry creates another shipment. Client financial/status fields ignored, but no tenant ownership constraints. |
| Assignment/status | Staff selects shipment/driver/vehicle; driver status limited by assignment and transition graph | Check-then-update without locking; races can accept stale transitions. History inserted atomically with update. Retrying transition generally errors; assignment may overwrite. Any staff can select globally. |
| Delivery confirm | Assigned driver + shipment selector + OTP → delivery hash/attempt/expiry read → attempt or custody update + history | Atomic writes but no read lock; concurrent bad attempts can lose increments. Success log before commit; replay errors; response loss ambiguous. Staff cannot bypass OTP via route. |
| Collect payment | Staff, invoice selector → invoice read → payment and shipment marked Paid | No external charge. Repeated collection changes paid date, no request identity. Two-row transaction, no audit event. |
| Fleet/customer create | Staff JSON fields → insert + subsequent response read | Unique constraints exist globally; repeated request creates duplicates or SQL errors. Cross-resource links largely unconstrained. |
| List/detail/search/dashboard/tracking | Cookie role → service filters by customer/driver; staff reads all | Separate DB snapshots; malicious known IDs checked in some paths, but tenants do not exist. LIKE values bound; no pagination limits. Synthetic tracking changes with time/provider. |
| Reports/browser export | Staff → unscoped aggregate/list → browser Blob | No durable export operation; any staff can export all data. Browser CSV spreadsheet-formula risk needs review. |
| Notifications | Global role-tagged messages; admin sees all, others role or NULL | A read flag is shared by entire role; arbitrary notification IDs checked only by role. Not per-user inbox semantics. |
| Startup | Import WSGI → create/alter/seed/link/rotate | Concurrent providers can race startup; exceptions in ALTER swallowed; SQLite AUTOINCREMENT translation loses PostgreSQL generated IDs. No migration contract. |

Every DB-writing operation lacks a response-loss/retry contract. Worker death before commit rolls back PostgreSQL, after commit does not. Database failures generally become 500, and failover cannot repair a shared failed database. No actual external deliveries or duplicate jobs exist to test in the baseline.

## Replacement decisions
Preserve domain services/UI; replace the dual-engine persistence and startup lifecycle. Use PostgreSQL in tests as well as production, with explicit schema migrations and non-owner runtime credentials. Establish identity → membership → immutable caller context; require that context for tenant transactions and FORCE RLS plus composite ownership foreign keys. Serialize tenant operations initially (correctness over throughput), with durable request replay and audit in the same unit of work. Replace non-revocable identity cookies with database sessions; add CSRF including login, database-backed brute-force controls. Disable fabricated tracking in production. Do not add fictional file/gateway/HA claims.

See SECURITY_MODEL.md and OPERATIONS.md for implemented contracts and outstanding gates.
