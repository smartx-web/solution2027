import os
import tempfile
import unittest
import sqlite3
import app

class PortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.original=app.DB
        app.DB=os.path.join(self.tmp.name,'test.sqlite3')
        app.init_db()
        self.pu=dict(lga='Agatu',ward='Sample ward',code='TEST-001',name='Sample PU',voters='200',source='Test fixture',source_date='2026-10-18')
    def tearDown(self):
        app.DB=self.original
        self.tmp.cleanup()
    def test_persistence_and_assignment(self):
        app.save('pu',self.pu)
        app.save('agent',dict(pu_id='1',name='Example Agent',phone='08000000000',role='Primary',status='Confirmed',consent='yes'))
        app.save('report',dict(pu_id='1',category='Logistics',detail='Sample report'))
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT voters FROM pus').fetchone()[0],200)
        self.assertIn('1/1',app.page())
    def test_invalid_total_and_duplicate(self):
        with self.assertRaises(ValueError): app.save('pu',dict(self.pu,voters='-1'))
        app.save('pu',self.pu)
        with self.assertRaises(sqlite3.IntegrityError): app.save('pu',self.pu)
    def test_consent_and_foreign_key(self):
        agent=dict(pu_id='99',name='Sample',phone='08000000000',role='Primary',status='Pending')
        with self.assertRaises(ValueError): app.save('agent',agent)
        with self.assertRaises(sqlite3.IntegrityError): app.save('agent',dict(agent,consent='yes'))
    def test_html_escaped(self):
        app.save('pu',dict(self.pu,name='<script>alert(1)</script>'))
        self.assertNotIn('<script>',app.page())
        self.assertIn('&lt;script&gt;',app.page())

if __name__=='__main__': unittest.main()
