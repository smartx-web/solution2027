import unittest
from unittest.mock import patch
import app
import auth
import test_app

class HistoryTests(unittest.TestCase):
    setUp=test_app.PortalTests.setUp
    tearDown=test_app.PortalTests.tearDown
    def seed(self):
        app.save('pu',self.pu)
        app.save('agent',dict(pu_id='1',name='Example Agent',phone='08000000000',role='Primary',status='Confirmed',consent='yes'))
    def test_archive_restore_coverage_and_export(self):
        self.seed()
        self.assertEqual(app.filtered_records()[-1],1)
        app.set_agent_archived({'id':'1','confirmed':'yes'},True)
        self.assertEqual(app.filtered_records()[-1],0)
        self.assertEqual(app.filtered_records()[2],[])
        self.assertNotIn('Example Agent',app.export_csv('agents'))
        with self.assertRaises(ValueError): app.save('agent',dict(id='1',pu_id='1',name='Changed',phone='08000000000',role='Primary',status='Trained',consent='yes'))
        with self.assertRaises(ValueError): app.set_agent_archived({'id':'1','confirmed':'yes'},True)
        app.init_db()
        app.set_agent_archived({'id':'1','confirmed':'yes'},False)
        self.assertEqual(app.filtered_records()[-1],1)
        self.assertIn('Example Agent',app.export_csv('agents'))
    def test_activity_tracks_actor_and_changed_fields(self):
        with app.connect() as db: auth.configure(db,'test-admin','sample-test-password')
        self.seed()
        app.save('pu',dict(self.pu,id='1',voters='300'))
        app.save('pu',dict(self.pu,id='1',voters='300')) # no-op is not a change
        with app.connect() as db: logs=db.execute('SELECT * FROM activity_log ORDER BY id').fetchall()
        self.assertEqual(len(logs),3)
        self.assertEqual(logs[-1]['actor'],'test-admin')
        self.assertEqual(logs[-1]['summary'],'Changed fields: voters')
        self.assertNotIn('08000000000',str([dict(r) for r in logs]))
    def test_failed_actions_do_not_log(self):
        self.seed()
        with self.assertRaises(ValueError): app.set_agent_archived({'id':'1'},True)
        with self.assertRaises(ValueError): app.set_agent_archived({'id':'999','confirmed':'yes'},True)
        with self.assertRaises(ValueError): app.save('pu',dict(self.pu,id='1',voters='invalid'))
        with app.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM activity_log').fetchone()[0],2)
    def test_audit_failure_rolls_back_archive_and_save(self):
        self.seed()
        with patch.object(app,'log_activity',side_effect=RuntimeError('simulated audit failure')):
            with self.assertRaises(RuntimeError):app.set_agent_archived({'id':'1','confirmed':'yes'},True)
            with self.assertRaises(RuntimeError):app.save('pu',dict(self.pu,id='1',voters='300'))
        self.assertEqual(app.filtered_records()[-1],1)
        with app.connect() as db: self.assertEqual(db.execute('SELECT voters FROM pus').fetchone()[0],200)

if __name__=='__main__': unittest.main()
