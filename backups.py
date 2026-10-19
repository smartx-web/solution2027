"""Consistent SQLite backups; copy backups off the service separately."""
import os
from contextlib import closing
import sqlite3
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path


def create_backup(database,directory,keep=14):
    source=Path(database).resolve(); target=Path(directory).resolve()
    if not source.is_file(): raise ValueError('Database does not exist.')
    if keep<1: raise ValueError('Keep at least one backup.')
    target.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    final=target/f'solution2027-{stamp}.sqlite3'; temporary=final.with_suffix('.partial')
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=30)) as src,closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst,pages=256,sleep=0.05)
            if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed.')
            if dst.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Backup relation check failed.')
        os.chmod(temporary,0o600);os.replace(temporary,final)
        copies=[path for path in target.glob('solution2027-*.sqlite3') if path.resolve()!=source]
        for old in sorted(copies,reverse=True)[keep:]:old.unlink()
        return final
    finally:temporary.unlink(missing_ok=True)


def start_backup_loop(database,directory,interval=86400,keep=14):
    stop=threading.Event()
    def run():
        while not stop.wait(interval):
            try:create_backup(database,directory,keep)
            except Exception:logging.exception('Scheduled database backup failed')
    thread=threading.Thread(target=run,name='database-backup',daemon=True);thread.start()
    return stop
