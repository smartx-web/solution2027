import json
import unittest
import app
import auth
import access
import test_auth

class RoleTests(unittest.TestCase):
    setUp=test_auth.AuthenticationTests.setUp
    tearDown=test_auth.AuthenticationTests.tearDown
    request=test_auth.AuthenticationTests.request
    csrf=test_auth.AuthenticationTests.csrf
    def seed(self):
        for lga,ward,code in [('Apa','Ward A','OWN'),('Apa','Ward B','OTHER-WARD'),('Agatu','Ward C','OTHER-LGA')]:
            app.save('pu',dict(lga=lga,ward=ward,code=code,name=code,voters='100',source='Fixture',source_date='2026-10-19'))
        for i in (1,2,3):
            app.save('agent',dict(pu_id=str(i),name='Agent '+str(i),phone='0800000000'+str(i),role='Primary',status='Confirmed',consent='yes'))
        with app.connect() as db:
            auth.manage_account(db,dict(username='viewer',password='sample-test-password',role='viewer',active='yes'),1)
            auth.manage_account(db,dict(username='coord',password='sample-test-password',role='coordinator',lga='Apa',wards='Ward A',active='yes'),1)
    def sign_in(self,username):
        self.cookie='';_,_,body=self.request('/login')
        status,_,_=self.request('/login',dict(csrf=self.csrf(body),username=username,password='sample-test-password'))
        self.assertEqual(status,303)
        _,_,body=self.request();return self.csrf(body)
    def agent(self,pu,id=None):
        data=dict(pu_id=str(pu),name='Changed',phone='08000000000',role='Primary',status='Confirmed',consent='yes')
        if id:data['id']=str(id)
        return data
    def test_viewer_cannot_write_any_record(self):
        self.seed();csrf=self.sign_in('viewer')
        self.assertEqual(self.request('/export/agents.csv')[0],200)
        for path,data in [('/agent',self.agent(1)),('/report',dict(pu_id='1',category='Incident',detail='Test')),('/pu',{}),('/archive-agent',dict(id='1',confirmed='yes')),('/restore-agent',dict(id='1',confirmed='yes')),('/import-preview',dict(csv='invalid')),('/import-confirm',dict(csv='invalid',confirmed='yes')),('/user',{})]:
            with self.subTest(path=path):self.assertEqual(self.request(path,dict(data,csrf=csrf))[0],403)
        self.assertNotIn('Accounts and permissions',self.request()[2])
    def test_coordinator_reads_and_exports_only_assigned_ward(self):
        self.seed();self.sign_in('coord')
        rows=json.loads(self.request('/api/locations')[2]);self.assertEqual([r['code'] for r in rows],['OWN'])
        status,_,body=self.request('/export/agents.csv');self.assertEqual(status,200)
        self.assertIn('Agent 1',body);self.assertNotIn('Agent 2',body);self.assertNotIn('Agent 3',body)
        self.assertNotIn('Agent 3',self.request('/?lga=Agatu&q=Agent')[2])
        self.assertNotIn('Activity log</h2>',self.request()[2])
    def test_coordinator_cannot_edit_or_move_other_assignments(self):
        self.seed();csrf=self.sign_in('coord')
        self.assertEqual(self.request('/agent',dict(self.agent(1,1),csrf=csrf))[0],303)
        for pu,id in [(2,None),(3,None),(1,2),(2,1)]:
            with self.subTest(pu=pu,id=id):self.assertEqual(self.request('/agent',dict(self.agent(pu,id),csrf=csrf))[0],403)
        self.assertEqual(self.request('/archive-agent',dict(id='3',confirmed='yes',csrf=csrf))[0],403)
        self.assertEqual(self.request('/archive-agent',dict(id='1',confirmed='yes',csrf=csrf))[0],303)
        self.assertEqual(self.request('/import-preview',dict(csv='bad',csrf=csrf))[0],403)
        self.assertEqual(self.request('/user',dict(csrf=csrf))[0],403)
        with app.connect() as db:
            log=db.execute("SELECT actor FROM activity_log WHERE action='Updated' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(log[0],'coord')
    def test_coordinator_total_update_and_scope_change_denied(self):
        self.seed();csrf=self.sign_in('coord')
        data=dict(id='1',lga='Apa',ward='Ward A',code='OWN',name='OWN',voters='150',source='Updated fixture',source_date='2026-10-19',csrf=csrf)
        self.assertEqual(self.request('/pu',data)[0],303)
        self.assertEqual(self.request('/pu',dict(data,code='RENAMED'))[0],403)
        self.assertEqual(self.request('/pu',dict(data,id='2',ward='Ward B',code='OTHER-WARD',name='OTHER-WARD'))[0],403)
    def test_account_change_revokes_live_session(self):
        self.seed();self.sign_in('coord')
        with app.connect() as db:
            row=db.execute("SELECT id FROM users WHERE username='coord'").fetchone()
            auth.manage_account(db,dict(id=str(row[0]),username='coord',role='coordinator',lga='Apa',wards='Ward A',active=''),1)
        self.assertEqual(self.request()[0],303)
    def test_admin_account_creation_and_self_protection(self):
        self.seed();csrf=self.sign_in('admin')
        data=dict(username='second-viewer',role='viewer',password='sample-test-password',active='yes',csrf=csrf)
        self.assertEqual(self.request('/user',data)[0],303)
        with app.connect() as db: admin=db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        self.assertEqual(self.request('/user',dict(username='admin',role='viewer',id=str(admin),active='yes',csrf=csrf))[0],400)
    def test_whole_lga_scope_and_password_reset(self):
        self.seed()
        with app.connect() as db:
            record_id=db.execute("SELECT id FROM users WHERE username='coord'").fetchone()[0]
            auth.manage_account(db,dict(id=str(record_id),username='coord',role='coordinator',lga='Apa',wards='',active='yes'),1)
        self.sign_in('coord')
        self.assertEqual(len(json.loads(self.request('/api/locations')[2])),2)
        with app.connect() as db:
            auth.manage_account(db,dict(id=str(record_id),username='coord',role='coordinator',lga='Apa',wards='',active='yes',password='new-sample-password'),1)
            self.assertIsNone(auth.authenticate(db,'coord','sample-test-password'))
            self.assertIsNotNone(auth.authenticate(db,'coord','new-sample-password'))
        self.assertEqual(self.request('/api/locations')[0],303)
    def test_invalid_scope_and_last_admin_rejected(self):
        self.seed()
        with app.connect() as db:
            with self.assertRaises(ValueError):auth.manage_account(db,dict(username='invalid-coord',role='coordinator',lga='Apa',wards='Not a ward',password='sample-test-password',active='yes'),1)
            root=db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
            with self.assertRaises(ValueError):auth.manage_account(db,dict(id=str(root),username='admin',role='viewer',active='yes'),999)
            self.assertEqual(db.execute("SELECT role FROM users WHERE id=?",(root,)).fetchone()[0],'admin')

    def test_historical_baseline_api_is_scoped(self):
        self.seed()
        app.init_db(seed_directory=True)
        # Assign the account to a real snapshot ward before checking API access.
        with app.connect() as db:
            record_id=db.execute("SELECT id FROM users WHERE username='coord'").fetchone()[0]
            auth.manage_account(db,dict(id=str(record_id),username='coord',role='coordinator',lga='Agatu',wards='EGBA',active='yes'),1)
        self.sign_in('coord')
        rows=json.loads(self.request('/api/locations')[2])
        self.assertEqual(len(rows),16)
        self.assertTrue(all(row['ward']=='EGBA' for row in rows))
        self.assertEqual(sum(row['voters_2023'] for row in rows),9569)
        self.assertTrue(all(row['voters'] is None for row in rows))

    def test_legacy_admin_is_migrated(self):
        with app.connect() as db: db.execute('DROP TABLE users')
        app.init_db()
        with app.connect() as db:
            user=auth.authenticate(db,'admin','sample-test-password')
            self.assertEqual(user['role'],'admin')

if __name__=='__main__':unittest.main()
