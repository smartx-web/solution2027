import http.client
import os
import re
import tempfile
import threading
import unittest
from urllib.parse import urlencode
import app
import auth

class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.original=app.DB
        app.DB=os.path.join(self.tmp.name,'auth.sqlite3'); app.init_db()
        with app.connect() as db: auth.configure(db,'admin','sample-test-password')
        auth._attempts.clear()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.cookie=''
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        app.DB=self.original; self.tmp.cleanup()
    def request(self,path='/',data=None):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        headers={'Cookie':self.cookie}
        if data is not None: headers['Content-Type']='application/x-www-form-urlencoded'
        client.request('POST' if data is not None else 'GET',path,urlencode(data) if data is not None else None,headers)
        response=client.getresponse(); status=response.status; location=response.getheader('Location'); cookie=response.getheader('Set-Cookie'); body=response.read().decode(); client.close()
        if cookie: self.cookie=cookie.split(';')[0]
        return status,location,body
    def csrf(self,body): return re.search(r'name="csrf" value="([^"]+)"',body)[1]
    def login(self):
        _,_,body=self.request('/login'); anonymous=self.cookie
        result=self.request('/login',dict(csrf=self.csrf(body),username='admin',password='sample-test-password'))
        self.assertEqual(result[0],303); self.assertNotEqual(anonymous,self.cookie)
        return anonymous
    def test_gate_login_write_logout(self):
        self.assertEqual(self.request()[:2],(303,'/login'))
        self.assertEqual(self.request('/pu',dict(name='no access'))[0],303)
        anonymous=self.login()
        status,_,body=self.request(); self.assertEqual(status,200)
        csrf=self.csrf(body)
        self.assertEqual(self.request('/pu',dict(csrf='invalid'))[0],403)
        self.assertEqual(self.request('/pu',dict(csrf=csrf,lga='Apa',ward='Test',code='T1',name='Test',voters='10',source='Fixture',source_date='2026-10-18'))[0],303)
        self.assertEqual(self.request('/logout',dict(csrf=csrf))[0],303)
        self.assertEqual(self.request()[0],303)
        self.cookie=anonymous; self.assertEqual(self.request()[0],303)
    def test_authenticated_import_and_edit(self):
        self.assertEqual(self.request('/import-preview',dict(csv='test'))[0],303)
        self.login(); _,_,body=self.request(); csrf=self.csrf(body)
        text='lga,ward,code,name,voters,source,source_date\nApa,Sample,T2,Sample PU,100,Fixture,2026-10-18\n'
        status,_,body=self.request('/import-preview',dict(csrf=csrf,csv=text))
        self.assertEqual(status,200);self.assertIn('Review PU import',body)
        self.assertEqual(self.request('/import-confirm',dict(csrf=csrf,csv=text))[0],400)
        self.assertEqual(self.request('/import-confirm',dict(csrf='bad',csv=text,confirmed='yes'))[0],403)
        self.assertEqual(self.request('/import-confirm',dict(csrf=csrf,csv=text,confirmed='yes'))[0],200)
        self.assertEqual(self.request('/pu',dict(csrf=csrf,id='1',lga='Apa',ward='Sample',code='T2',name='Corrected',voters='150',source='Fixture',source_date='2026-10-18'))[0],303)
        self.assertIn('Corrected',self.request()[2])

    def test_directory_requires_login(self):
        self.assertEqual(self.request('/api/locations')[0],303)
        self.assertEqual(self.request('/portal.js')[0],303)
        self.login()
        app.init_db(seed_directory=True)
        import json
        status,_,body=self.request('/api/locations')
        self.assertEqual(status,200)
        self.assertEqual(len(json.loads(body)),236)
        self.assertIn('location-picker',self.request()[2])
        self.assertEqual(self.request('/portal.js')[0],200)

    def test_bad_password_throttle(self):
        _,_,body=self.request('/login'); csrf=self.csrf(body)
        for _ in range(5): self.assertEqual(self.request('/login',dict(csrf=csrf,username='admin',password='incorrect'))[0],401)
        self.assertEqual(self.request('/login',dict(csrf=csrf,username='admin',password='sample-test-password'))[0],429)
    def test_expiry_and_hash(self):
        self.login(); token,_=auth.get_session(self.cookie)
        auth._sessions[token]['expires']=0
        self.assertEqual(self.request()[0],303)
        with app.connect() as db:
            row=db.execute('SELECT password_hash FROM administrator').fetchone()
            self.assertNotEqual(row[0],'sample-test-password')
            self.assertTrue(auth.verify(db,'admin','sample-test-password'))
            self.assertFalse(auth.verify(db,'wrong','sample-test-password'))

if __name__=='__main__': unittest.main()
