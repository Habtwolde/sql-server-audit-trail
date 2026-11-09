"""Application repository: SQL Server when configured; SQLite for legacy stores."""
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
STORE_CONNECTION_STRING = os.environ.get("AUDIT_STORE_CONNECTION_STRING", "").strip()
BACKEND = "sqlserver" if STORE_CONNECTION_STRING else "sqlite"
if BACKEND == "sqlite":
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

class Record:
    """Named and positional access shared by ODBC results and existing templates."""
    def __init__(self, columns, values):
        self._columns = columns
        self._values = tuple(values)
        self._lookup = dict(zip(columns, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, (int, slice)) else self._lookup[key]

    def keys(self):
        return self._columns

    def __iter__(self):
        return iter(self._values)


class SqlResult:
    def __init__(self, cursor):
        self.cursor = cursor
        self.columns = [c[0] for c in cursor.description] if cursor.description else []

    def fetchone(self):
        row = self.cursor.fetchone()
        return None if row is None else Record(self.columns, row)

    def fetchmany(self, size=500):
        return [Record(self.columns, row) for row in self.cursor.fetchmany(size)]

    def fetchall(self):
        return [Record(self.columns, row) for row in self.cursor.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class SqlStore:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(query, tuple(params))
            return SqlResult(cursor)
        except BaseException:
            cursor.close()
            raise


@contextmanager
def connect():
    if BACKEND == "sqlserver":
        import pyodbc
        db = pyodbc.connect(STORE_CONNECTION_STRING, timeout=15, autocommit=False)
        db.timeout = 120
        try:
            cursor = db.cursor()
            cursor.execute("SET XACT_ABORT ON; SET NOCOUNT ON; SET LOCK_TIMEOUT 30000;")
            cursor.close()
            yield SqlStore(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
    else:
        db = sqlite3.connect(DB_PATH, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()


def begin_write(db):
    """Serialize application writers across processes, including migration.

    This preserves the old SQLite atomicity contract. It is intentionally a
    correctness-first implementation, not a claim of 40-source throughput.
    """
    if BACKEND == "sqlite":
        db.execute("BEGIN IMMEDIATE")
    else:
        # Explicit BEGIN is necessary even before the first ODBC DML statement.
        result = db.execute("""IF @@TRANCOUNT = 0 BEGIN TRANSACTION;
        DECLARE @result int;
        EXEC @result = sys.sp_getapplock @Resource=N'SqlAuditTrail:write',
          @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=30000;
        SELECT @result;""").fetchone()[0]
        if result < 0:
            raise TimeoutError("Unable to acquire application database write lock")


def insert_id(db, query, params):
    if BACKEND == "sqlite":
        return db.execute(query, params).lastrowid
    head, values = query.split("VALUES", 1)
    return db.execute(head + " OUTPUT INSERTED.id VALUES" + values, params).fetchone()[0]


def limited(query, count):
    """Apply an internal, fixed row limit; never interpolate request values."""
    count = int(count)
    if not 1 <= count <= 500:
        raise ValueError("Invalid internal row limit")
    return (query.replace("SELECT ", f"SELECT TOP ({count}) ", 1)
            if BACKEND == "sqlserver" else query + f" LIMIT {count}")


def case_insensitive(column):
    # Column names supplied only by application code.
    if column != "login_name":
        raise ValueError("Unsupported case-insensitive column")
    return column + (" COLLATE Latin1_General_100_CI_AS" if BACKEND == "sqlserver" else " COLLATE NOCASE")


def upsert_state(db, table, keys, values, preserve=(), maximum=()):
    """Update state under the caller's write lock; identifiers are internal only."""
    allowed = {
        "collector_state": {"source_id", "last_attempt", "last_success", "status", "detail", "server_version", "edition"},
        "source_files": {"source_id", "file_name", "max_offset", "last_seen"},
        "report_scheduler_state": {"id", "status", "last_attempt", "last_success", "detail"},
    }
    if table not in allowed or not (set(keys) | set(values)) <= allowed[table]:
        raise ValueError("Unsupported state columns")
    where = " AND ".join(f"{key}=?" for key in keys)
    if db.execute(f"SELECT 1 FROM {table} WHERE {where}", tuple(keys.values())).fetchone():
        assignments, params = [], []
        for column, value in values.items():
            if column in preserve:
                assignments.append(f"{column}=COALESCE(?,{column})")
                params.append(value)
            elif column in maximum:
                assignments.append(f"{column}=CASE WHEN {column} IS NULL OR {column} < ? THEN ? ELSE {column} END")
                params.extend((value, value))
            else:
                assignments.append(f"{column}=?")
                params.append(value)
        db.execute(f"UPDATE {table} SET " + ",".join(assignments) + " WHERE " + where,
                   params + list(keys.values()))
    else:
        row = {**keys, **values}
        db.execute(f"INSERT INTO {table}(" + ",".join(row) + ") VALUES(" +
                   ",".join("?" for _ in row) + ")", tuple(row.values()))


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
        if BACKEND == "sqlserver":
            begin_write(db)
            db.execute((BASE_DIR / "sql" / "application_schema.sql").read_text(encoding="utf-8"))
        else:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)
            begin_write(db)
        identity = db.execute("SELECT mode FROM repository_identity").fetchone()
        if identity and identity[0] != MODE:
            raise RuntimeError("Repository belongs to a different mode; use a separate application database")
        if not identity:
            # An existing unlabelled database may contain the original demo seed.
            if MODE == "live" and db.execute("SELECT count(*) FROM audit_events").fetchone()[0]:
                raise RuntimeError("Refusing to treat existing unlabelled events as live data")
            db.execute("INSERT INTO repository_identity(mode) VALUES(?)", (MODE,))

