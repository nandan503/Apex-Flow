"""Run explicitly: python -m backend.migrate. Never imported for startup DDL."""
import hashlib
import os
from pathlib import Path

from backend.database import connect


def migration_files():
    return [(p, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted((Path(__file__).resolve().parent.parent / 'migrations').glob('*.sql'))]


def migrate(url):
    conn = connect(url)
    try:
        with conn, conn.cursor() as cur:
            # DDL goes in public; runtime uses a catalog-first, temp-last search path.
            cur.execute('SET LOCAL search_path = public, pg_temp')
            cur.execute('SELECT pg_advisory_xact_lock(715022601)')
            cur.execute("SELECT to_regclass('public.schema_migrations'), to_regclass('public.users')")
            versioned, legacy = cur.fetchone()
            if legacy and not versioned:
                raise RuntimeError('Unversioned legacy database: offline ownership mapping and reviewed import required; no automatic upgrade')
            cur.execute('''CREATE TABLE IF NOT EXISTS schema_migrations
                           (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
            cur.execute('SELECT version, checksum FROM schema_migrations ORDER BY version')
            applied = dict(cur.fetchall())
            files = migration_files()
            if set(applied) - {p.name for p, _ in files}:
                raise RuntimeError('Database is newer than this application')
            for p, checksum in files:
                if p.name in applied:
                    if applied[p.name] != checksum:
                        raise RuntimeError('Applied migration checksum mismatch')
                    continue
                cur.execute(p.read_text())
                cur.execute('INSERT INTO schema_migrations(version, checksum) VALUES (%s, %s)', (p.name, checksum))
            cur.execute('GRANT SELECT ON schema_migrations TO apex_app')
    finally:
        conn.close()


if __name__ == '__main__':
    url = os.environ.get('MIGRATION_DATABASE_URL')
    if not url:
        raise SystemExit('MIGRATION_DATABASE_URL is required for this explicit command')
    try:
        migrate(url)
    except Exception as exc:
        # Driver exceptions can contain SQL data or connection details.
        raise SystemExit(f'Migration failed ({type(exc).__name__}); inspect with privileged tooling') from None
    print('Migrations complete')
