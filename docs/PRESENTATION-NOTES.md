# APEX FLOW — Presentation Notes & Viva Defence Guide

Companion to **`presentation/index.html`** (18 slides).

Everything below is derived from this repository at commit `47d7308`, plus measurements taken by running the
application in a sandbox on **2026-09-12** (Python 3.11, dev-mode Flask on `127.0.0.1`, fresh SQLite database seeded
by `backend/database.py:initialize_database()`). Where something is a *code-level inference* rather than a
*measured* result, it is labelled as such. Do not upgrade one into the other while speaking.

---

## 1. The 30-second explanation

> APEX FLOW is a full-stack transport and logistics system: a Flask REST API with 26 endpoints over 21 URL paths, a
> framework-free HTML/CSS/JS client with 15 pages, and a 12-table SQL database that runs on SQLite locally and
> PostgreSQL in production. It models the whole lifecycle — booking, fleet and driver management, live tracking,
> an OTP-verified delivery confirmation, invoicing and KPI reporting. The part I would defend hardest is not the CRUD:
> it is the security layer — a documented 15-finding audit that I verified by attacking the running API — and a CI
> agent that reads my own deployment logs and diagnoses failures without ever being able to leak a credential.

---

## 2. The 2-minute explanation

**Problem.** In a small logistics operation, one shipment's truth is spread over a spreadsheet, WhatsApp messages
and a driver's memory. There is no shared status history, no proof of delivery that survives a dispute, and no place
where a vehicle's insurance or fitness expiry can be *queried*. The repository encodes that domain: 12 tables,
4 roles, 10 legal shipment states.

**Solution.** A single-origin web app. Flask serves the static client and a `/api` blueprint, so there is no
cross-origin hop. Every endpoint returns one JSON envelope, `{success, message, data}`, produced by one helper.
Business rules live in a service layer: `create_shipment()` turns one POST into five INSERTs behind a single `commit()` — the shipment,
its first status-history row, a delivery row carrying a 6-digit CSPRNG OTP with a 30-minute expiry, an invoice with
18 % GST, and a notification. The delivery's `Delivered` state can only be reached through OTP verification,
which then cascades back: delivery closed, invoice marked paid, history appended.

**How it works internally.** Requests pass a decorator gate (`@login_required`, `@require_role`) that both enforces
authorisation and writes a structured audit event, so enforcement and evidence cannot drift apart. Reads are
role-filtered in SQL — a CUSTOMER's list query carries `AND customer_id = ?` — and detail reads re-check ownership
for IDOR. Data access is hand-rolled SQL through a thin wrapper that switches between `sqlite3` and `psycopg2`
by rewriting placeholders, plus a `dict` subclass that makes Postgres rows indexable by position like SQLite rows.
Tracking is a time-parameterised interpolation between city coordinates; there is no GPS device feed.

**Engineering.** The repo carries its own quality machinery: custom Semgrep rules, gitleaks in pre-commit and CI,
a Conventional-Commits git hook, `render.yaml` as infrastructure-as-code with secrets excluded, and a
1,121-line stdlib-only ops pipeline (3,791 lines counting docs and workflow YAML, against 1,914 of backend) that observes the Render deployment for the pushed commit, classifies the failure
into one of 12 (category, subcategory) signatures across 4 top-level categories, redacts every byte it writes through a three-layer sanitizer, and either escalates to a
human or opens a PR — with a hard attempt cap and no automatic merge.

**What I did to earn the claims.** I ran the system and tested it. Mass-assignment protection, IDOR, RBAC, cookie
flags, the OTP lockout and the SQL parameterisation all held up under direct attack. Three things did not: the
per-endpoint rate limits documented in the README never fire, a customer cannot actually complete a booking (HTTP 500),
and one failed database write leaves a connection holding SQLite's write lock so every later write fails. Those are
on the slides, with the reproductions.

---

## 3. Important technical decisions

| # | Decision | Reasoning visible in the code | What it cost |
|---|---|---|---|
| 1 | **Sessions over JWT** — `HttpOnly`, `SameSite=Lax`, `Secure` when not DEBUG, 8 h lifetime, `session.clear()` before issuing | The only client is the browser; a bearer token would have to live in `localStorage`, where XSS can read it. Server-side sessions stay revocable. | No natural story for mobile or machine-to-machine clients. CSRF rests on `SameSite` alone — there is no token. |
| 2 | **Decorator-based authorisation** (`@login_required`, `@require_role`) | 25 of 26 routes carry one (only `/auth/login` is public, by design). Protection is a *property of the route*, reviewable in a diff. | Correctness depends on remembering the decorator. `.semgrep.yml` is the only automatic backstop. |
| 3 | **Field allowlist instead of a serialiser library** | `_SHIPMENT_ALLOWED_FIELDS` (`backend/services.py:23`) + server-set `status`, `payment_status`, `shipping_cost`. Zero new dependencies. | Hand-maintained lists. One field (`customer_id`) is read from raw `data` and bypasses the filter — the exact bug the fix was supposed to prevent. |
| 4 | **Raw SQL, no ORM** | ~1,900 lines, all queries parameterised with `?`; integrity pushed into the schema (10-value status `CHECK`, FKs with `ON DELETE CASCADE` where declared). | No migrations — a hand-rolled idempotent `ALTER TABLE` block wrapped in `except Exception: pass`. No pooling; a connection is opened and closed per service call. |
| 5 | **Dual-engine DB wrapper** | `get_db_connection()` picks Postgres when `DATABASE_URL` is set, else SQLite; `PostgresCursorWrapper` rewrites `?`→`%s` and strips `AUTOINCREMENT`; `PostgresRow(dict)` adds integer indexing. | Text replacement is not a parser (it would rewrite inside string literals too). A Postgres connect failure **silently falls back to SQLite** — a production instance could quietly write to a file. |
| 6 | **Framework-free frontend** | `fetchAPI()` centralises fetch + error toast; one JS module per page; `canvas` draws the map and bars. | No build step means no lint/type/test tooling; 24 `innerHTML` renderings interpolate server strings without escaping. |
| 7 | **OTP as the delivery gate, never returned by the API** | `secrets.randbelow(900000)+100000`, `otp_expires_at = +30 min`, `otp_attempts` lockout at 5, `get_all_deliveries()` deliberately omits the `otp_*` columns. | Plaintext at rest and a non-constant-time `!=` comparison (a hash would break the compare); no re-issue endpoint, so an expired OTP is a dead end. |
| 8 | **Bounded ops automation** | stdlib `urllib` only; GET-only client; `TokenGuard` strips the literal key from every output; `MAX_ATTEMPT = 1`; never pushes to `main`; never auto-fixes `auth/*`. | The "fix" stage only *classifies* — it never edits a file, so `result == 0` is unreachable and the PR path never runs. It is a diagnosis agent wearing a remediation name. |

---

## 4. Questions the professor is likely to ask

1. Why Flask and vanilla JS instead of Django/React? Isn't that reinventing frameworks?
2. Where are the tests? Your CI has a job called `test-sqlite` — what does it run?
3. You keep saying "verified". Verified how, and against what?
4. Why raw SQL instead of an ORM? How do you avoid SQL injection?
5. Sessions or JWTs — justify the choice for a system that has a *mobile* use case ("access from any device").
6. What protects against CSRF, given there is no token?
7. Your shipping cost is `base + weight×2.5 + f(hash(city-pair))`. Is that an algorithm or a placeholder?
8. Is the "live GPS tracking" real? Show me where GPS data enters the system.
9. `POST /api/routes/optimize` — which optimisation algorithm did you implement?
10. What is your rate-limiting strategy, and is it shared across workers?
11. Where is the OTP stored, and how is it compared? What stops brute force?
12. Why is `otp_code` absent from the deliveries API but present in the front-end template?
13. Your CI runs gitleaks but the audit says a `SECRET_KEY` was committed historically. Explain.
14. What exactly does the "self-healing" pipeline do on a failure, end to end?
15. How would this behave with 1,000 concurrent shipments? What breaks first?
16. Which part of this codebase are you least proud of?
17. If you had one week, what would you fix first — and why that order?
18. Does the system actually dispatch a vehicle and a driver to a shipment?
19. Is a `SELECT *` in `get_shipment_by_id()` a problem? Your Semgrep rule was written to catch it.
21. How do you prevent a customer from reading another customer's shipment? Show me the code.
22. This looks like a management information system, not a logistics *system*. What is missing to make it one?

---

## 5. Strong answers

**1 — Why this stack.** The requirement set is 26 JSON routes, sessions and 4 roles; Django would supply an admin,
an ORM and an auth stack I would then have to reason *around*. The audit is the interesting deliverable, and hand-written
SQL plus explicit decorators keep every control visible in one file. On the client: the app is 15 pages of tables, forms
and one `canvas`. A framework buys component reuse I do not need and adds a build step to a project that a reviewer
should be able to run with `pip install -r requirements.txt && python3 backend/app.py`. The cost — no linting, no typing,
no escaping — is stated on slides 13 and 16 rather than hidden.

**2 — Tests.** There are none. Zero test files exist in the tree, and the CI job is written so it exits 0 when
`tests/` is missing (`.github/workflows/ci.yml`, lines in the `test-sqlite` job). I put that on a slide with the
verbatim shell condition, because a green job that runs nothing is a worse signal than a red one. My replacement was
manual API testing (see Q3), and the first three tests I would commit are listed on slide 13.

**3 — How I verified.** I started the app, seeded the DB, and drove the HTTP API with a throwaway `urllib` harness
(cookie jars per role). Concretely: an unauthenticated `GET /api/shipments` → 401; a CUSTOMER on `/api/customers` →
403; `GET /api/shipments/SHP001` as another customer → 403; a booking carrying `status/payment_status/shipping_cost`
→ 201 with the injected values discarded and cost recomputed (₹3 169); a delivery list with 12 columns and no `otp_*`
key; four wrong OTPs returning "4 … 1 attempt(s) remaining", the fifth locking, and the correct code then refused;
215 rapid requests producing the first 429 at request 201. Section 8 has the full log and commands.

**4 — Raw SQL / injection.** Every query in `backend/` uses `?` placeholders with a parameter tuple — including the
search filter, where the `LIKE` term is bound as a parameter, so `?search=' OR 1=1--` returned an empty result set
instead of a dump. `initialize_database()` sets `PRAGMA foreign_keys = ON`, so the FKs are actually enforced — that is
what turns my `customer_id` bug into a 500 rather than silent cross-tenant writes. The reason there is no ORM is
that the aggregate KPI SQL in `reports.py` is clearer written out; the price is no migration tooling, which I would pay
with Alembic next.

**5 — Sessions vs JWT.** "Accessible from any device" is achieved by serving the client from the same origin over HTTPS
(`render.yaml`, `Procfile`), so the browser keeps the cookie — no token storage needed. A JWT would add revocation
problems for a system that has none (no token blacklist table exists). If a native client became a requirement, I would
add scoped API tokens with their own rate-limit bucket rather than retrofitting the session.

**6 — CSRF.** `SameSite=Lax` blocks cross-site *POST* bodies, which is the entire mutating surface here
(`POST/PUT/DELETE`; Lax still sends cookies on top-level GET navigations, which are read-only). Honest supplement:
there is no synchroniser token and no `Origin` check, so a same-site subdomain or a lax-browser edge case is
unmitigated, and there are no `Content-Security-Policy` / `X-Frame-Options` headers anywhere in the repo. Slide 12
lists it as a gap.

**7 — The cost function.** It is a placeholder priced by formula, and I say so on slide 10: `1500 + kg×2.5 +
(hash(pickup+destination) % 3000 + 500) × 0.5`. Worse than a placeholder, though: Python salts `str` hashing per
process, so I measured the *same* 8 000 kg Ludhiana→Delhi booking priced anywhere from ₹21 845 to ₹23 042 across
seven processes. It is not reproducible, which for an invoice is a defect, not a simplification. Fix: the `routes`
table already holds real distances, or use `zlib.crc32` if a stable pseudo-value is all that is wanted.

**8 — GPS.** There is no ingest path. `backend/tracking.py` computes
`current_time_factor = (int(time.time()) % 100) / 100.0` and linearly interpolates between two city coordinates, with
speed from `58 + int(sin(t)×12)`. I confirmed the behaviour: progress advanced 10 %→14 % in the 4 s between two polls
— one clock shared by every vehicle, resetting every 100 s — and with nothing in transit the module returns a
hardcoded SHP001 record. It is a *simulation of* live tracking, and it is presented as such on slide 10 and slide 16.
A real system needs a device ingest endpoint plus a `positions` table; the response shape would not change.

**9 — "Optimisation".** `optimize_route()` looks the corridor up in the `routes` table (case-insensitive on both
ends) and returns it; otherwise it synthesises `250 + (len(pickup)+len(destination))×12` km, an ETA at 65 km/h, fuel at
₹8.6/km and three generated stop names. So: a cache-then-heuristic lookup, no graph, no solver, no OR-Tools. Measured:
Ludhiana→Chennai = 430 km (real ≈ 2 400 km) and "Ludhiana→Che" = 382 km, because distance is a function of string
length. Naming the endpoint `optimize` oversells it; I renamed it in my own conclusion, not in the code, which is the gap.

**10 — Rate limiting.** Three buckets are configured: default 200/min globally, 10/min on login, 5/min on OTP
confirmation, storage `memory://` unless `REDIS_URL` is set. Measured: the global limit works — first 429 at request
201 with `X-RateLimit-*` headers. The two per-route limits do **not** apply: 25 rapid bad logins produced 25 × 401 and
no limit headers, and this reproduced on both `flask-limiter` 4.1.1 and 3.5.1, so it is not a version drift. Root cause:
`app.py` wraps the views with `limiter.limit(...)` *after* `register_blueprint()`, which that library does not re-read;
there is even an unused `set_limiter()` (`backend/routes.py:24`) that looks like the abandoned correct wiring. Two more
production-relevant facts: there is no `ProxyFix`, so `get_remote_address()` sees the platform's proxy IP (I confirmed a
spoofed `X-Forwarded-For` was ignored, and the audit log recorded `127.0.0.1`), and in-memory storage is per-worker.

**11 — The OTP.** Generated by `secrets.randbelow(900000)+100000` — 6 digits, 900 000 space, not `random.randint`,
and a Semgrep rule (`uuid.uuid4().int % $N`) exists to stop the old pattern coming back. Stored plaintext in
`deliveries.otp_code` with `otp_expires_at` and `otp_attempts`. `confirm_delivery()` checks lockout, then expiry, then
equality, persisting the counter *before* responding — so a client cannot slow-roll the attempts. Brute force is bounded
by 5 attempts, then permanent lockout for that shipment; a *correct* code after lockout is also refused, which I
measured. The accepted weaknesses: plaintext at rest, `!=` instead of `secrets.compare_digest`, no re-issue endpoint, and
lockout keyed per shipment rather than per account.

**12 — The `otp_code` mismatch.** That is a real defect I found, and it runs both ways. F-05 correctly removed the
`otp_*` columns from the API projection, but `frontend/js/deliveries.js` still renders `${d.otp_code}` and pre-fills the
confirm modal from it — so the column shows `undefined` and the "expected OTP" hint is empty. The security half shipped;
the client half did not. (And the *seeded* deliveries still carry 4-digit codes like `4912`, from the pre-patch schema.)
Fix: an ops-only "reveal OTP for this delivery" endpoint with its own audit event, or remove the UI affordance.

**13 — Gitleaks and the historical secret.** `.gitleaks.toml` allowlists exactly two commit SHAs by hash — not by path,
not by rule — for the `SECRET_KEY` that was committed before remediation; the value was rotated in the Render
environment and appears nowhere in the current tree. I want to be precise about what that means: gitleaks now passes,
but the secret *is* still in history, and the audit itself grades this "Mitigated", not "Fixed". If the repo is
published, history needs `git-filter-repo` plus rotation. Also worth noting: my sandbox clone has a squashed
single-commit history, so I could not personally inspect those commits — I am reporting what the config and audit say.

**14 — The pipeline end to end.** Push to `main` → `render-observe.yml` starts, concurrency-grouped per SHA.
`render_observe.py` discovers the service (env var, else paginated `GET /v1/services` matched on repo URL, ambiguous
match → exit 5), polls until a deploy for *that commit SHA* reaches a terminal state, and on failure pulls log lines,
classifies with 21 fragments into 12 (category, subcategory) pairs across 4 categories, and writes a bundle — every byte through `render_sanitize.py`
(JSON key walk → 10 text patterns → literal `TokenGuard` strip). `remediate` runs only when the deploy failed, exactly
one safe category matched, no escalation condition fired and the attempt counter is below the cap; it runs
`render_fix.py`, and if the working tree changed it pushes `agent/render-fix-<sha>` and opens a PR — never `main`,
never auto-merged. `escalate` writes a human-facing notice instead. Measured limitation: both fixers only *report*
(they return 1 or 2), so the PR branch is unreachable today; and `docs/render-agent.md` claims "9-layer sanitization"
where the code implements three layers with ten patterns.

**15 — Scale.** The first thing to break is the write path: one connection per service call, no pool, and a
`commit()`-at-the-end that leaks the connection when an INSERT raises — I reproduced exactly that on a laptop, where a
single failed booking made every subsequent write fail with `database is locked` after SQLite's 5 s timeout. It is not
raw contention (20 concurrent valid bookings all committed in 0.15 s), it is the leak. Then: no indexes beyond
PK/UNIQUE, `LIKE '%term%'` scans, unpaginated list endpoints, and an in-memory limiter that is per-worker and
per-proxy-IP. `SECURITY_AUDIT.md` itself flags the SQLite-in-production risk. The fix order is `try/finally`,
`tests/`, indexes + pagination, then Redis + `ProxyFix`.

**16 — Least proud of.** The F-02 mass-assignment fix, because it is half done: `customer_id` is read from the raw
body instead of the allowlisted `safe` dict. The FK constraint turns it into a 500 rather than an injection, but it is a
broken customer booking for a *documented* feature ("book and track your own shipments"), and the fix is one word. It is
the clearest evidence in the repo that a fix is not finished until its tests and its callers are updated — the front-end
`shipments.js` still posts `shipping_cost` and `payment_status`, which the server silently discards.

**17 — One week.** In this order: (1) `safe.get` for `customer_id` + a real user→customer mapping + `try/finally`
around connections — one day, removes a 500 and an outage mode; (2) commit my harness as `tests/test_api.py` and make
CI fail when `tests/` is absent — two days, converts "trust me" into a gate; (3) move the rate limits onto the view
functions, add `errorhandler(Exception)` so failures stop leaking SQL text, add CSP/HSTS/`ProxyFix` — two days;
(4) a transition table for the 10 states plus role scoping on `/deliveries` and `/notifications` — two days;
(5) either let `render_fix.py` write its one safe fix or delete the stage — half a day. Reasoning for the order: user-visible
breakage first, then verifiability, then hardening, then scale.

**18 — Dispatch.** No — and I can show the exact chain, which is the most instructive bug in the repo because four separate layers are each individually defensible. `frontend/shipments.html` renders `#shipmentVehicle` and `#shipmentDriver` selects; `frontend/js/shipments.js:61-62` posts them as `vehicle_reg` and `driver_name`; `_SHIPMENT_ALLOWED_FIELDS` (`services.py:23`) contains neither, so the allowlist filter drops them; `create_shipment()` then writes the literal `None, None, None, None` into `vehicle_id, vehicle_reg, driver_id, driver_name` (`services.py:113-124`); and there is no assignment endpoint — `grep -n "UPDATE vehicles\|SET vehicle_id" backend/*.py` returns nothing. So *no shipment is ever dispatched*, which is why `tracking.py:55-57` substitutes `VEH001` / `HR26BX4587` / “Rajesh Kumar” to keep the UI alive. The security fix (F-02) and the domain need collided, and the allowlist won: the correct resolution is not to widen the allowlist blindly but to add `vehicle_id`/`driver_id` to it **with an ownership + availability check** (`vehicles.status = 'Available'`), plus a `PUT /api/shipments/:id/assignment` so assignment is a deliberate, audited action rather than a side effect of booking.

**19 — `SELECT *`.** Two answers. In principle it is the smell that F-10 was about: `get_shipment_by_id()` and friends
`SELECT *`, and only the fact that `deliveries` is queried with an explicit column list keeps the OTP out of the API.
In practice, the guard meant to catch it does not work: I planted a `SELECT *` call in the backend and
`apexflow-select-star` never matched — its pattern is a *literal* string, so `cursor.execute("SELECT * FROM users
WHERE x = ?", …)` cannot match `"SELECT * FROM ..."`. Sixteen such sites exist today. So the finding is not
"the code is dirty", it is "the metric that says it is clean is broken" — which is exactly the class of error I want
credit for catching.

**19 — Customer isolation.** Two places, deliberately. List: `get_all_shipments()` appends `AND customer_id = ?`
with `params.append(caller_user_id)` when `caller_role == 'CUSTOMER'`, so the row never leaves the database.
Detail: `get_shipment()` in `routes.py` compares `shipment['customer_id']` against `session['user_id']` and returns
403. Measured both: `GET /api/shipments/SHP001` as the seeded customer → 403, and the list returned only rows bound to
that id. Caveat I volunteer: the same scoping does *not* exist on `/deliveries`, `/tracking` and `/notifications`, which
are `@login_required` only — a customer can enumerate delivery and telemetry rows for other customers' shipments.

**20 — MIS vs. system.** Missing to be operational: real ingest (GPS, and a device/scanner path for drivers), a
notification *delivery* channel (SMS/email) instead of an in-app table, an actual payment gateway (invoices are records,
not money movement), dispatch optimisation over multiple shipments and vehicle capacity constraints
(`capacity_mt` exists but nothing assigns vehicles to shipments after creation), audit-grade immutability, and
multi-tenancy. I would rather say that than imply the demo is the product.

---

## 6. Weak points the professor will spot

Ordered by how damaging they look, with whether I raise them first.

| Weak point | Reality | Do I raise it? |
|---|---|---|
| Zero automated tests behind a green `test-sqlite` job | Fact. The job skips when `tests/` is absent. | Yes — slide 13, unprompted. |
| Customer self-service booking returns HTTP 500 | Reproduced: `customer_id := user_id` (`USR004`) is absent from `customers`, FK rejects it. | Yes — slides 8, 14, 16. |
| Documented rate limits do not apply to `/auth/login` or `/deliveries/confirm` | Reproduced on two library versions; global default works. | Yes — slides 12, 14. |
| One failed DB write poisons subsequent writes | Reproduced deterministically; `conn.close()` is unreachable when an INSERT raises. | Yes — slides 12, 14. |
| "Live tracking" and "route optimisation" are simulations/formulas | Fact: `time.time() % 100`, and `250 + len(strings)×12`. | Yes — slide 10. |
| Freight price is not reproducible | `abs(hash(...))` is per-process salted; measured 7 values across a ₹1 200 spread. | Yes — slide 10. |
| No state-machine transition validation | Reproduced: `Delivered → Booked` → 200; a DRIVER moved an unassigned shipment to `Returned`. | Yes — slides 10, 12, 14. |
| Front end interpolates server strings into `innerHTML` with no escaping | Code-level finding (24 sites); stored payload returned verbatim. I could **not** execute it — no browser in the sandbox. | Yes — slide 16, with the caveat stated. |
| Docs/README overstate: "6 Semgrep rules" (there are 7), "9-layer sanitization" (3 layers / 10 patterns), `LICENSE` referenced but absent, `data/*.json` never read | All confirmed by reading + grep. | Yes — slides 11, 13, 16. |
| Nothing ever assigns a vehicle or driver | Full chain verified: UI selects → `shipments.js:61` posts `vehicle_reg`/`driver_name` → not in the allowlist → written `NULL` → no assignment endpoint → `tracking.py:55` fabricates `VEH001`. | Yes — slides 10, 14. |
| `maintenance_records` table exists with no read/write path anywhere | `grep -rn maintenance_records backend/` → only the `CREATE TABLE`. | Mention if asked about dead code. |
| Settings page and the dashboard's "Focus Telemetry" button are stubs | The form's `onsubmit` only shows a toast; the button fires a hardcoded toast string. | Mention if asked about UX completeness. |
| Plaintext seed credentials in git history | Audit says "Mitigated"; allowlisted by SHA after rotation. | Yes — slide 12/16, and the answer to Q13. |
| Single-writer SQLite, in-memory limiter, no pooling | Audit's own residual-risk register admits it. | Yes — slide 15/16. |
| `DELETE /api/shipments/:id` fails whenever an invoice exists | Reproduced: `payments` FK has no `ON DELETE CASCADE` → 500 `FOREIGN KEY constraint failed`. | Yes — slide 16. |

---

## 7. How to say the limitations honestly

Sentences to use verbatim — they keep the tone analytical instead of apologetic.

- "There are no automated tests in this repository. CI has a test job that succeeds when `tests/` is missing, so green
  does not mean verified. What I did instead was drive the live API and record the results — that is what the results
  slide is. My first contribution after this would be to commit that harness and make the gate strict."
- "The audit closes 15 findings, and I re-tested every one I could reach over HTTP. Fourteen hold. One does not: the
  per-endpoint rate limits are documented, wired in a way the library never reads, so they are inert. I verified it on
  two library versions so I could not blame a dependency bump."
- "Two of the fixes were half-migrations. The API stopped returning `otp_code`, but the front end still renders it, so
  the operator sees `undefined`. And the booking allowlist still reads `customer_id` from the raw body, which is why a
  customer booking 500s. Both tell me the same lesson: a security fix is not done when the backend says so — it is done
  when its callers and its tests agree."
- "The tracking page is a simulation. I can prove it in one line: every vehicle shares a single progress value derived
  from `time.time() % 100`. I chose to keep the simulation because the interesting engineering was the API contract,
  not the telemetry plumbing — but it must be labelled, or a demo becomes a claim."
- "Freight pricing uses `hash()`, which Python salts per process. So I measured an ₹1 200 spread on identical bookings
  across seven processes. That is not a rounding issue, it is a reproducibility failure, and the fix is a distance table
  that already exists in the schema."
- "My SAST guard for `SELECT *` never fires — the pattern is a literal string. I found that by planting violations,
  which I would recommend as a habit: a rule you have never seen fail is a rule you have not tested."
- "I am deliberately not showing you screenshots from a browser. There was no browser in my sandbox, so I showed raw
  API responses and log lines instead — you can reproduce every one from the notes."
- "What I would keep unchanged: the decorator-based authorisation, the server-owned money fields, the CSPRNG OTP with a
  persisted attempt counter, the three-layer redaction before any artifact leaves CI, and the fact that the ops agent can
  read logs but cannot leak the key it uses to read them."

If a question exposes something genuinely unknown, the answer is: "That is not measurable from this repository — I would
have to run it. Here is the experiment I would run." Then state the experiment. Never dress an inference as a measurement.

---

## 8. Claim → evidence map

**Verified by running the app** (sandbox, dev server, seeded SQLite):

| Claim | File | Symbol | How verified |
|---|---|---|---|
| Unauthenticated access is refused | `backend/auth.py:15` | `login_required` | `GET /api/shipments` with no cookie → 401 envelope |
| RBAC enforced | `backend/auth.py:29` | `require_role` | CUSTOMER → `GET /api/customers` → 403 "requires role: ADMIN or MANAGER" |
| IDOR guarded | `backend/routes.py:91` | `get_shipment` | customer → `GET /api/shipments/SHP001` → 403 |
| Owner-filtered list | `backend/services.py:33` | `get_all_shipments` | SQL gains `AND customer_id = ?` |
| Mass assignment blocked | `backend/services.py:77` | `create_shipment` | POST with `status/payment_status/shipping_cost` → stored `Booked / Pending / 3169.0` |
| Allowlist leak (`customer_id`) | `backend/services.py:103` | `create_shipment` | POST `customer_id:"CUST003"` → honoured (201); `"NOPE-404"` → 500 FK |
| Customer booking broken | `backend/services.py:103` | `customer_id = caller_user_id` | CUSTOMER POST → 500 `FOREIGN KEY constraint failed` |
| OTP: 6 digits, TTL, lockout | `backend/services.py:13`, `418` | `_generate_otp`, `confirm_delivery` | 4→3→2→1 "attempt(s) remaining", 5th locks; correct code after lockout refused |
| OTP never in API | `backend/services.py:403` | `get_all_deliveries` | response keys enumerated; no `otp_*` |
| Delivery cascade | `backend/services.py:194` | `update_shipment_status` | shipment `Delivered`, delivery `Delivered`, invoice `Paid` + `paid_date`, history row |
| Schema enum enforced | `backend/database.py:175` | `CHECK(status IN …)` | `PUT {"status":"Teleported"}` → `IntegrityError` |
| No transition validation | `backend/services.py:194` | `update_shipment_status` | `Delivered → Booked` → 200; DRIVER → unassigned `SHP003 = Returned` → 200 |
| Global rate limit works | `backend/app.py:42` | `Limiter(default_limits=…)` | 215 requests → first 429 at #201, `X-RateLimit-*` present |
| Per-route limits inert | `backend/app.py:70-72` | post-register `limiter.limit` | 25 logins → 0 × 429, no headers; same on `flask-limiter` 3.5.1 |
| Proxy IP not honoured | `backend/app.py` (no `ProxyFix`) | `get_remote_address` | `X-Forwarded-For: 8.8.8.8` → audit log recorded `127.0.0.1` |
| Cookie hardening | `backend/app.py:27` | `app.config.update` | `Set-Cookie` shows `HttpOnly; SameSite=Lax` |
| No injection | all of `backend/` | parameterised `?` | `?search=' OR 1=1--` → empty list |
| Connection leak after failure | `backend/services.py:77` | missing `try/finally` | after one failed INSERT, next valid write 500s `database is locked` after 5.02 s |
| 20 concurrent bookings fine | same | — | 20 parallel POSTs → 20 × 201 in 0.15 s |
| Cost not reproducible | `backend/services.py:186` | `_calculate_shipping_cost` | 7 processes: ₹21 845 … ₹23 042 (also `PYTHONHASHSEED=0..3`) |
| Client may pick the customer | `backend/services.py:103` | `data.get('customer_id', 'CUST001')` | fresh DB: omit → `CUST001`, send `CUST003` → stored `CUST003` (201), unknown → 500 |
| Booking writes 5 rows | `backend/services.py:77` | `create_shipment` | grep of the function: 5 `INSERT INTO` (shipments, history, deliveries, payments, notifications), 1 `commit()`, 1 `close()` |
| Deleting an invoiced shipment fails | `backend/services.py:238` | `delete_shipment` | ADMIN `DELETE /api/shipments/SHP002` → 500 `FOREIGN KEY constraint failed` (`payments` has no `ON DELETE CASCADE`) |
| Dispatch never happens | `backend/services.py:113` | `create_shipment` INSERT tuple | posts `vehicle_reg`/`driver_name`; both outside `_SHIPMENT_ALLOWED_FIELDS`; `None, None, None, None` stored; `grep 'SET vehicle_id' backend/*.py` → empty |
| Only 4 corridors are real | `backend/database.py:412` | `seed_demo_data` `routes_data` | 4 rows (`RTE001` Delhi–Jaipur 286 km … `RTE004`); `data/routes.json` has 2 rows and no reader |
| Tracking is simulated | `backend/tracking.py:28` | `get_live_tracking_data` | progress 10 %→14 % in 4 s, identical across vehicles |
| Route heuristic | `backend/routes.py:249` | `optimize_route` | Ludhiana→Chennai 430 km; →"Che" 382 km; Delhi→Jaipur → cached `RTE001` |
| Dashboard KPIs real | `backend/reports.py:4` | `get_dashboard_kpis` | 5 shipments, 2 in transit, ₹99 200 revenue, 2/5 vehicles |
| Delete blocked by FK | `backend/services.py:238` | `delete_shipment` | ADMIN `DELETE /api/shipments/SHP002` → 500 FK (payments row exists) |
| HTML stored verbatim | `backend/services.py` (no escaping) + `frontend/js/shipments.js:27` | `renderShipmentsTable` | `<img src=x onerror=…>` accepted, stored, returned unescaped (execution not tested) |
| Static gates pass | `ci.yml` steps 1, 3, 4, 5 | — | `py_compile` ✓ · `semgrep --error` ✓ (0 findings) · YAML parse ✓ · no prohibited files tracked ✓ |
| SAST rule effective | `.semgrep.yml` | 6 of 7 rules | planted violations in *copies* of `services.py`/`routes.py` fired all path-scoped rules |
| SAST rule inert | `.semgrep.yml` | `apexflow-select-star` | planted `SELECT *` not matched; 16 real sites in `backend/` |
| Ops-agent counts | `scripts/render_observe.py:85` | `FAILURE_PATTERNS` | 21 rows → 12 distinct (category, subcategory) pairs over 4 categories |
| Redaction depth | `scripts/render_sanitize.py:73` | `_REDACTION_PATTERNS` | 10 patterns, 3 public entry points (`sanitize_json`, `sanitize_text`, `sanitize_token`); docs claim 9 layers |
| No pagination on list endpoints | `backend/reports.py:35,39` | only `LIMIT`s in the backend | `grep -c "\bLIMIT\b" backend/*.py` → 2 (dashboard side-panels), `OFFSET` → 0 |
| Single 429 handler; dead helpers | `backend/app.py:51`, `backend/logger.py:104` | `log_app_error` | `grep errorhandler backend/*.py` → only 429; `log_app_error` has 0 call sites; correlation-ID/request-id refs → 0; `CREATE INDEX` → 0 |
| Two UI stubs | `frontend/settings.html`, `frontend/js/dashboard.js:69` | `trackLiveDemo` | settings form is `onsubmit="event.preventDefault(); showToast(…)"` with no API call; the dashboard button toasts a hardcoded vehicle/ETA string |
| Fixer can never succeed | `scripts/render_fix.py:202` | `main()` | both fixers return only 1 or 2, so `if result == 0:` (validation + PR) is unreachable; workflow gate is `git diff --quiet HEAD` |

**Code-level inference only** (say so if asked):

| Claim | Evidence | Status |
|---|---|---|
| Stored XSS is exploitable in a browser | 24 `innerHTML` sites in `frontend/js/*.js`; no escaping helper; backend returns raw strings | Inference — browser unavailable in sandbox |
| `SECRET_KEY` history leak remediated by rotation | `.gitleaks.toml` comments + audit F-07 | Documented, not inspectable: my clone has 1 squashed commit |
| Silent SQLite fallback in production | `get_db_connection()` `except` → prints and falls back | Inference from code; not exercised (no Postgres) |
| Unreachable PR-creation branch | `render_fix.py` returns only 1/2/3/4; workflow gates on `git diff --quiet` | Static reading of code + YAML |

**Documented-but-unverifiable** (report as claimed):
- `docs/SECURITY_AUDIT.md` "All 15 findings remediated" — 14 reproduced, 1 (rate limits) does not hold.
- `docs/render-agent.md` "9-layer sanitization" — implementation is 3 layers, 10 patterns.
- README "MIT License — see LICENSE" — no `LICENSE` file in the tree.
- README "Seed JSON data files" in `data/` — nothing in `backend/` or `frontend/` reads them.

---

## 9. Reproducing my measurements

```bash
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt            # Flask 2.3+/3.x, flask-cors, flask-limiter, limits, gunicorn, psycopg2, dotenv
python3 backend/app.py &                   # dev mode: ephemeral SECRET_KEY, seed passwords default

# authorisation + RBAC + IDOR
curl -s localhost:5050/api/shipments                                     # 401
curl -s -c /tmp/adm -X POST localhost:5050/api/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"email":"admin@apexflow.com","password":"Admin@123"}'           # Set-Cookie: HttpOnly; SameSite=Lax
curl -s -b /tmp/adm localhost:5050/api/reports/dashboard                  # real KPI aggregates

# mass assignment: status / payment_status / shipping_cost must be ignored
curl -s -b /tmp/adm -X POST localhost:5050/api/shipments \
     -H 'Content-Type: application/json' -d '{"customer_name":"QC","pickup_location":"Ludhiana",
       "destination":"Delhi","weight_kg":500,"customer_id":"CUST001",
       "status":"Delivered","payment_status":"Paid","shipping_cost":1}'
# → 201, status "Booked", payment_status "Pending", shipping_cost recomputed

# OTP lockout (create a shipment, then try wrong codes; the code lives only in the DB)
sqlite3 data/apexflow.db "SELECT otp_code,otp_attempts,otp_expires_at FROM deliveries ORDER BY rowid DESC LIMIT 1"
for i in 1 2 3 4 5; do curl -s -b /tmp/adm -X POST localhost:5050/api/deliveries/confirm \
  -H 'Content-Type: application/json' -d "{\"shipment_id\":\"<ID>\",\"otp_code\":\"00000$i\"}"; done
# → "4 attempt(s) remaining" … then "OTP locked after too many failed attempts."

# rate limiting: global fires, per-route does not
for i in $(seq 1 215); do curl -s -o /dev/null -w '%{http_code} ' -b /tmp/adm localhost:5050/api/notifications; done   # 429 at 201
for i in $(seq 1 25);  do curl -s -o /dev/null -w '%{http_code} ' -X POST localhost:5050/api/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"a@b.c","password":"bad"}'; done                                     # no 429

# cost non-determinism
for s in 0 1 2 3; do PYTHONHASHSEED=$s python3 -c "
import sys; sys.path.insert(0,'.')
from backend.services import _calculate_shipping_cost as c; print(c(8000.0,'Ludhiana','Delhi'))"; done

# SAST probe: copy the two target files, inject one violation per rule, run from the REPO ROOT,
# then restore. (Running it from a scratch directory makes semgrep scan 0 files and looks like
# "the rules never fire" — that false negative is how I nearly under-claimed on slide 13.)
cp backend/services.py backend/routes.py /tmp/probe_backup/
# ... inject, then:
semgrep --config .semgrep.yml backend/ --json | jq '.results[].check_id'
cp /tmp/probe_backup/*.py backend/

# one-shot re-measure of every count printed on the slides (~2 s, read-only)
python3 - <<'EOF'
import re,glob,os
L=lambda p:"".join(open(f).read() for f in glob.glob(p))
b=L("backend/*.py"); j=L("frontend/js/*.js")
print("backend LOC      ", sum(len(open(f).read().splitlines()) for f in glob.glob("backend/*.py")), "(expect 1,914)")
print("js LOC / modules ", sum(len(open(f).read().splitlines()) for f in glob.glob("frontend/js/*.js")), "/", len(glob.glob("frontend/js/*.js")), "(1,199 / 14)")
print("html LOC / pages ", sum(len(open(f).read().splitlines()) for f in glob.glob("frontend/*.html")), "/", len(glob.glob("frontend/*.html")), "(2,019 / 15)")
print("css LOC / files  ", sum(len(open(f).read().splitlines()) for f in glob.glob("frontend/css/*.css")), "/", len(glob.glob("frontend/css/*.css")), "(1,109 / 5)")
print("route handlers   ", len(re.findall(r"@api_bp.route", L("backend/routes.py"))), "(26)   auth decorators:", len(re.findall(r"@login_required|@require_role", L("backend/routes.py"))), "(25)")
print("tables / FK decl ", len(re.findall(r"CREATE TABLE IF NOT EXISTS", L("backend/database.py"))), "/", len(re.findall(r"FOREIGN KEY", L("backend/database.py"))), "(12 / 5 on 5 tables)")
print("SELECT * sites   ", len(re.findall(r"SELECT \* FROM", b)), "(16)   innerHTML sites:", len(re.findall(r"innerHTML", j)), "(24)")
print("innerHTML+interp", len(re.findall(r"\$\{[^}]*\}", j)), "template interpolations; escaping helper:", "escapeHtml" in j or "sanitize" in j)
print("semgrep rules    ", len(re.findall(r"id: apexflow", open(".semgrep.yml").read())), "(7)")
# do NOT use glob("tests/**") here: CPython returns a phantom ['tests/'] for a missing dir
print("tests/ present   ", os.path.isdir("tests"), "| test files tracked:",
      len([l for l in __import__("subprocess").run("git ls-files",shell=True,capture_output=True,text=True).stdout.split("\n")
           if re.search(r"(^|/)(test_[^/]*|[^/]*_test)\.py$", l)]), "(False / 0 — this is why the CI test job skips)")
EOF

# static gates, exactly as CI runs them
python3 -m py_compile backend/*.py
semgrep --config .semgrep.yml backend/ --error
python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/**/*.yml',recursive=True)+['.pre-commit-config.yaml','.semgrep.yml']]; print('YAML ok')"
git ls-files | grep -E '\.(pyc|db|sqlite|env)$|__pycache__' || echo "no prohibited files tracked"
```

---

## 10. Slide plan, timings and demo

| # | Slide | Time | One-line purpose |
|---|---|---|---|
| 1 | Title | 0:20 | Name, tagline, scope of evidence |
| 2 | Problem & motivation | 1:00 | Why the domain needs one shared status/proof of delivery |
| 3 | Project overview | 0:50 | User → system → processing → output |
| 4 | System architecture | 1:20 | Five layers, one process, CI side-channel (reveal step by step) |
| 5 | Technology stack | 1:10 | 8 dependencies, each with a reason from the code |
| 6 | Codebase architecture | 0:50 | Thin routes, fat services; where the lines actually are |
| 7 | One write end to end | 1:20 | `POST /api/shipments`, 7 steps |
| 8 | Key implementation 1/2 | 1:20 | Decorators + allowlist, including its one leak |
| 9 | Key implementation 2/2 | 1:20 | OTP: generate → store → verify → cascade, with measured output |
| 10 | Algorithms & logic | 1:40 | State machine, cost, tracking sim, route heuristic |
| 11 | Self-diagnosing deployment | 1:20 | The ops agent and its bounded authority |
| 12 | Security & reliability | 1:30 | Control matrix: verified / partial / gap |
| 13 | Testing & quality | 1:10 | Green gates, zero tests, a SAST rule that cannot fire |
| 14 | Results — what I measured | 1:30 | 15 claims, measured, with the failures included |
| 15 | Challenges & decisions | 1:20 | Challenge → decision → cost, five times |
| 16 | Limitations & future | 1:10 | Honest inventory, then an ordered plan |
| 17 | Conclusion | 0:40 | Problem, achievement, learning, direction |

≈ 22 minutes plus questions. To hit 15 slides: drop 5 and 11 (their content is already carried by 4, 6 and 13) and merge 3 into 2. Press **N** to show the per-slide speaker notes, **O** for the overview grid, **A** to freeze animations while
talking through a diagram.

**Live demo, if allowed:** `python3 backend/app.py` → `/login.html` → quick-login as ADMIN →
dashboard KPIs (real aggregates) → Deliveries (note the `undefined` OTP column, and explain why) → Tracking (say
"this is a clock, not a GPS") → log out, reopen as CUSTOMER and show `GET /api/customers` refusing with 403.
Do **not** demo a customer booking on stage unless you want to demonstrate the 500 live — it is a good story, but only
if you finish the sentence.

---

## 11. Deck housekeeping

- `presentation/index.html` + `deck.css` + `deck.js` — no build step, no CDN, no fonts fetched: works offline from disk
  and over any static host.
- Keyboard: `← →`, space, click (left third = back), swipe, wheel; `O` overview · `N` notes · `A` freeze
  animations · `F` fullscreen · `?` shortcut hint; `#7` deep-links to slide 7.
- Slides are authored at 1280 × 720 and scaled to any viewport; `deck.js` additionally measures each slide and, if a
  slide is taller than the stage, reflows and scales it so nothing can clip on a projector.
- **Print to PDF** for a submission copy: background graphics **on**, scale **100 %**, margins **none**. Page size
  needs no setting — the stylesheet declares `@page{size:1280px 724px}`. Print CSS reveals all fragments
  and paginates one slide per page.
- Palette and card geometry are taken from the product's own tokens in `frontend/css/style.css`:
  `--primary-navy #062b57`, `--accent-blue #0789ff`, `--success #08a75a`, `--warning #f59e0b`, `--danger #ef3348`
  are reused byte-for-byte as `--navy`, `--blue`, `--ok`, `--warn`, `--bad`, so the deck looks like the artefact.
  Two values are deliberately *derived* rather than copied, and I should say so if asked: the body ink is darkened
  from the product's `--text-main #14213d` to `--ink #0b1f3a`, and the page from `--bg-main #f4f7fb` to `--bg #f6f8fc`.
  Measured WCAG contrast: `--ink` on `--bg` = **15.54:1**; `--muted #5a6f88` on `--bg` = **4.86:1** (passes AA for
  normal text, not AAA — muted is used only for secondary text, never for a number the argument depends on).
  Type floor: every on-slide size is **≥ 11.4 px** at the 1280 × 720 design box, i.e. ≈ 17 px when the stage scales to
  a 1920-wide projector (11.4 × 1.5). The only smaller sizes are off-slide chrome: `#brand` and `#hint` (11 px),
  the overview tile number and the speaker-notes heading (10.5 px) — none of them carries slide content.
- **Fill in `META` at the top of `deck.js` (line 10) before the viva** (name, course, reviewer). Unfilled fields stay visibly
  marked, on screen and in the PDF — an unpersonalised title slide is the one self-inflicted wound available here.
- `prefers-reduced-motion` is honoured; a `.no-anim` mode (press **A**) exists for room projectors that stutter.
- Nothing on any slide is decorative-only: every diagram node names a file, and every "verified" tag traces to
  section 8.

---

## 12. Red-team pass — the ten self-check questions

Run against the final deck (18 slides) on **2026-09-12**. Each row records the *check that produced the answer*, not an
opinion. Where the check failed, what it found is listed in 12.11 — that list is the reason this section exists.

| # | Question | How I checked it | Result |
|---|---|---|---|
| 1 | Is every named component, file and function real? | Extracted all 20 repo paths and all 25 `name()` calls from the deck; `os.path.exists` / substring search in `backend/*.py`, `frontend/js/*.js`, `scripts/*.py` | **Pass.** All 20 exist. Three apparent misses were regex artefacts, each checked by hand: the tree diagram writes `js/app.js` and `css/style.css` *relative* to their `frontend/` parent node (correct for a tree; `frontend/js/app.js` is spelled in full wherever it is cited outside the diagram), and `scripts/render_*` is a deliberate glob. All functions exist except `app.test_client()`, which is Flask's own API, cited in the "what I would write first" card |
| 2 | Did I invent any metric? | Regexed every `%`, `ms`, throughput and "coverage" mention | **Pass.** The only percentages are `0 %` (coverage, honestly), `10 % → 14 %` (measured tracking drift) and `18 %` (the hardcoded GST rate). The only latency is `10 ms`, captioned *"That is not a benchmark"*. No throughput, no user counts, no coverage figure |
| 3 | Does any slide show a whole source file? | Counted `<span class="l">` rows per `<pre>` across all 13 code panels | **Pass.** Longest panel is **14 lines**; the largest real module is `services.py` at 526 |
| 4 | One primary idea per slide? | Counted `.eyebrow` per `<section>` | **Pass.** Exactly one per slide, 18/18 — the eyebrow is the slide's single claim |
| 5 | Are limitations separated from future work? | Read the slide list | **Pass.** Slide 16 *Limitations* (gaps + reproductions, with "deliberate scope decisions — not defects" kept distinct), slide 17 *Future Work* (six-item ordered plan). They were one wall-of-text slide until the pass split them |
| 6 | Is the architecture diagram free of imaginary components? | Same path check as #1, applied to every diagram node label | **Pass.** Every node carries a file it maps to; no "microservice", "cache", "queue" or "gateway" appears that the code does not contain |
| 7 | Is anything exaggerated or sold as production-ready? | Vocabulary scan for `revolutionary`, `game-changing`, `enterprise-grade`, `cutting-edge`, `seamless`, `robust`, `scalable`, `bulletproof`, `100% …`, `fully tested`, `production-ready`, and 12 more | **Pass — 0 hits.** The deck's own register is "verified", "reproduced", "heuristic", "simulated", "inert", "vacuous" |
| 8 | Is mocked, partial or dead code explicitly labelled? | Counted hedge vocabulary and semantic tags | **Pass.** `hardcoded` ×3, `heuristic` ×4, `simulation` ×8, `inert` ×3, `vacuous` ×1; tags: 17 ✓ *verified*, 8 ⚠ *partial*, 14 ✗ *gap*. Colour is the carrier: green = reproduced working, amber = partial/by-design, red = reproduced failure |
| 9 | Will it survive a projector and a print? | WCAG contrast from the actual hex pairs; every `font-size` in the deck; `@page` and the `--pz` handoff | **Pass after fix.** `--ink #0b1f3a` on `--bg #f6f8fc` = **15.54:1**; muted 4.86:1 (AA, used only for secondary text). On-slide type floor raised to **11.4 px** (≈ 17 px at 1920 wide). Print now reuses the auto-fit factor as `zoom` so a dense slide prints at the size it projects at |
| 10 | Is the animation restrained and the deck self-contained? | `@keyframes` inventory; `rotate/zoom/spin` scan; external-URL scan | **Pass.** Three keyframes only (`pulse`, `flowdash`, `rise`); `rotate()` appears in two *static* transforms (a vertical-flow arrow, a 45° list bullet); `prefers-reduced-motion` honoured plus a manual freeze (`A`). **Zero** external URLs — the only references are `deck.css` and `deck.js`, so it opens from disk with no network |

**12.11 · What the pass actually caught.** A self-check that finds nothing is theatre, so these are the real failures it
turned up, in the order they were found:

1. **"12 failure categories"** in the ops agent → the code has **21 fragments resolving to 12 (category, subcategory)
   pairs across 4 categories**. Overstated precision, corrected on slide 11.
2. **"No pagination"** → too blunt. The backend does use `LIMIT` twice (`reports.py:35,39`, dashboard side-panels);
   *list* endpoints have none. Slide 16 now says exactly that.
3. **"auth + 4 inserts"** on the booking flow → `create_shipment()` performs **5** INSERTs. Corrected on slide 7.
4. **"data/routes.json · 4 corridors"** → the DB seeds 4 route rows; the JSON file has **2 and is read by nothing**.
   Footer rewritten to name `backend/database.py:412`.
5. **Contrast "≈ 13:1"** → measured **15.54:1**. The claim was in the right direction but was a guess, and a guessed
   number in a defence document is worse than no number.
6. **Type floor was 10.5–11.3 px** in four on-slide rules (`.node .m`, `.evidence .k2`, `.meta .m1`, `.dense .mono-cell`)
   while the notes claimed "≥ 11.4 px". The code was wrong, not the claim — the rules were raised.
7. **The printed PDF clipped dense slides.** `autoFit()` writes an inline `width`/`transform` onto `.fit`; the old print
   block reset only `#stage`, and `.slide{height:720px}` truncated anything taller. Fixed via the `--pz` → `zoom`
   handoff and `min-height`.
8. **An earlier SAST conclusion was simply wrong**: "all 7 Semgrep rules are inert" came from running semgrep in a
   scratch directory, where it scans **0 files**. Re-run from the repo root with planted violations, **6 of 7 rules
   fire**; only `apexflow-select-star` is inert. This is the most important entry in the list, because the mistake was
   an error in *method*, not arithmetic, and it would have understated the project's own quality machinery.
9. **A slide-editing error of mine** merged slides 9 and 10 and deleted the OTP trade-offs card, because a
   tag-boundary search matched `<pre`/`<path` as if they were `<p`. Both slides were rebuilt by hand and re-verified.
   (Method note kept here on purpose: in this file, match a tag only when the name is followed by whitespace or `>`.)
10. **The title slide shipped with `<your name>` in it.** Now driven by a `META` object, with unfilled placeholders
    visibly marked and an on-slide warning, so it cannot reach a projector or a submitted PDF unnoticed.

**12.12 · What I could not check, stated plainly.** There is no browser in the sandbox (no Chromium, and WeasyPrint
cannot import without libpango/libcairo), so layout and print output are verified *structurally* — jsdom over the real
files reports 18 slides, 18 `.fit` wrappers, 97 fragments, `--pz` set on every slide, keyboard walk 18 → 1, deep-link
`#18` works, and **0 JS errors** — not as rendered pixels. The one remaining action on a real machine is a 20-second
print-to-PDF smoke test: expect 18 pages, one slide each, nothing cut off at the right or bottom edge.
