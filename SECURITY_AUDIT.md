# APEX Flow — reengineering verification status

This replaces the previous audit's stale deployment/default-credential guidance.
Original behavior is recorded in [docs/DISCOVERY.md](docs/DISCOVERY.md); Git retains
the earlier report. This is an engineering verification record, not certification.

## Implemented boundaries

- PostgreSQL-only runtime and tests; native SQL, explicit checksum-verified migration
  command, no import/factory-time DDL/seeding, no default credentials or ephemeral keys.
- Principal → membership → tenant Caller → service authorization → membership-backed
  FORCE RLS. Non-owner NOINHERIT/NOBYPASSRLS runtime; composite ownership FKs and
  tenant_id NOT NULL on all tenant-owned tables. Restricted control-plane path.
- Server-revocable PostgreSQL sessions, rotation, absolute expiry, secure production
  cookies, CSRF including login/logout and requests without Origin, durable shared
  login limits (failed-attempts-only account/source buckets; account budget enforced
  inside PostgreSQL; explicit trusted-proxy source model), operator password rotation
  with session invalidation.
- Sprint 3 credential boundary: the runtime database role holds column-level SELECT on
  non-secret identity columns only; password-hash access exists solely through two
  SECURITY DEFINER login functions with fixed search_path, migration-role ownership and
  runtime-only EXECUTE. Tenant-wide retrieval paths (lists, reports, tracking, history)
  are server-side hard-capped (2000/2000/500/1000 rows) with PostgreSQL-enforced Limit
  nodes; tracking additionally scopes roles in SQL.
- Common transaction/replay wrapper; full UUIDs; decimal money; explicit concurrent
  state serialization; OTP attempt consistency; stable paid dates; per-user notification
  receipts; transactional append-only-to-runtime audit.
- Encrypted transactional OTP outbox, bounded worker, stable event ID, retry/dead-letter
  semantics and explicit idempotent consumer contract. No fictional email/SMS success.
- Relative client API URLs, CSRF client support, persisted retry keys, CSV formula
  neutralization, provider-neutral factory/container/Procfile, liveness/readiness,
  secret-safe errors and correlation logs. Production synthetic tracking disabled.
- Hash-pinned runtime dependency lock, PostgreSQL CI, dependency-audit gate and
  shipped-source default-credential guard.

## Verification performed in this workspace — 2026-09-08 (Sprint 3)

Run the exact tests again with a disposable PostgreSQL `_test` database to reproduce.
Tests fail rather than silently substitute SQLite when PostgreSQL is unavailable.

- PostgreSQL integration suite: **132 tests passed** in the final Sprint 3 run
  (71 baseline security/contract tests, 33 red-team probes from the Phase 2 review, and
  28 Sprint 3 remediation tests). Existing 27 security
  regressions were retained/adapted for explicit PostgreSQL/CSRF/replay contracts.
- Test engine: local PostgreSQL 16.2 (test-only package binaries), Python 3.11.
  Runtime requests use apex_app, not migration superuser. CI is configured for PostgreSQL 16.
- Tests include all registered protected-route anonymous access and four-role endpoint
  matrix; tenant admin attacks; known full UUID / guessed UUID / direct URL / query /
  JSON selectors; reports/browser export source; search, pagination, sort; unsupported
  file/bulk/import capabilities; raw unscoped SQL and unauthorized tenant settings;
  cross-tenant foreign keys; missing/switched/forged contexts; connection cleanup.
- Two separately created Flask app instances sharing PostgreSQL and secrets: lost
  creation response is replayed exactly; concurrent same-key calls commit one logical
  shipment/invoice/outbox/audit; account limits are shared. This is **not** an actual
  Cloudflare/provider failover exercise.
- Failure-before-commit injection and an actual killed Python worker demonstrate
  rollback of uncommitted shipment/outbox/replay state. Concurrent wrong OTP attempts
  stop at five; successful OTP replay writes one Delivered history event. Explicit
  database-outage injection gives sanitized 503 while liveness remains up.
- Server logout, expiry, account disable, membership revocation, cross-provider password
  rotation revocation, permission-change replay rejection and replay-keyring rotation.
- Outbox consumer test simulates side effect followed by lost acknowledgement;
  receiver dedup yields one logical delivery. Tests also cover duplicate concurrent
  worker jobs, expired OTP dead-letter and bounded retry failure. Real HTTPS consumer
  delivery and cloud scheduler operation have **not** been tested.
- `pip-audit -r requirements.txt`: **14 resolved runtime packages, no known
  vulnerabilities reported** in the final scan (Sprint 3 re-verification). The initially selected cryptography
  version had reported advisories and was replaced with 50.0.1, re-audited, and used
  for subsequent tests. This scan does not cover OS/container/database vulnerabilities
  or guarantee no unknown vulnerabilities.

## Release gates still open (do not waive by calling this deployable)

| Gate | Status / required action |
|---|---|
| Legacy data migration | BLOCKED on approved tenant/identity ownership mapping and reviewed offline importer; runner intentionally refuses unversioned DBs |
| Database/storage high availability | NOT implemented/proven; configure replication, backups, fencing, recovery and restore/failover tests. Compute redundancy alone is not HA |
| Cloudflare and provider ingress | NOT provisioned/tested; origins, TLS trust, secret distribution, forwarded-header handling and direct-origin blocking need verification |
| Real external OTP delivery | Consumer contract/worker implemented; real email/SMS integration and recipient delivery proof absent |
| OTP issuance lifecycle | 30-minute code created at booking is unsuitable for multi-day trips; just-in-time issuance/reissuance, revocation and recovery workflow required |
| Documents/object storage | No file API or durable upload path exists. Do not introduce local filesystem persistence. Add metadata ownership, scoped object keys and presign/download tests before enabling |
| Load, backpressure, pagination | Initial whole-tenant serialization; other legacy lists/reports unbounded. Load tests, finer locking and bounded export jobs remain |
| Product/account management | Only explicit operator tenant bootstrap/password reset exists; tenant management UI, membership lifecycle UI, MFA, breached-password checks and reset email flow not implemented |
| Frontend security/UX | CSP retains unsafe-inline; full DOM-XSS/browser audit and tenant-switcher UI remain. UI defaults to first authorized membership |
| Accounting correctness | Decimal storage implemented, but price/distance heuristics and legacy denormalized counters need approved business rules; collection is not a payment gateway |
| Operational visibility | Correlated logs/probes/audit and inspectable queue state exist; external metrics, dashboards, paging and retention/cleanup scheduling not provisioned |
| Key/backup lifecycle | Replay keyring supported; outbox single-key maintenance reencrypt procedure documented but not automated or disaster-recovery tested |
| Control-plane security | Runtime privilege separation tested; operator identities, approval/audit infrastructure and backup isolation require deployment controls |
| Container and CI execution | Dockerfile/CI workflow supplied; no Docker daemon/image build or hosted GitHub Actions run verified here |

## Security claims deliberately NOT made

No end-to-end high availability, exactly-once external email, authenticated GPS,
provider redundancy test, object-storage durability guarantee, automatic safe legacy
migration, SQL-injection/RCE-proof tenant isolation, or immunity from privileged DB
operators is claimed. Security model and residual trust boundaries are explicit in
[docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).
