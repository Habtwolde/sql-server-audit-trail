import unittest
from unittest.mock import MagicMock, patch
import storage
from migrate_store import fingerprint


class SqlStoreTests(unittest.TestCase):
    def test_row_mapping_and_empty_result(self):
        cursor = MagicMock()
        cursor.description = [("id",), ("statement",)]
        cursor.fetchone.side_effect = [(123, "ሙከራ <script>"), None]
        result = storage.SqlResult(cursor)
        row = result.fetchone()
        self.assertEqual(row[0], 123)
        self.assertEqual(row["statement"], "ሙከራ <script>")
        self.assertEqual(row.keys(), ["id", "statement"])
        self.assertIsNone(result.fetchone())

    def test_lock_failures_propagate(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = (-1,)
        with patch.object(storage, "BACKEND", "sqlserver"), self.assertRaises(TimeoutError):
            storage.begin_write(db)

    def test_sqlserver_identity_and_limits(self):
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = (42,)
        with patch.object(storage, "BACKEND", "sqlserver"):
            self.assertEqual(storage.insert_id(db, "INSERT INTO reports(period) VALUES(?)", ("2026-10",)), 42)
            self.assertIn("OUTPUT INSERTED.id", db.execute.call_args.args[0])
            self.assertEqual(storage.limited("SELECT * FROM audit_events ORDER BY id", 8),
                             "SELECT TOP (8) * FROM audit_events ORDER BY id")
            self.assertIn("CI_AS", storage.case_insensitive("login_name"))

    def test_connection_rolls_back_and_closes_on_error(self):
        raw = MagicMock()
        with patch.object(storage, "BACKEND", "sqlserver"), patch("pyodbc.connect", return_value=raw):
            with self.assertRaises(ValueError):
                with storage.connect():
                    raise ValueError("failure")
        raw.rollback.assert_called_once()
        raw.commit.assert_not_called()
        raw.close.assert_called_once()

    def test_connection_commits_and_closes(self):
        raw = MagicMock()
        with patch.object(storage, "BACKEND", "sqlserver"), patch("pyodbc.connect", return_value=raw):
            with storage.connect():
                pass
        raw.commit.assert_called_once()
        raw.close.assert_called_once()

    def test_fingerprint_checks_contents_in_any_order(self):
        rows = [(1, "ሙከራ", None), (2, "sql", 123)]
        self.assertEqual(fingerprint(rows), fingerprint(reversed(rows)))
        self.assertNotEqual(fingerprint(rows), fingerprint([(1, "changed", None), rows[1]]))
        self.assertNotEqual(fingerprint(rows), fingerprint(rows + rows))
