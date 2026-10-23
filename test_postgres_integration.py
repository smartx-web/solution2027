"""Opt-in PostgreSQL 17+ tests using disposable databases on a local Unix socket.

Run with SOLUTION_TEST_PG_SOCKET=/private/socket SOLUTION_TEST_PG_PORT=55432
and optionally SOLUTION_TEST_PG_BIN=/path/to/postgresql/bin. No remote URLs or
production databases are accepted. Each test creates and drops only its own DBs.
"""
import base64
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import psycopg
from psycopg import sql
from PIL import Image
import access
import app
import auth
import database
import results
import transfer

SOCKET=os.environ.get('SOLUTION_TEST_PG_SOCKET')


@unittest.skipUnless(SOCKET,'Optional live PostgreSQL tests require a private local test socket.')
class LivePostgresTests(unittest.TestCase):
    def setUp(self):
        if not Path(SOCKET).is_absolute() or not Path(SOCKET).is_dir():
            raise ValueError('SOLUTION_TEST_PG_SOCKET must be an existing absolute Unix socket directory.')
        self.admin=psycopg.connect(dbname='postgres',host=SOCKET,port=os.environ.get('SOLUTION_TEST_PG_PORT','55432'),autocommit=True)
        self.databases=[]
        self.original=app.DB
        self.env=patch.dict(os.environ,{},clear=False);self.env.start()
        os.environ.pop('DATABASE_URL',None);os.environ.pop('DATABASE_URL_UNPOOLED',None)
        self.target=self.new_database()
        app.DB=self.target
        # Start with the actual v1 schema and an existing account, as on upgrade.
        with database.connect(self.target) as db:
            for statement in (Path(__file__).parent/'migrations/001_postgres.sql').read_text().split(';'):
                if statement.strip():db.execute(statement)
            salt='00'*16
            db.execute("INSERT INTO users(id,username,salt,password_hash,role,version) VALUES(17,'legacy-admin',?,?,'admin',4)",(salt,auth.password_hash('sample-test-password',salt)))
            db.execute("SELECT setval(pg_get_serial_sequence('users','id'),17,true)")
        app.init_db(seed_directory=True)
        with app.connect() as db:
            self.user=auth.authenticate(db,'legacy-admin','sample-test-password')
            self.units=[dict(r) for r in db.execute('SELECT * FROM pus ORDER BY id')]
        self.context=access.current_user.set(self.user)

    def tearDown(self):
        access.current_user.reset(self.context)
        app.DB=self.original;self.env.stop()
        for name in reversed(self.databases):
            self.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        self.admin.close()

    def new_database(self):
        name='solution2027_test_'+secrets.token_hex(8)
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        self.databases.append(name)
        from urllib.parse import urlencode
        return 'postgresql:///'+name+'?'+urlencode(dict(host=SOCKET,port=os.environ.get('SOLUTION_TEST_PG_PORT','55432')))

    def fixture(self,pu_id):
        data=dict(pu_id=str(pu_id),election='Sample contest 2027-02-20',submitter='Sample submitter',registered='200',accredited='100',valid='80',rejected='5',cast='85',cancelled='no',notes='Sample evidence')
        for i,party in enumerate(results.PARTIES):data['party_'+str(i)]=party;data['votes_'+str(i)]='10'
        return data

    def photo(self):
        output=io.BytesIO();Image.new('RGB',(8,8),'white').save(output,format='PNG');return output.getvalue()

    def test_v1_upgrade_preserves_accounts_and_repeated_startup(self):
        app.init_db(seed_directory=True);app.init_db(seed_directory=True)
        with app.connect() as db:
            row=db.execute("SELECT * FROM users WHERE username='legacy-admin'").fetchone()
            self.assertEqual((row['id'],row['version'],row['role']),(17,4,'admin'))
            self.assertTrue(auth.authenticate(db,'legacy-admin','sample-test-password'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pus').fetchone()[0],236)
            self.assertEqual(db.execute('SELECT SUM(registered_voters) FROM voter_baselines').fetchone()[0],131035)
            self.assertEqual([r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')],[1,2])
            uid=auth.manage_account(db,dict(username='pu-agent',password='sample-test-password',role='agent',active='yes',pu_id=str(self.units[0]['id'])),self.user['id'])
            self.assertIsNotNone(uid)

    def test_results_corrections_flags_reviews_and_scope(self):
        data=self.fixture(self.units[0]['id']);data['valid']='79'
        with app.connect() as db:first=results.save(db,data,self.photo())
        with app.connect() as db:
            results.review(db,dict(result_id=str(first),status='Disputed',review_notes='Party sum differs.'))
        data.update(previous_id=str(first),correction_reason='Corrected transcription',valid='80')
        with app.connect() as db:second=results.save(db,data,None)
        with app.connect() as db:
            old=results.get(db,first);new=results.get(db,second)
            self.assertTrue(json.loads(old['flags']));self.assertEqual(json.loads(new['flags']),[])
            self.assertEqual(old['photo'],new['photo']);self.assertEqual(new['revision'],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM result_reviews WHERE result_id=?',(second,)).fetchone()[0],0)
            self.assertIn('Pending verification',results.page(db,'csrf',second))
        with app.connect() as db:
            with self.assertRaisesRegex(ValueError,'newer revision'):results.save(db,data,None)
        for role in ('agent','coordinator','viewer'):
            outside=next(p for p in self.units if p['lga']!=self.units[0]['lga'])
            token=access.current_user.set(dict(self.user,role=role,pu_id=outside['id'],lga=outside['lga'],wards=[outside['ward']]))
            try:
                with app.connect() as db:
                    if role!='viewer':
                        with self.assertRaises(PermissionError):results.get(db,first)
                    with self.assertRaises(PermissionError):results.save(db,self.fixture(self.units[0]['id']),self.photo())
            finally:access.current_user.reset(token)

    def test_sqlite_transfer_preserves_photos_history_and_sequences(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.sqlite3';app.DB=str(source)
            app.init_db(seed_directory=True)
            with app.connect() as db:
                auth.configure(db,'sqlite-admin','sample-test-password')
            with app.connect() as db:first=results.save(db,self.fixture(self.units[0]['id']),self.photo())
            with app.connect() as db:results.review(db,dict(result_id=str(first),status='Disputed',review_notes='Review evidence.'))
            data=self.fixture(self.units[0]['id']);data.update(previous_id=str(first),correction_reason='Sample correction')
            with app.connect() as db:results.save(db,data,None)
            target=self.new_database()
            counts=transfer.migrate(source,target,apply=True)
            self.assertEqual(counts['pu_results'],2);self.assertEqual(counts['result_reviews'],1)
            app.DB=target
            with app.connect() as db:
                row=results.get(db,first)
                self.assertEqual(base64.b64decode(row['photo']),self.photo())
                self.assertTrue(auth.authenticate(db,'sqlite-admin','sample-test-password'))
                data.update(previous_id='2',correction_reason='Post-transfer correction')
                third=results.save(db,data,None)
                self.assertGreater(third,2)
            with self.assertRaisesRegex(ValueError,'contains records'):transfer.migrate(source,target,apply=True)

    def test_account_deletion_retains_results_and_rejects_admins(self):
        for role in ('coordinator','viewer','agent'):
            with app.connect() as db:
                uid=auth.manage_account(db,dict(username='delete-'+role,password='sample-test-password',role=role,lga='Apa',wards='',pu_id=str(self.units[0]['id']),active='yes'),self.user['id'])
                row=dict(db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
                _,session=auth.new_session(True,auth.user_snapshot(row))
            if role=='coordinator':
                unit=next(p for p in self.units if p['lga']=='Apa')
                context=access.current_user.set(auth.user_snapshot(row))
                try:
                    with app.connect() as db:rid=results.save(db,self.fixture(unit['id']),self.photo())
                finally:access.current_user.reset(context)
            app.delete_account(dict(id=str(uid),username=row['username'],version=str(row['version']),account_key=auth.account_key(row),confirmed='yes'))
            with app.connect() as db:
                self.assertIsNone(db.execute('SELECT id FROM users WHERE id=?',(uid,)).fetchone())
                self.assertIsNone(auth.session_user(db,session))
                self.assertIsNotNone(db.execute("SELECT id FROM activity_log WHERE entity='account' AND action='Deleted' AND record_id=?",(uid,)).fetchone())
        with app.connect() as db:
            self.assertEqual(results.get(db,rid)['account'],'delete-coordinator')
            admin=dict(db.execute('SELECT * FROM users WHERE id=?',(self.user['id'],)).fetchone())
        with self.assertRaisesRegex(ValueError,'Administrator'):
            app.delete_account(dict(id=str(admin['id']),username=admin['username'],version=str(admin['version']),account_key=auth.account_key(admin),confirmed='yes'))

    def test_pg_dump_and_restore_include_result_photos_and_reviews(self):
        with app.connect() as db:first=results.save(db,self.fixture(self.units[0]['id']),self.photo())
        with app.connect() as db:results.review(db,dict(result_id=str(first),status='Verified by team',review_notes='Sample evidence checked.'))
        binaries=Path(os.environ.get('SOLUTION_TEST_PG_BIN','/usr/bin'))
        with tempfile.TemporaryDirectory() as directory:
            backup=Path(directory)/'portal.dump'
            import sys
            process_env={**os.environ,'DATABASE_URL_UNPOOLED':self.target,'PATH':str(binaries)+os.pathsep+os.environ['PATH']}
            subprocess.run([sys.executable,'manage.py','dump-postgres','--output',str(backup)],env=process_env,check=True,capture_output=True)
            self.assertEqual(backup.stat().st_mode & 0o777,0o600)
            target=self.new_database()
            subprocess.run([str(binaries/'pg_restore'),'--no-owner','--no-acl','--exit-on-error','--dbname',target,str(backup)],check=True,capture_output=True)
            with database.connect(target) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM pu_results').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM result_reviews').fetchone()[0],1)
                self.assertEqual(base64.b64decode(db.execute('SELECT photo FROM pu_results').fetchone()[0]),self.photo())

if __name__=='__main__':unittest.main()
