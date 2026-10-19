import io
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator
import app
import auth
import backups
import deploy

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.original=app.DB;app.DB=str(self.root/'portal.sqlite3')
        self.env=patch.dict(os.environ,{'SOLUTION_ENV':'production','APP_ORIGIN':'https://portal.example','SOLUTION_DB':app.DB,'DATA_DIR':str(self.root),'BACKUP_DIR':str(self.root/'backups')});self.env.start()
        app.init_db(seed_directory=True)
        with app.connect() as db:auth.configure(db,'test-admin','sample-test-password')
        auth._attempts.clear();self.cookie=''
    def tearDown(self):self.env.stop();app.DB=self.original;self.tmp.cleanup()
    def request(self,path='/',data=None,**overrides):
        environ={};setup_testing_defaults(environ)
        body=urlencode(data).encode() if data is not None else b''
        environ.update(PATH_INFO=path,HTTP_HOST='portal.example',REQUEST_METHOD='POST' if data is not None else 'GET',CONTENT_LENGTH=str(len(body)),CONTENT_TYPE='application/x-www-form-urlencoded',HTTP_COOKIE=self.cookie,REMOTE_ADDR='127.0.0.1')
        environ['wsgi.input']=io.BytesIO(body);environ['wsgi.url_scheme']='https';environ.update(overrides)
        result={}
        def start(status,headers,exc_info=None):result.update(status=int(status.split()[0]),headers=headers)
        iterator=validator(deploy.application)(environ,start)
        try:result['body']=b''.join(iterator).decode()
        finally:iterator.close()
        for key,value in result['headers']:
            if key=='Set-Cookie':self.cookie=value.split(';')[0]
        self.assertEqual(sum(k.lower()=='content-length' for k,v in result['headers']),1)
        return result
    def login(self):
        body=self.request('/login')['body'];csrf=re.search(r'name="csrf" value="([^"]+)"',body)[1]
        response=self.request('/login',dict(csrf=csrf,username='test-admin',password='sample-test-password'),HTTP_ORIGIN='https://portal.example')
        self.assertEqual(response['status'],303)
        self.assertTrue(any(k=='Set-Cookie' and 'Secure' in v and 'HttpOnly' in v for k,v in response['headers']))
    def test_wsgi_auth_health_and_baseline(self):
        self.assertEqual(self.request('/healthz',HTTP_HOST='internal')['status'],200)
        self.assertEqual(self.request()['status'],303)
        self.login();response=self.request()
        self.assertEqual(response['status'],200);self.assertIn('131,035',response['body'])
        self.assertIn('Hosted portal',response['body'])
        self.assertEqual(self.request('/api/locations')['status'],200)
        self.assertEqual(self.request('/export/pus.csv')['status'],200)
        self.assertEqual(self.request('/missing')['status'],404)
    def test_host_origin_methods_and_size(self):
        self.assertEqual(self.request(HTTP_HOST='evil.example')['status'],400)
        self.assertEqual(self.request('/login',{},HTTP_ORIGIN='https://evil.example')['status'],403)
        self.assertEqual(self.request(REQUEST_METHOD='DELETE')['status'],405)
        self.assertEqual(self.request('/login',{},CONTENT_LENGTH='3100001')['status'],413)
    def test_bootstrap_initial_only(self):
        with app.connect() as db:db.execute('DELETE FROM users');db.execute('DELETE FROM administrator')
        salt='01'*16;hashed='pbkdf2_sha256$600000$'+salt+'$'+auth.password_hash('sample-bootstrap-password',salt)
        with patch.dict(os.environ,ADMIN_NAME='owner',ADMIN_PASSWORD_HASH=hashed):deploy.bootstrap_admin()
        with patch.dict(os.environ,ADMIN_NAME='ignored',ADMIN_PASSWORD_HASH='invalid'):deploy.bootstrap_admin()
        with app.connect() as db:
            self.assertTrue(auth.verify(db,'owner','sample-bootstrap-password'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
    def test_storage_configuration(self):
        self.assertEqual(deploy.settings()[1],Path(app.DB))
        with patch.dict(os.environ,SOLUTION_DB='/tmp/elsewhere.sqlite3'):
            with self.assertRaises(ValueError):deploy.settings()
        with patch.dict(os.environ,APP_ORIGIN='http://portal.example'):
            with self.assertRaises(ValueError):deploy.settings()
    def test_proxy_logins_are_separate_per_account(self):
        for _ in range(5):self.assertTrue(auth.allow_attempt('shared-proxy','user-one'))
        self.assertFalse(auth.allow_attempt('shared-proxy','user-one'))
        self.assertTrue(auth.allow_attempt('shared-proxy','user-two'))

    def test_backup_retention_never_deletes_source(self):
        source=self.root/'solution2027-000000.sqlite3'
        with sqlite3.connect(source) as db:db.execute('CREATE TABLE example(id INTEGER)')
        backups.create_backup(source,self.root,keep=1)
        backups.create_backup(source,self.root,keep=1)
        self.assertTrue(source.exists())

    def test_backup_restore_and_retention(self):
        directory=self.root/'backups'
        first=backups.create_backup(app.DB,directory,keep=2)
        with sqlite3.connect(first) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM voter_baselines').fetchone()[0],236)
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
        backups.create_backup(app.DB,directory,keep=2);latest=backups.create_backup(app.DB,directory,keep=2)
        self.assertEqual(len(list(directory.glob('*.sqlite3'))),2)
        self.assertFalse(first.exists());self.assertEqual(latest.stat().st_mode & 0o777,0o600)

if __name__=='__main__':unittest.main()
