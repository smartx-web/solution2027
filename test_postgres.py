"""Portable adapter and safe migration boundary regression tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import sqlite3
import psycopg
import app
import database
import deploy
import transfer

class PostgresTests(unittest.TestCase):
    def test_parameters_rows_and_transaction_rollback(self):
        raw=MagicMock();raw.execute.return_value.fetchone.return_value={'id':7,'name':'Agent'}
        with patch('psycopg.connect',return_value=raw):
            with database.connect('postgresql://test') as db:
                row=db.execute('SELECT id,name FROM agents WHERE name=?',("O'Connor",)).fetchone()
                self.assertEqual((row[0],row['name']),(7,'Agent'))
            raw.execute.assert_called_with('SELECT id,name FROM agents WHERE name=%s',("O'Connor",))
            raw.commit.assert_called_once();raw.close.assert_called_once()
            raw.reset_mock()
            with self.assertRaises(ValueError):
                with database.connect('postgresql://test') as db:raise ValueError('cancel')
            raw.rollback.assert_called_once();raw.close.assert_called_once()
    def test_database_constraints_normalised(self):
        raw=MagicMock();raw.execute.side_effect=psycopg.errors.UniqueViolation('duplicate')
        with patch('psycopg.connect',return_value=raw):
            with self.assertRaises(sqlite3.IntegrityError):
                with database.connect('postgresql://test') as db:db.execute('INSERT INTO users(username) VALUES(?)',('duplicate',))
        raw.rollback.assert_called_once()
    def test_connection_configuration(self):
        values={'APP_ORIGIN':'https://portal.example','DATABASE_URL':'postgresql://user:secret@ep-test-pooler.neon.tech/portal?sslmode=require','DATABASE_URL_UNPOOLED':'postgresql://user:secret@ep-test.neon.tech/portal?sslmode=require'}
        with patch.dict(os.environ,values):
            self.assertIsNone(deploy.settings()[2])
            with patch.dict(os.environ,DATABASE_URL_UNPOOLED=values['DATABASE_URL']):
                with self.assertRaises(ValueError):deploy.settings()
            with patch.dict(os.environ,DATABASE_URL='postgresql://user:secret@ep-test.neon.tech/portal'):
                with self.assertRaises(ValueError):deploy.settings()
    def test_transfer_validation_and_populated_target_refusal(self):
        original=app.DB
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.sqlite3';app.DB=str(source)
            try:app.init_db(seed_directory=True)
            finally:app.DB=original
            counts=transfer.migrate(source,'postgresql://test')
            self.assertEqual(counts['pus'],236);self.assertEqual(counts['voter_baselines'],236)
            target=MagicMock();target.__enter__.return_value=target
            target.execute.return_value.fetchone.return_value=[1]
            with patch('database.init_postgres'),patch('database.connect',return_value=target):
                with self.assertRaises(ValueError):transfer.migrate(source,'postgresql://test',apply=True)
            target.executemany.assert_not_called()
    def test_health_does_not_wake_neon(self):
        with patch('app.connect',side_effect=AssertionError('health must not connect')):
            response=deploy.application({'PATH_INFO':'/healthz','REQUEST_METHOD':'GET'},lambda *args:None)
            self.assertEqual(response,[b'ok'])

if __name__=='__main__':unittest.main()
