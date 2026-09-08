# Production login failure — end-to-end investigation (2026-09-08)

Browser symptom: clicking **Login** on https://apex.viability.in fails with
`Cross-origin request blocked`.

Investigation method: trace frontend code → reproduce the real request against
the live service → trace server CORS/session config → compare the live build
with repository history → regression tests → deployment reproduction.

## 1. What the frontend actually does

`frontend/js/auth.js` (login page submit handler):

- `fetchAPI('/auth/login', {method:'POST', body: JSON.stringify({email,password})})`
- `fetchAPI` (`frontend/js/app.js`) prefixes `API_BASE = '/api'` and calls
  `fetch('/api/auth/login', {credentials:'same-origin', headers:{'Content-Type':'application/json', ...}})`
- URL is **relative and same-origin** in every repository version: `https://apex.viability.in/api/auth/login`
  (page origin == API origin). There is **no** configurable absolute API base and
  no Render hostname in any frontend file (regression-tested).
- Because the request is same-origin and uses only a simple header
  (`Content-Type: application/json`), browsers do **not** preflight it — no
  OPTIONS is involved in the failing browser flow.
- Repository HEAD additionally fetches `/api/auth/csrf` first and attaches
  `X-CSRF-Token` to every mutation.

## 2. Reproduction against the live application

A probe runner with full network access issued the exact browser requests to
both candidate hosts on 2026-09-08 14:20 UTC (evidence in this investigation;
same result on `apex.viability.in` and `apex-flow-7mr9.onrender.com`):

| Probe | Result |
|---|---|
| `GET https://apex.viability.in/` | 200 `login.html` (Flask `x-render-origin-server: gunicorn`, edge `server: cloudflare`) |
| `GET /health/live`, `GET /health/ready` | **404** (routes absent from the live build) |
| `GET /api/auth/csrf` | **404** `{"success":false,"message":"Not found","data":null}` |
| `POST /api/auth/login`, `Origin: https://apex.viability.in` (its own origin) | **403** `{"error":"CSRF","message":"Cross-origin request blocked","success":false}` |
| `POST /api/auth/login`, `Origin: https://apex-flow-7mr9.onrender.com` | **403** same body |
| `OPTIONS /api/auth/login` (any Origin) | 200 with **no** `Access-Control-Allow-*` headers at all |

Answers to Phase 2 questions:

1. Request URL: `POST https://apex.viability.in/api/auth/login`
2. Method: `POST`
3. Origin header: `https://apex.viability.in` (equal to the URL origin)
4. HTTP status: **403**
5.–8. `Access-Control-Allow-*`: **absent** (not needed for same-origin; the app
   itself rejected the request with its own 403 JSON)
9. OPTIONS sent by browser: **no** (same-origin POST with a simple content type)
10. OPTIONS response: 200, empty body, `Allow: POST, OPTIONS, HEAD, GET`, no
    CORS headers — proves the live flask-cors allowlist does not recognize
    either production origin
11. Cloudflare: Render's edge (`server: cloudflare`, `cf-ray`, `x-render-origin-server: gunicorn`)
    forwards everything; it does **not** block or strip the request
12. Does it reach Flask? **Yes** — gunicorn answers and the 403 body is the
    application's own `error_response()` JSON

Cold-start caveat: the Render service is a free/auto-sleeping instance. On a
cold start the *first* request to any path returns Render's
"Service waking up…" interstitial (HTML, 200) instead of the app — observed
during this investigation. That can make an XHR fail JSON parsing on the first
hit, but it is not the reported error.

## 3. Server CORS / origin configuration

The **live build is not the repository HEAD**. Live static assets
(`frontend/js/app.js`, `frontend/js/auth.js`) byte-match commit `85882e4`
(PR #1, 2026-09-07), and the live API 404s `/api/auth/csrf`, `/health/live`,
`/health/ready` — endpoints that were added later in `0b5771b` (PR #2) and are
present in current `main`/HEAD (`d0cd934`). PR #2's commit message is
"…same-origin login fix".

The origin gate running live (85882e4-era `backend/app.py`) is:

```python
allowed = set(ALLOWED_ORIGINS)
allowed.add(request.host_url.rstrip('/'))   # own origin, scheme included
if origin.rstrip('/') not in allowed:
    return error_response('Cross-origin request blocked', 403, code='CSRF')
```

Behind Render's TLS-terminating edge the app sees plain HTTP on the wire. With
`TRUST_PROXY` unset, `request.host_url` is `http://apex.viability.in`, so the
browser's `Origin: https://apex.viability.in` (or the Render URL origin) never
matches `request.host_url`, and unless the exact origin is in the
`ALLOWED_ORIGINS` environment variable the POST is rejected. That is precisely
what the live probe reproduced.

Repository HEAD (`backend/app.py::_origin_matches_request`, merged in PR #2)
fixes the misclassification: a well-formed Origin equal to the request's own
scheme/host/port is treated as same-origin and never depends on the
cross-origin allowlist. flask-cors is initialized narrowly with
`origins=ALLOWED_ORIGINS`, `supports_credentials=True`, an explicit
`allow_headers`/`methods` allowlist — never `*`, never reflected.

## 4. Cookies / session

HEAD (and the live build) set: `HttpOnly`, `SameSite=Lax`, `Secure` in
production, no `Domain` attribute (host-only cookie), `Path=/`. Login rotates
the session (new `sid`, new CSRF token). No cross-site cookie sending is
required anywhere: the architecture is same-origin, so `SameSite=Lax`/`Secure`
are **not** weakened.

## 5. Deployment path

Browser → `https://apex.viability.in` → Render/Cloudflare edge → gunicorn →
Flask (static + `/api` on one origin). No redirect happens during the POST;
Cloudflare is Render's edge and forwards the request. The failure is entirely
inside the stale live build's origin gate plus deployment configuration
(`TRUST_PROXY=1` and/or the canonical origin in `ALLOWED_ORIGINS`).

## 6. Root cause (summary)

1. **Application cause:** the live service runs a pre-fix build whose origin
   gate classifies the *same-origin* production login POST as cross-origin and
   answers 403 `Cross-origin request blocked`. The fix for that defect
   (same-origin recognition in `backend/app.py`, plus `/api/auth/csrf` and
   health endpoints) is already merged into `main` (`0b5771b` → `d0cd934`).
2. **Deployment cause:** that fix is **not live**. Render still serves the
   PR #1-era build (verified by byte-identical frontend assets and the missing
   `/api/auth/csrf`, `/health/live`, `/health/ready` routes). Earlier deploys
   of PR #2/#3 did not take effect (startup/entrypoint and CI failures; the
   Dockerfile/Procfile factory invocation `backend.app:create_app()` was only
   corrected in PR #3 and is present at HEAD).
3. **Configuration cause:** for the corrected code (and for the pre-fix code's
   `request.host_url` shortcut) to recognize the apex origin, the service
   environment must set `TRUST_PROXY=1` (edge overwrites forwarded headers)
   and/or `ALLOWED_ORIGINS=https://apex.viability.in`. Without those, HEAD
   fails closed — exactly as designed — and login stays blocked.

## 7. Fix

No new wildcard CORS, no CSRF/cookie weakening, no new providers, no redesign.
The minimal correct fix is already in repository HEAD and is **small + narrow**:

- `backend/app.py` — same-origin requests are recognized by scheme/host/port
  (`_origin_matches_request`) and never require the cross-origin allowlist;
  hostile/malformed origins still fail closed with 403.
- `frontend/js/*` — relative `/api/...` URLs only; `credentials: 'same-origin'`;
  CSRF token header on mutations; CSP `connect-src 'self'`.
- `backend/config.py` + `.env.example`/`docs/OPERATIONS.md` — exact-origin
  allowlist (no `*`), `TRUST_PROXY` contract, production HTTPS/Secure cookies.

**To make login work in production (operator action, outside this repository):**
deploy `main` HEAD and set on the Render service:

```
TRUST_PROXY=1
ALLOWED_ORIGINS=https://apex.viability.in
```

plus the existing required secrets (`SECRET_KEY`, `DATABASE_URL` with
`sslmode=verify-full`, `OUTBOX_ENCRYPTION_KEY`, `IDEMPOTENCY_HASH_KEYS`), then
verify with `GET /health/live`, `GET /health/ready`, and a real browser login.
If the free-instance sleep interstitial is disruptive, keep the instance warm
or move off the auto-sleeping free tier.

## 8. Tests added by this investigation

- `tests/test_login_cors_contract.py` (19 tests, DB-free, all passing):
  1. same-origin login succeeds without allowlisting the apex origin
  2. canonical origin allowlisted with `TRUST_PROXY=0` succeeds
  3. **misconfiguration reproduction**: `TRUST_PROXY=0` + apex not allowlisted
     → exact 403 `Cross-origin request blocked`
  4. attacker/scheme-downgrade/port-mismatch/malformed origins rejected
  5. preflight for an allowlisted partner returns exact `Access-Control-Allow-Origin`
     (never `*`), credentials, headers and methods
  6. preflight for an unknown origin gets no CORS headers
  7. no wildcard `Access-Control-Allow-Origin` on any response
  8. cookie flags: `HttpOnly`, `Secure`, `SameSite=Lax`, no `Domain`
  9. login success rotates the session cookie; login failure → 401
  10. missing/wrong CSRF token → 403
  11. CSP `connect-src 'self'` on the login page; frontend contains no absolute URL
- `tests/test_gunicorn_factory_boot.py` — boots the exact production command
  `gunicorn 'backend.app:create_app()' --bind 127.0.0.1:$PORT` against a real
  PostgreSQL and verifies `/health/live`, `/health/ready`, CSRF bootstrap,
  same-origin login, authenticated mutation, hostile-origin rejection and
  narrow preflight (runs in CI; skipped where gunicorn is unavailable).
- Pre-existing coverage retained: `tests/test_origin_login.py` (canonical-origin
  end-to-end login incl. real HTTP server), security/red-team suites.

## 9. Production verification status

- Live today: **fails** — 403 `Cross-origin request blocked` reproduced on both
  `https://apex.viability.in/api/auth/login` and
  `https://apex-flow-7mr9.onrender.com/api/auth/login` because the stale build
  is still deployed and/or `TRUST_PROXY`/`ALLOWED_ORIGINS` are not set.
- Repository HEAD: full PostgreSQL suite is green on `main` CI (run
  `34230831509` after PR #3) and the new contract tests pass locally.
- **Do not merge/deploy further until HEAD is deployed with `TRUST_PROXY=1` and
  `ALLOWED_ORIGINS=https://apex.viability.in` and a browser login on
  https://apex.viability.in succeeds.**
