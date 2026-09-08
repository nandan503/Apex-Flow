# RED TEAM REPORT — Phase 2 adversarial review of the reengineered APEX Flow

Date: 2026-09-08 · Reviewer stance: hostile Staff/Principal engineer reviewing own changes.
Method: re-read every backend module and the full migration, then attacked below the HTTP API
(raw SQL as the runtime role), at the HTTP boundary, and at infrastructure level.
No production code was modified. New files are evidence probes only:
`tests/test_redteam_isolation.py`, `tests/test_redteam_http.py`, `tests/test_redteam_chaos.py`.

**Combined suite: 104 passed in 131.69s** (71 prior + 33 red-team probes) against real PostgreSQL 16.2.

---

## 1. Baseline (established before probing)

| Item | Value |
|---|---|
| Git commit | `861e8b1f` (branch `arena/01a07dc5-apex-flow`); hardening changes present as uncommitted working-tree state |
| Schema version | `001_baseline.sql`, sha256 `c81f6c2fa264…`, single row in `schema_migrations` (verified) |
| Migrations | 1 file; checksum-verified, advisory-lock serialized, transactional |
| Application version | **None exists — no version marker anywhere (F-11)** |
| Python / runtime | 3.11.2; PostgreSQL 16.2 locally, `postgres:16` in CI |
| Dependency locks | runtime `requirements.txt`: 14 pins / 323 hashes (sha256 `2a129d41…`); dev `requirements-dev.txt`: 44 pins / 686 hashes incl. unsafe pip (sha256 `f43d3889…`) |
| Clean-install proof | **NEW: hash-pinned dev lock installed into a fresh venv with `--require-hashes` — verified this session** (previously unverified) |
| Test count | 71 prior + 33 red-team probes = 104, all passing |
| Known release gates | Unchanged from SECURITY_AUDIT.md table; additions below (F-1..F-3, F-5) |
| Deployment assumptions | env-only config, provider-neutral, PostgreSQL authoritative, no Redis, no local disk state, no object storage feature, Docker build not executed (no daemon), cloud failover unproven |

---

## 2–3. Tenant isolation & connection/RLS red team — CONFIRMED, with one blast-radius finding

Probes (`tests/test_redteam_isolation.py`) attacked below the API with the actual runtime role:

- Role flags inspected from `pg_roles`: `apex_app` is LOGIN, NOSUPERUSER, **NOBYPASSRLS**,
  NOCREATEROLE, NOCREATEDB, **NOINHERIT**, no role memberships (`pg_auth_members` = 0),
  and is **not the owner** of any table.
- **Cannot** `ALTER TABLE … DISABLE/NO FORCE ROW LEVEL SECURITY`, **cannot** `CREATE/DROP POLICY`,
  **cannot** GRANT, **cannot** CREATE TABLE or FUNCTION on `public`, **cannot** `SET ROLE` —
  all fail with permission/owner errors (probe).
- `SET row_security = off` yields zero rows / error — never foreign rows (probe).
- **No SECURITY DEFINER functions exist** in `public` (`pg_proc` count = 0).
  `authorized_tenant_id()` is STABLE SQL, `SET search_path` pinned, executes with caller
  privileges and reads `memberships` under its own RLS — no privilege bridge.
- Direct SQL, no context: **0 rows** on all 17 tenant tables (default deny verified table by table).
  Forged context with no membership → 0 rows on `shipments` and even `tenants`.
  Context A + real A principal: B rows invisible; `UPDATE` on B rows affects 0 rows;
  `INSERT` with tenant B id → RLS WITH CHECK violation. Cross-tenant writes are impossible at the storage layer.
- RLS-backed code paths that omit app-level scope were exercised for real:
  `generate_analytics_report` and staff dashboard queries carry no scope SQL and still cannot
  cross tenants — RLS is the enforcement, not the app filter (probe + existing matrix tests).
- **Context lifecycle**: `set_config(..., true)` is transaction-local. Same physical connection
  reused sequentially across tenants: context is empty after commit AND after rollback; two
  threads interleaving `db_session` for tenants A/B (4 threads × 12 iterations) never saw a
  foreign row. Connections are opened and **closed per operation — no pool**, so state cannot
  ride a pooled connection (probe; `pg_stat_activity` shows 0 non-idle runtime connections after requests).
- "Change tenant context": the runtime role *can* set any `app.tenant_id`/`app.principal_id`
  value, but these are **inputs to a membership check, not capabilities** — forged values yield
  0 rows (verified). Stolen DB credentials, however, equal full access (documented residual risk).

**F-1 (P1, blast radius): the runtime role can read `users.password_hash` for every user of every
tenant.** `migrations/001_baseline.sql`: `GRANT SELECT ON users TO apex_app` (table-wide) and
`users` is an identity table with no RLS. Verified by probe
(`SELECT count(password_hash) FROM users` → all rows, cross-tenant rows visible with no context).
Not reachable through any HTTP path today, but any future SQLi/RCE or DB-level compromise
inherits every tenant's credential hashes. Remediation: column-level grants
(`GRANT SELECT(user_id,name,email,phone,active,created_at) ON users …`) plus a narrowly
granted path for the login lookup; regression test = assert the bare column SELECT fails.

---

## 4. Privileged / system admin path — CONFIRMED tenant-bounded

- There is **no in-application system administrator**. `ADMIN` is a per-tenant role
  (membership-backed); cross-tenant powers exist only in the operator control plane:
  `backend/migrate.py` and `backend/cli.py` (`provision-tenant`, `reset-password`) which require
  the privileged `MIGRATION_DATABASE_URL` — never the web runtime.
- The outbox worker builds a CLI `Caller(role='ADMIN')`, but `db_session` **revalidates the real
  membership**: an impostor principal (e.g. DRIVER claiming ADMIN) is rejected before any query
  (probe `test_worker_admin_role_revalidated_not_trusted`).
- Worker scope is tenant-local: an A-admin worker cannot see or claim B's outbox rows
  (RLS), verified by probe; worker transitions are written to `audit_events`.
- `validate_database()` fails boot on any privileged runtime role (superuser/BYPASSRLS/
  INHERIT/role memberships) — probe-verified flags plus existing boot test.

## 5. Authentication red team — CONFIRMED with exact semantics

All three mandated sequences verified live (`tests/test_redteam_http.py`):

1. Valid session → role revoked (MANAGER→CUSTOMER in DB) → old session:
   manager endpoints **403 immediately**; replaying the historical manager mutation with the
   same Idempotency-Key returns **403**, never the cached 201
   (`authorization_context` mismatch; `Idempotency-Replayed` absent).
2. Valid session → membership deleted → old session: **403** on tenant endpoints and `/me`;
   **logout still 200** (destruction path unblocked); post-logout requests 401.
3. Tenant A membership removed, B kept: selector `A` → **403** (no silent remap);
   no selector → **auto-selects the single remaining membership (B) → 200** — exact documented
   result; with ≥2 memberships a selector is mandatory (existing test).
4. Expired (`expires_at` past) and deleted session rows → **401** with cookie cleared.
5. Enumeration: unknown-user vs wrong-password responses are **byte-identical** (probe);
   dummy-hash comparison equalizes timing; rate-limit responses are separate.
6. CSRF: missing/wrong token 403; **token from another session rejected 403** (probe);
   no-origin mutations still require the token; foreign-origin Origin header 403.
7. Session rotation on login: prior sid deleted server-side, fresh sid + fresh CSRF token
   (fixation resisted); absolute 8h expiry enforced in the database, not the cookie.

## 6. Idempotency red team — CONFIRMED

- Scope: PK `(tenant_id, user_id, key)`; **same key from different users and different tenants
  are fully independent** — probe shows three separate logical shipments and three records.
- Same key + changed bytes → 409 `IDEMPOTENCY_CONFLICT` (HMAC fingerprint over
  method+path+query+raw body, rotating keyring with lazy rehash).
- State machine: PENDING (transaction-internal, **never committed** — probe asserts no PENDING
  rows exist after operations), COMPLETED (2xx–4xx cached and replayed with header),
  FAILED (deterministic 4xx cached and replayed identically). 5xx is never cached — the
  wrapper raises and the whole unit rolls back.
- **Cached responses cannot resurrect authority**: replay requires current-role/customer/driver
  equality before the cached body is returned, and driver endpoints additionally recheck
  resource visibility. Demotion probe returns 403 on replay.
- Ownership/lease: none needed — PENDING never survives a crash (same-transaction design);
  the 409 PENDING branch is defensive only.

## 7. Transaction / concurrency red team — CONFIRMED

Unit of work = one PostgreSQL transaction per mutation; READ COMMITTED; correctness enforced
by whole-tenant `pg_advisory_xact_lock` (serialization documented as a throughput trade-off),
unique constraints (idempotency PK, one payment row per shipment per tenant, per-tenant
email/registration/license uniqueness) and composite tenant FKs (cross-tenant relationship
inserts rejected — existing probe). `SET CONSTRAINTS ALL IMMEDIATE` forces deferred FK checks
inside the business savepoint before commit; IntegrityError → sanitized 409, no constraint
text leaked. Concurrent same-key creation, concurrent OTP failures (attempt loss), payment
double-collect and duplicate worker claims are covered by the existing suite and re-ran green.

## 8. Outbox red team — at-least-once RE-CONFIRMED (F-4)

Probe simulates a consumer side effect followed by worker death before acknowledgement:
the sender exception is converted into a retry with exponential backoff
(`PENDING`, attempts=1, `available_at` in future), a later retry delivers the event a
**second time**, then the queue drains. **The consumer received the same event twice —
exactly-once external delivery is impossible with this design and is not claimed.**
The stable `Idempotency-Key: event_id` + consumer dedup contract is mandatory and documented;
a consumer reference implementation still needs to be built (release gate). Retry policy
verified: 2^n seconds capped at 3600, max 10 attempts, expiry and attempt-exhaustion dead-letter,
`FOR UPDATE SKIP LOCKED` prevents double-claim (existing tests re-ran green).

## 9. OTP red team — protocol verified; delivery remains a gate

- Entropy: `secrets.randbelow` 6-digit code (~1,000,000 space, CSPRNG) with 5-attempt lock
  (DB CHECK bounds 0–5) → guessing probability per expiry window ≈ 5×10⁻⁶.
- Storage: werkzeug password-hash of the OTP; `'USED'` invalidation after success; row updated
  only while `status != 'Delivered'` (rowcount guard closes the double-confirm race).
- Expiry: UTC-aware comparison; expired OTP rejected (probe).
- No plaintext OTP in HTTP responses, deliveries payloads, logs (allowlisted fields, counts only)
  or the outbox (Fernet envelope) — all verified by probe.
- Cross-tenant confirm attempts get the same sanitized failure as unknown shipments (probe).
- **Still true**: codes are issued only at booking with a 30-minute TTL; there is no
  resend/reissue/revocation workflow, and no real email/SMS delivery exists. OTP *storage*
  being safe does not make the *lifecycle* production-grade — release gate unchanged.

## 10. File / object storage — NOT APPLICABLE (yet)

No upload/download/file API exists (grep + route inventory); the only `send_from_directory`
serves the static frontend. There is no object identifier to attack. The "do not introduce
local-filesystem persistence" rule currently holds trivially. All storage red-team items
(signed URLs, MIME, oversized files, archive bombs) remain **to be built with the feature**.

## 11. Resource exhaustion — partially bounded; F-3 is the real gap

- Request body hard-capped at 64 KiB (probe: 200 KiB POST → 413).
- Shipment list pagination bounded (limit ≤ 200, offset ≤ 10000; invalid → 400); sort/direction
  whitelists reject injection-shaped values (probe).
- Statement timeout 10 s / lock 5 s / idle-in-transaction 15 s / connect 5 s cap every query.
- **F-3 (P1)**: analytics reports and nine list services fetch the **whole tenant with no LIMIT**
  (`backend/reports.py` report branches; `get_all_{vehicles,drivers,customers,warehouses,routes,
  deliveries,payments,notifications}`), materializing everything in Python. A large tenant can
  OOM a small-tier worker even though time is bounded. Remediation: hard caps + streaming +
  bounded export jobs. Probe documents the uncapped report path.

## 12. Redis failure — NOT APPLICABLE

Redis appears nowhere in the application (grep: only a comment saying it isn't needed).
Sessions, login budgets, idempotency, outbox, audit are all PostgreSQL-backed and were proven
shared across independent app instances. No security control fails open or closed on Redis
because none uses it. PostgreSQL remains the single availability dependency (documented).

## 13. Database failure — CONFIRMED safe (existing evidence re-verified in suite)

Connection refused/outage → sanitized 503 `DATABASE_UNAVAILABLE` with `Retry-After`, no SQL or
DSN text, liveness stays 200, readiness fails; mutations during outage roll back (nothing
partial committed); no indefinite internal retry (single attempt, client retries via idempotency).
In-database errors inside the business savepoint → 409; unexpected exceptions → 500
`INTERNAL` with type-only logging.

## 14. PostgreSQL connection budget — explicit calculation

No pooling: connections are opened/closed per operation (2–3 sequential connections per API
request — churn, not concurrency). Worst-case concurrent demand:

```
providers_active × ( instances × workers × threads(1) + outbox_worker(1) )
+ control_plane(migrate/provision/reset: ≤2, rare) + health/readiness probes(≈1)
+ margin(5)
```

Worked example — **during failover both providers serve simultaneously** (2 providers ×
2 instances × 2 workers + 1 worker each): `2×(4+1) + 2 + 1 + 5 = 18` peak connections —
comfortably inside a typical managed-PG limit (≈100). The exposure is **churn and port
exhaustion at high RPS**, not max_connections: recommend pgbouncer (transaction mode is
compatible — all context is `SET LOCAL`/xact-scoped) as a later optimization. The outbox
worker holds its tenant advisory lock across the 5 s network call — documented throughput limit.

## 15. Migration red team — CONFIRMED fail-closed; rolling deploys NOT supported

- Two concurrent `backend.migrate` processes on a fresh database: both printed success,
  exactly one applied row — advisory-lock serialization works (bash probe).
- Checksum tamper, mid-migration failure rollback, and unversioned-legacy refusal remain
  covered by existing tests (re-ran green).
- `validate_database()` compares applied versions+checksums to the shipped set **at every
  boot**: old app + new schema → boot failure; new app + old schema → boot failure.
  This is fail-closed but means **zero-downtime rolling deployments are not supported**
  for breaking migrations; an expand/contract procedure is not implemented and not claimed.

## 16. Secrets red team

- Current tree: no secret-shaped literals, no private keys/cloud keys, no default-role
  passwords (the only scanner hit is the banned-string list inside my own probe file).
- Tests/CI use runtime-generated secrets (`secrets.token_*`, Fernet.generate_key) — verified by
  reading conftest and demo_data; CI guards shipped `backend`/`frontend`.
- Git **history** retains ~100 upstream placeholder strings (`Admin@123` etc.) and 0 private keys.
  Placeholders never function in this tree; note for any mirror/restore of the old repo.
- `/health/live`, `/health/ready`, `/api/auth/me` and error bodies were probed for leakage:
  no DSN, host, key, SQL, or stack content. Error handlers log exception **types** only.
- Production fail-close on missing/weak secrets is enforced in `load_config` and covered by
  existing tests (re-ran green).

## 17. Dependency / supply chain — re-audited

- Runtime lock: `pip-audit -r requirements.txt` → **"No known vulnerabilities found"**,
  14 resolved packages (blinker, cffi, click, cryptography, flask, flask-cors, gunicorn,
  itsdangerous, jinja2, markupsafe, packaging, psycopg2-binary, pycparser, werkzeug).
  Every direct dependency is imported and load-bearing (flask, flask-cors, gunicorn,
  psycopg2-binary, Werkzeug, cryptography); transitives are framework chains — nothing to remove.
- **F-5 (P2)**: dev lock — pytest 8.3.5 has PYSEC-2026-1845 (fix 9.0.3). Dev-only (test runner),
  not shipped; bump on next dev-lock regeneration.
- **F-6 (P3)**: docs say "13 resolved runtime packages"; the lock resolves 14. Documentation drift.
- Scanner scope: PyPI advisory DB via pip-audit only — no OS/container/database coverage, and
  "no known vulnerabilities" is not a security proof.

## 18. Observability — CONFIRMED

`X-Request-ID` (server-generated UUID) on every response (probe); structured security events
carry request_id + tenant_id; field allowlist blocks arbitrary payloads; email/phone redacted;
OTP appears only as counts; DB failure logs type only; Gunicorn access log emits
method/status/latency only (no URL/query → no token-in-query leakage).
Gap (P3): the generic `request_complete` line lacks tenant context (security events have it).

## 19. Security headers / HTTP boundaries — verified against live responses

Production-mode app probe: `Set-Cookie` with **HttpOnly + SameSite=Lax + Secure**;
HSTS `max-age=31536000; includeSubDomains`; CSP present (scripts still `'unsafe-inline'` —
known gate); `X-Frame-Options: DENY`; `nosniff`; `Referrer-Policy`; `Permissions-Policy`;
`Cache-Control: no-store` on `/api`; CORS allows exact origins with credentials and foreign
preflights receive **no** ACAO header (probe). Health endpoints leak nothing (probe).

## 20. External URL / SSRF — user-input SSRF NOT APPLICABLE

The only server-side outbound request is the outbox consumer POST to the **operator-configured**
`OUTBOX_DELIVERY_URL` (config enforces HTTPS, no credentials, no redirects followed, 5 s
timeout, bearer token). No user-controlled value reaches URL construction anywhere.
Residual (P2): the operator endpoint is not IP-pinned — DNS rebinding toward internal
addresses is theoretically possible if the configured hostname is compromised.

## 21. Backup / restore — restore VERIFIED in-workspace; operations UNVERIFIED

`pg_dump -Fc` of the live test database (156 KB) → `pg_restore` into a fresh database →
row counts identical across 10 spot-checked tables → **the application booted against the
restored copy and passed all startup validation** (role flags, migration checksums, FORCE-RLS
inventory). This proves the restore *mechanism*. Backup scheduling, retention, encryption at
rest, off-site copies, RPO/RTO targets and a timed drill remain **UNVERIFIED** (deployment work).

## 22. Failure matrix

| Failure | Expected behavior | Verified? | Evidence |
|---|---|---|---|
| Provider A dies | traffic fails over | **NO — gate** | no ingress/Cloudflare exercised |
| DB unavailable | sanitized 503, liveness up, nothing partial | Yes | `test_db_outage_is_503…` (suite) |
| Redis unavailable | n/a — Redis absent | Yes | grep + shared-budget test |
| Worker dies mid-transaction | full rollback incl. outbox/replay | Yes | `test_real_process_death…` (suite) |
| Response lost | identical replay on second instance | Yes | `test_lost_response_replayed…` (suite) |
| Duplicate POST | one logical operation | Yes | `test_concurrent_duplicate_creation…` (suite) |
| Stale/expired session | 401, cookie cleared | Yes | probes (revoked/expired) |
| Tenant mismatch | invisible/denied, incl. raw SQL | Yes | isolation probes + suite matrix |
| Object storage failure | n/a — feature absent | n/a | grep + route inventory |
| Outbox worker dies | backoff retry, duplicate possible, dead-letter | Yes | chaos probe + suite |
| Migration interrupted | atomic rollback; concurrent runs serialize | Yes | suite + bash probe |

## 23. Multi-provider reality — CONFIRMED statically, image build untested

No provider names or conditionals in backend/Procfile/Dockerfile (grep); port, workers and all
secrets come from environment; frontend uses relative URLs; no local-disk writes in backend
(grep); nothing authoritative in process memory, provider sessions, or queues (all state is
PostgreSQL rows). The same image source targets any provider. **Docker build was not executed**
(no daemon in this environment) — running the identical image on Northflank/Render/Koyeb
remains unproven.

## 24. Cloud failover — NOT VERIFIED (release gate, unchanged)

Nothing was tested end-to-end: no DNS/Cloudflare, no health-check thresholds, no failover or
failback timing, no dual-provider serving. The budget in §14 deliberately assumes all providers
serve simultaneously during failure. No instant or even rapid failover is claimed.

## 25. Legacy data — gate unchanged; concrete ambiguity recorded

`data/*.json`: 4 users, 2 customers, 3 drivers, 5 vehicles, 2 routes, 5 shipments — every record
lacks any tenant/ownership field, so **100% of legacy rows are ownership-ambiguous** and require
the approved mapping. The importer does not exist; the migration runner refuses unversioned
databases (verified). Fail-closed importer design, validation and rollback strategy remain
pre-implementation requirements.

## 26. Test quality audit (104 tests)

- **All run against real PostgreSQL 16.2** — no database mocks anywhere; auth/CSRF/replay go
  through real HTTP+cookies via the test client.
- Classification: security/adversarial ≈ 45; contract/idempotency ≈ 30; database/RLS internals 9
  (new); failure injection ≈ 8 (killed process, outage, migration tamper, outbox crash);
  concurrency ≈ 6 (threads, multi-instance); config/validation unit-ish ≈ 6.
- State-asserting: probes query tables directly (idempotency rows, outbox states, session rows,
  audit counts), not just status codes.
- **False-confidence risks**: single shared PG instance (no failover semantics); XFF/proxy
  behavior simulated, not exercised through a real ingress; no browser/E2E suite; no load/perf
  tests; fixture IDs are predictable (fine — RLS is ID-agnostic); timing side channels untested.

---

## 27. Final adversarial summary

### A. Confirmed guarantees (attack-survived)
Membership-backed FORCE RLS from raw SQL upward; role/policy/owner/SECURITY-DEFINER lockdown;
transaction-local context with no cross-request/cross-thread leakage (no pool by design);
session revocation/expiry/demotion semantics incl. exact multi-membership behavior; CSRF
incl. cross-session token and no-origin mutations; enumeration-resistant login; shared durable
login budgets; idempotency scoping/conflict/terminal-state rules and **no authority
resurrection on replay**; single logical operation under concurrency and lost responses;
transactional outbox with tenant-scoped workers, backoff and dead-letter; OTP protocol
mechanics; fail-closed config/migrations/role checks; sanitized failures and secret-free
responses/logs; verified logical restore path; provider-neutral configuration.

### B. Failed guarantees
- **F-1 (P1)** — runtime role reads all users' `password_hash` cross-tenant (defense-in-depth breach surface, not an HTTP path).
- **F-2 (P1)** — login availability/budget semantics: with `TRUST_PROXY=0` behind an LB, one shared IP bucket throttles **all** accounts at 30 logins/min (probe: 429 across 10 accounts); with `TRUST_PROXY=1`, XFF is client-controlled so the IP bucket is bypassable (account bucket holds — probed). Also successes consume budget (6 logins/min/account can self-lock).
- **F-3 (P1)** — unbounded whole-tenant reports/lists (memory exhaustion path, exact locations in §11).
- **F-4 (P2, by design)** — outbox duplicates proven; exactly-once is impossible; consumer dedup is mandatory and still unimplemented.

### C. Untested assumptions
Real ingress/TLS/XFF hygiene; Docker image build; dual-provider serving; multi-day soak;
timing side channels; managed-PG behavior (timeouts, restarts, upgrades); production key rotation.

### D. New vulnerabilities discovered
F-1, F-2, F-3 above; plus P2/P3: PYSEC-2026-1845 (dev pytest), docs package-count drift,
no app version marker, `auth_sessions` table-wide DELETE grant (DoS-only blast radius),
`login_attempts` never purged, outbox endpoint not IP-pinned, CSP `unsafe-inline`.

### E. P0 blockers (for any deployment)
None newly breaking isolation. Standing deployment gates: legacy ownership mapping/import,
database+storage redundancy with restore drills (mechanism now verified; operations not),
real OTP delivery consumer, ingress contract (direct-origin blocked, XFF scrubbed),
bounded exports before large tenants onboard.

### F. P1 blockers
F-1 (column-level grants + regression test), F-2 (failure-only counting + documented XFF
contract + ingress requirement), F-3 (hard caps + streaming/bounded export jobs).

### G. P2 technical debt
F-4 consumer dedup implementation; pytest→9.0.3; pgbouncer evaluation for connection churn;
`login_attempts`/audit retention job; app version marker; outbox endpoint pinning;
CSP hardening; expand/contract migration procedure; docs count fix; history-placeholder note.

### H. Release gates
Unchanged from SECURITY_AUDIT.md, augmented with F-1/F-2/F-3 and the consumer dedup
implementation. Cloud failover and legacy-data import remain unverified gates.

### I. Recommended next implementation steps
1. F-1 column grants + login-path isolation + regression test.
2. F-2 budget rework (count failures only; per-account stays; document ingress XFF contract).
3. F-3 report/list caps + streaming export job.
4. Consumer dedup reference service + contract tests (F-4).
5. Dev-lock pytest bump; docs corrections; retention cleanup job.
6. Restore/failover drill runbook with RPO/RTO targets; pgbouncer trial.

### J. Residual risk
Trusted DB operators and anyone with `apex_app` credentials (full data access — RLS defends
missing filters and forged HTTP identities, not stolen DB credentials); RCE/0-day SQLi outside
review scope (all SQL parameterized; only whitelisted identifiers interpolated); no MFA;
single-region PostgreSQL; CSP `unsafe-inline` leaves DOM-XSS window until the frontend audit;
heuristic tariffs/tracking placeholders remain accounting/product debt.

---
*No production code was changed in this phase. Probe modules added under `tests/test_redteam_*.py`;
all 104 tests pass together (131.69s). The disposable PostgreSQL instance is still running at
`.cache/pgdata` (socket) for reproduction.*

---

# SPRINT 3 REMEDIATION RECORD (2026-09-08)

Objective: close F-1, F-2 and F-3 with the smallest safe changes; adversarially verify;
document. No architecture, framework, runtime or dependency changes.

## Baseline (before remediation)

Commit `861e8b1f`, PostgreSQL 16.2, Python 3.11.2, runtime lock sha256 `2a129d41…`,
dev lock `f43d3889…`. Baseline suites re-run before any change:
**104 passed in 132.42s** (71 baseline + 33 red-team). Locks additionally proven installable
into a clean venv with `--require-hashes`.

## F-1 — runtime role could read all password hashes

- **Root cause**: `GRANT SELECT ON users TO apex_app` (table-wide) in migration 001; the only
  legitimate runtime consumer is the login lookup (`backend/auth.py::authenticate_user`).
- **Remediation**: migration `002_credential_boundary.sql` — table-wide SELECT revoked;
  column grants for the six non-secret columns actually read; credential material reachable
  only via the two locked-down SECURITY DEFINER functions (`fn_login_material`,
  `fn_login_success`) which also host the DB-enforced account budget.
  `backend/auth.py::authenticate_user` now authenticates through `fn_login_material`.
- **Exploit replay**: the original probe (`SELECT count(password_hash) FROM users` as
  `apex_app`) and three variants fail with `permission denied`
  (`tests/test_sprint3_f1.py::test_f1_original_exploit_bulk_hash_read_fails`,
  `tests/test_redteam_isolation.py::test_runtime_role_cannot_read_password_hashes`).
  Login succeeds, wrong password fails, disabled users cannot authenticate, operator
  `reset_password` still works (`tests/test_sprint3_f1.py`).
- **Residual risk**: DB-credential holders can still probe one candidate email at a time
  through the function channel; documented in SECURITY_MODEL.md.

## F-2 — login limiting proxy/source semantics

- **Root cause**: single combined limiter counted successes, and the IP bucket either
  collided behind unproxied load balancers (all users share one bucket) or was bypassable via
  client-supplied XFF when proxy trust was enabled.
- **Contract**: see SECURITY_MODEL.md "Login limit contract". Account bucket = failed
  attempts only, atomic DB-enforced upsert in `fn_login_material`, reset on success,
  threshold > 5/min. Source bucket = failed attempts only, threshold ≥ 30/min, keyed on the
  canonicalized TCP peer (XFF never read) or the rightmost proxy-appended XFF value under
  `TRUST_PROXY=1`; unparseable → shared `unknown` bucket.
- **Exploit replay**: rotated-IP attack on one account still capped (matrix 5/6); 31 shared
  failures from one source throttle only after 30 *failures* while 25 successful logins from
  one source all pass (matrix 1/7); spoofed XFF chains take the rightmost value only
  (matrix 4); buckets shared across instances (matrix 10); window expiry restores access
  (matrix 9); DB outage fails closed with 503 (matrix 13). All in
  `tests/test_sprint3_login_limits.py` (13 tests).
- **Concurrency**: 8 concurrent failed logins produce exactly `[401]×5 + [429]×3` — the
  atomic upsert loses no increments (matrix 12).

## F-3 — unbounded tenant-wide retrieval

- **Root cause**: nine list services, five report queries, tracking and status history
  selected whole tenant datasets without LIMIT (time-bounded by statement timeout but not
  memory-bounded).
- **Remediation**: server-side constants `LIST_HARD_CAP`/`REPORT_HARD_CAP` = 2000,
  `TRACKING_HARD_CAP` = 500, `HISTORY_HARD_CAP` = 1000 enforced in SQL; tracking additionally
  gained SQL-level role scoping it previously lacked; history gained a deterministic
  `(timestamp, history_id)` tiebreak. Contracts documented in DATA_CONTRACTS.md.
- **Exploit replay**: 2050-row tenants return exactly the caps on lists and all five reports
  (`tests/test_sprint3_resource_bounds.py`); EXPLAIN shows the PostgreSQL Limit node; caps do
  not weaken isolation (over-cap tenant A still cannot see B); tracking is scoped and capped;
  concurrent large requests stay bounded; invalid pagination still 400s.
- **Residual risk**: capped responses truncate (by design, documented); cursor pagination
  and streaming export jobs remain release-gated work for oversized tenants.

## Sprint 3 verification totals

**132 passed in 185.83s** — 71 baseline + 33 red-team (1 probe replaced by the closed-boundary
contract, documented in-file) + 28 new adversarial tests (7 F-1, 13 F-2, 8 F-3).
Static gates re-run: compileall, `ruff --select F821,F401`, `git diff --check`, JS syntax,
runtime pip-audit ("No known vulnerabilities found"), secret scans clean.
One pre-existing harness defect was exposed and fixed: the migration tamper test updated
checksums without a version predicate (ambiguous with >1 migration).
