"""Account authentication and session management for the local development server."""
import hashlib
import json
import hmac
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError

SESSION_SECONDS = 3600
_sessions = {}
_attempts = {}
_lock = threading.Lock()

def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS administrator (id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL)')
    db.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE,salt TEXT NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','viewer','coordinator')),lga TEXT NOT NULL DEFAULT '',wards TEXT NOT NULL DEFAULT '[]',active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1)")
    legacy=db.execute('SELECT username,salt,password_hash FROM administrator WHERE id=1').fetchone()
    if legacy and not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
        db.execute("INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,'admin')",tuple(legacy))

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()

def configure(db, username, password):
    username=username.strip()
    if not username or len(username)>100: raise ValueError('Username must be 1–100 characters.')
    if len(password)<12 or len(password)>1024: raise ValueError('Use a password of 12–1024 characters.')
    salt=secrets.token_hex(16)
    db.execute('INSERT OR REPLACE INTO administrator VALUES(1,?,?,?)',(username,salt,password_hash(password,salt)))
    row=db.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
    if row:
        db.execute("UPDATE users SET salt=?,password_hash=?,role='admin',lga='',wards='[]',active=1,version=version+1 WHERE id=?",(salt,password_hash(password,salt),row[0]))
    else:
        db.execute("INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,'admin')",(username,salt,password_hash(password,salt)))
    with _lock: _sessions.clear()

def user_snapshot(row):
    if not row: return None
    result={key:row[key] for key in ('id','username','role','lga','active','version')}
    result['wards']=json.loads(row['wards'])
    return result

def authenticate(db,username,password):
    row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
    candidate=password_hash(password,row['salt'] if row else '00'*16)
    if not row or not row['active'] or not hmac.compare_digest(candidate,row['password_hash']): return None
    return user_snapshot(row)

def session_user(db,session):
    if not session or not session.get('authenticated') or not session.get('user_id'): return None
    row=db.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone()
    if not row or not row['active'] or row['version']!=session.get('user_version'): return None
    return user_snapshot(row)

def manage_account(db,data,actor_id):
    username=data.get('username','').strip()
    role=data.get('role','');lga=data.get('lga','').strip()
    wards=sorted(set(w.strip() for w in data.get('wards','').splitlines() if w.strip()))
    password=data.get('password','')
    active=data.get('active')=='yes'
    record_id=int(data['id']) if data.get('id') else None
    if not username or len(username)>100 or role not in ('admin','viewer','coordinator'): raise ValueError('Choose a valid username and role.')
    if role=='coordinator':
        if lga not in ('Apa','Agatu'): raise ValueError('Choose the coordinator LGA.')
        known={r[0] for r in db.execute('SELECT DISTINCT ward FROM pus WHERE lga=?',(lga,))}
        if any(w not in known for w in wards): raise ValueError('Ward names must match the directory exactly, one per line.')
    else: lga='';wards=[]
    if record_id is not None and record_id==actor_id and (role!='admin' or not active): raise ValueError('You cannot disable or demote your own administrator account.')
    old=db.execute('SELECT * FROM users WHERE id=?',(record_id,)).fetchone() if record_id else None
    if record_id and not old: raise ValueError('Account does not exist.')
    if old and old['username']!=username: raise ValueError('Existing usernames cannot be changed.')
    if old and old['role']=='admin' and old['active'] and (role!='admin' or not active):
        if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]<=1: raise ValueError('Keep at least one active administrator.')
    if (not old or password) and not 12<=len(password)<=1024: raise ValueError('Use a password of 12–1024 characters.')
    if old:
        salt=secrets.token_hex(16) if password else old['salt']
        hashed=password_hash(password,salt) if password else old['password_hash']
        db.execute('UPDATE users SET role=?,lga=?,wards=?,active=?,salt=?,password_hash=?,version=version+1 WHERE id=?',(role,lga,json.dumps(wards),int(active),salt,hashed,record_id))
    else:
        salt=secrets.token_hex(16)
        cursor=db.execute('INSERT INTO users(username,salt,password_hash,role,lga,wards,active) VALUES(?,?,?,?,?,?,?)',(username,salt,password_hash(password,salt),role,lga,json.dumps(wards),int(active)))
        record_id=cursor.lastrowid
    return record_id

def verify(db, username, password):
    return authenticate(db,username,password) is not None


def new_session(authenticated=False,user=None):
    token=secrets.token_urlsafe(32)
    session={'csrf':secrets.token_urlsafe(32),'authenticated':authenticated,'expires':time.monotonic()+SESSION_SECONDS}
    if user:
        session['user_id']=user['id'];session['user_version']=user['version']
    with _lock:
        now=time.monotonic()
        for key in list(_sessions):
            if _sessions[key]['expires']<=now: del _sessions[key]
        if len(_sessions)>=1000: del _sessions[next(iter(_sessions))]
        _sessions[token]=session
    return token,session

def get_session(header):
    try:
        cookie=SimpleCookie(); cookie.load(header or '')
        token=cookie['solution_session'].value if 'solution_session' in cookie else ''
    except CookieError: return '',None
    with _lock:
        session=_sessions.get(token)
        if session and session['expires']>time.monotonic(): return token,session.copy()
        _sessions.pop(token,None)
    return '',None

def revoke(token):
    with _lock: _sessions.pop(token,None)

def allow_attempt(address):
    with _lock:
        now=time.monotonic()
        for key in list(_attempts):
            if _attempts[key][1]<=now: del _attempts[key]
        count,until=_attempts.get(address,(0,now+300))
        if count>=5: return False
        _attempts[address]=(count+1,until)
        return True

def cookie(token):
    return f'solution_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS if token else 0}'

def login_page(csrf,message=''):
    import html
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · Solution 2027</title><style>
    body{background:#edf3f1;color:#17352d;font:16px system-ui;margin:0;padding:20px}main{max-width:400px;margin:8vh auto;background:white;padding:32px;border-radius:16px}label{display:block;margin:20px 0}input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:8px;font:inherit;border:1px solid #a6bcb4;border-radius:6px}button{background:#096553;color:white}a{color:#096553}
    </style><main><small>APA · AGATU</small><h1>Solution 2027</h1><p>Portal sign in</p><p role="alert">'''+html.escape(message)+'''</p><form method="post" action="/login"><input type="hidden" name="csrf" value="'''+csrf+'''"><label>Username<input name="username" autocomplete="username" required maxlength="100"></label><label>Password<input name="password" type="password" autocomplete="current-password" required maxlength="1024"></label><button>Sign in</button></form></main></html>'''
