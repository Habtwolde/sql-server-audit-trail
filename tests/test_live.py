import csv
import io
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock

# Isolate tests from real source settings, persisted data, and credentials.
_temp = tempfile.TemporaryDirectory()
os.environ.update(AUDIT_MODE="live", AUDIT_INSTANCE_DIR=_temp.name,
                  AUDIT_DB_PATH=os.path.join(_temp.name, "test.sqlite3"),
                  AUDIT_REPORT_DIR=os.path.join(_temp.name, "reports"),
                  FLASK_SECRET_KEY="test-secret-" * 8,
                  SQL_SOURCE_ID="test-source", SQL_CONNECTION_STRING="test-only",
                  SQL_AUDIT_FILE_PATTERN="test-only*.sqlaudit")
from app import app
from collector import ingest, poll_once
from storage import connect
from werkzeug.security import generate_password_hash


def record(**changes):
    result = dict(event_time=datetime(2026, 10, 28, 13, 1, 2), file_name="audit-file.sqlaudit",
                  audit_file_offset=1024, sequence_number=1, sequence_group_id=b"group-one",
                  action_id="UP  ", succeeded=True, server_instance_name="TEST-SQL",
                  database_name="TestDb", session_server_principal_name="DOMAIN\\actor",
                  server_principal_name="effective_login", database_principal_name="dbo",
                  application_name="test-client", host_name="test-host", schema_name="dbo",
                  object_name="Probe", statement="UPDATE dbo.Probe SET Value='test';")
    result.update(changes)
    return result


class LiveTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        with connect() as db:
            for table in ("report_reviews", "reports", "source_records", "source_files", "audit_events", "collector_state", "app_users"):
                db.execute("DELETE FROM " + table)
            for name, role in (("viewer", "Viewer"), ("reviewer", "Reviewer"), ("approver", "Approver")):
                db.execute("INSERT INTO app_users VALUES(?,?,?)", (name, generate_password_hash("test-password-123"), role))

    def token(self):
        self.client.get("/login")
        with self.client.session_transaction() as session:
            return session["csrf"]

    def login(self, username):
        response = self.client.post("/login", data={"username": username, "password": "test-password-123", "csrf_token": self.token()})
        self.assertEqual(response.status_code, 302)

    def post(self, path, **data):
        data["csrf_token"] = self.token()
        return self.client.post(path, data=data)

    def test_live_start_has_no_demo_seed(self):
        from app import initialize
        initialize()
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit_events").fetchone()[0], 0)

    def test_replay_preserves_fragments_and_same_timestamp_events(self):
        self.assertTrue(ingest("test-source", record()))
        self.assertFalse(ingest("test-source", record()))
        self.assertTrue(ingest("test-source", record(sequence_number=2, statement="second fragment")))
        self.assertTrue(ingest("test-source", record(sequence_group_id=b"another-event")))
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit_events").fetchone()[0], 3)
            row = db.execute("SELECT * FROM audit_events LIMIT 1").fetchone()
            self.assertEqual(row["login_name"], "DOMAIN\\actor")
            self.assertEqual(row["outcome"], "Permission check passed")

    def test_atomic_failure_does_not_leave_orphan_event(self):
        with connect() as db:
            db.execute("CREATE TRIGGER reject_evidence BEFORE INSERT ON source_records BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        try:
            with self.assertRaises(Exception):
                ingest("test-source", record())
            with connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM audit_events").fetchone()[0], 0)
        finally:
            with connect() as db:
                db.execute("DROP TRIGGER reject_evidence")

    def test_web_auth_csrf_and_read_only_role(self):
        self.assertEqual(self.client.get("/events").status_code, 302)
        self.assertEqual(self.client.post("/login", data={"username": "viewer"}).status_code, 400)
        self.login("viewer")
        self.assertEqual(self.client.get("/events").status_code, 200)
        self.assertEqual(self.post("/reports/generate", period="2026-10").status_code, 403)

    def test_escaped_evidence_and_report_month_boundary(self):
        ingest("test-source", record(statement="<script>alert(1)</script>", object_name="<img src=x>"))
        ingest("test-source", record(event_time=datetime(2026, 11, 1), sequence_group_id=b"next-month"))
        self.login("reviewer")
        page = self.client.get("/events").get_data(as_text=True)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertEqual(self.post("/reports/generate", period="2026-10").status_code, 302)
        with connect() as db:
            report = db.execute("SELECT * FROM reports").fetchone()
            self.assertEqual(report["event_count"], 1)
        response = self.client.get(f"/reports/{report['id']}/download")
        rows = list(csv.reader(io.StringIO(response.get_data().decode("utf-8-sig"))))
        response.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][12], "<script>alert(1)</script>")
        self.assertEqual(len(rows[0]), len(rows[1]))

    def test_approval_separation_and_decision_history(self):
        self.login("reviewer")
        self.post("/reports/generate", period="2026-10")
        with connect() as db:
            report_id = db.execute("SELECT id FROM reports").fetchone()[0]
        url = f"/reports/{report_id}/decision"
        self.assertEqual(self.post(url, decision="Approved").status_code, 403)
        self.post("/logout")
        self.login("approver")
        self.assertEqual(self.post(url, decision="Approved", comment="Checked").status_code, 302)
        self.assertEqual(self.post(url, decision="Rejected").status_code, 409)
        with connect() as db:
            self.assertEqual(db.execute("SELECT reviewer FROM report_reviews").fetchone()[0], "approver")

    def fake_sql(self, major=15):
        sql = MagicMock()
        cursor = sql.cursor.return_value
        cursor.fetchone.return_value = (major, str(major)+".0.1", "Express Edition", 4)
        sample = record()
        cursor.description = [(name,) for name in sample]
        cursor.fetchmany.side_effect = [[tuple(sample.values())], []]
        return sql

    def test_actual_adapter_uses_parameterized_audit_read(self):
        sql = self.fake_sql()
        result = poll_once(connect_sql=lambda *a, **kw: sql)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(sql.cursor.return_value.execute.call_args.args,
                         ("SELECT * FROM sys.fn_get_audit_file(?, NULL, NULL)", ("test-only*.sqlaudit",)))
        sql.close.assert_called_once()

    def test_pre_2019_rejected_and_check_does_not_import(self):
        with self.assertRaises(ValueError):
            poll_once(connect_sql=lambda *a, **kw: self.fake_sql(14))
        result = poll_once(check_only=True, connect_sql=lambda *a, **kw: self.fake_sql())
        self.assertEqual(result["inserted"], 0)
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit_events").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
