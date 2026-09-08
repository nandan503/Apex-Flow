# APEX Flow

Flask logistics application with a PostgreSQL security and transaction baseline.
The existing HTML/JavaScript UI and domain services are retained; persistence,
tenancy, sessions, mutation replay and database lifecycle have been reengineered.

**This is not a claim of production readiness or end-to-end high availability.**
No cloud providers, database replicas or object-storage replication have been
provisioned or failover-tested by this change. See the release blockers below.

## Architecture

```text
Internet → Cloudflare (operator-managed routing / origin protection)
                 ↓
       interchangeable stateless Flask compute
       Northflank / Render / Koyeb / emergency provider
                 ↓
       shared PostgreSQL — source of truth
       Redis — not required; currently unused
       object storage — required before adding durable file features
```

There is no provider detection in application code. All compute instances must
use the same database, cookie signing secret, replay keyring and outbox key.
Cookies carry a random session handle; sessions, memberships, rate-limit buckets,
business records, idempotency results, audit events and outbox state live in PostgreSQL.

## Read first

- [Discovery and original operation traces](docs/DISCOVERY.md)
- [Security model and authorization matrix](docs/SECURITY_MODEL.md)
- [Transaction, retry and external-delivery contracts](docs/DATA_CONTRACTS.md)
- [Configuration, migration and operations runbook](docs/OPERATIONS.md)
- [Verification evidence and remaining gates](SECURITY_AUDIT.md)

## Setup (explicit database lifecycle)

Python 3.11 and PostgreSQL 16 are the tested baseline. **SQLite is no longer a
supported alternative:** it cannot prove the PostgreSQL isolation contract.

```sh
python -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements.txt
```

1. Have the database operator create a dedicated database and non-owner `apex_app`
   login as described in the runbook. Use generated credentials, never defaults.
2. In an isolated migration job, supply `MIGRATION_DATABASE_URL`, then run:
   ```sh
   python -m backend.migrate
   python -m backend.cli provision-tenant --name 'Your organization' \
     --email 'operator@your-domain.invalid' --admin-name 'Tenant administrator'
   ```
   Provisioning prompts for a password without echoing it. It is not a web endpoint.
3. Remove privileged credentials from the environment. Populate runtime values
   listed in `.env.example` through your secret manager. Development must explicitly
   use `APP_ENV=development`; production is the default. Secrets are required in
   **every** environment. The app does not automatically load `.env` files.
4. Start compute only; this does **not** seed or migrate anything:
   ```sh
   gunicorn 'backend.app:create_app()' --bind 0.0.0.0:5050 --workers 2 --timeout 30
   ```
   Dockerfile, Procfile and render.yaml use the same factory. There is no module-level
   `app`/`application` object, so `gunicorn backend.app:app` fails by design.
   Live probe: `/health/live`. Database readiness probe: `/health/ready`.

**Existing installations:** the migration runner deliberately rejects an
unversioned database. There is no safe way to infer tenant ownership from the old
global dataset. Preserve the old DB, map ownership and identities, rotate all
legacy passwords, and perform a reviewed offline import into a separate migrated
DB. This change does not silently relabel legacy data or supply an import script
that guesses tenancy.

## Browser/API contract changes

- Bootstrap CSRF with `GET /api/auth/csrf`; send `X-CSRF-Token` for **all** unsafe
  requests including login/logout. Origin allowlisting is additional, not a substitute.
- Authenticated requests use `X-Tenant-ID` as a selector. It must match a current
  membership. A sole membership is the server default; multiple memberships
  require an explicit selector. The current UI selects the first server-returned
  membership; a tenant-switcher UI is not yet implemented.
- Protected mutations require a 16–128 character `Idempotency-Key` containing
  letters, digits, `_` or `-`. Reuse the same key and exact body after an ambiguous
  response. Do not generate a new key to retry a failed network request.
- Shipment lists support `limit` (1–200), `offset` (0–10000), allowlisted `sort` and
  `direction`. Monetary/NUMERIC fields are serialized as decimal strings.
- Authentication failures are 401; insufficient privileges are 403; inaccessible
  private resource selectors normally return 404. Constraint failures are generic
  409, not PostgreSQL exception text. Database unavailability returns 503.

## OTP delivery

Shipment creation writes an encrypted OTP event in the same transaction as the
shipment, delivery, invoice, notification and replay record. Configure an HTTPS
consumer implementing the documented idempotent delivery contract, then schedule:

```sh
python -m backend.cli deliver-outbox --tenant AUTHORIZED_TENANT_ID \
  --user WORKER_TENANT_ADMIN_USER_ID --limit 100
```

Each job revalidates the worker's tenant membership. Running it twice is supported.
Without a consumer, events stay pending: **no email or SMS has been sent**. Existing
OTP expiration is 30 minutes from booking; a just-in-time issuance/reissuance workflow
is still a release blocker for real multi-day deliveries.

## Tests and dependency verification

Use a **disposable** PostgreSQL database whose name ends in `_test`. The fixture
resets that schema and creates/uses a restricted runtime role; it never uses SQLite.
Do not point it at production. Tests generate credentials at runtime.

```sh
pip install -r requirements-dev.txt
# Inject TEST_DATABASE_ADMIN_URL for the disposable database via your environment.
pytest -q
pip-audit -r requirements.txt
```

CI runs these contracts with PostgreSQL. Update `requirements.in`, regenerate the
hash-pinned lockfile with pip-compile, audit, and rerun tests before dependency changes.

## Release blockers / deliberate limitations

Legacy ownership migration, operational secret provisioning, edge/origin lockdown,
real consumer integration, OTP reissuance, alert routing, backups/restoration,
load testing and complete dependency failover require operator/product work.
Production tracking returns an explicit unavailable error rather than fabricated
GPS coordinates. No upload/download, object-storage, bulk/import, real payment
charge, password-reset email, system-admin HTTP API, or scheduler exists.

The initial transaction strategy serializes operations per tenant. This is a
correctness-first baseline, **not** a high-throughput design. See the audit for
remaining CSP, pagination, money/pricing and lifecycle limitations.
