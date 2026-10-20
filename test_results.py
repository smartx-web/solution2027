"""Results regression tests: real HTTP uploads, immutable history and PU-scoped access."""
import base64
import http.client
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from PIL import Image
import access
import app
import auth
import results


def photo():
    output=io.BytesIO();Image.new('RGB',(8,8),'white').save(output,format='PNG');return output.getvalue()


def fields(pu_id):
    data=dict(pu_id=str(pu_id),election='Council chair 2027-02-20',submitter='Sample submitter',registered='200',accredited='100',valid='80',rejected='5',cast='85',cancelled='no',notes='Sample result sheet')
    for i,party in enumerate(results.PARTIES):data['party_'+str(i)]=party;data['votes_'+str(i)]='10'
    return data


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=app.DB
        app.DB=os.path.join(self.tmp.name,'results.sqlite3');app.init_db(seed_directory=True)
        with app.connect() as db:
            units=db.execute('SELECT * FROM pus ORDER BY id').fetchall()
            self.pu=dict(units[0]);self.other=dict(next(p for p in units if p['lga']!=self.pu['lga']))
            auth.configure(db,'admin','sample-test-password')
            self.admin=auth.authenticate(db,'admin','sample-test-password')
        self.token=access.current_user.set(self.admin)
    def tearDown(self):
        access.current_user.reset(self.token);app.DB=self.old;self.tmp.cleanup()
    def save(self,data=None,picture=None):
        with app.connect() as db:return results.save(db,data or fields(self.pu['id']),picture)
    def get(self,result_id):
        with app.connect() as db:return dict(results.get(db,result_id))
    def user(self,role,pu=None):
        return dict(self.admin,role=role,lga=self.pu['lga'],wards=[self.pu['ward']],pu_id=pu or self.pu['id'])
    def test_revision_preserves_photo_flags_and_reviews(self):
        data=fields(self.pu['id']);data['valid']='79'
        first=self.save(data,photo());original=self.get(first)
        self.assertIn('Party votes',json.loads(original['flags'])[0])
        with app.connect() as db:results.review(db,dict(result_id=str(first),status='Disputed',review_notes='Arithmetic mismatch.'))
        correction=fields(self.pu['id']);correction.update(previous_id=str(first),correction_reason='Corrected transcription')
        second=self.save(correction)
        self.assertEqual(self.get(first),original)
        updated=self.get(second)
        self.assertEqual(updated['revision'],2);self.assertEqual(updated['photo'],original['photo'])
        self.assertEqual(json.loads(updated['flags']),[])
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM result_reviews WHERE result_id=?',(second,)).fetchone()[0],0)
            page=results.page(db,'csrf',second)
        self.assertIn('Disputed',page);self.assertIn('Pending verification',page);self.assertIn('Corrected transcription',page)
        with self.assertRaisesRegex(ValueError,'newer revision'):self.save(correction)
        with app.connect() as db:
            with self.assertRaisesRegex(ValueError,'latest revision'):results.review(db,dict(result_id=str(first),status='Verified by team',review_notes='Old'))
    def test_party_count_numbers_totals_and_cancellation(self):
        data=fields(self.pu['id']);del data['party_7'];del data['votes_7']
        with self.assertRaisesRegex(ValueError,'eight'):results.validate(data)
        for value in ('-1','1.5','1000000000','NaN',''):
            with self.subTest(value=value):
                bad=fields(self.pu['id']);bad['valid']=value
                with self.assertRaises(ValueError):results.validate(bad)
        data=fields(self.pu['id']);data['party_7']='apc'
        with self.assertRaisesRegex(ValueError,'distinct'):results.validate(data)
        data=fields(self.pu['id']);data.update(party_8='EXTRA',votes_8='0')
        self.assertEqual(len(results.validate(data)[1]),9)
        data.update(cancelled='yes',notes='')
        with self.assertRaisesRegex(ValueError,'cancellation'):results.validate(data)
        data.update(notes='Polling cancelled',registered='50',accredited='60',cast='90')
        flags=results.validate(data)[3]
        self.assertEqual(len(flags),4)
    def test_agent_and_coordinator_can_submit_and_correct_in_scope(self):
        for role in ('agent','coordinator'):
            token=access.current_user.set(self.user(role))
            try:
                data=fields(self.pu['id']);data['election']=role+' sample contest'
                first=self.save(data,photo())
                data.update(previous_id=str(first),correction_reason='Transcription correction',submitter='Updated submitter')
                corrected=self.save(data)
                self.assertEqual(self.get(corrected)['revision'],2)
                if role=='agent':
                    self.assertFalse(access.can_write())
                    with app.connect() as db:
                        self.assertIn(self.pu['code'],results.page(db,'csrf'))
                    self.assertNotIn(self.other['code'],app.page())
                    self.assertEqual(len(app.filtered_records()[0]),1)
            finally:access.current_user.reset(token)

    def test_backups_and_transfer_include_evidence_and_history(self):
        import backups
        import transfer
        first=self.save(picture=photo())
        with app.connect() as db:results.review(db,dict(result_id=str(first),status='Disputed',review_notes='Please review.'))
        backup=backups.create_backup(app.DB,os.path.join(self.tmp.name,'backups'))
        with sqlite3.connect(backup) as db:
            self.assertEqual(base64.b64decode(db.execute('SELECT photo FROM pu_results').fetchone()[0]),photo())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM result_reviews').fetchone()[0],1)
        counts=transfer.migrate(backup,'postgresql://not-contacted')
        self.assertEqual(counts['pu_results'],1);self.assertEqual(counts['result_reviews'],1)

    def test_production_wsgi_multipart_submission(self):
        import deploy
        from wsgiref.util import setup_testing_defaults
        from wsgiref.validate import validator
        token,session=auth.new_session(True,self.admin)
        data=fields(self.pu['id']);data['csrf']=session['csrf']
        boundary='production-test-boundary';body=b''
        for name,value in data.items():body+=f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        body+=f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="sheet.png"\r\nContent-Type: image/png\r\n\r\n'.encode()+photo()+f'\r\n--{boundary}--\r\n'.encode()
        environ={};setup_testing_defaults(environ)
        environ.update(PATH_INFO='/results',QUERY_STRING='',HTTP_HOST='portal.example',HTTP_ORIGIN='https://portal.example',REQUEST_METHOD='POST',CONTENT_LENGTH=str(len(body)),CONTENT_TYPE='multipart/form-data; boundary='+boundary,HTTP_COOKIE='solution_session='+token)
        environ['wsgi.input']=io.BytesIO(body);environ['wsgi.url_scheme']='https'
        response={}
        def start(status,headers,exc_info=None):response.update(status=status,headers=dict(headers))
        with patch.dict(os.environ,APP_ORIGIN='https://portal.example',SOLUTION_ENV='production'):
            iterator=validator(deploy.application)(environ,start)
            try:b''.join(iterator)
            finally:iterator.close()
        self.assertTrue(response['status'].startswith('303'))
        result_id=int(response['headers']['Location'].split('=')[1])
        self.assertEqual(base64.b64decode(self.get(result_id)['photo']),photo())

    def test_upload_validation_and_atomic_failure(self):
        for picture in (b'<svg/>',b'\xff\xd8\xff',b'x'*(results.MAX_PHOTO+1),None):
            with self.subTest(length=len(picture or b'')):
                with self.assertRaises(ValueError):self.save(picture=picture)
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM pu_results').fetchone()[0],0)
        with patch.object(Image,'open') as opening:
            opening.return_value.__enter__.return_value.width=20000001
            opening.return_value.__enter__.return_value.height=1
            opening.return_value.__enter__.return_value.format='PNG'
            with self.assertRaisesRegex(ValueError,'20 megapixels'):results.photo_value(b'photo')
    def test_scopes_and_reassignment(self):
        first=self.save(picture=photo())
        for role in ('agent','coordinator'):
            with self.subTest(role=role):
                token=access.current_user.set(self.user(role))
                try:
                    self.get(first)
                    with self.assertRaises(PermissionError):self.save(fields(self.other['id']),photo())
                    if role=='coordinator':
                        with app.connect() as db:
                            other_ward=db.execute('SELECT id FROM pus WHERE lga=? AND ward<>? LIMIT 1',(self.pu['lga'],self.pu['ward'])).fetchone()[0]
                        with self.assertRaises(PermissionError):self.save(fields(other_ward),photo())
                    with app.connect() as db:
                        with self.assertRaises(PermissionError):results.review(db,dict(result_id=str(first),status='Verified by team',review_notes='No authority'))
                finally:access.current_user.reset(token)
        token=access.current_user.set(self.user('agent',self.other['id']))
        try:
            with self.assertRaises(PermissionError):self.get(first)
            with app.connect() as db:self.assertNotIn(self.pu['code'],results.page(db,'csrf'))
        finally:access.current_user.reset(token)
        token=access.current_user.set(self.user('viewer'))
        try:
            self.get(first)
            with self.assertRaises(PermissionError):self.save(fields(self.other['id']),photo())
            with app.connect() as db:self.assertNotIn('action="/results"',results.page(db,'csrf',first))
        finally:access.current_user.reset(token)
    def test_new_submission_and_correction_cannot_replace_existing(self):
        first=self.save(picture=photo())
        with self.assertRaisesRegex(ValueError,'already has'):self.save(picture=photo())
        data=fields(self.other['id']);data.update(previous_id=str(first),correction_reason='Move it')
        with self.assertRaisesRegex(ValueError,'cannot change'):self.save(data)
        data=fields(self.pu['id']);data.update(previous_id=str(first),correction_reason='')
        with self.assertRaisesRegex(ValueError,'correction reason'):self.save(data)
    def test_agent_account_assignment_and_session_revocation(self):
        data=dict(username='agent-test',password='sample-test-password',active='yes',role='agent',pu_id=str(self.pu['id']))
        with app.connect() as db:
            uid=auth.manage_account(db,data,self.admin['id'])
            user=auth.authenticate(db,'agent-test','sample-test-password')
            self.assertEqual(user['pu_id'],self.pu['id'])
            _,session=auth.new_session(True,user)
            data.update(id=str(uid),password='',pu_id=str(self.other['id']))
            auth.manage_account(db,data,self.admin['id'])
            self.assertIsNone(auth.session_user(db,session))
            for value in ('','99999999','invalid'):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):auth.manage_account(db,dict(data,pu_id=value),self.admin['id'])
    def test_existing_sqlite_accounts_upgrade_idempotently(self):
        target=os.path.join(self.tmp.name,'legacy.sqlite3')
        with sqlite3.connect(target) as db:
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE,salt TEXT NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','viewer','coordinator')),lga TEXT NOT NULL DEFAULT '',wards TEXT NOT NULL DEFAULT '[]',active INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1)")
            db.execute("INSERT INTO users(id,username,salt,password_hash,role,version) VALUES(17,'legacy','salt','hash','admin',4)")
        with app.database.connect(target) as db:
            auth.init(db);auth.init(db)
            row=db.execute('SELECT * FROM users').fetchone()
            self.assertEqual((row['id'],row['username'],row['password_hash'],row['version']),(17,'legacy','hash',4))
            self.assertIsNone(row['pu_id'])
        app.init_db(seed_directory=True);app.init_db(seed_directory=True)
    def test_http_multipart_csrf_photo_authorization_and_review(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        token,_=auth.new_session(True,self.admin)
        cookie='solution_session='+token
        csrf=auth.get_session(cookie)[1]['csrf']
        def request(path,data=None,ctype=None,session_cookie=cookie):
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            headers={'Cookie':session_cookie}
            if ctype:headers['Content-Type']=ctype
            client.request('POST' if data is not None else 'GET',path,data,headers)
            response=client.getresponse();result=(response.status,dict(response.getheaders()),response.read());client.close();return result
        def upload(data,picture):
            boundary='test-boundary-2027';body=b''
            for name,value in data.items():body+=f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            body+=f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="sheet.png"\r\nContent-Type: image/png\r\n\r\n'.encode()+picture+f'\r\n--{boundary}--\r\n'.encode()
            return body,'multipart/form-data; boundary='+boundary
        try:
            self.assertEqual(request('/results',session_cookie='')[0],303)
            data=fields(self.pu['id']);data['csrf']='invalid';body,ctype=upload(data,photo())
            self.assertEqual(request('/results',body,ctype)[0],403)
            data['csrf']=csrf;body,ctype=upload(data,photo())
            status,headers,_=request('/results',body,ctype);self.assertEqual(status,303)
            path=headers['Location'];rid=int(path.split('=')[1])
            status,_,body=request(path);self.assertEqual(status,200);self.assertIn(b'Team-reported',body)
            self.assertEqual(request('/results/photo?id='+str(rid))[2],photo())
            review=urlencode(dict(csrf=csrf,result_id=str(rid),status='Verified by team',review_notes='Checked photo')).encode()
            self.assertEqual(request('/results/review',review,'application/x-www-form-urlencoded')[0],303)
            self.assertIn(b'Verified by team',request(path)[2])
            with app.connect() as db:
                uid=auth.manage_account(db,dict(username='outside-agent',password='sample-test-password',active='yes',role='agent',pu_id=str(self.other['id'])),self.admin['id'])
                other_user=auth.authenticate(db,'outside-agent','sample-test-password')
            outsider,_=auth.new_session(True,other_user)
            self.assertEqual(request('/results/photo?id='+str(rid),session_cookie='solution_session='+outsider)[0],403)
            self.assertEqual(request(path,session_cookie='solution_session='+outsider)[0],403)
            data.update(previous_id=str(rid),correction_reason='Corrected note',notes='<script>alert(1)</script>')
            body,ctype=upload(data,b'');self.assertEqual(request('/results',body,ctype)[0],303)
            self.assertIn(b'&lt;script&gt;',request(path)[2])
            self.assertNotIn(b'<script>alert',request(path)[2])
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
