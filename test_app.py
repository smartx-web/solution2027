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

class EditImportTests(unittest.TestCase):
    setUp=PortalTests.setUp
    tearDown=PortalTests.tearDown
    def csv_text(self,rows):
        import io,csv
        stream=io.StringIO(); writer=csv.DictWriter(stream,fieldnames=app.PU_COLUMNS)
        writer.writeheader();writer.writerows(rows);return stream.getvalue()
    def test_edit_preserves_links_and_rejects_bad_edit(self):
        app.save('pu',self.pu)
        agent=dict(pu_id='1',name='Example',phone='08000000000',role='Primary',status='Pending',consent='yes')
        app.save('agent',agent)
        app.save('pu',dict(self.pu,id='1',voters='300'))
        app.save('agent',dict(agent,id='1',status='Trained'))
        self.assertIn('1/1',app.page())
        with self.assertRaises(ValueError): app.save('pu',dict(self.pu,id='1',voters='-4'))
        with self.assertRaises(ValueError): app.save('pu',dict(self.pu,id='999'))
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT voters FROM pus').fetchone()[0],300)
            self.assertEqual(db.execute('SELECT pu_id FROM agents').fetchone()[0],1)
    def test_import_atomic_existing_duplicate(self):
        app.save('pu',self.pu)
        text=self.csv_text([dict(self.pu,code='NEW'),self.pu])
        with self.assertRaises(sqlite3.IntegrityError): app.import_pus(text)
        with app.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM pus').fetchone()[0],1)
    def test_import_preview_and_commit(self):
        text=self.csv_text([self.pu,dict(self.pu,code='SECOND')])
        self.assertIn('Review PU import',app.import_preview(text))
        with app.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM pus').fetchone()[0],0)
        self.assertEqual(app.import_pus(text),2)
        with self.assertRaises(ValueError): app.import_preview(text)
    def test_import_rejects_malformed_rows(self):
        for text in ['bad,header\n1,2',self.csv_text([dict(self.pu,voters='1.5')]),self.csv_text([self.pu,self.pu]),','.join(app.PU_COLUMNS)+'\nApa,Missing']:
            with self.subTest(text=text),self.assertRaises(ValueError): app.import_pus(text)
        with app.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM pus').fetchone()[0],0)

if __name__=='__main__': unittest.main()
