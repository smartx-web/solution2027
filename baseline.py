"""Versioned historical voter totals, separate from manually entered figures."""
import json
from pathlib import Path

PATH=Path(__file__).parent/'data'/'registered_voters_2023.json'

def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS voter_baselines (
      pu_code TEXT NOT NULL,year INTEGER NOT NULL,registered_voters INTEGER NOT NULL CHECK(registered_voters>=0),
      source_url TEXT NOT NULL,retrieved_on TEXT NOT NULL,PRIMARY KEY(pu_code,year))''')

def seed(db):
    data=json.loads(PATH.read_text())
    records=data['records']
    codes=[r['code'] for r in records]
    directory=json.loads((PATH.parent/'apa_agatu_directory.json').read_text())
    if data['year']!=2023 or len(codes)!=236 or len(set(codes))!=236 or set(codes)!={r['code'] for r in directory}:
        raise ValueError('Historical baseline does not match the bundled PU directory.')
    if any(type(r['registered_voters']) is not int or r['registered_voters']<0 for r in records):
        raise ValueError('Invalid historical registered-voter count.')
    totals={prefix:sum(r['registered_voters'] for r in records if r['code'].startswith(prefix)) for prefix in ('07/02/','07/03/')}
    if totals!={'07/02/':64315,'07/03/':66720}: raise ValueError('Historical baseline failed total validation.')
    existing={row['pu_code']:row for row in db.execute('SELECT pu_code,registered_voters,source_url FROM voter_baselines WHERE year=2023')}
    pending=[]
    for row in records:
        old=existing.get(row['code'])
        if old and (old['registered_voters']!=row['registered_voters'] or old['source_url']!=data['source_url']):
            raise ValueError('Conflicting historical baseline; existing records have been preserved.')
        if not old:pending.append((row['code'],row['registered_voters'],data['source_url'],data['retrieved_on']))
    if pending:db.executemany('INSERT INTO voter_baselines(pu_code,year,registered_voters,source_url,retrieved_on) VALUES(?,2023,?,?,?) ON CONFLICT(pu_code,year) DO NOTHING',pending)
    return len(pending)
