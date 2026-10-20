"""Small DB-API boundary for the portal's parameterised SQL."""
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

IntegrityError=sqlite3.IntegrityError

def is_postgres(target):return str(target).startswith(('postgres://','postgresql://'))

class SQLiteConnection(sqlite3.Connection):
    def begin_write(self):self.execute('BEGIN IMMEDIATE')
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

class Row(dict):
    def __getitem__(self,key):
        if isinstance(key,int):return list(self.values())[key]
        return super().__getitem__(key)

class Cursor:
    def __init__(self,cursor):self.cursor=cursor
    @property
    def rowcount(self):return self.cursor.rowcount
    def fetchone(self):
        row=self.cursor.fetchone();return Row(row) if row is not None else None
    def fetchall(self):return [Row(row) for row in self.cursor.fetchall()]
    def __iter__(self):
        for row in self.cursor:yield Row(row)

class PostgresConnection:
    def __init__(self,target):
        import psycopg
        from psycopg.rows import dict_row
        self.connection=psycopg.connect(target,connect_timeout=15,row_factory=dict_row,prepare_threshold=None)
    def __enter__(self):return self
    def __exit__(self,typ,value,traceback):
        try:
            if typ:self.connection.rollback()
            else:self.connection.commit()
        finally:self.connection.close()
    def execute(self,sql,params=()):
        import psycopg
        try:return Cursor(self.connection.execute(sql.replace('?', '%s'),params))
        except psycopg.IntegrityError:raise IntegrityError('Database constraint failed; check duplicate codes, account names and record links.') from None
    def executemany(self,sql,rows):
        import psycopg
        try:
            cursor=self.connection.cursor();cursor.executemany(sql.replace('?', '%s'),rows);return Cursor(cursor)
        except psycopg.IntegrityError:raise IntegrityError('Database constraint failed; no records were saved.') from None
    def begin_write(self):
        # Serialises portal writes, including self/last-admin checks, across requests.
        self.execute('SELECT pg_advisory_xact_lock(2027001)')
    def commit(self):self.connection.commit()
    def rollback(self):self.connection.rollback()


def connect(target):
    if is_postgres(target):return PostgresConnection(target)
    db=sqlite3.connect(target,timeout=30,factory=SQLiteConnection);db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON');return db


def init_postgres(target):
    direct=os.environ.get('DATABASE_URL_UNPOOLED') or target
    if '-pooler' in (urlsplit(direct).hostname or ''):raise ValueError('Use a direct DATABASE_URL_UNPOOLED for migrations.')
    with connect(direct) as db:
        db.begin_write()
        for migration in sorted((Path(__file__).parent/'migrations').glob('*_postgres.sql')):
            version=int(migration.name.split('_',1)[0])
            if version>1 and db.execute('SELECT 1 FROM schema_migrations WHERE version=?',(version,)).fetchone():
                continue
            for statement in migration.read_text().split(';'):
                if statement.strip():db.execute(statement)
