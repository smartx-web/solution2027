"""Single-administrator authentication for the local development server."""
import hashlib
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

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()

def configure(db, username, password):
    username=username.strip()
    if not username or len(username)>100: raise ValueError('Username must be 1–100 characters.')
    if len(password)<12 or len(password)>1024: raise ValueError('Use a password of 12–1024 characters.')
    salt=secrets.token_hex(16)
    db.execute('INSERT OR REPLACE INTO administrator VALUES(1,?,?,?)',(username,salt,password_hash(password,salt)))
    with _lock: _sessions.clear()

def verify(db, username, password):
    row=db.execute('SELECT username,salt,password_hash FROM administrator WHERE id=1').fetchone()
    if not row: return False
    candidate=password_hash(password,row[1])
    return hmac.compare_digest(username.encode(),row[0].encode()) and hmac.compare_digest(candidate,row[2])

def new_session(authenticated=False):
    token=secrets.token_urlsafe(32)
    session={'csrf':secrets.token_urlsafe(32),'authenticated':authenticated,'expires':time.monotonic()+SESSION_SECONDS}
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
    </style><main><small>APA · AGATU</small><h1>Solution 2027</h1><p>Administrator sign in</p><p role="alert">'''+html.escape(message)+'''</p><form method="post" action="/login"><input type="hidden" name="csrf" value="'''+csrf+'''"><label>Username<input name="username" autocomplete="username" required maxlength="100"></label><label>Password<input name="password" type="password" autocomplete="current-password" required maxlength="1024"></label><button>Sign in</button></form></main></html>'''
