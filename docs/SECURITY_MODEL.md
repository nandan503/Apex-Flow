# Security model

## Assets, actors and trust boundaries

| Asset | Security objective / authority |
|---|---|
| Tenant operational data, shipments, fleet, invoices | PostgreSQL authority; membership-scoped access; composite ownership foreign keys; no fallback DB |
| Accounts, password hashes, memberships | Global identity registry / privileged control plane; memberships, not user/cookie role fields, grant tenant capabilities |
| Sessions, CSRF and credentials | Random handles, hashed session tokens in PostgreSQL, signed HttpOnly cookies, finite lifetime and revocation; secret-manager ownership |
| Documents / durable files | No such backend feature exists. A future implementation must use object storage and tenant-authorized metadata, not local disk |
| Audit and replay records | Durable PostgreSQL records; replay isolation by tenant + principal + key; runtime cannot update/delete audit records |
| OTPs / API credentials | Password-hashed OTP verification; separate-key encrypted outbox payload; outbound token supplied by secret manager |
| Database and storage backups | Infrastructure trust boundary: privileged operators, separate backup credentials and restoration tests required |

Actors: anonymous attacker, authenticated customer/driver, malicious tenant user,
malicious tenant administrator, compromised account, automated bot, privileged
control-plane administrator, and infrastructure attacker.

Boundaries:
1. Browser → Cloudflare: TLS, untrusted URLs/JSON/headers, XSS/CSRF/bots.
2. Cloudflare → compute ingress → Flask: operator must prevent direct-origin
   bypass and overwrite forwarded headers. TRUST_PROXY is off unless configured.
   No host header is used as CSRF authority or a tenant selector.
3. Flask → PostgreSQL: restricted non-owner runtime role, verified TLS in
   production, bound parameters, membership revalidation, transaction-local RLS.
4. Flask → Redis: currently no dependency. Loss of Redis cannot change authoritative
   business, identity, rate-limit, replay or queue state.
5. Worker → HTTPS consumer → email/SMS provider: encrypted queue, authenticated
   consumer, stable event ID, at-least-once delivery; receiver-side dedup is mandatory.
6. Flask → object storage: not implemented; future authorized metadata → short-lived
   presign/stream contract must be reviewed before exposing files.
7. Tenant A → Tenant B: no cross-tenant HTTP administration; even ADMIN is tenant-local.
8. Customer/driver → manager → tenant admin → system operator: distinct capabilities.

## Identity, tenancy and authorization

The browser session contains only a random session handle and CSRF token, not an
authoritative role/user/tenant. `_principal` validates a hashed session token,
expiry and account activation in PostgreSQL. Membership lookup establishes a
frozen `Caller`. `X-Tenant-ID` is an untrusted selector, never a grant. JSON/query
`tenant_id`, `role`, `user_id` and ownership cannot override that context.

Each tenant `db_session(caller)` revalidates current membership and ownership links.
Nested services must present the identical Caller and reuse its transaction. Context
is set with transaction-local `set_config`, never session-level SET. Connections
are currently opened and closed per transaction, **not pooled**. If pooling is
introduced, these tests and transaction-local context rules must remain mandatory.

All tenant-owned tables have tenant_id NOT NULL and FORCE RLS. RLS resolves the
selected tenant through `authorized_tenant_id()` against the current principal's
memberships; a raw unauthorized tenant setting alone returns no rows. Missing
context returns no rows and rejects inserts. Cross-tenant foreign keys are rejected
even if an application query forgets an ownership check. Membership rows have
principal RLS; per-user notification receipts also enforce the principal.

Runtime startup rejects superuser, BYPASSRLS, inherited role membership, table
ownership, CREATE ROLE/DB privileges, missing FORCE RLS and incompatible migration
checksums. The runtime role cannot alter schema, accounts or memberships. The
explicit isolated migration/provisioning path uses privileged credentials; it is
not available via HTTP and must never be supplied to compute/worker environments.

**Limit of RLS:** the application establishes the principal. This is defense against
missing filters and unauthorized selectors, not protection against arbitrary code
execution or unrestricted SQL execution with stolen runtime credentials (which can
set context variables). Global credential/session lookup is an intentional identity
registry exception to tenant RLS. A compromised application or DB operator remains
inside that trust boundary. No infrastructure-compromise immunity is claimed.

## Authorization matrix (all API routes)

C = customer (normal user), D = driver (normal user), M = manager, A = tenant admin.
“Own” means linked customer record or assigned driver, established from membership.
S = system operator: **no system-admin HTTP role**, no implicit tenant bypass. An
operator who explicitly has a membership is treated as that membership, not as S.

| Endpoint (prefix /api) | Action | C | D | M | A | S HTTP |
|---|---|---|---|---|---|---|
| GET /auth/csrf | Bootstrap | Public | Public | Public | Public | Public |
| POST /auth/login | Authenticate | Valid credentials + CSRF | Same | Same | Same | No special role |
| POST /auth/logout | Revoke session | Yes | Yes | Yes | Yes | No bypass |
| GET /auth/me | Read current identity | Own | Own | Own | Own | No bypass |
| GET /shipments | Read/search/filter/sort/page | Own | Assigned | Tenant | Tenant | Deny |
| GET /shipments/{id} | Read | Own | Assigned | Tenant | Tenant | Deny |
| POST /shipments | Create | Own customer forced | Deny | Tenant customer selector | Same | Deny |
| PUT /shipments/{id}/status | Update | Deny | Assigned + driver transitions | Allowed graph | Allowed graph | Deny |
| PUT /shipments/{id}/assign | Update | Deny | Deny | Tenant driver + vehicle | Same | Deny |
| DELETE /shipments/{id} | Delete | Deny | Deny | Deny | Tenant | Deny |
| GET /vehicles | Read | Deny | Assigned | Tenant | Tenant | Deny |
| POST /vehicles | Create | Deny | Deny | Tenant | Tenant | Deny |
| GET /drivers | Read | Deny | Own | Tenant | Tenant | Deny |
| POST /drivers | Create | Deny | Deny | Tenant | Tenant | Deny |
| GET /customers | Read | Deny | Deny | Tenant | Tenant | Deny |
| POST /customers | Create | Deny | Deny | Tenant | Tenant | Deny |
| GET /warehouses | Read | Deny | Deny | Tenant | Tenant | Deny |
| GET /routes | Read | Deny | Tenant | Tenant | Tenant | Deny |
| POST /routes/optimize | Calculate (no side effect) | Deny | Yes | Yes | Yes | Deny |
| GET /tracking, /tracking/{vehicle_id} | Read | Own shipment | Assigned shipment | Tenant | Tenant | Deny |
| GET /deliveries | Read (no OTP secrets) | Own shipment | Assigned | Tenant | Tenant | Deny |
| POST /deliveries/confirm | Custody transfer | Deny | Assigned + valid OTP | Deny | Deny | Deny |
| GET /payments | Read | Own shipment | Deny | Tenant | Tenant | Deny |
| POST /payments/{invoice_id}/collect | Record (NOT charge) | Deny | Deny | Pending tenant invoice | Same | Deny |
| GET /reports/dashboard | Read aggregates | Own | Assigned | Tenant | Tenant | Deny |
| GET /reports/{shipments,revenue,fleet,drivers,general} | Read/browser export | Deny | Deny | Tenant | Tenant | Deny |
| GET /notifications | Read | Addressed to principal | Same | Manager or addressed | All tenant | Deny |
| PUT /notifications/{id}/read | Mark own receipt | Addressed | Addressed | Visible | Visible | Deny |

Tracking is explicitly unavailable in production until real telemetry exists.
Logout requires a valid principal and CSRF but not an active tenant membership,
so membership removal cannot prevent revocation of the current session.

No other CRUD/bulk/import/file/export-job/admin capability exists. Unsupported URLs
return 404/405, not a broad catch-all. CSV/JSON export is a browser download of
already authorized report data; CSV formula-leading cells are escaped.

The tests iterate all protected registered routes for unauthenticated requests,
the entire read/write matrix for all four roles, and foreign known/guessed IDs,
query/body/header selectors, reports, file/bulk non-capabilities, jobs and raw SQL.
Unauthorized private resources use 404 to avoid existence leakage. 401 means no
valid session; 403 means authenticated but insufficient capability/membership.

## Additional controls and residual threats

- Passwords use Werkzeug scrypt; provisioning/rotation enforces 14–1024 characters,
  no hardcoded seed credentials. Login never normalizes password characters. Missing
  users get a dummy scrypt verification and the same error as wrong credentials.
- Shared PostgreSQL buckets cap account attempts at 5/min and source IP at 30/min.
  The edge must additionally rate-limit unauthenticated floods. Account lockout DoS,
  credential stuffing and distributed low-rate attacks remain operational threats;
  MFA and breached-password checks are not implemented.
- CSRF tokens are required for login/logout and all unsafe API calls even with no
  Origin. Origin is an additional same-origin/allowlist gate (see "Browser origin
  boundary"); CORS is exact-origin, credential-aware.
- Production cookies: Secure, HttpOnly, SameSite=Lax. Absolute eight-hour server
  expiry, no sliding refresh; login rotates handle and CSRF. Logout and operator
  password rotation revoke server sessions across providers.
- Full UUID4 randomness for newly exposed IDs; old sample identifiers stay only in
  tests/examples. **Identifier obfuscation != authorization.**
- Body limit, statement/lock/connect timeouts, no-store API responses, random request
  IDs, sanitized logs and generic database errors. No /version/env dump endpoint.
- CSP still permits inline script/style for legacy templates. Escaping is retained,
  but inline removal, DOM-XSS review and browser automation remain release work.
- Tenant admins can operate on their tenant's data, not account/membership registry.
  Operational logs are not immutable evidence; transactional audit table is append-only
  to runtime, but DB operators can change it. External tamper-evident archival remains
  an infrastructure requirement.

## Runtime database privilege boundary (F-1, Sprint 3)

The runtime role `apex_app` holds **no table-wide SELECT on `users`**. Column grants cover only
the non-secret identity columns the application reads (`user_id`, `name`, `email`, `phone`,
`active`, `created_at`). Credential material is reachable exclusively through two SECURITY
DEFINER functions created by migration `002_credential_boundary.sql`:

- `fn_login_material(bucket_digest, email)` — atomically increments the per-account
  failed-login bucket (shared across every instance/provider by construction) and returns
  **at most one row**: the login record for the explicit candidate email, or
  `limited = true` with no material once the bucket exceeds five failed attempts.
- `fn_login_success(bucket_digest)` — resets the caller's own account bucket after a
  successful authentication.

Both functions have a fixed `search_path = pg_catalog, public`, contain only static SQL, are
owned by the migration/table owner (never `apex_app`), and hold EXECUTE for `apex_app` only.
Probes prove `apex_app` cannot read `password_hash` by any direct SQL path, cannot replace or
drop the functions, and cannot create objects or policies in `public`.

Residual risk: an attacker who already holds valid `apex_app` database credentials can still
probe credential material one candidate email at a time through the function channel. The
bulk-read channel demonstrated by F-1 is closed; database-credential compromise remains a
trusted-operator-level event, as documented in the original threat model.

## Login limit contract (F-2, Sprint 3)

Both buckets live in the PostgreSQL `login_attempts` table — Redis is not used anywhere, so
nothing fails open on a cache outage; with the database unavailable login returns a sanitized
503 (fail closed) and no process-local limiter state exists, keeping multi-provider semantics
identical.

- **Account bucket**: key = HMAC-SHA256(SECRET_KEY, `"account:" + lower(email)`) — no PII
  stored. Incremented exactly once per attempt that reaches credential verification, inside
  the SECURITY DEFINER `fn_login_material` (atomic upsert; DB-enforced; cross-instance).
  Blocked while the 1-minute window count exceeds 5. **Failed attempts only**: a successful
  login deletes the bucket via `fn_login_success`, so busy legitimate users are not
  self-locked and a correct password after earlier failures starts from a fresh budget.
- **Source bucket**: key = HMAC-SHA256(SECRET_KEY, `"ip:" + source`). Counts **failed
  attempts only** (atomic app-side upsert on the failure path); blocked at the pre-check when
  the live window already holds ≥ 30 failures. Successes never consume or reset it.
- **Source identity / trusted-proxy model**: with `TRUST_PROXY` unset (default), the source is
  the TCP peer and `X-Forwarded-For` is never read — client-supplied forwarding headers cannot
  influence bucketing. With `TRUST_PROXY=1`, Werkzeug ProxyFix replaces the peer with the
  **rightmost** XFF entry, i.e. the value appended/overwritten by the trusted ingress; this
  mode is valid only when the ingress overwrites client-supplied forwarding chains and the
  application origin is not directly reachable. Unparseable values (malformed headers, garbage
  chains) collapse into a shared conservative `unknown` bucket. IPv4 and IPv6 are normalized
  through `ipaddress.ip_address`.
- **Window reset**: each bucket re-arms when its 1-minute `expires_at` passes.
- **Password reset interaction**: operator CLI rotation revokes sessions; no self-service
  reset exists, so no additional bucket coupling applies.

## Browser origin boundary (2026-09-08)

Mutations carry a browser-controlled `Origin` header. The gate in `backend/app.py` accepts a
mutation when the Origin is (a) an exact entry in `ALLOWED_ORIGINS`, or (b) **the request's own
origin** — scheme, host and port equal to the request (ProxyFix-restored under
`TRUST_PROXY=1`). Same-origin POSTs are not cross-site; browsers attach Origin to them, so
requiring allowlist membership for them was a deployment-coupled defect (it caused the
production login failure on apex.viability.in). Everything else fails closed: malformed
origins, scheme/port/host mismatches, userinfo, path/query/fragment, `null`, wildcards and
reflections are all rejected.

`Host` is used only to recognize the request's own origin; it never grants a foreign Origin
cross-origin access. A request whose Origin and Host both claim attacker control carries no
victim session (cookies are domain-bound, never sent to a forged Host), so it is denied at
authentication — regression-pinned in `tests/test_security.py`.

Residual: DNS rebinding can present attacker-Host/Origin as "same origin", but the victim's
`HttpOnly; Secure; SameSite=Lax` session cookie is not sent to the attacker's hostname and the
CSRF token lives in that session, so no authenticated capability is reachable; direct
non-browser clients carry no ambient credentials and are not CSRF subjects.
