# Render Deployment Agent — Operations Guide

**Workflow:** `.github/workflows/render-observe.yml`  
**Scripts:** `scripts/render_observe.py`, `render_sanitize.py`, `render_fix.py`, `render_escalate.py`

---

## Table of Contents

1. [What This Does](#what-this-does)
2. [Required GitHub Secrets](#required-github-secrets)
3. [Required Repository Variables](#required-repository-variables)
4. [How Service Discovery Works](#how-service-discovery-works)
5. [How Deployment Matching Works](#how-deployment-matching-works)
6. [How Logs Are Retrieved](#how-logs-are-retrieved)
7. [How Redaction Works](#how-redaction-works)
8. [Failure Classification](#failure-classification)
9. [How the Agent Decides Whether to Modify Code](#how-the-agent-decides-whether-to-modify-code)
10. [How PRs Are Created](#how-prs-are-created)
11. [How Automatic Deployment Works](#how-automatic-deployment-works)
12. [Retry and Loop Protection](#retry-and-loop-protection)
13. [How to Disable the Automation](#how-to-disable-the-automation)
14. [How to Test Safely](#how-to-test-safely)
15. [Workflow Flow](#workflow-flow)
16. [Security Model](#security-model)

---

## What This Does

On every push to `main`, this workflow:

1. **Observes** the Render deployment triggered by that push
2. **Polls** Render's deploy status (up to 20 minutes)
3. **Collects** runtime logs on failure
4. **Classifies** the failure into a structured category
5. **Attempts one bounded automated fix** if the failure is safe to fix
6. **Opens a PR** with the fix for human review
7. **Escalates to humans** when automatic resolution is not possible

It is **not** an infinite self-healing bot. It stops after `MAX_REPAIR_ATTEMPTS` and requires human intervention.

---

## Required GitHub Secrets

Configure these at: **GitHub → Repository → Settings → Secrets and variables → Actions → Secrets**

| Secret | Required | Description |
|---|---|---|
| `RENDER_API_KEY` | **Yes** | Render REST API key. Generate at: Render Dashboard → Account Settings → API Keys. |

> **Security note:** `RENDER_API_KEY` is passed only to the `observe` step's `env:` block. It is never echoed, logged, written to artifacts, PR bodies, or commit messages. The legacy name `RENDER_API_TOKEN` is accepted as a fallback.

---

## Required Repository Variables

Configure these at: **GitHub → Repository → Settings → Secrets and variables → Actions → Variables**

Variables (unlike secrets) are visible in logs — only store non-sensitive values here.

| Variable | Required | Default | Description |
|---|---|---|---|
| `RENDER_SERVICE_ID` | Recommended | _(auto-discover)_ | The Render service ID (e.g. `srv-abc123`). Bypasses repo-URL discovery. Find it in Render Dashboard → Service → Settings → Service ID. |
| `AUTO_DEPLOY_RENDER` | No | `false` | Set to `"true"` to observe a post-fix redeploy. Does **not** auto-merge the PR. |
| `MAX_REPAIR_ATTEMPTS` | No | `2` | Maximum automated fix attempts per commit SHA before stopping. |

### Finding Your Render Service ID

```
Render Dashboard → Your Service → Settings
URL pattern: https://dashboard.render.com/web/srv-XXXXXXXXXXXXXXXXXX
Service ID = srv-XXXXXXXXXXXXXXXXXX
```

---

## How Service Discovery Works

The observer uses a two-path discovery strategy:

### Fast path (recommended)

When `RENDER_SERVICE_ID` is set as a repository variable:

```
RENDER_SERVICE_ID="srv-abc123"
    ↓
GET /v1/services/srv-abc123   (single authenticated request)
    ↓
service_name + owner_id resolved
    ↓
Proceed to deployment matching
```

### Slow path (fallback)

When `RENDER_SERVICE_ID` is not configured:

```
GET /v1/services?type=web_service&limit=100
    ↓ (paginated, up to 10 pages)
Match each service's repo URL against github.repository
    ↓
Normalize: strip git@, https://, .git suffix, lowercased
    ↓
If exactly 1 match → use it
If 0 matches → exit code 4 (service not found)
If >1 matches → exit code 5 (ambiguous)
```

**If auto-discovery fails**, the workflow prints:
```
Set RENDER_SERVICE_ID as a GitHub Actions repository variable.
```

---

## How Deployment Matching Works

```
Current GitHub SHA (GITHUB_SHA)
    ↓
GET /v1/services/{serviceId}/deploys?limit=10
    ↓
For each deploy, compare deploy.commit.id against GITHUB_SHA (prefix match)
    ↓
If commit SHA found → track that specific deploy
    ↓
If not found yet → poll every 20s up to 20 minutes
    ↓
Fallback: if most-recent deploy is in-progress, track it (SHA mismatch warned)
```

**Polling parameters:**

| Parameter | Value |
|---|---|
| Initial wait before first poll | 30 seconds |
| Poll interval | 20 seconds |
| Maximum observation window | 20 minutes |
| Maximum polls | 60 |

---

## How Logs Are Retrieved

```
Render API: GET /v1/logs
Parameters:
  ownerId   = service owner ID
  resource  = service ID
  startTime = (deploy start epoch - buffer) in milliseconds
  endTime   = now in milliseconds
  limit     = 80 lines maximum
```

**Limitations documented:**
- Render API `/v1/logs` returns **runtime logs only** — not build logs
- Build logs are visible in the Render dashboard but not exposed via the REST API
- The workflow retrieves the last 80 log lines from the relevant time window
- Log timestamps must be in **milliseconds** (common API gotcha)

---

## How Redaction Works

All output passes through `scripts/render_sanitize.py` before being written to:
- Artifacts
- GitHub step summaries
- PR bodies

### 9-layer redaction pipeline

| Layer | Pattern | Example |
|---|---|---|
| 1 | PEM private key blocks | `-----BEGIN RSA PRIVATE KEY-----` |
| 2 | Authorization headers | `Authorization: Bearer <token>` |
| 3 | Render API key shape | `rnd_XXXXXXXXXXXXXXXXXXXX` |
| 4 | Generic Bearer tokens | `Bearer eyJhbG...` |
| 5 | URLs with embedded credentials | `postgresql://user:pass@host` |
| 6 | JWTs | `eyJhbGciOiJIUzI1NiJ9...` |
| 7 | Cookie headers | `Cookie: session=...` |
| 8 | Provider token prefixes | `sk_`, `ghp_`, `SG.`, `AIza` |
| 9 | key=value secret patterns | `SECRET_KEY=abc123` |

Additionally, `TokenGuard` strips the **literal `RENDER_API_KEY` value** from all output as a final safeguard — even if it appeared in an unexpected log line.

**Design principle:** over-redaction is preferred over under-redaction. Commit SHAs, request IDs, and service names survive redaction.

---

## Failure Classification

The observer classifies failures into 12 categories:

| Category | Subcategory | Log Pattern | Auto-fix? |
|---|---|---|---|
| `build` | `syntax_error` | `SyntaxError` | ✅ AST scan |
| `build` | `import_error` | `ImportError` | ✅ requirements check |
| `build` | `module_not_found` | `ModuleNotFoundError` | ❌ human |
| `build` | `pip_error` | `Could not find a version` | ❌ human |
| `runtime` | `missing_env_var` | `is required` / `environment variable` | ❌ set in Render dashboard |
| `runtime` | `port_bind_error` | `Address already in use` | ❌ human |
| `runtime` | `startup_exception` | `Traceback` / `RuntimeError` | ❌ human |
| `runtime` | `worker_timeout` | `CRITICAL WORKER TIMEOUT` | ❌ human |
| `deploy` | `health_check_fail` | `health check failed` | ❌ human |
| `deploy` | `timeout` | `Timed out` | ❌ human |
| `auth` | `cors_error` | `CORS` / `Cross-origin` | **Never** — security |
| `auth` | `cookie_error` | `SameSite` / `Secure cookie` | **Never** — security |

**Human escalation is mandatory when:**
- Observation timed out
- Zero categories matched (unclassified)
- 3+ categories matched (ambiguous)
- Any `auth/*` category (never auto-fix security)
- Category has no defined safe fix

---

## How the Agent Decides Whether to Modify Code

The agent (`render_fix.py`) only runs when **all** of the following are true:

1. `deploy_status == "failed"`
2. `has_safe_fix == "true"` (exactly one safe-fixable category)
3. `needs_human == "false"` (no escalation conditions triggered)
4. Attempt counter is below `MAX_REPAIR_ATTEMPTS`

**When it runs, it follows the Staff Engineer investigation sequence:**

```
1. Identify the classified failure category
2. Form a hypothesis (e.g. "SyntaxError in backend/*.py")
3. Verify the hypothesis (run ast.parse() on all .py files)
4. If verified → apply minimum fix
5. Run test suite (if tests/ exists)
6. Report result (exit 0 = fix applied + validated)
```

**It never:**
- Modifies security controls
- Disables authentication
- Weakens CORS/cookie settings
- Hard-codes credentials
- Fixes by disabling failing tests

---

## How PRs Are Created

When a fix is applied, the workflow:

1. Creates branch: `agent/render-fix-<12-char-sha>`
2. Commits the fix + increments the repair counter
3. Pushes the branch
4. Opens a PR to `main` via `gh pr create`

**PR body template:**

```markdown
## Root cause
What failed and why (classified failure type + deploy ID)

## Evidence
Link to sanitized diagnostic artifact (no secrets)

## Fix
Exactly what changed and why

## Validation
Tests/checks performed before committing

## Deployment impact
Render deployment implications after merge

## Remaining risks
What still requires human review
```

**The PR is never auto-merged.** A human must review and merge. Merging to `main` triggers a new Render deploy automatically (Render watches `main`).

---

## How Automatic Deployment Works

By default, `AUTO_DEPLOY_RENDER=false` — the workflow stops after creating the PR.

When `AUTO_DEPLOY_RENDER=true` (set as a repository variable):

```
Fix applied
    ↓
PR created (human must still review and merge)
    ↓
Observation note logged
    ↓
STOP (no automated merge, no automated redeploy)
```

> **Important:** Even with `AUTO_DEPLOY_RENDER=true`, the workflow does **not** auto-merge PRs or directly trigger Render deploys. Render deploys are triggered by merges to `main`, which require human action.

---

## Retry and Loop Protection

### Per-commit attempt counter

Each commit SHA gets a counter file at `.github/repair-attempts/<sha>.count`.

```
First failure → attempt_count = 0
    ↓ remediate job increments to 1
First fix PR opened

Second failure (if PR was merged) → attempt_count = 1
    ↓ remediate job increments to 2 (= MAX_REPAIR_ATTEMPTS)

Third failure → attempt_count = 2 ≥ MAX_REPAIR_ATTEMPTS
    ↓ observe job exits 1 with ::error::
    ↓ remediate job never runs
    ↓ STOP — human required
```

Counter files (`.count`) are `.gitignore`'d from the repair-attempts directory — they only exist within a single workflow run's filesystem and are NOT committed to git. This means the counter resets on each workflow run, providing **per-run** protection rather than cross-run protection. Cross-run protection is enforced by the hard-coded `REMEDIATION_ATTEMPT` env var in `render_fix.py` which refuses to run if attempt > MAX.

### Infinite loop prevention

- Render auto-deploys from `main` branch only (not from `agent/render-fix-*` branches)
- Fix branches require human merge to `main` to trigger a new deploy
- No automated merges → no automated deployment loops
- `MAX_REPAIR_ATTEMPTS` caps the number of fix cycles per workflow execution

---

## How to Disable the Automation

### Option 1: Disable in GitHub UI (fastest, no code change)
```
GitHub → Repository → Actions → Workflows
→ "Render - Observe and Diagnose" → Disable workflow
```

### Option 2: Remove the trigger (code change, auditable)
```yaml
# In render-observe.yml, change:
on:
  push:
    branches: [main]
# To:
on:
  workflow_dispatch:  # manual trigger only
```

### Option 3: Delete the workflow file
```bash
git rm .github/workflows/render-observe.yml
git commit -m "chore: disable render observer"
git push origin main
```

**Disabling the observer does not affect:**
- The `ci.yml` PR gate (fast-checks + pytest)
- Render auto-deployment (Render watches `main` independently)

---

## How to Test Safely

### Test 1: Dry-run the observer script locally
```bash
# Requires RENDER_API_KEY to be set in your shell (NOT committed)
export RENDER_API_KEY="rnd_yourkey"
export GITHUB_SHA="$(git rev-parse HEAD)"
export GITHUB_REPOSITORY="nandan503/Apex-Flow"

python scripts/render_observe.py \
  --output /tmp/test-diagnostics \
  --commit "$GITHUB_SHA"
```

### Test 2: Validate sanitization
```bash
python3 -c "
from scripts.render_sanitize import sanitize_text, sanitize_json, sanitize_token
guard = sanitize_token('rnd_FAKE_TOKEN_12345')
print(guard('Error: auth failed with rnd_FAKE_TOKEN_12345'))
# Output: Error: auth failed with [REDACTED]
"
```

### Test 3: Test with RENDER_SERVICE_ID set (avoids discovery)
```bash
export RENDER_SERVICE_ID="srv-your-service-id"
python scripts/render_observe.py --output /tmp/diag --service-id "$RENDER_SERVICE_ID"
```

### Test 4: Trigger workflow_dispatch manually
```
GitHub → Actions → "Render - Observe and Diagnose" → Run workflow
```
This observes the current HEAD without pushing a new commit.

### Test 5: Intentional safe failure (staging only)
To test the full pipeline without breaking production:
1. Create a `staging` branch in Render (separate service)
2. Modify the workflow trigger to `branches: [staging]`
3. Push a known-bad commit to `staging`
4. Observe the classification and PR creation

> ⚠️ **Never intentionally break `main`** to test this workflow.

---

## Workflow Flow

```
push to main
    │
    ├── ci.yml (existing, unchanged)
    │   └── fast-checks: py_compile, gitleaks, semgrep, YAML validation
    │   └── test-sqlite: pytest (if tests/ exists)
    │   └── dependency-audit: pip-audit
    │
    └── render-observe.yml
         │
         ▼ observe job  [permissions: contents:read]
         │
         ├── Check MAX_REPAIR_ATTEMPTS counter
         │     If count ≥ max → exit(1) STOP
         │
         ├── GET /v1/services/{id}  OR  GET /v1/services (auto-discover)
         │
         ├── Poll GET /v1/services/{id}/deploys every 20s (max 20 min)
         │     Find deploy matching current GitHub SHA
         │
         ├── STATUS: live
         │     └── Success summary → DONE ✅
         │
         └── STATUS: failed / timeout
               ├── GET /v1/logs (last 80 lines, 30-min window)
               ├── Sanitize all log content (9-layer redaction)
               ├── Classify → {category}/{subcategory}
               ├── Determine: needs_human? has_safe_fix?
               ├── Upload sanitized artifact
               │
               ├── needs_human=false AND has_safe_fix=true
               │     ▼ remediate job  [contents:write, pull-requests:write]
               │       Increment attempt counter
               │       render_fix.py → apply minimum fix
               │       Run test suite
               │       git checkout -b agent/render-fix-<sha>
               │       git commit + push
               │       gh pr create (structured PR body)
               │       → Human reviews and merges
               │       → Merge triggers new Render deploy
               │       STOP
               │
               └── needs_human=true
                     ▼ escalate job  [contents:read]
                       Write step summary with actionable guide
                       STOP — no further automation
```

---

## Security Model

| Property | Implementation |
|---|---|
| Secret isolation | `RENDER_API_KEY` in one `env:` block only |
| Fork protection | `pull_request` trigger absent |
| Log redaction | 9-layer sanitization + TokenGuard |
| Artifact safety | All artifacts pass through sanitizer |
| PR safety | No secrets in PR body, title, or comments |
| Infinite loop | `MAX_REPAIR_ATTEMPTS` hard cap |
| Auth failures | Never auto-fixed (always escalated) |
| Branch safety | Never pushes to `main` directly |
| Merge safety | No automated merges |
| Privilege | Per-job least-privilege permissions |
