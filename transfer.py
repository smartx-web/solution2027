"""Explicit, atomic transfer from a verified SQLite copy to an empty PostgreSQL portal."""
from pathlib import Path
import sqlite3
import database

TABLES={
 'administrator':'id username salt password_hash',
 'users':'id username salt password_hash role lga wards active version pu_id',
 'pus':'id lga ward code name voters source source_date',
 'agents':'id pu_id name phone role status consent archived_at',
 'reports':'id pu_id category detail created_at',
 'activity_log':'id actor action entity record_id summary created_at',
 'pu_results':'id pu_id election revision previous_id figures parties cancelled notes submitter account submitted_at correction_reason flags photo photo_type',
 'result_reviews':'id result_id status notes reviewer reviewed_at',
 'voter_baselines':'pu_code year registered_voters source_url retrieved_on',
}

def migrate(source,target,apply=False):
    if not database.is_postgres(target):raise ValueError('The destination must be PostgreSQL.')
    path=Path(source).resolve(strict=True)
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as old:
        if old.execute('PRAGMA quick_check').fetchone()[0]!='ok' or old.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Source database failed integrity checks.')
        rows={name:old.execute('SELECT '+','.join(cols.split())+' FROM '+name+(' ORDER BY id' if 'id' in cols.split() else '')).fetchall() for name,cols in TABLES.items()}
    if not apply:return {name:len(values) for name,values in rows.items()}
    database.init_postgres(target)
    with database.connect(target) as new:
        new.begin_write()
        if any(new.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in TABLES):
            raise ValueError('Destination contains records. Transfer only to an empty database before first portal startup.')
        for name,cols in TABLES.items():
            columns=cols.split()
            if rows[name]:new.executemany('INSERT INTO '+name+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',rows[name])
            if name not in ('administrator','voter_baselines'):
                new.execute("SELECT setval(pg_get_serial_sequence('"+name+"','id'),COALESCE(MAX(id),1),MAX(id) IS NOT NULL) FROM "+name)
        for name,values in rows.items():
            if new.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]!=len(values):raise ValueError('Transfer count mismatch; rolled back.')
    return {name:len(values) for name,values in rows.items()}
