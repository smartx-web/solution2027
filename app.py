"""Local development portal. Run with python3 app.py."""
import html
import auth
import os
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

DB = os.environ.get('SOLUTION_DB', 'solution2027.sqlite3')
TOKEN = secrets.token_urlsafe(32)

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def init_db():
    with connect() as db:
        auth.init(db)
        db.executescript('''
        CREATE TABLE IF NOT EXISTS pus (
          id INTEGER PRIMARY KEY, lga TEXT NOT NULL CHECK(lga IN ('Apa','Agatu')),
          ward TEXT NOT NULL, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          voters INTEGER NOT NULL CHECK(voters>=0), source TEXT NOT NULL, source_date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agents (
          id INTEGER PRIMARY KEY, pu_id INTEGER NOT NULL REFERENCES pus(id),
          name TEXT NOT NULL, phone TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('Primary','Backup')),
          status TEXT NOT NULL CHECK(status IN ('Pending','Confirmed','Trained')), consent INTEGER NOT NULL CHECK(consent=1));
        CREATE TABLE IF NOT EXISTS reports (
          id INTEGER PRIMARY KEY, pu_id INTEGER NOT NULL REFERENCES pus(id),
          category TEXT NOT NULL CHECK(category IN ('Logistics','Incident','Community concern')),
          detail TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        ''')

def save(kind, data):
    def required(key, maximum=500):
        value = data.get(key, '').strip()
        if not value or len(value)>maximum:
            raise ValueError(f'{key}: required, maximum {maximum} characters.')
        return value
    with connect() as db:
        if kind == 'pu':
            import datetime
            lga=required('lga')
            if lga not in ('Apa','Agatu'): raise ValueError('Choose Apa or Agatu.')
            voters=int(required('voters'))
            if voters<0: raise ValueError('Voter total cannot be negative.')
            date=required('source_date')
            datetime.date.fromisoformat(date)
            db.execute('INSERT INTO pus(lga,ward,code,name,voters,source,source_date) VALUES(?,?,?,?,?,?,?)',
                       (lga,required('ward'),required('code'),required('name'),voters,required('source'),date))
        elif kind == 'agent':
            phone=required('phone',20)
            if not phone.replace('+','',1).isdigit() or not 10<=len(phone)<=15: raise ValueError('Enter a valid phone number.')
            if data.get('consent')!='yes': raise ValueError('Agent consent is required.')
            db.execute('INSERT INTO agents(pu_id,name,phone,role,status,consent) VALUES(?,?,?,?,?,1)',
                       (int(required('pu_id')),required('name'),phone,required('role'),required('status')))
        elif kind == 'report':
            db.execute('INSERT INTO reports(pu_id,category,detail) VALUES(?,?,?)',
                       (int(required('pu_id')),required('category'),required('detail',2000)))
        else: raise ValueError('Unknown action.')

def esc(value): return html.escape(str(value), quote=True)
def select(name, values):
    return f'<select name="{name}" required>'+''.join(f'<option value="{esc(v)}">{esc(label)}</option>' for v,label in values)+'</select>'
def field(name,label,typ='text'):
    return f'<label>{label}<input name="{name}" type="{typ}" required maxlength="500"'+(' min="0" step="1"' if typ=='number' else '')+'></label>'
def form(kind,content):
    return f'<form method="post" action="/{kind}"><input type="hidden" name="csrf" value="{TOKEN}">{content}<button>Save {kind}</button></form>'
def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join(f'<th>{esc(h)}</th>' for h in headers)+'</tr></thead><tbody>'+(''.join('<tr>'+''.join(f'<td>{esc(v)}</td>' for v in row)+'</tr>' for row in rows) or f'<tr><td colspan="{len(headers)}">No records yet.</td></tr>')+'</tbody></table></div>'
def page(message=''):
    with connect() as db:
        pus=db.execute('SELECT * FROM pus ORDER BY lga,ward,code').fetchall()
        agents=db.execute('SELECT a.*,p.code FROM agents a JOIN pus p ON p.id=a.pu_id ORDER BY a.id DESC').fetchall()
        reports=db.execute('SELECT r.*,p.code FROM reports r JOIN pus p ON p.id=r.pu_id ORDER BY r.id DESC').fetchall()
        covered=db.execute("SELECT COUNT(DISTINCT pu_id) FROM agents WHERE status IN ('Confirmed','Trained')").fetchone()[0]
    options=[(p['id'],f"{p['lga']} / {p['ward']} / {p['code']} — {p['name']}") for p in pus]
    puform=form('pu', '<label>LGA'+select('lga',[(x,x) for x in ('Apa','Agatu')])+'</label>'+''.join(field(*args) for args in [('ward','Ward'),('code','Official PU code'),('name','PU name'),('voters','Registered voters (total)','number'),('source','Figure source'),('source_date','Source date','date')]))
    agentform=form('agent','<label>Polling unit'+select('pu_id',options)+'</label>'+field('name','Agent name')+field('phone','Phone','tel')+'<label>Assignment'+select('role',[(x,x) for x in ('Primary','Backup')])+'</label><label>Status'+select('status',[(x,x) for x in ('Pending','Confirmed','Trained')])+'</label><label><input type="checkbox" name="consent" value="yes" required> Agent agreed to storage of these details for this assignment</label>') if pus else '<p>Add a polling unit first.</p>'
    reportform=form('report','<label>Polling unit'+select('pu_id',options)+'</label><label>Category'+select('category',[(x,x) for x in ('Logistics','Incident','Community concern')])+'</label><label>Report<textarea name="detail" required maxlength="2000"></textarea></label><p>Record operational facts. Do not include voter identities or political affiliations.</p>') if pus else '<p>Add a polling unit first.</p>'
    body=f'''<header><small>APA · AGATU</small><h1>Solution 2027</h1><p>Engr. Solomon Eigege Oloche’woche · Agent coordination portal</p></header>
    <aside>Local development · Administrator access · Use sample data until production deployment is ready.</aside>
    <form method="post" action="/logout"><input type="hidden" name="csrf" value="{TOKEN}"><button>Sign out</button></form><nav><a href="#pus">Polling units</a><a href="#agents">Agents</a><a href="#reports">Field reports</a></nav><p role="status">{esc(message)}</p>
    <div class="stats"><article><strong>{len(pus)}</strong>Polling units entered</article><article><strong>{sum(p['voters'] for p in pus):,}</strong>Registered voters recorded</article><article><strong>{covered}/{len(pus)}</strong>PUs with confirmed agents</article><article><strong>{len(agents)}</strong>Agent assignments</article></div>
    <section id="pus"><h2>Polling units</h2><p>Totals cover entered PUs only. Enter sourced figures; no official dataset is preloaded.</p>{puform}{table(['LGA','Ward','Code','PU','Voters','Source','Date'],[(p['lga'],p['ward'],p['code'],p['name'],p['voters'],p['source'],p['source_date']) for p in pus])}</section>
    <section id="agents"><h2>Agent assignments</h2>{agentform}{table(['PU','Agent','Phone','Role','Status'],[(a['code'],a['name'],a['phone'],a['role'],a['status']) for a in agents])}</section>
    <section id="reports"><h2>Field reports</h2>{reportform}{table(['PU','Category','Report','Recorded (UTC)'],[(r['code'],r['category'],r['detail'],r['created_at']) for r in reports])}</section>'''
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Solution 2027</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#182b35;font:16px system-ui}header{background:#0c3430;color:white;padding:40px max(5%,20px)}h1{font-size:40px;margin:8px 0}main{max-width:1200px;margin:auto;padding:20px}aside{background:#fff0c6;padding:16px;border-radius:10px}nav{display:flex;gap:24px;padding:24px 0}a{color:#086052}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:15px}article,section{background:white;border:1px solid #dce4e6;border-radius:12px;padding:24px}strong{display:block;font-size:30px;color:#096553}section{margin-top:24px}form{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px}label{display:block;font-weight:600}input:not([type=checkbox]),select,textarea,button{width:100%;padding:12px;border:1px solid #bdcbce;border-radius:6px;margin-top:7px;font:inherit}button{background:#096553;color:white;cursor:pointer;align-self:end}textarea{min-height:100px}th,td{text-align:left;padding:12px;border-bottom:1px solid #e2e8eb;vertical-align:top;overflow-wrap:anywhere}.scroll{overflow:auto}table{border-collapse:collapse;width:100%}form p{font-size:14px}input:focus,select:focus,textarea:focus{outline:3px solid #e7bd55}
    </style></head><body>'''+body.split('<aside>')[0]+'<main><aside>'+body.split('<aside>',1)[1]+'</main></body></html>'

class Handler(BaseHTTPRequestHandler):
    def respond(self,status,document,cookie=None):
        payload=document.encode()
        self.send_response(status)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        if cookie is not None: self.send_header('Set-Cookie',auth.cookie(cookie))
        self.end_headers(); self.wfile.write(payload)
    def redirect(self,path,cookie=None):
        self.send_response(303)
        self.send_header('Location',path)
        self.send_header('Cache-Control','no-store')
        if cookie is not None: self.send_header('Set-Cookie',auth.cookie(cookie))
        self.end_headers()
    def do_GET(self):
        if self.path not in ('/','/login'): self.send_error(404); return
        token,session=auth.get_session(self.headers.get('Cookie'))
        if session and session['authenticated']:
            if self.path=='/login': self.redirect('/'); return
            self.respond(200,page().replace(TOKEN,session['csrf'])); return
        if self.path=='/': self.redirect('/login'); return
        cookie=None
        if not session: cookie,session=auth.new_session()
        self.respond(200,auth.login_page(session['csrf']),cookie)
    def do_POST(self):
        if self.path not in ('/login','/logout','/pu','/agent','/report'): self.send_error(404); return
        token,session=auth.get_session(self.headers.get('Cookie'))
        if not session or (self.path!='/login' and not session['authenticated']):
            self.redirect('/login'); return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=16384: self.send_error(413); return
            data={k:v[0] for k,v in parse_qs(self.rfile.read(size).decode()).items()}
            if not secrets.compare_digest(data.get('csrf',''),session['csrf']): self.send_error(403); return
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
            save(self.path[1:],data)
        except (ValueError,sqlite3.IntegrityError):
            self.respond(400,page('Could not save. Check required fields, unique PU code and polling unit assignment.').replace(TOKEN,session['csrf'])); return
        self.redirect('/')

if __name__=='__main__':
    import sys
    init_db()
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
