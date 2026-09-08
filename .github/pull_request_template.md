## What does this PR do?

<!--
One paragraph. What changed and why? If this closes an issue, link it here.
"Fixes #42" in this section triggers GitHub's auto-close on merge.
-->



## Type of change

<!-- Check the one that applies. Multiple are fine for mixed PRs. -->

- [ ] `feat` — New feature or user-visible behaviour
- [ ] `fix` — Bug fix
- [ ] `security` — Security patch / vulnerability remediation
- [ ] `chore` — Dependency update, tooling, build configuration
- [ ] `docs` — Documentation only (no code change)
- [ ] `refactor` — Internal restructure (no behaviour change)
- [ ] `test` — New or improved tests
- [ ] `perf` — Performance improvement

---

## How was this tested?

<!--
Describe what you actually ran. "Tested locally" without specifics is not useful.
Example: "Started dev server, hit POST /api/shipments with curl, verified 201 response
and new row in SQLite DB. Re-ran login with wrong password, confirmed 401."
-->



## Risk assessment

<!--
Answer only what's relevant. Delete lines that don't apply.
-->

**Risk level:** Low / Medium / High

| Area | Detail |
|---|---|
| Breaking API change? | |
| Database schema change? | |
| Auth / RBAC change? | |
| New dependency added? | |
| Affects Render/production deployment? | |
| Rollback possible without migration? | |

---

## Security considerations

<!--
If this touches auth, sessions, OTP, CORS, rate limiting, or data access:
- Did you run semgrep locally? (`semgrep --config .semgrep.yml backend/`)
- Did you check for hardcoded secrets? (`gitleaks detect --source . --verbose`)
- If no security impact: delete this section entirely rather than writing "N/A".
-->



## Reviewer focus

<!--
Optional but helpful. Tell your reviewer where to look hardest.
Example: "The critical path is services.py:confirm_delivery() — the OTP comparison
logic needs the most scrutiny."
-->

