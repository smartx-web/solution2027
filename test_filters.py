import csv
import io
import unittest
import app
import test_app

class FilterTests(unittest.TestCase):
    setUp=test_app.PortalTests.setUp
    tearDown=test_app.PortalTests.tearDown
    def seed(self):
        app.save('pu',self.pu)
        app.save('pu',dict(self.pu,lga='Apa',ward='Other ward',code='OTHER',name='Other PU'))
        app.save('agent',dict(pu_id='1',name='Sample Agent',phone='08000000000',role='Primary',status='Confirmed',consent='yes'))
        app.save('report',dict(pu_id='1',category='Logistics',detail='Sample report'))
    def test_location_search_and_coverage(self):
        self.seed()
        for filters,count in [({'lga':'Apa'},1),({'lga':'Apa','ward':'Sample ward'},0),({'q':'sample agent'},1),({'q':'TEST-001'},1),({'q':'not found'},0),({'coverage':'covered'},1),({'coverage':'uncovered'},1)]:
            with self.subTest(filters=filters): self.assertEqual(len(app.filtered_records(filters)[1]),count)
        _,pus,agents,reports,covered=app.filtered_records({'q':'sample agent'})
        self.assertEqual((len(pus),len(agents),len(reports),covered),(1,1,1,1))
        self.assertIn('Find records',app.page(filters={'lga':'Apa'}))
    def test_filtered_export(self):
        self.seed()
        rows=list(csv.DictReader(io.StringIO(app.export_csv('agents',{'lga':'Agatu'}).lstrip('\ufeff'))))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['phone'],"'08000000000")
        self.assertEqual(len(list(csv.DictReader(io.StringIO(app.export_csv('agents',{'lga':'Apa'}).lstrip('\ufeff'))))),0)
    def test_export_unknown_and_formula_safety(self):
        app.init_db(seed_directory=True)
        rows=list(csv.DictReader(io.StringIO(app.export_csv('pus').lstrip('\ufeff'))))
        self.assertTrue(all(row['voters']=='' for row in rows))
        app.save('pu',dict(self.pu,name='=1+1',source=' @SUM(A1)'))
        rows=list(csv.DictReader(io.StringIO(app.export_csv('pus',{'q':'TEST-001'}).lstrip('\ufeff'))))
        self.assertEqual(rows[0]['name'],"'=1+1"); self.assertEqual(rows[0]['source'],"'@SUM(A1)")
    def test_reject_invalid_filters(self):
        for filters in ({'lga':'Invalid'},{'coverage':'invalid'},{'q':'x'*121}):
            with self.assertRaises(ValueError):app.filtered_records(filters)

if __name__=='__main__':unittest.main()
