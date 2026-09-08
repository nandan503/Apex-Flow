#!/usr/bin/env python3
"""Write human escalation notice to $GITHUB_STEP_SUMMARY.

Called by the escalate job in render-observe.yml.
Reads deployment details from environment variables (set by the workflow)
so that no secrets are involved.
"""
import os

sha        = os.environ.get("COMMIT_SHA",      "?")
status     = os.environ.get("DEPLOY_STATUS",   "?")
classes    = os.environ.get("FAILURE_CLASSES", "?")
deploy_id  = os.environ.get("DEPLOY_ID",       "?")
summary_f  = os.environ.get("GITHUB_STEP_SUMMARY", "")

lines = [
    "## ⚠️ Render Deployment Requires Human Intervention",
    "",
    "The deployment failed and could not be automatically resolved.",
    "",
    "| Field | Value |",
    "|---|---|",
    f"| Commit | `{sha}` |",
    f"| Deploy status | `{status}` |",
    f"| Failure classes | `{classes}` |",
    f"| Deploy ID | `{deploy_id}` |",
    "",
    "**Actions required:**",
    "1. Check the [Render dashboard](https://dashboard.render.com) for full logs.",
    "2. Download the sanitized diagnostic artifact from this workflow run.",
    "3. Review the failure classification and escalation notes in `report.txt`.",
    "4. Apply the minimum necessary fix and push to a PR for CI validation.",
    "5. Do NOT disable security controls or authentication to make the deploy succeed.",
    "",
    "**Failure classification guide:**",
    "- `build/syntax_error` or `build/import_error` — Fix the Python code/requirements.",
    "- `runtime/missing_env_var` — Set the missing env var in Render (not in code).",
    "- `runtime/startup_exception` — Check application startup logs for the full trace.",
    "- `deploy/health_check_fail` — Verify the health endpoint responds with 200.",
    "- `auth/*` — Check ALLOWED_ORIGINS, FLASK_ENV, SECRET_KEY, cookie/proxy config.",
    "- (empty) — Unclassified; requires manual Render log review.",
    "",
]

text = "\n".join(lines)
print(text)

if summary_f:
    with open(summary_f, "a") as f:
        f.write(text + "\n")
