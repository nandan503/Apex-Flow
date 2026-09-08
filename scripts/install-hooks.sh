#!/usr/bin/env bash
# install-hooks.sh
# One-time setup: installs the project's Git hooks and pre-commit framework.
# Run from the repository root: bash scripts/install-hooks.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_SRC="$REPO_ROOT/.git-hooks"
HOOKS_DST="$REPO_ROOT/.git/hooks"

echo "=== APEX FLOW — Git hooks installer ==="

# ── 1. Install commit-msg hook (Conventional Commits enforcement) ──────────────
if [ -f "$HOOKS_SRC/commit-msg" ]; then
  cp "$HOOKS_SRC/commit-msg" "$HOOKS_DST/commit-msg"
  chmod +x "$HOOKS_DST/commit-msg"
  echo "✅ commit-msg hook installed."
else
  echo "⚠️  .git-hooks/commit-msg not found. Skipping."
fi

# ── 2. Install pre-commit framework (gitleaks + semgrep + general hooks) ───────
if ! command -v pre-commit &>/dev/null; then
  echo "ℹ️  pre-commit not found. Installing..."
  pip install pre-commit --quiet
fi

pre-commit install --hook-type pre-commit
echo "✅ pre-commit hooks installed (gitleaks, semgrep, whitespace checks)."

# ── 3. Remind about semgrep ────────────────────────────────────────────────────
if ! command -v semgrep &>/dev/null; then
  echo "ℹ️  semgrep not found. Install with: pip install semgrep"
fi

echo ""
echo "=== Setup complete. Hooks active on next commit. ==="
echo ""
echo "To run all hooks manually against the full codebase:"
echo "  pre-commit run --all-files"
echo ""
echo "To test the commit-msg hook:"
echo "  echo 'bad message' | .git/hooks/commit-msg /dev/stdin"
