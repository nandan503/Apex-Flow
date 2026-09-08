"""Explicit control-plane commands. Credentials never appear as CLI arguments."""
import argparse
import getpass
import os
import uuid

from werkzeug.security import generate_password_hash

from backend.database import connect


def provision(url, tenant_name, email, name):
    password = getpass.getpass('New tenant administrator password: ')
    if len(password) < 14 or len(password) > 1024:
        raise ValueError('Password must be 14–1024 characters')
    tenant_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn = connect(url)
    try:
        with conn, conn.cursor() as cur:
            # Privileged control plane, never available to web runtime.
            cur.execute('INSERT INTO tenants(tenant_id,name) VALUES (%s,%s)', (tenant_id, tenant_name))
            cur.execute('INSERT INTO users(user_id,name,email,password_hash) VALUES (%s,%s,%s,%s)',
                        (user_id, name, email.strip().lower(), generate_password_hash(password)))
            cur.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (%s,%s,'ADMIN')", (tenant_id,user_id))
    finally:
        conn.close()
    print(f'Created tenant {tenant_id}, administrator {user_id}')


def reset_password(url, user_id):
    password = getpass.getpass('New password (14–1024 characters): ')
    if not 14 <= len(password) <= 1024:
        raise ValueError('Password must be 14–1024 characters')
    conn = connect(url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute('UPDATE users SET password_hash=%s WHERE user_id=%s', (generate_password_hash(password), user_id))
            if cur.rowcount != 1:
                raise ValueError('Unknown user')
            cur.execute('DELETE FROM auth_sessions WHERE user_id=%s', (user_id,))
    finally:
        conn.close()
    print('Password rotated and all sessions revoked')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('provision-tenant')
    create.add_argument('--name', required=True)
    create.add_argument('--email', required=True)
    create.add_argument('--admin-name', required=True)
    reset = sub.add_parser('reset-password')
    reset.add_argument('--user', required=True)
    worker = sub.add_parser('deliver-outbox')
    worker.add_argument('--tenant', required=True)
    worker.add_argument('--user', required=True)
    worker.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    if args.command in ('provision-tenant', 'reset-password'):
        url = os.environ.get('MIGRATION_DATABASE_URL')
        if not url:
            raise RuntimeError('Privileged MIGRATION_DATABASE_URL required')
        if args.command == 'provision-tenant':
            provision(url, args.name, args.email, args.admin_name)
        else:
            reset_password(url, args.user)
    else:
        from backend.app import create_app
        from backend.authz import Caller
        from backend.database import identity_session
        from backend.outbox import deliver_one
        app = create_app()
        with app.app_context(), identity_session() as conn, conn.cursor() as cur:
            cur.execute('SELECT user_id,name,email FROM users WHERE user_id=%s AND active', (args.user,))
            user = cur.fetchone()
            if not user:
                raise RuntimeError('Unknown worker principal')
            caller = Caller(tenant_id=args.tenant, role='ADMIN', **dict(user))
            # db_session revalidates membership even for this CLI-created context.
            for _ in range(max(0, min(args.limit, 1000))):
                if not deliver_one(caller):
                    break


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(f'Command failed ({type(exc).__name__}); no credentials or row data logged') from None
