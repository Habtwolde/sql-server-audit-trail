"""Shared local audit repository. SQLite is for single-host integration deployments."""
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
MODE = os.environ.get("AUDIT_MODE", "live").lower()
if MODE not in {"live", "demo"}:
    raise RuntimeError("AUDIT_MODE must be live or demo")
INSTANCE_DIR = Path(os.environ.get("AUDIT_INSTANCE_DIR", BASE_DIR / "instance")).resolve()
DB_PATH = Path(os.environ.get("AUDIT_DB_PATH", INSTANCE_DIR / f"audit-{MODE}.sqlite3")).resolve()
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR", INSTANCE_DIR / "reports" / MODE)).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT NOT NULL,
    server_name TEXT NOT NULL,
    database_name TEXT NOT NULL,
    login_name TEXT NOT NULL,
    effective_user TEXT NOT NULL,
    application_name TEXT NOT NULL,
    client_host TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    object_name TEXT NOT NULL,
    operation TEXT NOT NULL,
    outcome TEXT NOT NULL,
    statement TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',
    UNIQUE(period, version)
);
CREATE TABLE IF NOT EXISTS scheduled_reports (
    period TEXT PRIMARY KEY,
    report_id INTEGER NOT NULL UNIQUE REFERENCES reports(id)
);
CREATE TABLE IF NOT EXISTS report_scheduler_state (
    id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL,
    last_attempt TEXT NOT NULL, last_success TEXT, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_users (
    username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collector_state (
    source_id TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT,
    status TEXT NOT NULL, detail TEXT NOT NULL, server_version TEXT, edition TEXT
);
CREATE TABLE IF NOT EXISTS source_records (
    record_key TEXT PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES audit_events(id),
    source_id TEXT NOT NULL, file_name TEXT NOT NULL, file_offset INTEGER,
    sequence_number INTEGER, sequence_group_id TEXT, action_id TEXT,
    collected_at TEXT NOT NULL, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_files (
    source_id TEXT NOT NULL, file_name TEXT NOT NULL, max_offset INTEGER,
    last_seen TEXT NOT NULL, PRIMARY KEY(source_id, file_name)
);
CREATE TABLE IF NOT EXISTS repository_identity (mode TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_time_idx ON audit_events(event_time);
CREATE INDEX IF NOT EXISTS source_event_idx ON source_records(event_id);
"""

def initialize_store():
    with connect() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)
        identity = db.execute("SELECT mode FROM repository_identity").fetchone()
        if identity and identity[0] != MODE:
            raise RuntimeError("Repository belongs to a different mode; use a separate AUDIT_DB_PATH")
        if not identity:
            # An existing unlabelled database may contain the original demo seed.
            if MODE == "live" and db.execute("SELECT count(*) FROM audit_events").fetchone()[0]:
                raise RuntimeError("Refusing to treat existing unlabelled events as live data")
            db.execute("INSERT INTO repository_identity(mode) VALUES(?)", (MODE,))
