"""Local development portal. Run with python3 app.py."""
from datetime import datetime, timezone
import html
import csv
import io
import json
from pathlib import Path
import auth
import results
import access
import baseline
import database
import os
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, urlencode

DB = os.environ.get('DATABASE_URL') or os.environ.get('SOLUTION_DB', 'solution2027.sqlite3')
TOKEN = secrets.token_urlsafe(32)

def connect():
    return database.connect(DB)

def seed_directory_data(db):
    rows=json.loads((Path(__file__).parent/'data'/'apa_agatu_directory.json').read_text())
    db.executemany('INSERT INTO pus(lga,ward,code,name,voters,source,source_date) VALUES(?,?,?,?,NULL,?,?) ON CONFLICT(code) DO NOTHING',
                   [(r['lga'],r['ward'],r['code'],r['name'],'User-supplied Nigeria polling-unit directory snapshot','') for r in rows])
    added=baseline.seed(db)
    if added:log_activity(db,'Imported','historical baseline',None,f'{added} PU totals added for 2023; existing sourced totals preserved.')

def init_db(seed_directory=False):
    if database.is_postgres(DB):
        database.init_postgres(DB)
        if seed_directory:
            with connect() as db:
                db.begin_write();seed_directory_data(db)
        return
    with connect() as db:
        auth.init(db)
        baseline.init(db)
        db.executescript('''
        CREATE TABLE IF NOT EXISTS activity_log (
          id INTEGER PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,
          entity TEXT NOT NULL,record_id INTEGER,summary TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS pus (
          id INTEGER PRIMARY KEY, lga TEXT NOT NULL CHECK(lga IN ('Apa','Agatu')),
          ward TEXT NOT NULL, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          voters INTEGER CHECK(voters>=0), source TEXT NOT NULL, source_date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agents (
          id INTEGER PRIMARY KEY, pu_id INTEGER NOT NULL REFERENCES pus(id),
          name TEXT NOT NULL, phone TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('Primary','Backup')),
          status TEXT NOT NULL CHECK(status IN ('Pending','Confirmed','Trained')), consent INTEGER NOT NULL CHECK(consent=1));
        CREATE TABLE IF NOT EXISTS reports (
          id INTEGER PRIMARY KEY, pu_id INTEGER NOT NULL REFERENCES pus(id),
          category TEXT NOT NULL CHECK(category IN ('Logistics','Incident','Community concern')),
          detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        ''')

    with connect() as db:
        if not any(r['name']=='archived_at' for r in db.execute('PRAGMA table_info(agents)')):
            db.execute('ALTER TABLE agents ADD COLUMN archived_at TEXT')

    # Existing databases used NOT NULL, which cannot represent an unknown total.
    with connect() as db:
        columns=db.execute('PRAGMA table_info(pus)').fetchall()
        if any(c['name']=='voters' and c['notnull'] for c in columns):
            db.execute('PRAGMA foreign_keys=OFF')
            try:
                db.begin_write()
                db.execute("CREATE TABLE pus_new(id INTEGER PRIMARY KEY,lga TEXT NOT NULL CHECK(lga IN ('Apa','Agatu')),ward TEXT NOT NULL,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,voters INTEGER CHECK(voters>=0),source TEXT NOT NULL,source_date TEXT NOT NULL)")
                db.execute('INSERT INTO pus_new SELECT * FROM pus')
                db.execute('DROP TABLE pus')
                db.execute('ALTER TABLE pus_new RENAME TO pus')
                if db.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('Database links failed validation.')
                db.commit()
            except Exception:
                db.rollback(); raise
            finally: db.execute('PRAGMA foreign_keys=ON')
        results.init(db)
        if seed_directory:seed_directory_data(db)


PU_COLUMNS = ('lga','ward','code','name','voters','source','source_date')

def required(data,key,maximum=500):
    value=data.get(key,'').strip()
    if not value or len(value)>maximum:
        raise ValueError(f'{key}: required, maximum {maximum} characters.')
    return value

def pu_values(data):
    import datetime
    lga=required(data,'lga')
    if lga not in ('Apa','Agatu'): raise ValueError('Choose Apa or Agatu.')
    raw=required(data,'voters')
    if not raw.isascii() or not raw.isdecimal(): raise ValueError('Voter total must be a nonnegative whole number.')
    voters=int(raw)
    if voters>2147483647: raise ValueError('Voter total is too large.')
    date=required(data,'source_date')
    datetime.date.fromisoformat(date)
    return (lga,required(data,'ward'),required(data,'code'),required(data,'name'),voters,required(data,'source'),date)

def parse_import(text):
    if len(text.encode('utf-8'))>1000000: raise ValueError('CSV must be under 1 MB.')
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')),strict=True)
    if reader.fieldnames!=list(PU_COLUMNS):
        raise ValueError('CSV header must be: '+','.join(PU_COLUMNS))
    rows=[]; codes=set()
    for number,row in enumerate(reader,2):
        if number>5001: raise ValueError('Import a maximum of 5,000 PUs at a time.')
        if None in row or None in row.values(): raise ValueError(f'Row {number}: incorrect number of columns.')
        try: values=pu_values(row)
        except ValueError as error: raise ValueError(f'Row {number}: {error}') from error
        if values[2] in codes: raise ValueError(f'Row {number}: duplicate PU code {values[2]}.')
        codes.add(values[2]); rows.append(values)
    if not rows: raise ValueError('CSV contains no polling units.')
    return rows

def log_activity(db,action,entity,record_id,summary):
    user=access.current_user.get()
    admin=db.execute('SELECT username FROM administrator WHERE id=1').fetchone()
    db.execute('INSERT INTO activity_log(actor,action,entity,record_id,summary) VALUES(?,?,?,?,?)',
               (user['username'] if user else (admin[0] if admin else 'Local setup'),action,entity,record_id,summary))

def set_agent_archived(data,archived):
    access.require_write()
    if data.get('confirmed')!='yes': raise ValueError('Confirm the action first.')
    record_id=int(required(data,'id'))
    with connect() as db:
        db.begin_write()
        agent=db.execute('SELECT a.archived_at,p.lga,p.ward FROM agents a JOIN pus p ON p.id=a.pu_id WHERE a.id=?',(record_id,)).fetchone()
        if not agent: raise ValueError('Agent assignment does not exist.')
        access.require_scope(agent)
        if bool(agent['archived_at'])==archived: raise ValueError('Agent assignment is already in that state.')
        if archived:
            db.execute('UPDATE agents SET archived_at=? WHERE id=?',(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),record_id))
        else:
            db.execute('UPDATE agents SET archived_at=NULL WHERE id=?',(record_id,))
        log_activity(db,'Archived' if archived else 'Restored','agent',record_id,
                     'Assignment removed from active coverage and exports.' if archived else 'Assignment returned to active coverage and exports.')

def import_pus(text):
    access.require_admin()
    rows=parse_import(text)
    with connect() as db:
        db.begin_write()
        # A single transaction: a duplicate or invalid row rolls back the whole batch.
        db.executemany('INSERT INTO pus(lga,ward,code,name,voters,source,source_date) VALUES(?,?,?,?,?,?,?)',rows)
        log_activity(db,'Imported','pu',None,f'{len(rows)} polling units imported.')
    return len(rows)

def save(kind, data):
    access.require_write()
    if kind=='agent' and data.get('consent')!='yes': raise ValueError('Agent consent is required.')
    def req(key,maximum=500): return required(data,key,maximum)
    editing=bool(data.get('id'))
    if editing and kind not in ('pu','agent'): raise ValueError('Unsupported edit.')
    with connect() as db:
        db.begin_write()
        before=None
        if editing:
            table_name={'pu':'pus','agent':'agents'}[kind]
            before=db.execute(f'SELECT * FROM {table_name} WHERE id=?',(int(req('id')),)).fetchone()
            if not before: raise ValueError('Record no longer exists.')
            if kind=='agent' and before['archived_at']: raise ValueError('Restore the archived assignment before editing it.')
        if kind=='pu':
            if not access.is_admin():
                if not before: raise PermissionError('Only administrators may add directory records.')
                access.require_scope(before)
                if any(req(k)!=str(before[k]) for k in ('lga','ward','code','name')):
                    raise PermissionError('Coordinators may update voter totals and their sources, but cannot change the directory.')
        if kind in ('agent','report'):
            destination=db.execute('SELECT lga,ward FROM pus WHERE id=?',(int(req('pu_id')),)).fetchone()
            if destination is None: raise sqlite3.IntegrityError('Polling unit does not exist.')
            access.require_scope(destination)
            if before:
                origin=db.execute('SELECT lga,ward FROM pus WHERE id=?',(before['pu_id'],)).fetchone()
                access.require_scope(origin)
        if kind in ('agent','report') and ('location_lga' in data or 'location_ward' in data):
            location=db.execute('SELECT lga,ward FROM pus WHERE id=?',(int(req('pu_id')),)).fetchone()
            if not location or location['lga']!=req('location_lga') or location['ward']!=req('location_ward'):
                raise ValueError('Polling unit does not belong to the selected LGA and ward.')
        if kind=='pu':
            values=pu_values(data)
            if editing:
                cursor=db.execute('UPDATE pus SET lga=?,ward=?,code=?,name=?,voters=?,source=?,source_date=? WHERE id=?',values+(int(req('id')),))
            else:
                cursor=db.execute('INSERT INTO pus(lga,ward,code,name,voters,source,source_date) VALUES(?,?,?,?,?,?,?) RETURNING id',values)
        elif kind=='agent':
            phone=req('phone',20)
            if not phone.replace('+','',1).isascii() or not phone.replace('+','',1).isdigit() or not 10<=len(phone)<=15: raise ValueError('Enter a valid phone number.')
            if data.get('consent')!='yes': raise ValueError('Agent consent is required.')
            values=(int(req('pu_id')),req('name'),phone,req('role'),req('status'))
            if editing:
                cursor=db.execute('UPDATE agents SET pu_id=?,name=?,phone=?,role=?,status=?,consent=1 WHERE id=?',values+(int(req('id')),))
            else:
                cursor=db.execute('INSERT INTO agents(pu_id,name,phone,role,status,consent) VALUES(?,?,?,?,?,1) RETURNING id',values)
        elif kind=='report':
            cursor=db.execute('INSERT INTO reports(pu_id,category,detail) VALUES(?,?,?) RETURNING id',(int(req('pu_id')),req('category'),req('detail',2000)))
        else: raise ValueError('Unknown action.')
        if editing and cursor.rowcount!=1: raise ValueError('Record no longer exists.')
        record_id=int(req('id')) if editing else cursor.fetchone()[0]
        if editing:
            columns=PU_COLUMNS if kind=='pu' else ('pu_id','name','phone','role','status')
            after=db.execute(f'SELECT * FROM {table_name} WHERE id=?',(record_id,)).fetchone()
            changed=[key for key in columns if before[key]!=after[key]]
            if changed: log_activity(db,'Updated',kind,record_id,'Changed fields: '+', '.join(changed))
        else: log_activity(db,'Created',kind,record_id,'New record saved.')

def esc(value): return html.escape(str(value), quote=True)
def select(name, values, selected=None):
    return f'<select name="{name}" required>'+''.join(f'<option value="{esc(v)}"{(" selected" if str(v)==str(selected) else "")}>{esc(label)}</option>' for v,label in values)+'</select>'
def field(name,label,typ='text',value=''):
    return f'<label>{label}<input name="{name}" type="{typ}" value="{esc(value)}" required maxlength="500"'+(' min="0" step="1"' if typ=='number' else '')+'></label>'
def form(kind,content):
    if not access.can_write() or (kind in ('import-preview','import-confirm','user') and not access.is_admin()): return ''
    button={'import-preview':'Preview import','import-confirm':'Confirm import','pu':'Save polling unit','agent':'Save agent','report':'Save report','archive-agent':'Archive assignment','restore-agent':'Restore assignment','user':'Save account'}[kind]
    return f'<form method="post" action="/{kind}"><input type="hidden" name="csrf" value="{TOKEN}">{content}<button>{button}</button></form>'
def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join(f'<th>{esc(h)}</th>' for h in headers)+'</tr></thead><tbody>'+(''.join('<tr>'+''.join(f'<td>{esc(v)}</td>' for v in row)+'</tr>' for row in rows) or f'<tr><td colspan="{len(headers)}">No records yet.</td></tr>')+'</tbody></table></div>'
def location_picker(pus,selected=None):
    current=next((p for p in pus if str(p['id'])==str(selected)),None)
    lga=current['lga'] if current else ''
    ward=current['ward'] if current else ''
    lgas=[('', 'Select an LGA')]+[(v,v) for v in sorted({p['lga'] for p in pus})]
    wards=[('', 'Choose LGA first')]+[(v,v) for v in sorted({p['ward'] for p in pus if p['lga']==lga})]
    units=[('', 'Choose ward first')]+[(p['id'],p['code']+' — '+p['name']) for p in pus if p['lga']==lga and p['ward']==ward]
    return '<div class="location-picker"><label>Local Government Area'+select('location_lga',lgas,lga)+'</label><label>Ward'+select('location_ward',wards,ward)+'</label><label>Polling unit'+select('pu_id',units,selected)+'</label><p class="directory-status" role="status">Choose LGA, then ward, then polling unit.</p></div>'

def edit_forms(pus,agents,options,all_pus):
    if not access.can_write(): return '<p>This account has viewing access.</p>'
    result='<h3>Edit polling units</h3>'
    for pu in (pus if access.is_admin() else []):
        fields='<input type="hidden" name="id" value="'+str(pu['id'])+'">'
        fields+='<label>LGA'+select('lga',[(x,x) for x in ('Apa','Agatu')],pu['lga'])+'</label>'
        for key,label,typ in [('ward','Ward','text'),('code','Official PU code','text'),('name','PU name','text'),('voters','Registered voters','number'),('source','Figure source','text'),('source_date','Source date','date')]:
            fields+=field(key,label,typ,'' if pu[key] is None else pu[key])
        result+='<details><summary>'+esc(pu['code'])+' · '+esc(pu['name'])+'</summary>'+form('pu',fields)+'</details>'
    result+='<h3>Edit agent assignments</h3>'
    for agent in agents:
        fields='<input type="hidden" name="id" value="'+str(agent['id'])+'">'
        fields+=location_picker(all_pus if all_pus is not None else pus,agent['pu_id'])
        fields+=field('name','Agent name',value=agent['name'])+field('phone','Phone','tel',agent['phone'])
        for key,label,values in [('role','Assignment',('Primary','Backup')),('status','Status',('Pending','Confirmed','Trained'))]:
            fields+='<label>'+label+select(key,[(x,x) for x in values],agent[key])+'</label>'
        fields+='<label><input type="checkbox" name="consent" value="yes" required checked> Agent consent remains valid for these details</label>'
        result+='<details><summary>'+esc(agent['name'])+' · '+esc(agent['code'])+'</summary>'+form('agent',fields)+form('archive-agent','<input type="hidden" name="id" value="'+str(agent['id'])+'"><label><input type="checkbox" name="confirmed" value="yes" required> Remove this assignment from active coverage and exports</label>')+'</details>'
    return result

def import_form():
    return '<p>Paste CSV with this exact header. PU codes must be unique. Existing records are never overwritten.</p><code>'+','.join(PU_COLUMNS)+'</code>'+form('import-preview','<label>CSV data<textarea name="csv" required maxlength="1000000" placeholder="Paste the header and your sourced PU rows here"></textarea></label>')

def import_preview(text):
    access.require_admin()
    rows=parse_import(text)
    with connect() as db:
        existing={r[0] for r in db.execute('SELECT code FROM pus')}
    conflicts=existing.intersection(r[2] for r in rows)
    if conflicts: raise ValueError('PU codes already exist: '+', '.join(sorted(conflicts)[:10]))
    content='<h2>Review PU import</h2><p>'+str(len(rows))+' PUs; '+str(sum(r[4] for r in rows))+' registered voters. Validation checks formatting, not the accuracy of your source.</p>'
    content+=table(PU_COLUMNS,rows[:100])
    if len(rows)>100: content+='<p>Showing the first 100 rows. All rows have been validated.</p>'
    content+=form('import-confirm','<input type="hidden" name="csv" value="'+esc(text)+'"><label><input type="checkbox" name="confirmed" value="yes" required> I reviewed the figures and their sources</label>')
    return page(extra=content)

def filtered_records(filters=None):
    filters=filters or {}
    with connect() as db:
        all_pus=[dict(r) for r in db.execute('SELECT p.*,b.registered_voters AS voters_2023,b.source_url AS baseline_source_url,b.retrieved_on AS baseline_retrieved_on FROM pus p LEFT JOIN voter_baselines b ON b.pu_code=p.code AND b.year=2023 ORDER BY p.lga,p.ward,p.code')]
        all_agents=[dict(r) for r in db.execute('SELECT a.*,p.code,p.lga,p.ward,p.name AS pu_name FROM agents a JOIN pus p ON p.id=a.pu_id WHERE a.archived_at IS NULL ORDER BY a.id DESC')]
        all_reports=[dict(r) for r in db.execute('SELECT r.*,p.code,p.lga,p.ward,p.name AS pu_name FROM reports r JOIN pus p ON p.id=r.pu_id ORDER BY r.id DESC')]
    all_pus=[p for p in all_pus if access.in_scope(p)]
    allowed_ids={p['id'] for p in all_pus}
    all_agents=[a for a in all_agents if a['pu_id'] in allowed_ids]
    all_reports=[r for r in all_reports if r['pu_id'] in allowed_ids]
    lga=filters.get('lga','').strip(); ward=filters.get('ward','').strip(); query=filters.get('q','').strip().casefold()
    coverage=filters.get('coverage','')
    if len(query)>120 or len(ward)>500 or lga not in ('','Apa','Agatu') or coverage not in ('','covered','uncovered'):
        raise ValueError('Invalid search filters.')
    covered={a['pu_id'] for a in all_agents if a['status'] in ('Confirmed','Trained')}
    matching_agents={a['pu_id'] for a in all_agents if query and any(query in str(a[k]).casefold() for k in ('name','phone'))}
    pus=[p for p in all_pus if (not lga or p['lga']==lga) and (not ward or p['ward']==ward)
         and (not query or p['id'] in matching_agents or any(query in str(p[k]).casefold() for k in ('name','code')))
         and (not coverage or (p['id'] in covered)==(coverage=='covered'))]
    ids={p['id'] for p in pus}
    return all_pus,pus,[a for a in all_agents if a['pu_id'] in ids],[r for r in all_reports if r['pu_id'] in ids],len(ids & covered)

def filter_panel(all_pus,filters):
    lga=filters.get('lga',''); ward=filters.get('ward','')
    content='<section id="filters"><h2>Find records</h2><form method="get" action="/">'
    content+='<label>LGA'+select('lga',[('','All LGAs')]+[(v,v) for v in sorted({p['lga'] for p in all_pus})],lga).replace(' required','')+'</label>'
    wards=sorted({p['ward'] for p in all_pus if not lga or p['lga']==lga})
    content+='<label>Ward'+select('ward',[('','All wards')]+[(v,v) for v in wards],ward).replace(' required','')+'</label>'
    content+='<label>Search PU or agent<input name="q" value="'+esc(filters.get('q',''))+'" maxlength="120" placeholder="PU name, code, agent name or phone"></label>'
    content+='<label>Agent coverage'+select('coverage',[('','All PUs'),('covered','With confirmed agent'),('uncovered','Without confirmed agent')],filters.get('coverage','')).replace(' required','')+'</label><button>Apply filters</button><a href="/">Clear filters</a></form>'
    content+='<p>Search finds matching PUs and shows all their assignments and reports. Confirmed coverage includes Confirmed and Trained agents. Counts below follow the active filters.</p><p>'
    query=urlencode({k:v for k,v in filters.items() if v})
    for kind,label in [('pus','Polling units'),('agents','Agents'),('reports','Field reports')]:
        content+='<a href="/export/'+kind+('.csv?'+esc(query) if query else '.csv')+'">Download '+label+' CSV</a> &nbsp; '
    return content+'</p></section>'

def export_csv(kind,filters=None):
    _,pus,agents,reports,_=filtered_records(filters)
    if kind=='pus':
        headers=['lga','ward','code','name','voters_2023','baseline_source_url','baseline_retrieved_on','voters','source','source_date']
        rows=[[p[k] for k in headers] for p in pus]
    elif kind=='agents':
        headers=['lga','ward','pu_code','pu_name','agent_name','phone','role','status']
        rows=[[a[k] for k in ('lga','ward','code','pu_name','name','phone','role','status')] for a in agents]
        for row in rows: row[5]="'"+row[5] # Preserve phone leading zeros in spreadsheet applications.
    elif kind=='reports':
        headers=['lga','ward','pu_code','pu_name','category','detail','created_at_utc']
        rows=[[r[k] for k in ('lga','ward','code','pu_name','category','detail','created_at')] for r in reports]
    else: raise ValueError('Unknown export.')
    def safe(value):
        if value is None: return ''
        value=str(value)
        if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')): return "'"+value
        return value
    stream=io.StringIO(newline=''); writer=csv.writer(stream)
    writer.writerow(headers);writer.writerows([[safe(v) for v in row] for row in rows])
    return '\ufeff'+stream.getvalue()

def history_panel():
    with connect() as db:
        archived=db.execute('SELECT a.id,a.pu_id,a.name,a.archived_at,p.code,p.lga,p.ward FROM agents a JOIN pus p ON p.id=a.pu_id WHERE a.archived_at IS NOT NULL ORDER BY a.archived_at DESC,a.id DESC').fetchall()
        archived=[a for a in archived if access.in_scope(dict(id=a['pu_id'],lga=a['lga'],ward=a['ward']))]
        history=db.execute('SELECT * FROM activity_log ORDER BY id DESC LIMIT 50').fetchall() if access.is_admin() else []
    content='<section id="archive"><h2>Archived assignments</h2><p>Archived agents are excluded from active lists, coverage and exports. This list is independent of dashboard filters. Restore only if the agent still consents to the assignment.</p>'
    if not archived: content+='<p>No archived assignments.</p>'
    for agent in archived:
        content+='<details><summary>'+esc(agent['name'])+' · '+esc(agent['code'])+' · '+esc(agent['archived_at'])+' UTC</summary>'
        content+=form('restore-agent','<input type="hidden" name="id" value="'+str(agent['id'])+'"><label><input type="checkbox" name="confirmed" value="yes" required> Confirm this agent still consents and should return to active assignments</label>')+'</details>'
    if not access.is_admin(): return content+'</section>'
    content+='</section><section id="history"><h2>Activity log</h2><p>Latest 50 recorded changes across the portal, newest first. Times are UTC. Logs record changed field names, without copying phone numbers or report text. Earlier changes made before this feature are not reconstructed.</p>'
    content+=table(['Time (UTC)','Account','Action','Record','Details'],[(r['created_at'],r['actor'],r['action'],r['entity']+(' #'+str(r['record_id']) if r['record_id'] else ''),r['summary']) for r in history])
    return content+'</section>'

def account_panel():
    if not access.is_admin(): return ''
    with connect() as db:
        accounts=db.execute('SELECT id,username,role,lga,wards,active,pu_id FROM users ORDER BY username').fetchall()
        directory=[dict(row) for row in db.execute('SELECT * FROM pus ORDER BY lga,ward,code')]
    def account_fields(account=None):
        account=dict(account) if account else dict(username='',role='viewer',lga='',wards='[]',active=1)
        fields='<input type="hidden" name="id" value="'+str(account.get('id',''))+'">'
        fields+=field('username','Username',value=account['username']).replace(' required maxlength',' readonly required maxlength') if account.get('id') else field('username','Username')
        fields+='<label>Role'+select('role',[(v,label) for v,label in [('admin','Administrator'),('viewer','Viewer'),('coordinator','Coordinator'),('agent','PU Agent')]],account['role'])+'</label>'
        fields+='<p>PU agents may submit results only for the assigned polling unit. Assignment is separate from the contact directory.</p>'+location_picker(directory,account.get('pu_id'))
        fields=fields.replace('<select name="pu_id" required>','<select name="pu_id">').replace('<select name="location_lga" required>','<select name="location_lga">').replace('<select name="location_ward" required>','<select name="location_ward">')
        fields+='<label>Coordinator LGA'+select('lga',[('','Not applicable'),('Apa','Apa'),('Agatu','Agatu')],account['lga']).replace(' required','')+'</label>'
        fields+='<label>Coordinator wards (one exact ward name per line; blank means entire LGA)<textarea name="wards" maxlength="10000">'+esc('\n'.join(json.loads(account['wards'])))+'</textarea></label>'
        fields+='<label>'+('New password (leave blank to keep current)' if account.get('id') else 'Password (12 characters minimum)')+'<input name="password" type="password" autocomplete="new-password" maxlength="1024"'+('' if account.get('id') else ' required')+'></label>'
        fields+='<label><input type="checkbox" name="active" value="yes"'+(' checked' if account['active'] else '')+'> Account enabled</label>'
        return fields
    content='<section id="accounts"><h2>Accounts and permissions</h2><p>PU agents can view their assigned polling unit and submit its results. Viewers can read and export the full portal. Coordinators can read, export and manage assignments/reports within one LGA or selected wards, and update sourced voter totals there. Administrators manage the full portal and accounts. Changing an account signs it out.</p><details><summary>Create an account</summary>'+form('user',account_fields())+'</details>'
    for account in accounts:
        content+='<details><summary>'+esc(account['username'])+' · '+esc(account['role'])+(' · disabled' if not account['active'] else '')+'</summary>'+form('user',account_fields(account))+'</details>'
    return content+'</section>'

def save_account(data):
    access.require_admin()
    with connect() as db:
        db.begin_write()
        user=access.current_user.get()
        record_id=auth.manage_account(db,data,user['id'] if user else None)
        log_activity(db,'Updated' if data.get('id') else 'Created','account',record_id,'Account credentials, role or area settings saved.')

def portal_header(title='Solution 2027',description='Polling units, agent assignments and field reports in one place.'):
    destination,label=('/', 'Back to dashboard') if title=='PU Results' else ('/results', 'Open PU Results')
    return '<header class="portal-hero"><div class="hero-copy"><span class="hero-eyebrow">APA · AGATU / COORDINATION PORTAL</span><h1>'+esc(title)+'</h1><p class="hero-name">Engr. Solomon Eigege Oloche’woche</p><p class="hero-description">'+esc(description)+'</p><a class="hero-action" href="'+destination+'">'+label+'</a></div><div class="hero-portrait"><img src="/assets/solomon.jpg" alt="Portrait of Solomon" width="702" height="1536"></div><span class="hero-watermark" aria-hidden="true">2027</span></header>'


def page(message='',extra='',filters=None):
    filters=filters or {}
    all_pus,pus,agents,reports,covered=filtered_records(filters)
    options=[(p['id'],f"{p['lga']} / {p['ward']} / {p['code']} — {p['name']}") for p in all_pus]
    puform=form('pu', '<label>LGA'+select('lga',[(x,x) for x in ('Apa','Agatu')])+'</label>'+''.join(field(*args) for args in [('ward','Ward'),('code','Official PU code'),('name','PU name'),('voters','Registered voters (total)','number'),('source','Figure source'),('source_date','Source date','date')]))
    if not access.is_admin(): puform=''
    manual_pu='<details><summary>Add a PU missing from the snapshot</summary>'+puform+'</details>' if access.is_admin() else ''
    agentform=form('agent',location_picker(all_pus)+field('name','Agent name')+field('phone','Phone','tel')+'<label>Assignment'+select('role',[(x,x) for x in ('Primary','Backup')])+'</label><label>Status'+select('status',[(x,x) for x in ('Pending','Confirmed','Trained')])+'</label><label><input type="checkbox" name="consent" value="yes" required> Agent agreed to storage of these details for this assignment</label>') if all_pus else '<p>Add a polling unit first.</p>'
    reportform=form('report',location_picker(all_pus)+'<label>Category'+select('category',[(x,x) for x in ('Logistics','Incident','Community concern')])+'</label><label>Report<textarea name="detail" required maxlength="2000"></textarea></label><p>Record operational facts. Do not include voter identities or political affiliations.</p>') if all_pus else '<p>Add a polling unit first.</p>'
    directoryform=form('pu',location_picker(all_pus)+''.join('<input type="hidden" name="'+key+'" value="">' for key in ('id','lga','ward','code','name'))+field('voters','Other sourced voter total','number')+field('source','Voter figure source')+field('source_date','Voter figure source date','date')) if all_pus else '<p>Add or import polling units first.</p>'
    baseline_count=sum(p['voters_2023'] is not None for p in pus)
    baseline_total=sum(p['voters_2023'] or 0 for p in pus)
    baseline_note='<p>2023 totals are a historical baseline from the CCIJ public dataset, whose methodology attributes the register figures to INEC’s early-2023 publication. They are not 2027 figures or a newly certified INEC register. Other entered totals are shown separately and are never added to the historical baseline.</p><p><a href="https://github.com/anndawn/Nigeria-2023-election/blob/e38e3bc9a22ac005564a9e95b4e846208a594522/README.md" target="_blank" rel="noopener noreferrer">Source and methodology</a></p>'
    user=access.current_user.get()
    identity=esc(user['username']+' · '+user['role']) if user else 'Local setup'
    admin_links='<a href="#import">Import PUs</a><a href="#history">Activity log</a><a href="#accounts">Accounts</a>' if access.is_admin() else ''
    import_section='<section id="import"><h2>Import polling units</h2>'+import_form()+'</section>' if access.is_admin() else ''
    environment_label='Hosted portal · Account access required' if os.environ.get('SOLUTION_ENV')=='production' else 'Local development · Use sample data until production deployment is ready.'
    body=f'''{portal_header()}
    <aside>Signed in: {identity}<br>{environment_label}</aside>
    <form method="post" action="/logout"><input type="hidden" name="csrf" value="{TOKEN}"><button>Sign out</button></form><nav><a href="/results">PU Results</a><a href="#pus">Polling units</a><a href="#agents">Agents</a><a href="#reports">Field reports</a><a href="#edit">Edit records</a><a href="#archive">Archived agents</a>{admin_links}</nav><p role="status">{esc(message)}</p>
    {filter_panel(all_pus,filters)}<div class="stats"><article><strong>{len(pus)}</strong>Polling units entered</article><article><strong>{sum(p['voters'] or 0 for p in pus):,}</strong>Other sourced voter totals<br><small>{sum(p["voters"] is None for p in pus)} PUs without totals</small></article><article><strong>{baseline_total:,}</strong>2023 registered voters<br><small>Historical baseline · {baseline_count}/{len(pus)} PUs matched</small></article><article><strong>{covered}/{len(pus)}</strong>PUs with confirmed agents</article><article><strong>{len(agents)}</strong>Agent assignments</article></div>
    {extra}<section id="pus"><h2>Polling units</h2><p>Select LGA → ward → polling unit to record a sourced voter total. Directory names come from your saved snapshot and may need updating. Blank totals mean not recorded.</p>{baseline_note}{directoryform}{manual_pu}{table(['LGA','Ward','Code','PU','2023 registered voters (historical)','Other entered total','Other total source','Other total source date'],[(p['lga'],p['ward'],p['code'],p['name'],('Not recorded' if p['voters_2023'] is None else p['voters_2023']),('Not recorded' if p['voters'] is None else p['voters']),p['source'],p['source_date']) for p in pus])}</section>
    <section id="agents"><h2>Agent assignments</h2>{agentform}{table(['PU','Agent','Phone','Role','Status'],[(a['code'],a['name'],a['phone'],a['role'],a['status']) for a in agents])}</section>
    <section id="reports"><h2>Field reports</h2>{reportform}{table(['PU','Category','Report','Recorded (UTC)'],[(r['code'],r['category'],r['detail'],r['created_at']) for r in reports])}</section><section id="edit"><h2>Edit records</h2>{edit_forms(pus,agents,options,all_pus)}</section>{import_section}{history_panel()}{account_panel()}'''
    hero,content=body.split('<aside>',1)
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Solution 2027</title><link rel="stylesheet" href="/portal.css"></head><body class="portal-body">'+hero+'<main><aside>'+content+'<footer class="portal-footer">SOLUTION 2027 <span>APA · AGATU / COORDINATION WORKSPACE</span></footer><script src="/portal.js" defer></script></main></body></html>'

class Handler(BaseHTTPRequestHandler):
    def respond(self,status,document,cookie=None,content_type='text/html; charset=utf-8',filename=None):
        payload=document if isinstance(document,bytes) else document.encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        if cookie is not None: self.send_header('Set-Cookie',auth.cookie(cookie))
        if filename: self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers(); self.wfile.write(payload)
    def redirect(self,path,cookie=None):
        self.send_response(303)
        self.send_header('Location',path)
        self.send_header('Cache-Control','no-store')
        if cookie is not None: self.send_header('Set-Cookie',auth.cookie(cookie))
        self.end_headers()
    def run_with_user(self,handler):
        token,session=auth.get_session(self.headers.get('Cookie'))
        with connect() as db: user=auth.session_user(db,session)
        if session and session['authenticated'] and not user:
            auth.revoke(token)
            self.redirect('/login',''); return
        context=access.current_user.set(user)
        try: handler()
        except PermissionError as error:
            self.respond(403,'<!doctype html><title>Access denied</title><h1>Access denied</h1><p>'+esc(error)+'</p><a href="/">Return to portal</a>')
        finally: access.current_user.reset(context)
    def do_GET(self):
        public={'/portal.css':('portal.css','text/css; charset=utf-8'),'/assets/solomon.jpg':('assets/solomon.jpg','image/jpeg')}
        asset=public.get(urlsplit(self.path).path)
        if asset:
            self.respond(200,(Path(__file__).parent/asset[0]).read_bytes(),content_type=asset[1]);return
        self.run_with_user(self.handle_get)
    def do_POST(self): self.run_with_user(self.handle_post)
    def handle_get(self):
        url=urlsplit(self.path); path=url.path
        exports={f'/export/{kind}.csv':kind for kind in ('pus','agents','reports')}
        if path not in ('/','/login','/portal.js','/api/locations','/results','/results/photo') and path not in exports: self.send_error(404); return
        token,session=auth.get_session(self.headers.get('Cookie'))
        if session and session['authenticated']:
            if path=='/login': self.redirect('/'); return
            if path in ('/results','/results/photo'):
                try:
                    params=parse_qs(url.query,max_num_fields=2)
                    result_id=params.get('id',[None])[0]
                    with connect() as db:
                        if path=='/results/photo':
                            import base64
                            row=results.get(db,int(result_id or '0'))
                            self.respond(200,base64.b64decode(row['photo']),content_type=row['photo_type']);return
                        document=results.page(db,session['csrf'],result_id)
                    self.respond(200,document);return
                except ValueError as error:
                    self.respond(400,esc(error));return
            if path=='/api/locations':
                with connect() as db:
                    rows=[dict(r) for r in db.execute('SELECT p.id,p.lga,p.ward,p.code,p.name,p.voters,p.source,p.source_date,b.registered_voters AS voters_2023 FROM pus p LEFT JOIN voter_baselines b ON b.pu_code=p.code AND b.year=2023 ORDER BY p.code') if access.in_scope(r)]
                self.respond(200,json.dumps(rows),content_type='application/json'); return
            if path=='/portal.js':
                self.respond(200,(Path(__file__).parent/'portal.js').read_text(),content_type='text/javascript'); return
            try:
                params=parse_qs(url.query,max_num_fields=10)
                filters={k:params.get(k,[''])[0] for k in ('lga','ward','q','coverage')}
                if path in exports:
                    kind=exports[path]
                    self.respond(200,export_csv(kind,filters),content_type='text/csv; charset=utf-8',filename='solution2027-'+kind+'.csv'); return
                self.respond(200,page(filters=filters).replace(TOKEN,session['csrf'])); return
            except ValueError:
                self.respond(400,page('Invalid search filters.').replace(TOKEN,session['csrf'])); return
        if path!='/login': self.redirect('/login'); return
        cookie=None
        if not session: cookie,session=auth.new_session()
        self.respond(200,auth.login_page(session['csrf']),cookie)
    def handle_result_post(self):
        _,session=auth.get_session(self.headers.get('Cookie'))
        if not session or not session['authenticated']:
            self.redirect('/login');return
        data={}
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=results.MAX_BODY:
                self.send_error(413);return
            data,photo=results.parse_request(self.headers.get('Content-Type',''),self.rfile.read(size))
            if not secrets.compare_digest(data.get('csrf','').encode(),session['csrf'].encode()):
                self.send_error(403);return
            with connect() as db:
                if self.path=='/results/review':
                    results.review(db,data);record_id=int(data['result_id'])
                else:record_id=results.save(db,data,photo)
            self.redirect('/results?id='+str(record_id))
        except (ValueError,sqlite3.IntegrityError,UnicodeError) as error:
            # Do not echo credentials or upload bodies into an error response.
            self.respond(400,'<!doctype html><title>Result not saved</title><h1>Result not saved</h1><p>'+esc(error)+'</p><p>Use your browser’s Back button to retain the form and correct it.</p><a href="/results">PU Results</a>')

    def handle_post(self):
        if self.path in ('/results','/results/review'):
            self.handle_result_post();return
        if self.path not in ('/login','/logout','/pu','/agent','/report','/import-preview','/import-confirm','/archive-agent','/restore-agent','/user'): self.send_error(404); return
        token,session=auth.get_session(self.headers.get('Cookie'))
        if not session or (self.path!='/login' and not session['authenticated']):
            self.redirect('/login'); return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=(3100000 if self.path.startswith('/import-') else 16384): self.send_error(413); return
            data={k:v[0] for k,v in parse_qs(self.rfile.read(size).decode()).items()}
            if not secrets.compare_digest(data.get('csrf','').encode(),session['csrf'].encode()): self.send_error(403); return
            if self.path=='/login':
                if not auth.allow_attempt(self.client_address[0],data.get('username','')):
                    self.respond(429,auth.login_page(session['csrf'],'Too many attempts. Try again in five minutes.')); return
                with connect() as db: valid=auth.authenticate(db,data.get('username',''),data.get('password',''))
                if not valid:
                    self.respond(401,auth.login_page(session['csrf'],'Invalid username or password.')); return
                auth.revoke(token)
                new_token,_=auth.new_session(True,valid)
                self.redirect('/',new_token); return
            if self.path=='/logout':
                auth.revoke(token); self.redirect('/login',''); return
            if self.path=='/user':
                save_account(data)
                self.redirect('/#accounts'); return
            if self.path=='/import-preview':
                self.respond(200,import_preview(data.get('csv','')).replace(TOKEN,session['csrf'])); return
            if self.path=='/import-confirm':
                if data.get('confirmed')!='yes': raise ValueError('Confirm the import first.')
                count=import_pus(data.get('csv',''))
                self.respond(200,page(f'Imported {count} polling units.').replace(TOKEN,session['csrf'])); return
            if self.path in ('/archive-agent','/restore-agent'):
                set_agent_archived(data,self.path=='/archive-agent')
                self.redirect('/#archive'); return
            save(self.path[1:],data)
        except (ValueError,sqlite3.IntegrityError,csv.Error) as error:
            self.respond(400,page('Could not save: '+str(error)).replace(TOKEN,session['csrf'])); return
        self.redirect('/')

if __name__=='__main__':
    import sys
    init_db(seed_directory=True)
    if '--setup-admin' in sys.argv:
        from getpass import getpass
        try:
            username=input('Administrator username: ')
            password=getpass('Password (at least 12 characters): ')
            if password!=getpass('Confirm password: '): raise ValueError('Passwords do not match.')
            with connect() as db: auth.configure(db,username,password)
        except ValueError as error: raise SystemExit(str(error))
        print('Administrator saved. Restart the server if it is running.')
        raise SystemExit(0)
    with connect() as db:
        if not db.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone():
            raise SystemExit('First run: python3 app.py --setup-admin')
    print('Development portal: http://127.0.0.1:8000 (Ctrl+C to stop)',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
