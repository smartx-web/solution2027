"""Login deletion permissions, confirmation, session revocation and retained evidence."""
import unittest
from unittest.mock import patch
import access
import app
import auth
import results
import test_auth
from test_results import fields, photo


class AccountDeletionTests(unittest.TestCase):
    setUp=test_auth.AuthenticationTests.setUp
    tearDown=test_auth.AuthenticationTests.tearDown
    request=test_auth.AuthenticationTests.request
    csrf=test_auth.AuthenticationTests.csrf

    def login(self,username='admin'):
        self.cookie=''
        _,_,body=self.request('/login')
        self.assertEqual(self.request('/login',dict(csrf=self.csrf(body),username=username,password='sample-test-password'))[0],303)
        return self.csrf(self.request()[2])

    def create(self,role,username=None):
        app.init_db(seed_directory=True)
        with app.connect() as db:
            pu=db.execute('SELECT id FROM pus ORDER BY id LIMIT 1').fetchone()[0]
            uid=auth.manage_account(db,dict(username=username or role,password='sample-test-password',role=role,lga='Apa',wards='',pu_id=str(pu),active='yes'),1)
            return dict(db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())

    def deletion(self,row,csrf):
        return dict(id=str(row['id']),username=row['username'],version=str(row['version']),confirmed='yes',csrf=csrf,account_key=auth.account_key(row))

    def test_admin_deletes_each_supported_role_and_revokes_sessions(self):
        for role in ('coordinator','viewer','agent'):
            with self.subTest(role=role):
                row=self.create(role);token,session=auth.new_session(True,auth.user_snapshot(row))
                csrf=self.login()
                self.assertEqual(self.request('/delete-user',self.deletion(row,csrf))[:2],(303,'/#accounts'))
                with app.connect() as db:
                    self.assertIsNone(db.execute('SELECT * FROM users WHERE id=?',(row['id'],)).fetchone())
                    self.assertIsNone(auth.authenticate(db,row['username'],'sample-test-password'))
                    log=db.execute("SELECT * FROM activity_log WHERE action='Deleted' ORDER BY id DESC LIMIT 1").fetchone()
                    self.assertEqual(log['actor'],'admin');self.assertEqual(log['record_id'],row['id'])
                    self.assertIn(row['username'],log['summary'])
                self.assertIsNone(auth.get_session('solution_session='+token)[1])
                admin_cookie=self.cookie;self.cookie='solution_session='+token
                self.assertEqual(self.request()[0],303);self.cookie=admin_cookie
                self.assertEqual(self.request()[0],200)

    def test_nonadmins_and_anonymous_cannot_delete(self):
        target=self.create('viewer','target')
        for role in ('coordinator','viewer','agent'):
            row=self.create(role)
            csrf=self.login(row['username'])
            self.assertNotIn('action="/delete-user"',self.request()[2])
            self.assertEqual(self.request('/delete-user',self.deletion(target,csrf))[0],403)
        self.cookie=''
        self.assertEqual(self.request('/delete-user',self.deletion(target,'anonymous'))[0],303)
        with app.connect() as db:self.assertIsNotNone(db.execute('SELECT id FROM users WHERE id=?',(target['id'],)).fetchone())

    def test_confirmation_csrf_admin_protection_and_invalid_targets(self):
        row=self.create('viewer');other_admin=self.create('admin','other-admin');csrf=self.login()
        data=self.deletion(row,csrf)
        self.assertEqual(self.request('/delete-user',dict(data,confirmed=''))[0],400)
        self.assertEqual(self.request('/delete-user',dict(data,csrf='bad'))[0],403)
        for identifier in ('','bad','0','99999999','9'*100):
            self.assertEqual(self.request('/delete-user',dict(data,id=identifier))[0],400)
        with app.connect() as db:own=dict(db.execute('SELECT * FROM users WHERE id=1').fetchone())
        for administrator in (own,other_admin):
            self.assertEqual(self.request('/delete-user',self.deletion(administrator,csrf))[0],400)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM activity_log WHERE action='Deleted'").fetchone()[0],0)

    def test_changed_account_must_be_refreshed(self):
        row=self.create('viewer');csrf=self.login()
        with app.connect() as db:
            auth.manage_account(db,dict(id=str(row['id']),username=row['username'],role='coordinator',lga='Apa',wards='',active='yes'),1)
        self.assertEqual(self.request('/delete-user',self.deletion(row,csrf))[0],400)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT role FROM users WHERE id=?',(row['id'],)).fetchone()[0],'coordinator')
        self.assertEqual(self.request('/delete-user',dict(self.deletion(row,csrf),username='wrong'))[0],400)

    def test_deleted_login_keeps_submissions_photos_and_review_history(self):
        row=self.create('coordinator');csrf=self.login()
        with app.connect() as db:
            pu=db.execute("SELECT id FROM pus WHERE lga='Apa' ORDER BY id LIMIT 1").fetchone()[0]
        context=access.current_user.set(auth.user_snapshot(row))
        try:
            with app.connect() as db:rid=results.save(db,fields(pu),photo())
        finally:access.current_user.reset(context)
        with app.connect() as db:
            results.review(db,dict(result_id=str(rid),status='Verified by team',review_notes='Test review'))
        with app.connect() as db:
            original=dict(results.get(db,rid));review=dict(db.execute('SELECT * FROM result_reviews WHERE result_id=?',(rid,)).fetchone())
        self.assertEqual(self.request('/delete-user',self.deletion(row,csrf))[0],303)
        with app.connect() as db:
            self.assertEqual(dict(results.get(db,rid)),original)
            self.assertEqual(dict(db.execute('SELECT * FROM result_reviews WHERE result_id=?',(rid,)).fetchone()),review)
        self.assertEqual(self.request('/results?id='+str(rid))[0],200)

    def test_audit_failure_rolls_back_deletion_and_keeps_session(self):
        row=self.create('viewer');token,session=auth.new_session(True,auth.user_snapshot(row))
        with patch('app.log_activity',side_effect=RuntimeError('Audit failed')):
            with self.assertRaises(RuntimeError):app.delete_account(self.deletion(row,'unused'))
        with app.connect() as db:self.assertIsNotNone(auth.session_user(db,session))
        self.assertIsNotNone(auth.get_session('solution_session='+token)[1])

    def test_deleted_sessions_cannot_revive_when_sqlite_reuses_id(self):
        row=self.create('viewer');token,_=auth.new_session(True,auth.user_snapshot(row))
        csrf=self.login();self.assertEqual(self.request('/delete-user',self.deletion(row,csrf))[0],303)
        replacement=self.create('viewer','replacement')
        self.assertEqual(replacement['id'],row['id'])
        self.assertIsNone(auth.get_session('solution_session='+token)[1])

    def test_recreated_same_username_rejects_old_forms_and_late_sessions(self):
        row=self.create('viewer');snapshot=auth.user_snapshot(row);csrf=self.login()
        data=self.deletion(row,csrf)
        self.assertEqual(self.request('/delete-user',data)[0],303)
        recreated=self.create('viewer')
        self.assertEqual(recreated['id'],row['id'])
        self.assertEqual(self.request('/delete-user',data)[0],400)
        # A login already in flight when deletion committed cannot gain the new account.
        _,late_session=auth.new_session(True,snapshot)
        with app.connect() as db:self.assertIsNone(auth.session_user(db,late_session))

    def test_delete_ui_only_shows_nonadmin_logins(self):
        for role in ('coordinator','viewer','agent'):self.create(role)
        self.create('admin','other-admin');self.login()
        body=self.request()[2]
        self.assertEqual(body.count('action="/delete-user"'),3)
        self.assertIn('Permanently delete the login for coordinator',body)
        self.assertNotIn('Permanently delete the login for admin',body)
        self.assertNotIn('Permanently delete the login for other-admin',body)

if __name__=='__main__':unittest.main()
