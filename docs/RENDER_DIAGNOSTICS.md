# Render observability diagnostics

`/.github/workflows/render-diagnostics.yml` is a manually triggered
(`workflow_dispatch`) GitHub Actions workflow that gives engineering a
controlled, reproducible way to inspect the Render deployment state for this
repository and produce **sanitized** diagnostics. It is observability only:
**it does not deploy, restart, suspend, scale or modify anything** — the
collector issues read-only `GET` requests.

## How to run it

GitHub → *Actions* → **Render diagnostics** → *Run workflow*. Choose the
branch to run from (usually `main`) and optionally set the Render log lookback
window in minutes (default 90, range 5–1440).

The job requires the existing **Production** GitHub environment and reads
exactly one secret:

| Secret | Where it lives | How it is referenced |
|---|---|---|
| `RENDER_API_TOKEN` | GitHub environment `Production` | `${{ secrets.RENDER_API_TOKEN }}` |

The token is passed to a single step as a process environment variable. It is
never echoed, never placed in YAML/source/`.env` files, and never written to
artifacts. Do not enable Actions runner step-debug logging for this workflow.

The workflow permission baseline is `permissions: contents: read`; no write
permissions are granted or needed.

## What it collects

Via the official Render API (`https://api.render.com/v1`, Bearer
authentication with the API token):

| Endpoint | Purpose |
|---|---|
| `GET /v1/users` | Authentication proof only (the account email is discarded) |
| `GET /v1/services` | All services: id, name, type, repo, branch, suspended state, suspenders, auto-deploy, creation/update times, service details (URL, health check path, instance count, plan, region) |
| `GET /v1/services/{id}` | Fresh detail for the identified production service |
| `GET /v1/services/{id}/deploys` | Recent deploys: status, trigger, commit id/message, started/finished timestamps |
| `GET /v1/services/{id}/instances` | Current instance ids and creation times |
| `GET /v1/logs` (filtered by `ownerId` + `resource`, epoch time window, `direction=backward`, limit 100) | Recent logs for the production service only |

The production service is **derived, not assumed**: the primary match is the
service whose `repo` equals this repository (`nandan503/Apex-Flow`); the
fallback is the service whose Render URL hostname equals the origin
documented in `docs/OPERATIONS.md` (`apex-flow-7mr9.onrender.com`). If
several services match, all candidates are collected and the run fails with
exit code 5 (`ambiguous_service`) so a human resolves it.

It also probes the canonical production origin `https://apex.viability.in`
using this repository's **actual** endpoints:

- `GET /health/live` — liveness; expects HTTP 200 with a JSON body containing
  `"live"` (a provider interstitial can also return 200 HTML, so the body is
  validated, not just the status code).
- `GET /health/ready` — readiness; expects HTTP 200 with `"ready"`. HTTP 503
  means the database `SELECT 1` failed (see `docs/OPERATIONS.md`).
- `GET /` — canonical reachability; the Flask-served login page.

`/version` is **not probed because this repository does not implement it**.
That is a deliberate finding, not an omission: commit correlation therefore
compares the GitHub commit (`GITHUB_SHA`) with the Render deploy commit only,
and `summary.json` reports the application-reported version as unavailable.
Adding a `/version` endpoint would be an application change and belongs to a
separate, reviewed workstream.

## What it deliberately does NOT collect

- Render **environment variable values**, secret files, custom header values
  or database connection info — those API endpoints are never called.
- The authenticated account's email (used only as an auth proof).
- Authenticated application request/response bodies, cookies or sessions.
- Service `ipAllowList` and `sshAddress` fields (network posture) — dropped
  before artifacts are written.

## Diagnostics artifact

Each run uploads the artifact `render-diagnostics-<run id>-<attempt>`
(retention 14 days) containing:

| File | Contents |
|---|---|
| `summary.json` | Start here: run metadata, collection status, service identification, deployment state, commit correlation, health assessment, unknowns, exit code |
| `render-services.json` | Every visible service, sanitized, with repository-match flags and the identification result |
| `render-deploys.json` | Recent deploys and instances for the identified service(s) |
| `render-logs.txt` | Recent sanitized logs (bounded window, ≤100 lines, messages truncated to 1000 chars) |
| `canonical-health.json` | Per-endpoint HTTP status, latency, content type, sanitized body snippet, error |
| `README.md` | In-artifact guide to the above |

Raw API responses are never written to disk; only the sanitized projections
above exist. Raw responses are also never uploaded alongside sanitized ones.

## Sanitization

Every artifact byte, every stdout line and the GitHub step summary pass
through `scripts/render_sanitize.py`, which layers:

1. **JSON key redaction** — values under secret-looking keys (snake/kebab/
   camelCase segments such as `password`, `token`, `credential`, `api_key`,
   `secret`, `session`, `dsn`) are replaced before serialization.
2. **Text pattern redaction** — private key blocks, `Authorization`/API-key
   headers, bearer tokens, Render API keys (`rnd_`/`rmt_`), URLs with
   embedded credentials (`postgres://`, `redis://`, …), JWTs, cookie headers,
   common provider token prefixes (GitHub, Slack, AWS, Stripe, Google) and
   secret-looking `key=value` assignments.
3. **Literal token guard** — the exact `RENDER_API_TOKEN` value is stripped
   from all output wherever it could have leaked into a response or error.

Commit SHAs, request IDs and infrastructure metadata intentionally survive
redaction. Over-redaction is preferred to under-redaction. The layer is
covered by unit tests (`tests/test_render_sanitize.py`) and the collector is
covered by offline end-to-end tests with a mock Render API
(`tests/test_render_diagnostics.py`).

## Result interpretation and failure definitions

The workflow distinguishes four things:

- **Deployment (infrastructure) state** — from the Render API: `live`,
  `failed`, `in_progress`, `not_live`, `no_deploys`, `suspended`, or
  `not_evaluated`. A `live` deploy means Render finished the deploy; it is
  **not** proof that the application is healthy.
- **Application health** — from `/health/live` and the canonical root.
- **Database/readiness state** — from `/health/ready`.
- **Diagnostic collection** — whether the collector itself completed.

Failure kinds and exit codes:

| Exit code | Kind | Meaning |
|---|---|---|
| 0 | `none` | Collection succeeded; deployment live; liveness, readiness and canonical reachability all passed |
| 1 | usage | Configuration error (e.g. `RENDER_API_TOKEN` missing) |
| 2 | `render_api` | Authentication or connectivity failure; artifacts are partial |
| 3 | `health_or_deployment` | Collection succeeded but health failed, the latest deploy is in a failed state (`build_failed`/`update_failed`/`pre_deploy_failed`), or the service is suspended |
| 4 | `no_matching_service` | No Render service matches this repository |
| 5 | `ambiguous_service` | Production service identification is ambiguous |

A green run means *collection succeeded and everything probed was healthy at
that moment*. It does not certify outbox delivery, migrations, data integrity
or end-to-end user journeys; see `unknowns` in `summary.json` and the release
gates in `SECURITY_AUDIT.md`.

## Local execution (operators)

```sh
RENDER_API_TOKEN=... python scripts/render_diagnostics.py --output diagnostics
```

Flags: `--lookback-minutes` (or env `RENDER_LOG_LOOKBACK_MINUTES`),
`--repo`, `--canonical-base`, `--render-hostname`, `--api-base`,
`--timeout`. The `diagnostics/` directory is gitignored so local output
cannot be committed accidentally.

## Security notes and least privilege

- The Render API token is an account-level credential; Render does not offer
  a read-only API key scope. Least privilege is enforced **in the collector**:
  it can only issue `GET` requests and never calls the env-var, secret-file
  or connection-info endpoints. Rotate the token if it is ever exposed.
- Only one secret exists for this workflow; no PATs or unrelated credentials
  are created.
- No application runtime secrets are read, modified or added to GitHub.
- Render API 429/5xx and network errors are retried briefly; every request is
  timeout-bounded so the workflow cannot hang.
