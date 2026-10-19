"""Local/service-shell credential and backup commands."""
import argparse
from getpass import getpass
import secrets
import auth
import app
import backups


def main():
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('hash-password')
    backup=commands.add_parser('backup');backup.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    if args.command=='hash-password':
        password=getpass('Password (at least 12 characters): ')
        if not 12<=len(password)<=1024:raise SystemExit('Use 12–1024 characters.')
        if password!=getpass('Confirm password: '):raise SystemExit('Passwords do not match.')
        salt=secrets.token_hex(16)
        print('pbkdf2_sha256$600000$'+salt+'$'+auth.password_hash(password,salt))
    else:print(backups.create_backup(app.DB,args.output_dir))

if __name__=='__main__':main()
