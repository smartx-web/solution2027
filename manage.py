"""Local/service-shell credential and backup commands."""
import argparse
import os
import json
import subprocess
import database
import transfer
from getpass import getpass
import secrets
import auth
import app
import backups


def postgres_dump_environment(target):
    """Pass parsed libpq settings through env vars, keeping secrets out of argv."""
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.pq import Conninfo
    bindings={option.keyword.decode():option.envvar.decode() for option in Conninfo.get_defaults() if option.envvar}
    settings=conninfo_to_dict(target)
    if any(key not in bindings for key in settings):
        raise ValueError('The PostgreSQL URL uses a setting pg_dump cannot receive securely.')
    return {**os.environ,**{bindings[key]:value for key,value in settings.items()}}


def main():
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('hash-password')
    backup=commands.add_parser('backup');backup.add_argument('--output-dir',required=True)
    migration=commands.add_parser('migrate-sqlite')
    migration.add_argument('--source',required=True)
    migration.add_argument('--apply',action='store_true',help='Write to an empty PostgreSQL target; otherwise validate only.')
    dump=commands.add_parser('dump-postgres');dump.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.command=='hash-password':
        password=getpass('Password (at least 12 characters): ')
        if not 12<=len(password)<=1024:raise SystemExit('Use 12–1024 characters.')
        if password!=getpass('Confirm password: '):raise SystemExit('Passwords do not match.')
        salt=secrets.token_hex(16)
        print('pbkdf2_sha256$600000$'+salt+'$'+auth.password_hash(password,salt))
    elif args.command=='migrate-sqlite':
        print(json.dumps(transfer.migrate(args.source,app.DB,apply=args.apply),indent=2))
    elif args.command=='dump-postgres':
        target=os.environ.get('DATABASE_URL_UNPOOLED','')
        if not database.is_postgres(target):raise SystemExit('Set DATABASE_URL_UNPOOLED.')
        os.umask(0o077)
        # Credentials stay in the child environment, never command arguments.
        with open(args.output,'xb') as output:
            subprocess.run(['pg_dump','--format=custom','--no-owner','--no-acl'],env=postgres_dump_environment(target),stdout=output,check=True)
        print('Backup saved. Verify it with pg_restore --list before storing it securely.')
    else:
        if database.is_postgres(app.DB):raise SystemExit('Use dump-postgres for PostgreSQL backups.')
        print(backups.create_backup(app.DB,args.output_dir))

if __name__=='__main__':main()
