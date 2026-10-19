import json
import unittest
import app
import test_app

class DirectoryTests(unittest.TestCase):
    setUp=test_app.PortalTests.setUp
    tearDown=test_app.PortalTests.tearDown
    def test_snapshot_and_repeat_start(self):
        app.init_db(seed_directory=True)
        app.init_db(seed_directory=True)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pus').fetchone()[0],236)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM (SELECT DISTINCT lga,ward FROM pus)').fetchone()[0],21)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pus WHERE voters IS NULL').fetchone()[0],236)
            pu=dict(db.execute("SELECT * FROM pus WHERE code='07/02/01/001'").fetchone())
        pu['voters']='123';pu['source']='Sample';pu['source_date']='2026-10-19';pu={k:str(v) for k,v in pu.items()}
        app.save('pu',pu);app.init_db(seed_directory=True)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT voters FROM pus WHERE code='07/02/01/001'").fetchone()[0],123)
        self.assertIn('Not recorded',app.page())
    def test_mismatched_hierarchy_rejected(self):
        app.save('pu',self.pu)
        data=dict(pu_id='1',name='Example',phone='08000000000',role='Primary',status='Pending',consent='yes',location_lga='Apa',location_ward='Wrong')
        with self.assertRaises(ValueError): app.save('agent',data)
        data.update(location_lga='Agatu',location_ward='Sample ward')
        app.save('agent',data)
    def test_old_schema_migration_preserves_links(self):
        app.save('pu',self.pu)
        app.save('agent',dict(pu_id='1',name='Example',phone='08000000000',role='Primary',status='Pending',consent='yes'))
        with app.connect() as db:
            db.execute('PRAGMA foreign_keys=OFF')
            db.execute('BEGIN')
            db.execute("CREATE TABLE old_pus(id INTEGER PRIMARY KEY,lga TEXT NOT NULL CHECK(lga IN ('Apa','Agatu')),ward TEXT NOT NULL,code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,voters INTEGER NOT NULL CHECK(voters>=0),source TEXT NOT NULL,source_date TEXT NOT NULL)")
            db.execute('INSERT INTO old_pus SELECT * FROM pus');db.execute('DROP TABLE pus');db.execute('ALTER TABLE old_pus RENAME TO pus')
        app.init_db(seed_directory=True)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT pu_id FROM agents').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT voters FROM pus WHERE id=1').fetchone()[0],200)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

if __name__=='__main__':unittest.main()
