# Operations and secret inventory

## Runtime configuration

Configuration is evaluated per Flask factory, not at import. Modes are production
(default), development and test; unknown modes fail. The FLASK_ENV variable is only
a backward-compatible mode selector when APP_ENV is absent. Production rejects
TESTING/DEBUG and non-HTTPS origins. Secrets are required even in development/test;
only test fixtures generate them automatically.

| Secret / configuration | Purpose and consumers | Source / requirement | Rotation and failure |
|---|---|---|---|
| SECRET_KEY | Signed transport cookies; HMAC login buckets | Runtime secret manager; >=32 generated characters, shared across compute | Coordinated replacement invalidates cookies; revoke DB sessions for incident response. Existing business replay survives because its key is separate. Missing/obvious placeholder fails startup |
| DATABASE_URL credentials | PostgreSQL runtime, identity and business transactions | Runtime secret manager, dedicated apex_app login; required, PostgreSQL only | DBA rotates role password with coordinated compute replacement. No SQLite fallback. Production requires sslmode=verify-full and trusted CA. Connection/schema/role failures stop boot; runtime outage → sanitized 503 |
| MIGRATION_DATABASE_URL credentials | Schema owner / privileged account and tenant provisioning, password rotation | Isolated control-plane/migration job only; never consumed by factory | DBA rotates and audits; deliberately separate from runtime. Command fails when absent. Never make it available to web/worker processes |
| OUTBOX_ENCRYPTION_KEY | Fernet encryption/decryption of recipient/OTP queue payloads | Runtime/worker secret manager, separate from DB backup; required valid Fernet key | Currently a single-key maintenance rotation: pause writers/workers, privileged offline reencrypt ALL retained outbox payloads with old→new key, atomically switch fleet, validate before resume. Keep old key protected for retained backups. No automatic unsafe key discard. Missing/invalid format stops boot; mismatched worker key yields retry/dead-letter, never plaintext fallback |
| IDEMPOTENCY_HASH_KEYS | HMAC request fingerprints including sensitive OTP request body | Runtime secret manager, comma-separated generated keys, newest first; required | Deploy new+old ring to all providers, then new records use new key; successful old replays lazily rehash. Fingerprint carries non-secret key ID. Retain old keys while ANY stored fingerprint references them. Removing one early causes conflict, never duplicate execution |
| OUTBOX_API_TOKEN | Authenticated outbound consumer | Runtime worker secret manager; required if OUTBOX_DELIVERY_URL set | Rotate at consumer with overlap, then worker rollout. Never log headers. Missing configuration makes delivery fail/retry; no simulated success |
| Database CA / PGSSLROOTCERT | Trust chain for verify-full connections | Read-only mounted public CA bundle (not itself a secret) | Rotate certificates before expiry; connection fails closed on validation failure |
| ALLOWED_ORIGINS | CSRF/CORS exact-origin allowlist for CROSS-ORIGIN browsers | Required deployment config, HTTPS only in production | Same-origin requests (Origin == serving origin) are always accepted; list only genuinely separate trusted first-party origins; no wildcard, no reflection. Canonical production: `https://apex.viability.in` |
| TRUST_PROXY | Trust one ingress-sanitized forwarded hop for source IP/protocol | **Set to 1** for the canonical production topology (apex.viability.in via Cloudflare -> provider ingress) | Restores https scheme + rightmost forwarding values (ProxyFix); required for the same-origin login path on proxied deployments; enable only after origin access controls and header overwrite tests |
| OUTBOX_DELIVERY_URL | HTTPS idempotent consumer endpoint | Optional operator config, no URL credentials/redirects | Provider-independent adapter endpoint. Changing it must preserve event dedup history across consumers |
| PORT / WEB_CONCURRENCY | Compute listening port/process count | Procfile/Docker config; defaults 5050/2 | Bind 0.0.0.0. No provider detection in Python |

Redis, object-storage and gateway credentials do not exist in this implementation.
Do not add placeholder credentials “for deployment.” Future integrations need their
own least-privilege inventory, retry, authorization and rotation contract.

Never collect secrets in chat, source, Docker build arguments or logs. Inject at
runtime. `.env` variants are ignored except the empty `.env.example` template. The
factory does not load local environment files or print configuration. Driver
exceptions are not exposed; no Authorization/cookie/body/query logging is configured.
Disable such logging at ingress/APM too. Avoid Gunicorn logging formats that include
query strings. Requirements/lockfile do not contain private registry credentials.

## Database roles and migration/provisioning path

1. DBA creates a dedicated database owned by a migration role, **not apex_app**.
2. Create runtime login using operator tooling, with a generated password set via a
   secret channel (e.g. psql `\password`, not literal SQL in shell history):
   ```sql
   CREATE ROLE apex_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
     NOINHERIT NOBYPASSRLS;
   ```
   Do not grant other roles to apex_app. Restrict its network ingress to compute and
   worker identities. Revoke public database CONNECT/TEMP where supported and grant
   CONNECT only to needed roles. Public schema CREATE is revoked by migration.
3. Isolated job uses schema-owner MIGRATION_DATABASE_URL and runs
   `python -m backend.migrate`. Database administrator must authorize this role's
   grants to apex_app. Migrations use an advisory lock, atomic SQL application,
   version/checksum table and no swallowed ALTER failures. Re-running is a no-op
   only when checksums match; edits to applied migrations are rejected.
4. Provisioning/global recovery requires a tightly controlled **BYPASSRLS** operator
   role (or DBA), not merely table ownership (FORCE RLS applies to owners):
   `python -m backend.cli provision-tenant ...` / `reset-password --user ID`.
   These commands are explicit, not startup hooks. Password rotation changes the
   hash and deletes all user sessions in one transaction. Capture operator identity,
   approval/change ticket and control-plane access in infrastructure audit logs;
   this repository has no independent operator authentication platform.
5. Runtime receives only DATABASE_URL. Factory verifies schema version, table
   ownership/RLS and role properties without DDL or writes.

Current schema is a **new-install baseline**, not an automatic legacy upgrade.
For existing data: freeze old writes, create immutable backup, prove restore,
map every old record and user link to an approved tenant, identify orphan links
and duplicate natural keys, rotate all legacy account passwords, then import
into a separate migrated DB under privileged operator review. Validate row counts,
financial totals and adversarial access before cutover. No default “legacy tenant”
is silently assigned. Preserve original DB for rollback. Mapping/import tooling is
not supplied because the repository contains no authority for ownership decisions.

Exact migration checksums are required by each application version. Consequently,
this baseline does **not** support arbitrary mixed-schema rolling upgrades. Use a
coordinated maintenance release or design/test expand-contract compatibility first.
No destructive downgrade command exists; restore/reconcile from a tested backup.

## Compute independence and preview configuration

Same image/factory on Northflank, Render, Koyeb or another provider. Configure PORT,
health checks, resource limits, secrets and public ingress outside Python. No durable
uploads, generated reports or database files go on compute local disk. Process death
must not require restoring its filesystem or sessions. No application schema job is
run in a web startup command.

## WSGI entrypoint (the only supported start command)

`backend/app.py` exposes the application **factory** `create_app()` and intentionally
defines no module-level Flask object. Every provider must therefore use Gunicorn's
factory syntax, exactly as `Procfile`, `Dockerfile` and `render.yaml` already do:

```sh
gunicorn 'backend.app:create_app()' --bind 0.0.0.0:$PORT --workers ${WEB_CONCURRENCY:-2} --timeout 30
```

Importing `backend.app` opens no database connection and builds no application; the
factory is safe to import under Gunicorn (including `--preload`) and each worker
builds its own app. Startup performs only the read-only `validate_database()` checks
(role privileges, applied migration checksums, table ownership/FORCE RLS) — no DDL,
no migration, no seeding, no credential creation. Flask's development server is never
started, and `DEBUG`/`TESTING` are rejected under `APP_ENV=production`.

Do **not** "fix" a startup failure by adding a module-level `app = create_app()`: that
would build an application (and connect to PostgreSQL) on every import, including in
the CLI, the migration runner and the tests.

Startup incident 2026-09-08: Render's Start Command was
`gunicorn backend.app:app --bind 0.0.0.0:$PORT`, which aborts with
`AttributeError: module 'backend.app' has no attribute 'app'` /
`gunicorn.errors.AppImportError: Failed to find attribute 'app' in 'backend.app'`.
The service never bound a port, so the browser saw the edge error page rather than any
application response. Resolution: use the factory command above. A dashboard-created
service does not read `render.yaml`; its Start Command must be corrected in the Render
dashboard (Settings → Start Command) and the service redeployed.

## Canonical production login topology (2026-09-08)

`https://apex.viability.in` → Cloudflare edge → provider service (e.g. apex-flow-7mr9.onrender.com)
→ Flask, which serves both the static frontend and `/api` from the same origin. Required
deployment configuration on the provider:

- `TRUST_PROXY=1` — the ingress overwrites/extends `X-Forwarded-For`/`X-Forwarded-Proto`
  and blocks direct origin access; ProxyFix restores the https scheme so browser
  `Origin: https://apex.viability.in` is recognized as same-origin.
- `ALLOWED_ORIGINS=https://apex.viability.in` — documents the canonical origin; also covers
  the origin gate if `TRUST_PROXY` is ever disabled (fail closed, not open).
- No provider hostname may appear in frontend JavaScript; the browser contract is
  `https://apex.viability.in` exclusively (relative `/api` URLs only).

Resolution history: production login previously failed with "Cross-origin request blocked"
because the origin gate compared browser Origin only against ALLOWED_ORIGINS and same-origin
POSTs were misclassified as cross-origin when the canonical origin was absent from the env
var. Fixed in `backend/app.py::_origin_matches_request` with regression tests in
`tests/test_origin_login.py`; DNS topology verified 2026-09-08 (CNAME apex-flow-7mr9.onrender.com
→ Cloudflare edge). Render "Application loading" interstitial observed via remote fetch during
diagnosis — instance state, not an application defect.

Cloudflare routing is **compute redundancy only**. All providers currently depend on
the same PostgreSQL authority and consumer. If that authority fails, switching compute
does not fix the outage. No database/storage redundancy has been provisioned or tested.
Before any HA claim, test database promotion/fencing, replication lag/RPO, object-store
failures (if added), key distribution, consumer failover/dedup, DNS/TLS and edge behavior.

For a sandbox preview, bind 0.0.0.0 and use relative browser `/api` URLs (already in the
client). Put the exact HTTPS preview origin in ALLOWED_ORIGINS. Do not point browser
JavaScript at localhost. Don't enable TRUST_PROXY unless the preview ingress sanitizes
forwarded headers. Development mode still needs PostgreSQL and real generated secrets;
it does not authorize publishing unreviewed seed data.

## Probes, logs and required alerts

- `/health/live`: process responds; no DB dependency, no credentials.
- `/health/ready`: short database SELECT; 503 + Retry-After on outage. Schema checked
  at boot, not every probe. Readiness does not certify outbox delivery or storage HA.
- Every request gets a server-generated X-Request-ID and logs endpoint template name,
  method, status and duration. No raw query/body/Authorization/cookie. Login success
  logs safe account ID and partial email; malformed/login/CSRF/rejected requests are
  observable through endpoint/status, not secret data.
- Audit business commit/result in PostgreSQL; stdout security events may precede
  commit and are **not** proof of a committed business action.
- Required operator alerts: 503/error-rate and latency, database saturation/lock wait,
  login 429/401 spikes, disk/connection usage, migration failures, oldest PENDING age,
  FAILED outbox count, queue size versus throughput, imminent OTP expiry, backup age
  and restoration failures. No external alert destination/dashboard is provisioned.

Queue inspection must be tenant-authorized or through the privileged control plane.
Use counts/ages/kind/state; never dump encrypted payloads into logs (nor decrypt them
for dashboards). Failed outbox records require review; do not simply reset all events
and cause duplicate receiver actions. Preserve event IDs for retried deliveries.

## Maintenance and bounded failure

- Database connection timeout 5s, lock timeout 5s, statement timeout 10s, idle-in-transaction timeout 15s; Gunicorn
  timeout 30s; outbound request timeout 5s. Tune from measurement, not provider guessing.
- One fresh connection per identity/business transaction; budget max compute instances
  × workers and worker jobs against DB connection limits. No pool configuration is
  claimed to be tested; future pool must retain SET LOCAL and clear ownership on exit.
- Expired login_attempts and auth_sessions require scheduled privileged cleanup:
  ```sql
  DELETE FROM login_attempts WHERE expires_at < now() - interval '1 day';
  DELETE FROM auth_sessions WHERE expires_at < now();
  ```
  Scheduler is not included. Auth correctness does not rely on cleanup. Edge bot
  protection is necessary to avoid unbounded distinct-account/IP bucket growth.
- Replay rows and audit records have **no automatic deletion**. Define legal retention,
  replay window/tombstones and secure archive/restore policies before pruning.
- Outbox secrets, DB backups and decryption keys require separately controlled access.
  Single-key outbox rotation is a maintenance operation, not yet an automated tool.
- Other than shipment lists, legacy report/list queries are not yet paginated/bounded;
  load-test large tenant datasets and implement explicit page/export-job contracts
  before production scale. Current per-tenant serialization limits concurrency.
