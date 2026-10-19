import csv
import io
import unittest
from unittest.mock import patch
import app
import access
import test_app

class BaselineTests(unittest.TestCase):
    setUp=test_app.PortalTests.setUp
    tearDown=test_app.PortalTests.tearDown
    def test_loads_exact_baseline_once(self):
        app.init_db(seed_directory=True);app.init_db(seed_directory=True)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM voter_baselines').fetchone()[0],236)
            totals=dict(db.execute('SELECT p.lga,SUM(b.registered_voters) FROM pus p JOIN voter_baselines b ON b.pu_code=p.code GROUP BY p.lga').fetchall())
            self.assertEqual(totals,{'Apa':66720,'Agatu':64315})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM activity_log WHERE entity=?',('historical baseline',)).fetchone()[0],1)
        self.assertIn('131,035',app.page())
        self.assertIn('2023 registered voters',app.page())
        self.assertIn('historical baseline',app.page().lower())
    def test_preserves_sourced_counts_and_links(self):
        app.save('pu',dict(self.pu,code='07/02/01/001',voters='1000',source='Newer verified record',source_date='2026-10-19'))
        app.save('agent',dict(pu_id='1',name='Sample',phone='08000000000',role='Primary',status='Confirmed',consent='yes'))
        app.init_db(seed_directory=True)
        with app.connect() as db:
            pu=db.execute("SELECT * FROM pus WHERE code='07/02/01/001'").fetchone()
            self.assertEqual((pu['id'],pu['voters'],pu['source']),(1,1000,'Newer verified record'))
            self.assertEqual(db.execute('SELECT pu_id FROM agents').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT registered_voters FROM voter_baselines WHERE pu_code='07/02/01/001'").fetchone()[0],750)
        app.save('pu',dict(self.pu,id='1',code='07/02/01/001',voters='1200',source='Newer record',source_date='2026-10-19'))
        app.init_db(seed_directory=True)
        _,pus,_,_,_=app.filtered_records({'q':'07/02/01/001'})
        self.assertEqual((pus[0]['voters'],pus[0]['voters_2023']),(1200,750))
    def test_export_and_scope(self):
        app.init_db(seed_directory=True)
        context=access.current_user.set(dict(id=2,username='coord',role='coordinator',lga='Agatu',wards=['EGBA']))
        try:
            rows=list(csv.DictReader(io.StringIO(app.export_csv('pus').lstrip('\ufeff'))))
            self.assertEqual(len(rows),16)
            self.assertTrue(all(r['lga']=='Agatu' and r['ward']=='EGBA' for r in rows))
            self.assertEqual(sum(int(r['voters_2023']) for r in rows),9569)
            self.assertTrue(all(r['voters']=='' for r in rows))
            self.assertTrue(all('e38e3bc9' in r['baseline_source_url'] for r in rows))
        finally:access.current_user.reset(context)
    def test_conflicting_history_not_overwritten(self):
        app.init_db(seed_directory=True)
        with app.connect() as db:db.execute("UPDATE voter_baselines SET registered_voters=999 WHERE pu_code='07/02/01/001'")
        with self.assertRaises(ValueError):app.init_db(seed_directory=True)
        with app.connect() as db:self.assertEqual(db.execute("SELECT registered_voters FROM voter_baselines WHERE pu_code='07/02/01/001'").fetchone()[0],999)

if __name__=='__main__':unittest.main()
