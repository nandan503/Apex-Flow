#!/usr/bin/env python3
"""Render deployment observer and diagnostics collector for APEX FLOW.

Observes the Render deployment triggered by the current git push,
classifies failures, and writes a structured sanitized diagnostic bundle.

Security contract:
  - RENDER_API_TOKEN is read from the process environment and is NEVER
    printed, logged, or written to any artifact.
  - Only GET requests are issued (read-only; cannot mutate Render state).
  - Endpoints that return secret values are never called.
  - All output passes through render_sanitize before being written.
  - The token is stripped from all output as a final guard.

Exit codes:
  0  Deployment is live (success).
  1  Configuration/usage error (e.g. RENDER_API_TOKEN missing).
  2  Render API authentication or connectivity failure.
  3  Deployment failed or timed out (failure bundle written).
  4  No Render service matched this repository.
  5  Service identification ambiguous (multiple matches).
  6  Deployment did not appear within the initial wait window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.render_sanitize import sanitize_json, sanitize_text, sanitize_token
except ImportError:
    from render_sanitize import sanitize_json, sanitize_text, sanitize_token

# ---------------------------------------------------------------------------
# Repository / deployment constants
# ---------------------------------------------------------------------------

DEFAULT_REPO         = "nandan503/Apex-Flow"
DEFAULT_API_BASE     = "https://api.render.com/v1"
DEFAULT_SERVICE_URL  = "https://apex-flow-7mr9.onrender.com"  # canonical Render hostname

# Deploy status sets (from Render API v1 docs)
FAILED_STATUSES      = frozenset({"build_failed", "update_failed", "pre_deploy_failed"})
IN_PROGRESS_STATUSES = frozenset({
    "created", "queued", "build_in_progress",
    "update_in_progress", "pre_deploy_in_progress",
})
TERMINAL_STATUSES    = frozenset({"live", "deactivated", "canceled"}) | FAILED_STATUSES

# Polling configuration
INITIAL_WAIT_S       = 30    # seconds to wait before polling for the new deploy
POLL_INTERVAL_S      = 20    # seconds between status polls
MAX_POLL_MINUTES     = 20    # maximum observation window in minutes
MAX_POLLS            = (MAX_POLL_MINUTES * 60) // POLL_INTERVAL_S

# Log / artifact limits
MAX_LOG_LINES        = 80
MAX_LOG_MSG_CHARS    = 800
HTTP_TIMEOUT_S       = 15.0
HTTP_ATTEMPTS        = 3

# Fields that must never appear in artifacts (Render may return them)
DROPPED_SERVICE_FIELDS = frozenset({"ipAllowList", "sshAddress"})

# Labels worth keeping from Render log entries
KEEP_LOG_LABELS = frozenset({
    "resource", "instance", "host", "level", "type",
    "statusCode", "method", "path",
})


# ---------------------------------------------------------------------------
# Failure classification patterns (applied to raw log text)
# ---------------------------------------------------------------------------

FAILURE_PATTERNS: list[tuple[str, str, str]] = [
    # (category, subcategory, pattern_fragment_to_search_in_log)
    ("build",    "syntax_error",        "SyntaxError"),
    ("build",    "import_error",        "ImportError"),
    ("build",    "module_not_found",    "ModuleNotFoundError"),
    ("build",    "pip_error",           "ERROR: Could not find a version"),
    ("build",    "pip_error",           "No matching distribution found"),
    ("runtime",  "missing_env_var",     "is required"),
    ("runtime",  "missing_env_var",     "environment variable"),
    ("runtime",  "port_bind_error",     "Address already in use"),
    ("runtime",  "port_bind_error",     "Error: That port is already"),
    ("runtime",  "startup_exception",   "Traceback (most recent call last)"),
    ("runtime",  "startup_exception",   "RuntimeError"),
    ("runtime",  "startup_exception",   "Exception"),
    ("runtime",  "worker_timeout",      "Worker failed to boot"),
    ("runtime",  "worker_timeout",      "CRITICAL WORKER TIMEOUT"),
    ("deploy",   "health_check_fail",   "health check failed"),
    ("deploy",   "health_check_fail",   "Health check"),
    ("deploy",   "timeout",             "Timed out"),
    ("auth",     "cors_error",          "CORS"),
    ("auth",     "cors_error",          "Cross-origin"),
    ("auth",     "cookie_error",        "SameSite"),
    ("auth",     "cookie_error",        "Secure cookie"),
]

# Safe, bounded automated fixes (applied only when unambiguous)
SAFE_AUTO_FIXES: dict[tuple[str, str], str] = {
    # (category, subcategory) -> description of what the fix script checks
    # These are documentation of intent; actual fixes are in render_fix.py
    ("build", "syntax_error"):   "python syntax check + fix",
    ("build", "import_error"):   "verify requirements.txt covers all imports",
}


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[observe] {msg}", flush=True)


class RenderApiError(Exception):
    """A Render API call failed (message is sanitized before raising)."""


# ---------------------------------------------------------------------------
# HTTP helper — stdlib only, no external dependencies
# ---------------------------------------------------------------------------

def _http_get(url: str, token: str, params: dict | None = None,
              timeout: float = HTTP_TIMEOUT_S) -> tuple[int, object]:
    """Issue an authenticated GET; return (status_code, parsed_json_or_None)."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    for attempt in range(HTTP_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                try:
                    return resp.status, json.loads(body)
                except json.JSONDecodeError:
                    return resp.status, None
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < HTTP_ATTEMPTS - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return exc.code, None
        except (urllib.error.URLError, OSError):
            if attempt < HTTP_ATTEMPTS - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return 0, None
    return 0, None


# ---------------------------------------------------------------------------
# Render API client (read-only)
# ---------------------------------------------------------------------------

class RenderClient:
    def __init__(self, token: str, base: str = DEFAULT_API_BASE) -> None:
        self._token = token
        self._base  = base.rstrip("/")

    def _get(self, path: str, params: dict | None = None) -> tuple[int, object]:
        return _http_get(self._base + path, self._token, params)

    def whoami(self) -> bool:
        """Verify credential works.  Returns True on 200."""
        status, _ = self._get("/users")
        return status == 200

    def list_services(self) -> list[dict]:
        """Return all web services (paginated, up to 500)."""
        services: list[dict] = []
        cursor: str | None = None
        for _ in range(10):  # max 10 pages × 100 = 1000 services
            params: dict = {"limit": 100, "type": "web_service"}
            if cursor:
                params["cursor"] = cursor
            status, data = self._get("/services", params)
            if status != 200 or not isinstance(data, list):
                break
            if not data:
                break
            for item in data:
                svc = item.get("service") if isinstance(item, dict) else item
                if isinstance(svc, dict):
                    services.append(svc)
            # Render returns a cursor in the last element when more pages exist
            last = data[-1] if data else {}
            cursor = last.get("cursor") if isinstance(last, dict) else None
            if not cursor:
                break
        return services

    def list_deploys(self, service_id: str, limit: int = 20) -> list[dict]:
        status, data = self._get(
            f"/services/{urllib.parse.quote(service_id, safe='')}/deploys",
            {"limit": limit},
        )
        if status != 200 or not isinstance(data, list):
            raise RenderApiError(f"HTTP {status} listing deploys")
        return [
            (d.get("deploy") if isinstance(d, dict) and "deploy" in d else d)
            for d in data
            if isinstance(d, dict)
        ]

    def get_deploy(self, service_id: str, deploy_id: str) -> dict:
        status, data = self._get(
            f"/services/{urllib.parse.quote(service_id, safe='')}/"
            f"deploys/{urllib.parse.quote(deploy_id, safe='')}",
        )
        if status != 200 or not isinstance(data, dict):
            raise RenderApiError(f"HTTP {status} retrieving deploy {deploy_id}")
        return data.get("deploy", data) if "deploy" in data else data

    def get_logs(self, owner_id: str, resource_id: str,
                 start_epoch: float, end_epoch: float,
                 limit: int = MAX_LOG_LINES) -> list[dict]:
        """GET /v1/logs — runtime logs for a given resource and time range."""
        status, data = self._get("/logs", {
            "ownerId":   owner_id,
            "resource":  resource_id,
            "startTime": int(start_epoch * 1000),  # milliseconds
            "endTime":   int(end_epoch   * 1000),
            "limit":     limit,
        })
        if status != 200 or not isinstance(data, list):
            return []
        return data


# ---------------------------------------------------------------------------
# Service identification
# ---------------------------------------------------------------------------

def _normalize_repo(repo: str) -> str:
    repo = repo.strip().lower()
    for scheme in ("https://", "http://", "git@"):
        if repo.startswith(scheme):
            repo = repo[len(scheme):]
    repo = repo.replace(":", "/", 1).removesuffix(".git")
    return repo


def find_service(services: list[dict], repo_full_name: str) -> dict | None:
    """Return the single service whose repo matches *repo_full_name*, or None."""
    target = _normalize_repo(repo_full_name)
    matches = []
    for svc in services:
        repo_field = (svc.get("repo") or
                      svc.get("serviceDetails", {}).get("repo", ""))
        if _normalize_repo(repo_field).endswith(target):
            matches.append(svc)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RenderApiError(f"Ambiguous: {len(matches)} services match repo {repo_full_name}")
    return None


# ---------------------------------------------------------------------------
# Deploy polling
# ---------------------------------------------------------------------------

def _is_for_commit(deploy: dict, commit_sha: str) -> bool:
    """Return True if *deploy* is for *commit_sha* (prefix match)."""
    sha = (deploy.get("commit", {}) or {}).get("id", "")
    return bool(sha and commit_sha.lower().startswith(sha[:7].lower()))


def wait_for_deploy(client: RenderClient, service_id: str,
                    commit_sha: str, out_dir: Path,
                    guard: "TokenGuard") -> tuple[str, dict | None]:
    """
    Poll until a deploy for *commit_sha* reaches a terminal state.

    Returns (final_status, deploy_dict | None).
    final_status is one of: 'live', 'failed', 'timeout', 'not_found'.
    """
    _log(f"Waiting {INITIAL_WAIT_S}s for Render to register the deploy …")
    time.sleep(INITIAL_WAIT_S)

    target_deploy: dict | None = None
    polls = 0

    while polls < MAX_POLLS:
        polls += 1
        try:
            deploys = client.list_deploys(service_id, limit=10)
        except RenderApiError as exc:
            _log(f"  poll {polls}: API error — {sanitize_text(str(exc))}")
            time.sleep(POLL_INTERVAL_S)
            continue

        # Locate the deploy for our commit on first encounter
        if target_deploy is None:
            for d in deploys:
                if _is_for_commit(d, commit_sha):
                    target_deploy = d
                    _log(f"  Found deploy {d.get('id')} for commit {commit_sha[:12]}")
                    break
            else:
                # Also accept the most-recent in-progress or created deploy
                # as a fallback when commit SHA matching fails
                if deploys and deploys[0].get("status") in IN_PROGRESS_STATUSES:
                    target_deploy = deploys[0]
                    _log(f"  Falling back to most-recent deploy {target_deploy.get('id')}"
                         f" (commit SHA mismatch; status={target_deploy.get('status')})")

        if target_deploy is None:
            _log(f"  poll {polls}/{MAX_POLLS}: deploy not yet visible …")
            time.sleep(POLL_INTERVAL_S)
            continue

        # Refresh the deploy object directly
        try:
            target_deploy = client.get_deploy(service_id, target_deploy["id"])
        except RenderApiError:
            pass

        status = target_deploy.get("status", "unknown")
        _log(f"  poll {polls}/{MAX_POLLS}: status={status}")

        if status == "live":
            _log("✅ Deploy is LIVE.")
            return "live", target_deploy

        if status in FAILED_STATUSES:
            _log(f"❌ Deploy FAILED: {status}")
            return "failed", target_deploy

        if status in ("deactivated", "canceled"):
            _log(f"⚠️  Deploy {status}.")
            return status, target_deploy

        # Still in progress — keep polling
        time.sleep(POLL_INTERVAL_S)

    _log(f"⏰ Deploy observation timed out after {MAX_POLL_MINUTES} minutes.")
    return "timeout", target_deploy


# ---------------------------------------------------------------------------
# Log collection and classification
# ---------------------------------------------------------------------------

def _format_log_line(entry: dict) -> str:
    """Format a single Render log entry to a readable line."""
    ts  = entry.get("timestamp", "")
    msg = str(entry.get("message", ""))[:MAX_LOG_MSG_CHARS]
    labels = entry.get("labels") or []
    tag_parts = []
    for lbl in labels:
        if isinstance(lbl, dict):
            name, val = lbl.get("name", ""), lbl.get("value", "")
            if name in KEEP_LOG_LABELS and val:
                tag_parts.append(f"{name}={val}")
    tag = " ".join(sorted(tag_parts))
    return f"{ts}  {('[' + tag + '] ') if tag else ''}{msg}"


def collect_logs(client: RenderClient, owner_id: str, service_id: str,
                 deploy: dict, window_minutes: int = 30) -> str:
    """Collect, sanitize, and format recent runtime logs."""
    now_epoch = time.time()
    start_epoch = now_epoch - (window_minutes * 60)
    try:
        entries = client.get_logs(owner_id, service_id, start_epoch, now_epoch)
    except Exception:
        entries = []

    if not entries:
        return "(no runtime logs returned by Render API)"

    lines = [_format_log_line(e) for e in entries[:MAX_LOG_LINES]]
    raw = "\n".join(lines)
    return sanitize_text(raw)


def classify_failure(log_text: str, deploy_status: str) -> list[tuple[str, str]]:
    """
    Return a list of (category, subcategory) matches from FAILURE_PATTERNS.
    Empty list means unclassified.
    """
    findings: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for category, subcategory, fragment in FAILURE_PATTERNS:
        if fragment.lower() in log_text.lower():
            key = (category, subcategory)
            if key not in seen:
                findings.append(key)
                seen.add(key)
    return findings


# ---------------------------------------------------------------------------
# Diagnostic bundle writer
# ---------------------------------------------------------------------------

def write_bundle(out_dir: Path, payload: dict, guard: "TokenGuard") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # summary.json — sanitized structured payload
    safe_payload = sanitize_json(payload)
    summary_text = guard(json.dumps(safe_payload, indent=2))
    (out_dir / "summary.json").write_text(summary_text)

    # human-readable report (also sanitized)
    report_lines = [
        "APEX FLOW — Render Deployment Diagnostic Report",
        "=" * 55,
        f"Generated : {payload.get('collected_at', utc_now_iso())}",
        f"Commit    : {payload.get('commit_sha', 'unknown')}",
        f"Service   : {payload.get('service_name', 'unknown')}",
        f"Deploy ID : {payload.get('deploy_id', 'unknown')}",
        f"Status    : {payload.get('deploy_status', 'unknown')}",
        "",
        "─── Failure Classification ──────────────────────",
    ]
    for cat, sub in payload.get("failure_classes", []):
        auto = SAFE_AUTO_FIXES.get((cat, sub))
        auto_note = f"  [auto-fix available: {auto}]" if auto else ""
        report_lines.append(f"  {cat}/{sub}{auto_note}")
    if not payload.get("failure_classes"):
        report_lines.append("  (unclassified)")

    report_lines += [
        "",
        "─── Log Excerpt (last ~80 lines, sanitized) ─────",
        payload.get("log_excerpt", "(none)"),
        "",
        "─── Human Escalation Required? ──────────────────",
        payload.get("escalation_note", ""),
        "",
        "─── README ──────────────────────────────────────",
        "This artifact contains SANITIZED logs.",
        "No secrets, credentials, or tokens are included.",
        "Raw logs are available in the Render dashboard.",
    ]

    report_text = guard(sanitize_text("\n".join(report_lines)))
    (out_dir / "report.txt").write_text(report_text)
    _log(f"Diagnostic bundle written to {out_dir}/")


# ---------------------------------------------------------------------------
# GitHub Actions step summary writer
# ---------------------------------------------------------------------------

def write_step_summary(payload: dict, guard: "TokenGuard") -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    status_icon = {
        "live":    "✅",
        "failed":  "❌",
        "timeout": "⏰",
    }.get(payload.get("deploy_status", ""), "⚠️")

    lines = [
        f"## {status_icon} Render Deployment — {payload.get('deploy_status', 'unknown').upper()}",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| Commit | `{payload.get('commit_sha', '?')[:12]}` |",
        f"| Service | `{payload.get('service_name', '?')}` |",
        f"| Deploy ID | `{payload.get('deploy_id', '?')}` |",
        f"| Status | `{payload.get('deploy_status', '?')}` |",
        f"| Observed at | {payload.get('collected_at', '?')} |",
        "",
    ]

    classes = payload.get("failure_classes", [])
    if classes:
        lines += ["### Failure Classification", ""]
        for cat, sub in classes:
            auto = SAFE_AUTO_FIXES.get((cat, sub))
            auto_note = f" — auto-fix: _{auto}_" if auto else ""
            lines.append(f"- **{cat}** / `{sub}`{auto_note}")
        lines.append("")

    escalation = payload.get("escalation_note", "")
    if escalation:
        lines += [
            "### ⚠️ Human Escalation Required",
            "",
            escalation,
            "",
        ]

    excerpt = payload.get("log_excerpt", "")
    if excerpt and excerpt != "(no runtime logs returned by Render API)":
        lines += [
            "### Log Excerpt (sanitized)",
            "",
            "```",
            excerpt[:3000],  # hard cap for step summary
            "```",
            "",
        ]

    summary_raw = "\n".join(lines)
    summary_safe = guard(sanitize_text(summary_raw))
    try:
        with open(summary_file, "a") as f:
            f.write(summary_safe + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render deployment observer")
    parser.add_argument("--output", default="diagnostics",
                        help="Directory to write diagnostic bundle")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    args = parser.parse_args(argv)

    token = os.environ.get("RENDER_API_TOKEN", "")
    if not token:
        print("ERROR: RENDER_API_TOKEN environment variable is not set.", file=sys.stderr)
        return 1

    guard = sanitize_token(token)
    out_dir = Path(args.output)
    commit_sha = args.commit.strip()

    client = RenderClient(token)

    # Phase 1: Verify credential
    _log("Verifying Render API credential …")
    if not client.whoami():
        _log("ERROR: Render API authentication failed (401 or network error).")
        return 2

    # Phase 2: Identify service
    _log(f"Identifying Render service for repo {args.repo} …")
    try:
        services = client.list_services()
    except RenderApiError as exc:
        _log(f"ERROR listing services: {sanitize_text(str(exc))}")
        return 2

    try:
        service = find_service(services, args.repo)
    except RenderApiError as exc:
        _log(str(exc))
        return 5

    if service is None:
        _log(f"ERROR: No Render service found for repo {args.repo}.")
        _log("       Ensure the service is connected to the correct GitHub repository.")
        return 4

    service_id   = service.get("id", "")
    service_name = service.get("name", service_id)
    owner_id     = service.get("ownerId", "")
    _log(f"Service: {service_name} ({service_id})")

    # Phase 3: Observe the deployment
    deploy_status, deploy = wait_for_deploy(
        client, service_id, commit_sha, out_dir, guard,
    )

    deploy_id = (deploy or {}).get("id", "unknown")
    payload: dict = {
        "collected_at":    utc_now_iso(),
        "commit_sha":      commit_sha,
        "service_name":    service_name,
        "service_id":      service_id,
        "deploy_id":       deploy_id,
        "deploy_status":   deploy_status,
        "failure_classes": [],
        "log_excerpt":     "",
        "escalation_note": "",
    }

    if deploy_status == "live":
        write_bundle(out_dir, payload, guard)
        write_step_summary(payload, guard)
        return 0

    # Phase 4: Collect and classify logs on any non-live outcome
    _log("Collecting runtime logs …")
    log_text = collect_logs(client, owner_id, service_id, deploy or {})
    payload["log_excerpt"] = log_text

    failure_classes = classify_failure(log_text, deploy_status)
    payload["failure_classes"] = failure_classes

    # Phase 5: Determine escalation requirement
    escalation_conditions = []
    if deploy_status == "timeout":
        escalation_conditions.append(
            "Deployment observation timed out. The deploy may still be in progress."
        )
    if len(failure_classes) == 0:
        escalation_conditions.append(
            "Failure could not be classified from available logs. "
            "Manual Render dashboard inspection required."
        )
    if len(failure_classes) > 2:
        escalation_conditions.append(
            "Multiple unrelated failure categories detected. "
            "Manual diagnosis required to avoid incorrect automated fix."
        )
    has_safe_fix = any(
        (cat, sub) in SAFE_AUTO_FIXES for cat, sub in failure_classes
    )
    if failure_classes and not has_safe_fix:
        escalation_conditions.append(
            "Failure category does not have a safe automated fix. "
            "Human review required."
        )
    auth_failures = [(c, s) for c, s in failure_classes if c == "auth"]
    if auth_failures:
        escalation_conditions.append(
            "Authentication/CORS/cookie failure detected. "
            "Do NOT fix by disabling security controls. "
            "Check: ALLOWED_ORIGINS env var, FLASK_ENV=production, "
            "SECRET_KEY set, HTTPS/cookie Secure flag, proxy headers."
        )

    if escalation_conditions:
        payload["escalation_note"] = "\n".join(escalation_conditions)
        _log("⚠️  HUMAN ESCALATION REQUIRED:")
        for note in escalation_conditions:
            _log(f"   {note}")

    write_bundle(out_dir, payload, guard)
    write_step_summary(payload, guard)

    # Export classification for the workflow to act on
    gha_output = os.environ.get("GITHUB_OUTPUT")
    if gha_output:
        with open(gha_output, "a") as f:
            f.write(f"deploy_status={deploy_status}\n")
            classes_str = ";".join(f"{c}/{s}" for c, s in failure_classes)
            f.write(f"failure_classes={classes_str}\n")
            f.write(f"needs_human={'true' if escalation_conditions else 'false'}\n")
            f.write(f"has_safe_fix={'true' if has_safe_fix and not escalation_conditions else 'false'}\n")
            f.write(f"deploy_id={deploy_id}\n")

    return 3  # non-zero = deployment did not succeed


if __name__ == "__main__":
    sys.exit(main())
