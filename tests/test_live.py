import csv
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Isolate tests from real source settings, persisted data, and credentials.
_temp = tempfile.TemporaryDirectory()
os.environ.update(AUDIT_MODE="live", AUDIT_INSTANCE_DIR=_temp.name,
                  AUDIT_DB_PATH=os.path.join(_temp.name, "test.sqlite3"),
                  AUDIT_REPORT_DIR=os.path.join(_temp.name, "reports"),
                  FLASK_SECRET_KEY="test-secret-" * 8,
                  REPORT_ARCHIVE_BACKEND="local", SQL_SOURCE_ID="test-source", SQL_CONNECTION_STRING="test-only",
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
            for table in ("report_archives", "report_archive_state", "scheduled_reports", "report_scheduler_state", "report_reviews", "reports", "source_records", "source_files", "audit_events", "collector_state", "app_users"):
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

    def test_account_filter_and_visible_sql(self):
        ingest("test-source", record(statement="<script>visible SQL</script>"))
        ingest("test-source", record(sequence_number=2,
               session_server_principal_name="OtherAccount", action_id="IN  "))
        self.login("viewer")
        response = self.client.get("/events", query_string={"account": "domain\\actor", "operation": "UPDATE"})
        html = response.get_data(as_text=True)
        self.assertIn("&lt;script&gt;visible SQL&lt;/script&gt;", html)
        self.assertNotIn("<summary>View SQL</summary>", html)
        self.assertNotIn("<td>OtherAccount<br>", html)
        self.assertIn('type="checkbox" name="account"', html)
        self.assertNotIn('name="operation"', html)
        self.assertIn('1 selected', html)
        combined = self.client.get("/events", query_string=[
            ("account", "DOMAIN\\actor"), ("account", "OtherAccount"), ("operation", "DROP")
        ]).get_data(as_text=True)
        self.assertIn("<td>OtherAccount<br>", combined)
        self.assertIn("&lt;script&gt;visible SQL&lt;/script&gt;", combined)
        self.assertIn("2 selected", combined)
        self.assertEqual(combined.count(' checked>'), 2)
        self.assertIn("No audit records found", self.client.get(
            "/events", query_string={"account": "' OR 1=1 --"}).get_data(as_text=True))
        self.assertIn("OtherAccount", self.client.get("/events").get_data(as_text=True))

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

    def reporting_env(self, **changes):
        values = {"REPORT_SCHEDULE_ENABLED": "true",
                  "REPORT_SCHEDULE_START_MONTH": "2026-10",
                  "REPORT_SCHEDULE_DELAY_HOURS": "1"}
        values.update(changes)
        return patch.dict(os.environ, values)

    def test_scheduled_month_boundary_and_restart_idempotence(self):
        from report_scheduler import run_due_reports, due_periods
        from reporting import SCHEDULE_ACTOR
        from pathlib import Path
        import hashlib
        ingest("test-source", record())
        ingest("test-source", record(event_time=datetime(2026, 11, 1), sequence_group_id=b"nov"))
        with self.reporting_env():
            self.assertEqual(due_periods(datetime(2026, 11, 1, 0, 59, tzinfo=timezone.utc)), [])
            now = datetime(2026, 11, 1, 1, tzinfo=timezone.utc)
            result = run_due_reports(now)
            self.assertEqual(len(result["generated"]), 1)
            self.assertEqual(run_due_reports(now)["generated"], [])
        with connect() as db:
            report = db.execute("SELECT * FROM reports").fetchone()
            self.assertEqual(report["created_by"], SCHEDULE_ACTOR)
            self.assertEqual(report["event_count"], 1)
            self.assertEqual(report["status"], "Pending")
            self.assertEqual(db.execute("SELECT count(*) FROM scheduled_reports").fetchone()[0], 1)
        data = Path(report["file_path"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), report["sha256"])
        self.assertEqual(len(list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))), 2)
        self.login("approver")
        page = self.client.get("/reports")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Automatic monthly reporting", page.get_data(as_text=True))
        self.assertIn(SCHEDULE_ACTOR, page.get_data(as_text=True))
        self.assertEqual(self.post(f"/reports/{report['id']}/decision", decision="Approved").status_code, 302)

    def test_scheduled_catchup_empty_month_and_manual_version(self):
        from report_scheduler import run_due_reports
        from reporting import generate_monthly_report
        manual = generate_monthly_report("2026-10", "reviewer")
        with self.reporting_env():
            result = run_due_reports(datetime(2027, 1, 1, 1, tzinfo=timezone.utc))
        self.assertEqual([r["period"] for r in result["generated"]], ["2026-10", "2026-11", "2026-12"])
        self.assertEqual(result["generated"][0]["version"], 2)
        self.assertTrue(all(r["records"] == 0 for r in result["generated"]))
        with connect() as db:
            self.assertEqual(db.execute("SELECT status FROM reports WHERE id=?", (manual["report_id"],)).fetchone()[0], "Pending")

    def test_scheduled_failure_rolls_back_and_retries(self):
        from report_scheduler import run_due_reports
        from storage import REPORT_DIR
        now = datetime(2026, 11, 1, 1, tzinfo=timezone.utc)
        before = set(REPORT_DIR.iterdir())
        with self.reporting_env(), patch("reporting.os.replace", side_effect=OSError("private path")):
            result = run_due_reports(now)
        self.assertEqual(result["status"], "Error")
        self.assertNotIn("private path", result["detail"])
        self.assertEqual(set(REPORT_DIR.iterdir()), before)
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM scheduled_reports").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM reports").fetchone()[0], 0)
        with self.reporting_env():
            self.assertEqual(len(run_due_reports(now)["generated"]), 1)
        with connect() as db:
            self.assertEqual(db.execute("SELECT status FROM report_scheduler_state").fetchone()[0], "Ready")

    def test_scheduled_disabled_and_invalid_config(self):
        from report_scheduler import run_due_reports
        now = datetime(2026, 11, 1, 1, tzinfo=timezone.utc)
        with self.reporting_env(REPORT_SCHEDULE_ENABLED="false"):
            self.assertEqual(run_due_reports(now)["status"], "Disabled")
        with self.reporting_env(REPORT_SCHEDULE_START_MONTH=""):
            self.assertEqual(run_due_reports(now)["status"], "Error")
        with self.reporting_env(REPORT_SCHEDULE_DELAY_HOURS="-1"):
            self.assertEqual(run_due_reports(now)["status"], "Error")
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reports").fetchone()[0], 0)

    def test_scheduled_concurrent_workers_generate_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from reporting import generate_monthly_report, SCHEDULE_ACTOR
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: generate_monthly_report(
                "2026-10", SCHEDULE_ACTOR, scheduled=True), range(2)))
        self.assertEqual(sum(r["created"] for r in results), 1)
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reports").fetchone()[0], 1)

    def test_reporting_leap_year_and_open_month_excluded(self):
        from report_scheduler import due_periods
        with self.reporting_env(REPORT_SCHEDULE_START_MONTH="2028-02"):
            self.assertEqual(due_periods(datetime(2028, 2, 29, 23, tzinfo=timezone.utc)), [])
            self.assertEqual(due_periods(datetime(2028, 3, 1, 1, tzinfo=timezone.utc)), ["2028-02"])

    def test_collector_only_schedules_after_successful_full_scan(self):
        from collector import main
        with patch("sys.argv", ["collector.py", "--check"]), patch("collector.poll_once", return_value={}), patch("report_scheduler.run_due_reports") as scheduled, patch("builtins.print"):
            main()
            scheduled.assert_not_called()
        with patch("sys.argv", ["collector.py", "--once"]), patch("collector.poll_once", side_effect=ValueError("scan failed")), patch("report_scheduler.run_due_reports") as scheduled, patch("builtins.print"):
            with self.assertRaises(SystemExit):
                main()
            scheduled.assert_not_called()
        with patch("sys.argv", ["collector.py", "--once"]), patch("collector.poll_once", return_value={}), patch("report_scheduler.run_due_reports", return_value={"status": "Ready", "generated": []}) as scheduled, patch("builtins.print"):
            main()
            scheduled.assert_called_once()

    def archive_env(self, **changes):
        values = {"REPORT_ARCHIVE_BACKEND": "s3", "REPORT_S3_BUCKET": "audit-test-bucket",
                  "REPORT_S3_PREFIX": "pilot", "REPORT_S3_ENCRYPTION": "AES256"}
        values.update(changes)
        return patch.dict(os.environ, values)

    def new_report(self):
        from reporting import generate_monthly_report
        result = generate_monthly_report("2026-10", "reviewer")
        with connect() as db:
            return db.execute("SELECT * FROM reports WHERE id=?", (result["report_id"],)).fetchone()

    def test_s3_history_upload_checksum_encryption_and_no_duplicate(self):
        from report_archive import sync_archives
        import base64
        report = self.new_report()
        client = MagicMock()
        client.put_object.return_value = {"VersionId": "version-one"}
        with self.archive_env():
            self.assertEqual(sync_archives(client)["uploaded"], 1)
            self.assertEqual(sync_archives(client)["uploaded"], 0)
        client.put_object.assert_called_once()
        args = client.put_object.call_args.kwargs
        self.assertEqual(args["Bucket"], "audit-test-bucket")
        self.assertEqual(args["IfNoneMatch"], "*")
        self.assertEqual(args["ServerSideEncryption"], "AES256")
        self.assertEqual(args["ChecksumSHA256"], base64.b64encode(bytes.fromhex(report["sha256"])).decode())
        self.assertTrue(args["Key"].startswith("pilot/live/2026/10/"))
        self.assertEqual(args["Body"], __import__("pathlib").Path(report["file_path"]).read_bytes())
        with connect() as db:
            self.assertEqual(db.execute("SELECT status FROM reports").fetchone()[0], "Pending")
            self.assertEqual(db.execute("SELECT version_id FROM report_archives").fetchone()[0], "version-one")

    def test_s3_failure_retries_original_destination_without_losing_local_report(self):
        from report_archive import sync_archives
        from pathlib import Path
        report = self.new_report()
        client = MagicMock()
        client.put_object.side_effect = OSError("private credentials")
        with self.archive_env():
            self.assertEqual(sync_archives(client)["failed"], 1)
        self.assertTrue(Path(report["file_path"]).is_file())
        client.put_object.side_effect = None
        client.put_object.return_value = {}
        with self.archive_env(REPORT_S3_BUCKET="new-bucket"):
            self.assertEqual(sync_archives(client)["uploaded"], 1)
        self.assertEqual(client.put_object.call_args.kwargs["Bucket"], "audit-test-bucket")
        with connect() as db:
            self.assertEqual(db.execute("SELECT error FROM report_archives").fetchone()[0], "")

    def test_s3_existing_object_is_verified_after_uncertain_upload(self):
        from report_archive import sync_archives
        from pathlib import Path
        report = self.new_report()
        error = RuntimeError("already exists")
        error.response = {"Error": {"Code": "PreconditionFailed"}}
        client = MagicMock()
        client.put_object.side_effect = error
        client.get_object.return_value = {"Body": io.BytesIO(Path(report["file_path"]).read_bytes()),
                                          "VersionId": "existing-version"}
        with self.archive_env():
            self.assertEqual(sync_archives(client)["uploaded"], 1)
        client.get_object.assert_called_once()
        with connect() as db:
            self.assertEqual(db.execute("SELECT version_id FROM report_archives").fetchone()[0], "existing-version")

    def test_s3_conflicting_remote_bytes_are_not_accepted(self):
        from report_archive import sync_archives
        self.new_report()
        error = RuntimeError("already exists")
        error.response = {"Error": {"Code": "412"}}
        client = MagicMock()
        client.put_object.side_effect = error
        client.get_object.return_value = {"Body": io.BytesIO(b"wrong csv")}
        with self.archive_env():
            self.assertEqual(sync_archives(client)["failed"], 1)
        with connect() as db:
            self.assertEqual(db.execute("SELECT status FROM report_archives").fetchone()[0], "Error")

    def test_s3_local_corruption_is_rejected_before_upload_and_download(self):
        from report_archive import sync_archives
        from pathlib import Path
        report = self.new_report()
        Path(report["file_path"]).write_bytes(b"modified report")
        client = MagicMock()
        with self.archive_env():
            self.assertEqual(sync_archives(client)["failed"], 1)
        client.put_object.assert_not_called()
        self.login("viewer")
        response = self.client.get(f"/reports/{report['id']}/download")
        self.assertEqual(response.status_code, 503)

    def test_missing_local_csv_can_download_verified_s3_version(self):
        from report_archive import sync_archives
        from pathlib import Path
        report = self.new_report()
        data = Path(report["file_path"]).read_bytes()
        client = MagicMock()
        client.put_object.return_value = {"VersionId": "pinned-version"}
        with self.archive_env():
            sync_archives(client)
        Path(report["file_path"]).unlink()
        client.get_object.return_value = {"Body": io.BytesIO(data)}
        self.login("viewer")
        with patch("report_archive.s3_client", return_value=client):
            response = self.client.get(f"/reports/{report['id']}/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), data)
        response.close()
        self.assertEqual(client.get_object.call_args.kwargs["VersionId"], "pinned-version")

    def test_archive_local_mode_and_config_validation(self):
        from report_archive import sync_archives
        self.new_report()
        client = MagicMock()
        with patch.dict(os.environ, {"REPORT_ARCHIVE_BACKEND": "local"}):
            self.assertEqual(sync_archives(client)["status"], "Local")
        client.put_object.assert_not_called()
        with self.archive_env(REPORT_S3_BUCKET=""):
            self.assertEqual(sync_archives(client)["status"], "Error")
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM report_archives").fetchone()[0], 0)

    def test_s3_request_matches_official_sdk_model(self):
        import boto3
        from botocore.stub import Stubber, ANY
        from report_archive import sync_archives
        self.new_report()
        client = boto3.client("s3", region_name="us-east-1",
                              aws_access_key_id="test", aws_secret_access_key="test")
        with Stubber(client) as stub:
            stub.add_response("put_object", {"VersionId": "one"},
                              {"Bucket": "audit-test-bucket", "Key": ANY, "Body": ANY,
                               "ContentType": "text/csv; charset=utf-8", "IfNoneMatch": "*",
                               "ChecksumSHA256": ANY, "ServerSideEncryption": "AES256",
                               "Metadata": ANY})
            with self.archive_env():
                self.assertEqual(sync_archives(client)["uploaded"], 1)
            stub.assert_no_pending_responses()

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
