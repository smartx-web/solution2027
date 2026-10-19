"""Local development portal. Run with python3 app.py."""
import html
import csv
import io
import json
from pathlib import Path
import auth
import os
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, urlencode

DB = os.environ.get('SOLUTION_DB', 'solution2027.sqlite3')
TOKEN = secrets.token_urlsafe(32)

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def init_db(seed_directory=False):
    with connect() as db:
        auth.init(db)
        db.executescript('''
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

    # Existing databases used NOT NULL, which cannot represent an unknown total.
    with connect() as db:
        columns=db.execute('PRAGMA table_info(pus)').fetchall()
        if any(c['name']=='voters' and c['notnull'] for c in columns):
            db.execute('PRAGMA foreign_keys=OFF')
            try:
                db.execute('BEGIN IMMEDIATE')
                db.execute("CREATE TABLE pus_new(id INTEGER PRIMARY KEY,lga TEXT NOT NULL CHECK(lga IN ('Apa','Agatu')),ward TEXT NOT NULL,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,voters INTEGER CHECK(voters>=0),source TEXT NOT NULL,source_date TEXT NOT NULL)")
                db.execute('INSERT INTO pus_new SELECT * FROM pus')
                db.execute('DROP TABLE pus')
                db.execute('ALTER TABLE pus_new RENAME TO pus')
                if db.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('Database links failed validation.')
                db.commit()
            except Exception:
                db.rollback(); raise
            finally: db.execute('PRAGMA foreign_keys=ON')
        if seed_directory:
            rows=json.loads((Path(__file__).parent/'data'/'apa_agatu_directory.json').read_text())
            db.executemany('INSERT OR IGNORE INTO pus(lga,ward,code,name,voters,source,source_date) VALUES(?,?,?,?,NULL,?,?)',
                           [(r['lga'],r['ward'],r['code'],r['name'],'User-supplied Nigeria polling-unit directory snapshot','') for r in rows])


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

def import_pus(text):
    rows=parse_import(text)
    with connect() as db:
        # A single transaction: a duplicate or invalid row rolls back the whole batch.
        db.executemany('INSERT INTO pus(lga,ward,code,name,voters,source,source_date) VALUES(?,?,?,?,?,?,?)',rows)
    return len(rows)

def save(kind, data):
    def req(key,maximum=500): return required(data,key,maximum)
    editing=bool(data.get('id'))
    if editing and kind not in ('pu','agent'): raise ValueError('Unsupported edit.')
    with connect() as db:
        if kind in ('agent','report') and ('location_lga' in data or 'location_ward' in data):
            location=db.execute('SELECT lga,ward FROM pus WHERE id=?',(int(req('pu_id')),)).fetchone()
            if not location or location['lga']!=req('location_lga') or location['ward']!=req('location_ward'):
                raise ValueError('Polling unit does not belong to the selected LGA and ward.')
        if kind=='pu':
            values=pu_values(data)
            if editing:
                cursor=db.execute('UPDATE pus SET lga=?,ward=?,code=?,name=?,voters=?,source=?,source_date=? WHERE id=?',values+(int(req('id')),))
            else:
                cursor=db.execute('INSERT INTO pus(lga,ward,code,name,voters,source,source_date) VALUES(?,?,?,?,?,?,?)',values)
        elif kind=='agent':
            phone=req('phone',20)
            if not phone.replace('+','',1).isascii() or not phone.replace('+','',1).isdigit() or not 10<=len(phone)<=15: raise ValueError('Enter a valid phone number.')
            if data.get('consent')!='yes': raise ValueError('Agent consent is required.')
            values=(int(req('pu_id')),req('name'),phone,req('role'),req('status'))
            if editing:
                cursor=db.execute('UPDATE agents SET pu_id=?,name=?,phone=?,role=?,status=?,consent=1 WHERE id=?',values+(int(req('id')),))
            else:
                cursor=db.execute('INSERT INTO agents(pu_id,name,phone,role,status,consent) VALUES(?,?,?,?,?,1)',values)
        elif kind=='report':
            cursor=db.execute('INSERT INTO reports(pu_id,category,detail) VALUES(?,?,?)',(int(req('pu_id')),req('category'),req('detail',2000)))
        else: raise ValueError('Unknown action.')
        if editing and cursor.rowcount!=1: raise ValueError('Record no longer exists.')

def esc(value): return html.escape(str(value), quote=True)
def select(name, values, selected=None):
    return f'<select name="{name}" required>'+''.join(f'<option value="{esc(v)}"{(" selected" if str(v)==str(selected) else "")}>{esc(label)}</option>' for v,label in values)+'</select>'
def field(name,label,typ='text',value=''):
    return f'<label>{label}<input name="{name}" type="{typ}" value="{esc(value)}" required maxlength="500"'+(' min="0" step="1"' if typ=='number' else '')+'></label>'
def form(kind,content):
    button={'import-preview':'Preview import','import-confirm':'Confirm import','pu':'Save polling unit','agent':'Save agent','report':'Save report'}[kind]
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
    result='<h3>Edit polling units</h3>'
    for pu in pus:
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
        result+='<details><summary>'+esc(agent['name'])+' · '+esc(agent['code'])+'</summary>'+form('agent',fields)+'</details>'
    return result

def import_form():
    return '<p>Paste CSV with this exact header. PU codes must be unique. Existing records are never overwritten.</p><code>'+','.join(PU_COLUMNS)+'</code>'+form('import-preview','<label>CSV data<textarea name="csv" required maxlength="1000000" placeholder="Paste the header and your sourced PU rows here"></textarea></label>')

def import_preview(text):
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
        all_pus=[dict(r) for r in db.execute('SELECT * FROM pus ORDER BY lga,ward,code')]
        all_agents=[dict(r) for r in db.execute('SELECT a.*,p.code,p.lga,p.ward,p.name AS pu_name FROM agents a JOIN pus p ON p.id=a.pu_id ORDER BY a.id DESC')]
        all_reports=[dict(r) for r in db.execute('SELECT r.*,p.code,p.lga,p.ward,p.name AS pu_name FROM reports r JOIN pus p ON p.id=r.pu_id ORDER BY r.id DESC')]
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
    content+='<label>LGA'+select('lga',[('','All LGAs')]+[(v,v) for v in ('Apa','Agatu')],lga).replace(' required','')+'</label>'
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
        headers=['lga','ward','code','name','voters','source','source_date']
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

def page(message='',extra='',filters=None):
    filters=filters or {}
    all_pus,pus,agents,reports,covered=filtered_records(filters)
    options=[(p['id'],f"{p['lga']} / {p['ward']} / {p['code']} — {p['name']}") for p in all_pus]
    puform=form('pu', '<label>LGA'+select('lga',[(x,x) for x in ('Apa','Agatu')])+'</label>'+''.join(field(*args) for args in [('ward','Ward'),('code','Official PU code'),('name','PU name'),('voters','Registered voters (total)','number'),('source','Figure source'),('source_date','Source date','date')]))
    agentform=form('agent',location_picker(all_pus)+field('name','Agent name')+field('phone','Phone','tel')+'<label>Assignment'+select('role',[(x,x) for x in ('Primary','Backup')])+'</label><label>Status'+select('status',[(x,x) for x in ('Pending','Confirmed','Trained')])+'</label><label><input type="checkbox" name="consent" value="yes" required> Agent agreed to storage of these details for this assignment</label>') if all_pus else '<p>Add a polling unit first.</p>'
    reportform=form('report',location_picker(all_pus)+'<label>Category'+select('category',[(x,x) for x in ('Logistics','Incident','Community concern')])+'</label><label>Report<textarea name="detail" required maxlength="2000"></textarea></label><p>Record operational facts. Do not include voter identities or political affiliations.</p>') if all_pus else '<p>Add a polling unit first.</p>'
    directoryform=form('pu',location_picker(all_pus)+''.join('<input type="hidden" name="'+key+'" value="">' for key in ('id','lga','ward','code','name'))+field('voters','Registered voter total','number')+field('source','Voter figure source')+field('source_date','Voter figure source date','date')) if all_pus else '<p>Add or import polling units first.</p>'
    body=f'''<header><small>APA · AGATU</small><h1>Solution 2027</h1><p>Engr. Solomon Eigege Oloche’woche · Agent coordination portal</p></header>
    <aside>Local development · Administrator access · Use sample data until production deployment is ready.</aside>
    <form method="post" action="/logout"><input type="hidden" name="csrf" value="{TOKEN}"><button>Sign out</button></form><nav><a href="#pus">Polling units</a><a href="#agents">Agents</a><a href="#reports">Field reports</a><a href="#edit">Edit records</a><a href="#import">Import PUs</a></nav><p role="status">{esc(message)}</p>
    {filter_panel(all_pus,filters)}<div class="stats"><article><strong>{len(pus)}</strong>Polling units entered</article><article><strong>{sum(p['voters'] or 0 for p in pus):,}</strong>Registered voters recorded<br><small>{sum(p["voters"] is None for p in pus)} PUs without totals</small></article><article><strong>{covered}/{len(pus)}</strong>PUs with confirmed agents</article><article><strong>{len(agents)}</strong>Agent assignments</article></div>
    {extra}<section id="pus"><h2>Polling units</h2><p>Select LGA → ward → polling unit to record a sourced voter total. Directory names come from your saved snapshot and may need updating. Blank totals mean not recorded.</p>{directoryform}<details><summary>Add a PU missing from the snapshot</summary>{puform}</details>{table(['LGA','Ward','Code','PU','Voters','Source','Date'],[(p['lga'],p['ward'],p['code'],p['name'],('Not recorded' if p['voters'] is None else p['voters']),p['source'],p['source_date']) for p in pus])}</section>
    <section id="agents"><h2>Agent assignments</h2>{agentform}{table(['PU','Agent','Phone','Role','Status'],[(a['code'],a['name'],a['phone'],a['role'],a['status']) for a in agents])}</section>
    <section id="reports"><h2>Field reports</h2>{reportform}{table(['PU','Category','Report','Recorded (UTC)'],[(r['code'],r['category'],r['detail'],r['created_at']) for r in reports])}</section><section id="edit"><h2>Edit records</h2>{edit_forms(pus,agents,options,all_pus)}</section><section id="import"><h2>Import polling units</h2>{import_form()}</section>'''
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Solution 2027</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#182b35;font:16px system-ui}header{background:#0c3430;color:white;padding:40px max(5%,20px)}h1{font-size:40px;margin:8px 0}main{max-width:1200px;margin:auto;padding:20px}aside{background:#fff0c6;padding:16px;border-radius:10px}nav{display:flex;flex-wrap:wrap;gap:24px;padding:24px 0}a{color:#086052}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:15px}article,section{background:white;border:1px solid #dce4e6;border-radius:12px;padding:24px}strong{display:block;font-size:30px;color:#096553}section{margin-top:24px}form{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px}label{display:block;font-weight:600}input:not([type=checkbox]),select,textarea,button{width:100%;padding:12px;border:1px solid #bdcbce;border-radius:6px;margin-top:7px;font:inherit}button{background:#096553;color:white;cursor:pointer;align-self:end}details{padding:12px 0;border-bottom:1px solid #dce4e6}summary{cursor:pointer;font-weight:600;margin-bottom:12px}textarea{min-height:140px}th,td{text-align:left;padding:12px;border-bottom:1px solid #e2e8eb;vertical-align:top;overflow-wrap:anywhere}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}form p{font-size:14px}.location-picker{grid-column:1/-1;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.directory-status{grid-column:1/-1}input:focus,select:focus,textarea:focus{outline:3px solid #e7bd55}
    </style></head><body>'''+body.split('<aside>')[0]+'<main><aside>'+body.split('<aside>',1)[1]+'<script src="/portal.js" defer></script></main></body></html>'

class Handler(BaseHTTPRequestHandler):
    def respond(self,status,document,cookie=None,content_type='text/html; charset=utf-8',filename=None):
        payload=document.encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        if cookie is not None: self.send_header('Set-Cookie',auth.cookie(cookie))
        if filename: self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers(); self.wfile.write(payload)
    def redirect(self,path,cookie=None):
        self.send_response(303)
        self.send_header('Location',path)
        self.send_header('Cache-Control','no-store')
        if cookie is not None: self.send_header('Set-Cookie',auth.cookie(cookie))
        self.end_headers()
    def do_GET(self):
        url=urlsplit(self.path); path=url.path
        exports={f'/export/{kind}.csv':kind for kind in ('pus','agents','reports')}
        if path not in ('/','/login','/portal.js','/api/locations') and path not in exports: self.send_error(404); return
        token,session=auth.get_session(self.headers.get('Cookie'))
        if session and session['authenticated']:
            if path=='/login': self.redirect('/'); return
            if path=='/api/locations':
                with connect() as db:
                    rows=[dict(r) for r in db.execute('SELECT id,lga,ward,code,name,voters,source,source_date FROM pus ORDER BY code')]
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
    def do_POST(self):
        if self.path not in ('/login','/logout','/pu','/agent','/report','/import-preview','/import-confirm'): self.send_error(404); return
        token,session=auth.get_session(self.headers.get('Cookie'))
        if not session or (self.path!='/login' and not session['authenticated']):
            self.redirect('/login'); return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=(3100000 if self.path.startswith('/import-') else 16384): self.send_error(413); return
            data={k:v[0] for k,v in parse_qs(self.rfile.read(size).decode()).items()}
            if not secrets.compare_digest(data.get('csrf','').encode(),session['csrf'].encode()): self.send_error(403); return
            if self.path=='/login':
                if not auth.allow_attempt(self.client_address[0]):
                    self.respond(429,auth.login_page(session['csrf'],'Too many attempts. Try again in five minutes.')); return
                with connect() as db: valid=auth.verify(db,data.get('username',''),data.get('password',''))
                if not valid:
                    self.respond(401,auth.login_page(session['csrf'],'Invalid username or password.')); return
                auth.revoke(token)
                new_token,_=auth.new_session(True)
                self.redirect('/',new_token); return
            if self.path=='/logout':
                auth.revoke(token); self.redirect('/login',''); return
            if self.path=='/import-preview':
                self.respond(200,import_preview(data.get('csv','')).replace(TOKEN,session['csrf'])); return
            if self.path=='/import-confirm':
                if data.get('confirmed')!='yes': raise ValueError('Confirm the import first.')
                count=import_pus(data.get('csv',''))
                self.respond(200,page(f'Imported {count} polling units.').replace(TOKEN,session['csrf'])); return
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
        if not db.execute('SELECT 1 FROM administrator').fetchone():
            raise SystemExit('First run: python3 app.py --setup-admin')
    print('Development portal: http://127.0.0.1:8000 (Ctrl+C to stop)',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
