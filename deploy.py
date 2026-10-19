"""Single-process Waitress entry point for Render behind its HTTPS edge."""
import io
import logging
import os
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit
import app
import auth
import backups


def settings():
    origin=os.environ.get('APP_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL','')
    parsed=urlsplit(origin)
    if parsed.scheme!='https' or not parsed.netloc or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Set APP_ORIGIN to the canonical HTTPS portal URL.')
    database=Path(os.environ.get('SOLUTION_DB',''))
    root=Path(os.environ.get('DATA_DIR','/var/data')).resolve()
    if not database.is_absolute() or root not in database.resolve().parents:
        raise ValueError('SOLUTION_DB must be an absolute path under DATA_DIR on the persistent disk.')
    if not root.is_dir():raise ValueError('Persistent DATA_DIR is not mounted.')
    backup_dir=Path(os.environ.get('BACKUP_DIR',str(root/'backups'))).resolve()
    if root not in backup_dir.parents:raise ValueError('BACKUP_DIR must be under DATA_DIR.')
    return origin.rstrip('/'),database,backup_dir


def bootstrap_admin():
    with app.connect() as db:
        if db.execute("SELECT 1 FROM users WHERE role='admin' AND active=1").fetchone():return
        username=os.environ.get('ADMIN_NAME','').strip()
        encoded=os.environ.get('ADMIN_PASSWORD_HASH','')
        parts=encoded.split('$')
        if not username or len(username)>100 or len(parts)!=4 or parts[:2]!=['pbkdf2_sha256','600000']:
            raise ValueError('Set ADMIN_NAME and ADMIN_PASSWORD_HASH for the initial administrator.')
        salt,digest=parts[2:]
        try:
            if len(salt)!=32 or len(digest)!=64:raise ValueError()
            if len(bytes.fromhex(salt))!=16 or len(bytes.fromhex(digest))!=32:raise ValueError()
        except ValueError:raise ValueError('Invalid administrator password hash.') from None
        if db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
            raise ValueError('Bootstrap username already exists; recover the administrator from the service shell.')
        db.execute("INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,'admin')",(username,salt,digest))
        app.log_activity(db,'Created','account',None,'Initial administrator provisioned at startup.')


class Request(app.Handler):
    """Reuse tested portal routes without running the development HTTP server."""
    def __init__(self,environ):
        self.path=environ.get('PATH_INFO','/')
        if environ.get('QUERY_STRING'):self.path+='?'+environ['QUERY_STRING']
        self.headers=Message()
        for key,value in environ.items():
            if key.startswith('HTTP_'):self.headers[key[5:].replace('_','-')]=value
        for key in ('CONTENT_TYPE','CONTENT_LENGTH'):
            if environ.get(key):self.headers[key.replace('_','-')]=environ[key]
        self.rfile=environ['wsgi.input'];self.wfile=io.BytesIO()
        self.client_address=(environ.get('REMOTE_ADDR','unknown'),0)
        self.code=200;self.response_headers=[]
    def send_response(self,code,message=None):self.code=code
    def send_header(self,name,value):self.response_headers.append((name,str(value)))
    def end_headers(self):pass
    def send_error(self,code,message=None,explain=None):
        self.respond(code,'<!doctype html><title>Request error</title><h1>'+str(code)+' '+HTTPStatus(code).phrase+'</h1>')
    def log_message(self,*args):pass


def application(environ,start_response):
    path=environ.get('PATH_INFO','/')
    method=environ.get('REQUEST_METHOD','GET')
    origin=os.environ.get('APP_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL','')
    expected=urlsplit(origin).netloc.lower()
    headers=[('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Strict-Transport-Security','max-age=31536000')]
    def finish(code,body,extra=()):
        body=body.encode() if isinstance(body,str) else body
        extra=[(name,value) for name,value in extra if name.lower() not in {'content-length','cache-control','x-content-type-options','strict-transport-security'}]
        if not any(name.lower()=='content-type' for name,_ in extra):extra.append(('Content-Type','text/plain; charset=utf-8'))
        start_response(str(code)+' '+HTTPStatus(code).phrase,headers+extra+[('Content-Length',str(len(body)))])
        return [body]
    # Health probes expose no records and do not depend on a public Host header.
    if path=='/healthz' and method=='GET':
        try:
            with app.connect() as db:db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
        except Exception:return finish(503,'unavailable')
        return finish(200,'ok')
    if not expected or environ.get('HTTP_HOST','').lower()!=expected:return finish(400,'Invalid host')
    if method not in ('GET','POST'):return finish(405,'Method not allowed',[('Allow','GET, POST')])
    if method=='POST' and environ.get('HTTP_ORIGIN') and environ['HTTP_ORIGIN']!=origin.rstrip('/'):
        return finish(403,'Invalid request origin')
    try:
        size=int(environ.get('CONTENT_LENGTH') or '0')
        if size<0 or size>3100000:return finish(413,'Request too large')
        request=Request(environ)
        if method=='GET':request.do_GET()
        else:request.do_POST()
        return finish(request.code,request.wfile.getvalue(),request.response_headers)
    except Exception:
        logging.exception('Portal request failed')
        return finish(500,'Unable to process this request')


def main():
    if os.environ.get('SOLUTION_ENV')!='production':raise SystemExit('Set SOLUTION_ENV=production for deploy.py.')
    os.umask(0o077)
    origin,database,backup_dir=settings()
    database.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    app.DB=str(database)
    if database.is_file():backups.create_backup(database,backup_dir)
    app.init_db(seed_directory=True)
    bootstrap_admin()
    backups.create_backup(database,backup_dir)
    stop=backups.start_backup_loop(database,backup_dir)
    from waitress import serve
    try:
        # One process: sessions/throttling are shared across its worker threads.
        # Render owns TLS; do not expose this HTTP listener directly to the internet.
        serve(application,host='0.0.0.0',port=int(os.environ.get('PORT','10000')),threads=4,
              url_scheme='https',max_request_body_size=3100000,max_request_header_size=32768,
              channel_timeout=30,expose_tracebacks=False)
    finally:stop.set()

if __name__=='__main__':main()
