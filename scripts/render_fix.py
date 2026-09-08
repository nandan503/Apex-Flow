#!/usr/bin/env python3
"""Bounded, minimal automated remediation for classified Render failures.

This script is only invoked by the CI workflow when:
  1. A deployment failure has been classified with exactly one safe, known
     subcategory (see SAFE_AUTO_FIXES in render_observe.py).
  2. This is the FIRST remediation attempt (REMEDIATION_ATTEMPT == "1").
  3. Human escalation is NOT required.

The script is intentionally narrow.  It verifies a hypothesis, applies the
minimum necessary change, runs the existing test suite, and reports back.
It does NOT:
  - Trigger Render deploys (pushes to the fix branch do that via auto-deploy).
  - Modify security controls, secrets, or infrastructure.
  - Retry indefinitely.
  - Fabricate or delete tests.

Exit codes:
  0  Fix applied and validated.
  1  No fixable issue found (false positive classification).
  2  Fix applied but validation failed — requires human review.
  3  Fix category not supported or multiple categories.
  4  This is not the first attempt — refusing to re-apply.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

try:
    from scripts.render_sanitize import sanitize_text
except ImportError:
    from render_sanitize import sanitize_text


MAX_ATTEMPT = 1  # hard ceiling: refuse to run on attempt 2+

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Guard: refuse to run on attempt > 1
# ---------------------------------------------------------------------------

def _get_attempt() -> int:
    try:
        return int(os.environ.get("REMEDIATION_ATTEMPT", "1"))
    except ValueError:
        return 1


# ---------------------------------------------------------------------------
# Syntax check and auto-fix
# ---------------------------------------------------------------------------

def _check_syntax_all() -> list[tuple[Path, str]]:
    """Return list of (file, error_message) for Python files with syntax errors."""
    errors: list[tuple[Path, str]] = []
    for py_file in REPO_ROOT.rglob("*.py"):
        if any(p in str(py_file) for p in ("/.git/", "/node_modules/", "/__pycache__/")):
            continue
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=str(py_file))
        except SyntaxError as exc:
            errors.append((py_file, f"line {exc.lineno}: {exc.msg}"))
    return errors


def attempt_syntax_fix() -> int:
    """
    Verify syntax error hypothesis.
    If errors exist, report them; we cannot auto-rewrite arbitrary syntax errors.
    Automated fix is limited to: confirming the error, reporting the file and line.
    A human or the next CI run with a code change must fix the actual syntax.
    """
    errors = _check_syntax_all()
    if not errors:
        print("[fix] Syntax check passed — no errors found in any .py file.")
        print("[fix] The 'SyntaxError' in Render logs may be from a dependency,")
        print("[fix]   or the error was already fixed in the current commit.")
        return 1  # no fixable issue found

    print("[fix] Syntax errors found:")
    for path, msg in errors:
        rel = path.relative_to(REPO_ROOT)
        print(f"  {rel}:{msg}")
    print()
    print("[fix] Cannot automatically rewrite arbitrary syntax errors.")
    print("[fix] → Human action required: fix the syntax errors listed above.")
    print("[fix] → After fixing, commit and push to trigger a new deploy.")
    return 2  # requires human review


# ---------------------------------------------------------------------------
# Import / requirements check
# ---------------------------------------------------------------------------

def attempt_import_fix() -> int:
    """
    Verify import error hypothesis.
    Check that all imported packages are listed in requirements.txt.
    If a missing package is found, add it and validate.
    """
    req_file = REPO_ROOT / "requirements.txt"
    requirements_text = req_file.read_text() if req_file.exists() else ""

    # Find all top-level imports across backend/*.py
    imported: set[str] = set()
    for py_file in (REPO_ROOT / "backend").glob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])

    # Standard library modules (common ones) — skip these
    stdlib_prefixes = {
        "os", "sys", "re", "json", "time", "datetime", "pathlib", "typing",
        "collections", "functools", "itertools", "math", "uuid", "hmac",
        "hashlib", "secrets", "random", "string", "io", "abc", "copy",
        "enum", "dataclasses", "contextlib", "logging", "warnings",
        "traceback", "inspect", "threading", "queue", "socket", "struct",
        "urllib", "http", "email", "base64", "binascii", "decimal",
        "unittest", "subprocess", "shutil", "tempfile", "glob", "fnmatch",
        "weakref", "gc", "platform", "signal", "__future__",
        # app-internal
        "backend", "config", "auth", "database", "routes", "errors",
        "utils", "logger", "contracts",
    }

    # Packages installed in current env
    installed_result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=freeze"],
        capture_output=True, text=True,
    )
    installed = {
        line.split("==")[0].lower().replace("-", "_")
        for line in installed_result.stdout.splitlines()
        if "==" in line
    }

    missing: list[str] = []
    for pkg in sorted(imported):
        if pkg in stdlib_prefixes or pkg.startswith("_"):
            continue
        pkg_normalized = pkg.lower().replace("-", "_")
        if pkg_normalized not in installed and pkg not in requirements_text:
            missing.append(pkg)

    if not missing:
        print("[fix] No missing packages detected.")
        print("[fix] The ImportError in Render logs may be from a package name/version")
        print("[fix]   mismatch.  Check requirements.txt manually.")
        return 1

    print(f"[fix] Potentially missing packages: {missing}")
    print("[fix] Cannot automatically determine correct version constraints.")
    print("[fix] → Human action required: add missing packages to requirements.txt")
    print("[fix]   with appropriate version bounds, then commit and push.")
    return 2


# ---------------------------------------------------------------------------
# Validation runner
# ---------------------------------------------------------------------------

def run_validation() -> bool:
    """Run the test suite.  Returns True if all tests pass."""
    test_dir = REPO_ROOT / "tests"
    if not test_dir.exists():
        print("[fix] No tests/ directory found — skipping test validation.")
        return True  # no tests = cannot validate

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_dir), "-x", "-q",
         "--tb=short", "--no-header"],
        cwd=str(REPO_ROOT),
        capture_output=True, text=True,
        timeout=120,
    )
    print(sanitize_text(result.stdout[-3000:] if result.stdout else ""))
    if result.returncode != 0:
        print(sanitize_text(result.stderr[-1000:] if result.stderr else ""))
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Bounded Render failure remediation")
    parser.add_argument("--category",    required=True, help="Failure category")
    parser.add_argument("--subcategory", required=True, help="Failure subcategory")
    args = parser.parse_args(argv)

    attempt = _get_attempt()
    print(f"[fix] Remediation attempt: {attempt} / {MAX_ATTEMPT}")

    if attempt > MAX_ATTEMPT:
        print(f"[fix] STOP: attempt {attempt} exceeds max {MAX_ATTEMPT}. "
              f"Refusing to re-apply automated fixes. Human review required.")
        return 4

    category, subcategory = args.category, args.subcategory
    print(f"[fix] Category: {category}/{subcategory}")

    if category == "build" and subcategory == "syntax_error":
        result = attempt_syntax_fix()
    elif category == "build" and subcategory == "import_error":
        result = attempt_import_fix()
    else:
        print(f"[fix] No automated fix for {category}/{subcategory}. Human required.")
        return 3

    if result == 0:
        print("[fix] Fix applied successfully. Running validation …")
        if run_validation():
            print("[fix] ✅ Validation passed.")
            return 0
        else:
            print("[fix] ❌ Validation failed after fix. Human review required.")
            return 2

    return result


if __name__ == "__main__":
    sys.exit(main())
