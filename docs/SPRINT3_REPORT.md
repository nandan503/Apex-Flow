# SPRINT 3 ENGINEERING REPORT — P1 remediation + resource-safety hardening

Date: 2026-09-08 · Standard applied: minimal change, explicit contracts, DB enforcement,
adversarial testing, regression safety, no false claims.

## 1. Baseline

| Item | Value |
|---|---|
| Git commit | `861e8b1f` (branch `arena/01a07dc5-apex-flow`); Sprint 3 changes as working-tree state |
| Runtime | Python 3.11.2 |
| PostgreSQL | 16.2 (local disposable instance, socket `.cache/pgdata`); `postgres:16` in CI |
| Baseline tests | 71 baseline + 33 red-team = **104 passed in 132.42s before any code change** |
| Runtime lock | `requirements.txt` sha256 `2a129d41…`, 14 pins / 323 hashes |
| Dev lock | `requirements-dev.txt` sha256 `f43d3889…`, 44 pins / 686 hashes |
| Environment note | The clean-venv `--require-hashes` install of both locks was verified this session (previously unproven) |

## 2. F-1 — database credential boundary

- **Original exploit**: `SELECT count(password_hash) FROM users` (and any hash projection) as
  the runtime role `apex_app` returned every tenant's hashes — probe
  `test_evidence_runtime_role_reads_global_users_including_hashes` (Phase 2).
- **Investigation findings** (all verified, none inferred): readers of the column are exactly
  `backend/auth.py::authenticate_user` (runtime role), `backend/cli.py` provision/reset
  (privileged control-plane role only), test fixtures (admin connection). Grant was
  `apex_app=r/postgres`; `users` has no RLS (identity table); zero SECURITY DEFINER functions
  existed; login shares the single runtime DB role with ordinary requests.
- **Remediation**: `migrations/002_credential_boundary.sql` — `REVOKE SELECT ON users FROM
  apex_app`; column grants `(user_id, name, email, phone, active, created_at)`; credential
  material exposed only via `fn_login_material(bucket, email)` and `fn_login_success(bucket)`:
  SECURITY DEFINER, fixed `search_path = pg_catalog, public`, static SQL only, owned by the
  migration role, EXECUTE granted solely to `apex_app`. `backend/auth.py::authenticate_user`
  now authenticates through `fn_login_material`. No new dependencies; authentication
  architecture unchanged (werkzeug hash verification stays in Python).
- **Exploit replay**: all four hash-read variants fail `permission denied`
  (`tests/test_sprint3_f1.py::test_f1_original_exploit_bulk_hash_read_fails`,
  `tests/test_redteam_isolation.py::test_runtime_role_cannot_read_password_hashes` — the Phase 2
  probe replaced by the closed-boundary contract, replacement documented in-file).
- **Regression tests**: normal login succeeds; wrong password 401; disabled user 401;
  operator reset_password rotates and revokes sessions; functions tamper-proof (drop/replace
  denied); single-candidate contract (one row per call).
- **Residual risk**: holders of real `apex_app` DB credentials can still probe one candidate
  email per call through the function channel; bulk-read channel is closed. Documented in
  `docs/SECURITY_MODEL.md`.

## 3. F-2 — login rate limiting

- **Original exploits**: 10 different accounts behind one untrusted source consumed the shared
  30/min IP bucket (429 for everyone); rotated client-controlled XFF against one account
  attempted to bypass source limiting.
- **Contract** (`docs/SECURITY_MODEL.md` "Login limit contract"):
  - Account bucket: key = HMAC(SECRET_KEY, `"account:"+lower(email)`) (no PII stored);
    **failed attempts only**; atomic DB-enforced upsert inside `fn_login_material`;
    blocked while count > 5 per 1-minute window; **reset on successful login** via
    `fn_login_success`. Shared across all instances/providers by construction.
  - Source bucket: key = HMAC(SECRET_KEY, `"ip:"+source`); **failed attempts only**; blocked
    at the pre-check when the live window already holds ≥ 30 failures; successes never
    consume it.
  - Trusted proxy model: `TRUST_PROXY` unset → source is the TCP peer, XFF never read;
    `TRUST_PROXY=1` → ProxyFix `x_for=1`, i.e. the rightmost proxy-appended XFF entry; valid
    only with an ingress that overwrites/extends client chains and blocks direct origin
    access; unparseable values → shared conservative `unknown` bucket; IPv4/IPv6 normalized
    via `ipaddress`.
  - Redis: not used; buckets are PostgreSQL rows. DB unavailable → sanitized 503, fail
    closed; no process-local limiter state, so multi-provider semantics are unchanged.
  - Successful logins: reset the account bucket, never touch the source bucket (deliberate,
    tested — the old consume-on-success self-lockout is gone).
- **Exploit replay** (`tests/test_sprint3_login_limits.py`, 13 tests): direct-client shared
  source throttles only after 30 *failures*; trusted-proxy legitimate forwarding works;
  untrusted XFF cannot create fresh buckets; spoofed chains take the rightmost value only;
  rotated-IP attack on one account still capped at 5 failures/min; one account across many
  networks capped; 25 successful logins from one source all pass; success grants a fresh
  failure budget; window expiry restores access; buckets shared across instances; IPv6 +
  malformed headers never crash; 8 concurrent failures yield exactly `[401]×5 + [429]×3`
  (atomic upsert, no lost updates); DB outage → 503 fail-closed.
- **Residual risk**: with `TRUST_PROXY=1` and an ingress that does not sanitize forwarding
  headers, sources are attacker-chosen — the account bucket (DB-enforced) is the surviving
  control; ingress contract is documented and remains a deployment gate.

## 4. F-3 — resource-exhaustion / unbounded retrieval

- **Complete inventory** (searched lists, reports, exports, dashboards, searches,
  aggregations, CSV, background jobs, admin listings): unbounded paths were the nine tenant
  lists (vehicles, drivers, customers, warehouses, routes, deliveries, payments,
  notifications — plus tracking), the five report queries, shipment status history; already
  bounded: shipment list pagination (limit ≤ 200 / offset ≤ 10 000), dashboard (LIMIT 5 +
  bounded aggregations), outbox worker (≤ 1000/job), CLI.
- **Remediation**: server-side constants enforced in SQL — `LIST_HARD_CAP` = `REPORT_HARD_CAP`
  = 2000, `TRACKING_HARD_CAP` = 500, `HISTORY_HARD_CAP` = 1000 (newest rows, returned
  oldest-first with a new deterministic `(timestamp, history_id)` tiebreak — a real
  non-determinism bug the new tests exposed and fixed). Tracking previously lacked SQL-level
  role scoping and now scopes in the query itself. Client input cannot raise any cap
  (reports accept no limit parameter). Bounds documented in `docs/DATA_CONTRACTS.md`.
- **Adversarial results** (`tests/test_sprint3_resource_bounds.py`, 8 tests): 2050-row tenant
  returns exactly the caps on lists and all five reports; EXPLAIN (FORMAT JSON) shows the
  PostgreSQL `Limit` node (no full materialization); caps do not weaken isolation (over-cap
  tenant A never sees B rows; B staff see only B); tracking scoped + capped (600 active
  rows → exactly 500, foreign driver sees none); history newest-1000 with stable order;
  concurrent large requests all bounded; invalid/boundary pagination still 400s / max works.
- **Residual risk**: responses beyond the caps truncate by design (documented); cursor
  pagination and streaming export jobs remain release-gated for oversized tenants; the bound
  is row-count-based, not a hard per-request memory guarantee (statement timeout backstop
  applies).

## 5. Tests

| Suite | Count | Result |
|---|---|---|
| Baseline (security + contracts) | 71 | pass |
| Red-team probes (Phase 2; 1 replaced by closed-boundary contract, documented) | 33 | pass |
| Sprint 3 F-1 | 7 | pass |
| Sprint 3 F-2 | 13 | pass |
| Sprint 3 F-3 | 8 | pass |
| **Total** | **132** | **132 passed in 178.60s (final run)** |

Runtime: Python 3.11.2, PostgreSQL 16.2. Static gates: compileall clean, `ruff
--select F821,F401` clean, `git diff --check` clean, all JS `node --check` clean,
`pip-audit -r requirements.txt` → "No known vulnerabilities found", secret scans clean.

## 6. Security regression

| Question | Answer | Evidence |
|---|---|---|
| Can runtime SQL read password hashes? | **PASS — No** | 4 exploit variants `permission denied` as `apex_app` (F-1 probes); column grants inspected from `information_schema` |
| Can an attacker spoof source identity? | **PASS — No (direct) / documented with TRUST_PROXY** | XFF ignored unproxied (matrix 3); rightmost-only under trusted proxy (matrix 4); ingress contract documented |
| Can one account be attacked indefinitely by rotating IPs? | **PASS — No** | matrices 5 & 6: 429 after 5 failed attempts regardless of source rotation (DB-enforced) |
| Can one shared network unintentionally throttle every account? | **PASS — No for legitimate traffic** | 25 successful logins from one source all pass (matrix 7); source bucket counts failures only (matrix 1) |
| Can tenant-wide API requests force unbounded result retrieval? | **PASS — No** | caps verified at 2050-row tenants, EXPLAIN Limit node (F-3 probes) |
| Can cross-tenant resource access occur? | **PASS — No** | full isolation probe file + over-cap isolation test; RLS boundary unchanged |
| Did any previous security invariant regress? | **PASS — No** | all 71 baseline + 33 red-team tests pass unchanged (except the one probe replaced because it encoded the F-1 vulnerability itself) |

## 7. Files changed

Production:
- `migrations/002_credential_boundary.sql` (new) — F-1 privilege boundary + DB-enforced account budget.
- `backend/auth.py` — F-2 contract implementation; login now consumes `fn_login_material`; source normalization.
- `backend/services.py` — F-3 caps (lists, tracking constant, history bound + deterministic order).
- `backend/reports.py` — F-3 report caps.
- `backend/tracking.py` — F-3 SQL-level scoping + cap.

Tests:
- `tests/test_sprint3_f1.py`, `tests/test_sprint3_login_limits.py`, `tests/test_sprint3_resource_bounds.py` (new).
- `tests/test_redteam_isolation.py` — F-1 probe replaced by closed-boundary contract (documented in-file).
- `tests/test_contracts.py` — two harness fixes exposed by having ≥ 1→2 migrations (row-specific checksum tamper; migration count from files). No assertion weakened.

Migrations: `002_credential_boundary.sql` (new; additive privileges only, no data change).

Docs: `docs/SECURITY_MODEL.md` (privilege boundary + login-limit contract), `docs/DATA_CONTRACTS.md`
(retrieval/export bounds), `docs/RED_TEAM_REPORT.md` (Sprint 3 remediation record), `SECURITY_AUDIT.md`
(verification counts, Sprint 3 controls, 14-package audit figure).

## 8. Remaining release gates (unchanged, not closed)

Cloud failover; Docker image build; legacy-data ownership migration; real OTP delivery;
OTP reissuance lifecycle; rolling-deployment compatibility (boot fails closed on schema/app
mismatch — expand/contract procedure not implemented); operational backup automation
(restore *mechanism* verified in Phase 2; operations unverified); RPO/RTO; load testing;
storage redundancy; real outbox consumer with deduplication; CSP/DOM-XSS frontend audit;
bounded export jobs for over-cap tenants.

## 9. Final decision

**READY FOR SPRINT 4.**

Evidence: both original exploits now fail at the correct boundary (DB privilege denial for
F-1; DB-enforced failed-attempt budget for F-2), the F-3 paths are capped inside PostgreSQL
with plan-level proof, legitimate behavior is preserved (logins, sessions, revocation,
business flows — 104 pre-existing tests pass unchanged), and the full suite of 132 tests is
green in 178.60s with all static/audit gates clean. No new P0/P1 issues were discovered
during remediation; one test-harness defect (ambiguous checksum tampering) was found and
fixed. Residual risks are documented, bounded, and assigned to existing release gates — none
blocks the next engineering sprint.
