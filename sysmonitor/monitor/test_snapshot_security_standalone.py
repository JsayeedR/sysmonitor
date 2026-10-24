"""Standalone source-only regression test: run with python -m unittest monitor.test_snapshot_security_standalone."""
import os, sqlite3, tempfile, unittest
from pathlib import Path
from monitor import mirror_push

class SnapshotSecurityTest(unittest.TestCase):
    def test_consistent_snapshot_scrubs_secrets_and_restricts_mode(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)/'primary.sqlite3'; dest = Path(d)/'mirror.sqlite3'
            with sqlite3.connect(src) as conn:
                conn.executescript("""
                CREATE TABLE django_session(session_key TEXT);
                INSERT INTO django_session VALUES ('private-session');
                CREATE TABLE monitor_notificationgateway(wa_access_token TEXT, tg_bot_token TEXT, email_password TEXT);
                INSERT INTO monitor_notificationgateway VALUES ('wa-secret','tg-secret','mail-secret');
                CREATE TABLE shiftreport_shiftreportconfig(smtp_password TEXT);
                INSERT INTO shiftreport_shiftreportconfig VALUES ('smtp-secret');
                CREATE TABLE monitor_notificationrequest(pairing_code TEXT);
                INSERT INTO monitor_notificationrequest VALUES ('pairing-secret');
                CREATE TABLE auth_user(password TEXT);
                INSERT INTO auth_user VALUES ('hashed-password-needed-for-login');
                """)
            prior=mirror_push.DB_PATH
            try:
                mirror_push.DB_PATH=src
                mirror_push.make_snapshot(str(dest))
            finally:
                mirror_push.DB_PATH=prior
            self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(dest) as conn:
                self.assertEqual(conn.execute('select count(*) from django_session').fetchone()[0],0)
                self.assertEqual(conn.execute('select smtp_password from shiftreport_shiftreportconfig').fetchone()[0], '')
                self.assertEqual(conn.execute('select pairing_code from monitor_notificationrequest').fetchone()[0], '')
                self.assertEqual(conn.execute('select * from monitor_notificationgateway').fetchone(), ('','',''))
                self.assertEqual(conn.execute('select password from auth_user').fetchone()[0], 'hashed-password-needed-for-login')

if __name__ == '__main__': unittest.main()
