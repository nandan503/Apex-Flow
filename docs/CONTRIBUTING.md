# Contributing to APEX FLOW

Thank you for contributing! Please read these guidelines before submitting a pull request.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Branch Policy](#branch-policy)
- [Commit Message Standards](#commit-message-standards)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Security Guidelines](#security-guidelines)
- [Pre-Commit Hooks](#pre-commit-hooks)

---

## Code of Conduct

Be respectful, professional, and constructive in all interactions.

---

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/Apex-Flow.git
   cd Apex-Flow
   ```
3. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   pip install pre-commit semgrep
   pre-commit install
   ```
4. Copy `.env.example` to `.env` and configure for local development
5. Run the dev server: `python3 backend/app.py`

---

## Branch Policy

| Branch | Purpose | Direct Push |
|---|---|---|
| `main` | Production-ready code | ❌ Never |
| `feature/*` | New features | ✅ Your fork |
| `fix/*` | Bug fixes | ✅ Your fork |
| `chore/*` | Maintenance, deps, tooling | ✅ Your fork |
| `docs/*` | Documentation only | ✅ Your fork |
| `security/*` | Security patches | ✅ Your fork |

**Core Law:** Never push directly to `main`. Always create a branch and open a Pull Request.

Create a branch:
```bash
git checkout -b feature/my-new-feature
```

---

## Commit Message Standards

All commits must follow [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <short description>

[Optional body: what and why, not how]

[Optional footer: BREAKING CHANGE, Fixes #issue]
```

### Types

| Type | When to use |
|---|---|
| `feat` | New feature or behavior |
| `fix` | Bug fix |
| `security` | Security patch or remediation |
| `chore` | Build, tooling, dependency updates |
| `docs` | Documentation only |
| `refactor` | Code change that is neither a fix nor feature |
| `test` | Adding or fixing tests |
| `perf` | Performance improvement |

### Examples

```bash
git commit -m "feat(shipments): add bulk status update endpoint"
git commit -m "fix(auth): prevent session fixation on login"
git commit -m "security(otp): upgrade to 6-digit CSPRNG OTP with lockout"
git commit -m "docs(api): add route optimization response examples"
git commit -m "chore(deps): upgrade flask-limiter to 3.5.0"
```

### Rules

- Use **imperative mood**: "add X" not "adds X" or "added X"
- Keep the subject line under 72 characters
- Reference issues: `Fixes #42` in the footer
- No `git add .` — always enumerate files explicitly

---

## Pull Request Process

### Before Opening a PR

1. **Inspect working tree** before staging anything:
   ```bash
   git status --porcelain=v2 --branch
   git diff --stat
   ```

2. **Secret scan** — run gitleaks manually:
   ```bash
   gitleaks detect --source . --verbose
   ```

3. **SAST scan** — run semgrep:
   ```bash
   semgrep --config .semgrep.yml backend/
   ```

4. **Verify no binary artifacts are staged:**
   - `backend/__pycache__/` — must not be staged
   - `data/apexflow.db` — must not be staged
   - `.env` — must not be staged

5. Push your branch:
   ```bash
   git push -u origin feature/your-branch-name
   ```

### PR Template

When opening a PR, fill in:

```markdown
## Summary
What does this PR do?

## Type of Change
- [ ] feat: New feature
- [ ] fix: Bug fix
- [ ] security: Security patch
- [ ] chore: Maintenance
- [ ] docs: Documentation

## Testing Done
- [ ] Ran the dev server locally
- [ ] Tested affected endpoints with curl or Postman
- [ ] No new secrets or hardcoded values

## Checklist
- [ ] Commits follow Conventional Commits format
- [ ] No .pyc, .db, or .env files staged
- [ ] Secret scan passed
- [ ] Semgrep SAST passed
```

### Merge Policy

- Require at least **1 reviewer approval** before merging
- All CI checks must pass
- Use **Squash and Merge** for feature branches to keep `main` history clean
- Delete the branch after merging

---

## Coding Standards

### Python

- Follow [PEP 8](https://peps.python.org/pep-0008/)
- Use type hints for all function signatures
- All functions must have a docstring if they have side effects or non-obvious behavior
- No bare `except:` clauses — always catch specific exceptions
- Use `secrets` module for all cryptographic randomness — never `random`

### API Routes

- Every new route must have `@login_required` or `@require_role` — no exceptions
- Use `json_response()` and `error_response()` from `backend/utils.py`
- Validate required fields before calling service functions
- Never accept user-controlled values for audit fields (`updated_by`, timestamps, etc.)

### Database

- Use parameterized queries (SQLite `?` / PostgreSQL `%s`) — never f-string SQL
- Prefer explicit `SELECT col1, col2` over `SELECT *`
- Close all connections explicitly with `conn.close()`
- New tables must have a migration path in `initialize_database()`

### Frontend

- All API calls must handle both `success: true` and `success: false` responses
- Include appropriate loading and error states
- Responsive CSS required for all new pages (mobile-first)

---

## Security Guidelines

- **Never hardcode secrets** — use environment variables
- **Never log raw PII** — use the redaction helpers in `backend/logger.py`
- **Never trust client-supplied role or user_id** — always use `session.get()`
- **Always use `@login_required` or `@require_role`** on new endpoints
- If you find a security vulnerability, report it privately via GitHub Security Advisories — do NOT open a public issue

---

## Pre-Commit Hooks

The repository includes pre-commit hooks that run automatically on `git commit`:

```yaml
# .pre-commit-config.yaml
- gitleaks  # Scans for secrets/credentials
- semgrep   # Runs custom SAST rules
```

Setup (one-time):
```bash
pip install pre-commit
pre-commit install
```

To run manually against all files:
```bash
pre-commit run --all-files
```

If a hook blocks your commit, fix the flagged issue before committing. Do **not** use `--no-verify` to bypass hooks.

---

*Questions? Open a GitHub Discussion or contact the maintainers.*
