"""Read SQL Server Audit records through ODBC; never execute captured SQL text.

This first integration deliberately replays retained files. Source identities make
replay idempotent, avoiding unsafe timestamp or buffer-offset skipping. Benchmark
scan cost before production; a busy server needs a validated incremental reader.
"""
import argparse
import hashlib
import json
import os
import time
from contextlib import closing
from datetime import datetime, timezone

from storage import MODE, connect, initialize_store


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def serializable(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat(timespec="microseconds") if value.tzinfo is None else value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def settings():
    source = os.environ.get("SQL_SOURCE_ID", "").strip()
    connection = os.environ.get("SQL_CONNECTION_STRING", "").strip()
    pattern = os.environ.get("SQL_AUDIT_FILE_PATTERN", "").strip()
    if not source or not connection or not pattern:
        raise ValueError("Set SQL_SOURCE_ID, SQL_CONNECTION_STRING and SQL_AUDIT_FILE_PATTERN in .env")
    if len(pattern) > 260:
        raise ValueError("SQL_AUDIT_FILE_PATTERN exceeds SQL Server's 260-character limit")
    return source, connection, pattern


def set_state(source, status, detail, version=None, edition=None, success=False):
    now = utc_now()
    with connect() as db:
        db.execute("""INSERT INTO collector_state(source_id,last_attempt,last_success,status,detail,server_version,edition)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
        last_attempt=excluded.last_attempt, last_success=COALESCE(excluded.last_success,collector_state.last_success),
        status=excluded.status, detail=excluded.detail,
        server_version=COALESCE(excluded.server_version,collector_state.server_version),
        edition=COALESCE(excluded.edition,collector_state.edition)""",
        (source, now, now if success else None, status, detail, version, edition))


def ingest(source, record):
    """Commit an event and its deduplication identity in the same transaction."""
    raw = json.dumps(record, sort_keys=True, ensure_ascii=False, default=serializable)
    key = hashlib.sha256((source + "\0" + raw).encode("utf-8")).hexdigest()
    event_time = record.get("event_time")
    if not isinstance(event_time, datetime):
        raise ValueError("Audit row has no valid event_time")
    file_name = record.get("file_name")
    if not file_name or record.get("audit_file_offset") is None:
        raise ValueError("Audit row lacks file identity")
    action = str(record.get("action_id") or "").strip()
    # Label the audited action, not an inferred execution outcome. Preserve raw ID.
    operation = {"IN": "INSERT", "UP": "UPDATE", "DL": "DELETE", "CR": "CREATE", "AL": "ALTER", "DR": "DROP"}.get(action, action or "UNKNOWN")
    outcome = "Permission check passed" if record.get("succeeded") else "Permission check failed"
    now = utc_now()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM source_records WHERE record_key=?", (key,)).fetchone():
            return False
        event_id = db.execute("""INSERT INTO audit_events(event_time,server_name,database_name,login_name,effective_user,
        application_name,client_host,schema_name,object_name,operation,outcome,statement)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            serializable(event_time), record.get("server_instance_name") or "",
            record.get("database_name") or "", record.get("session_server_principal_name") or record.get("server_principal_name") or "",
            record.get("database_principal_name") or record.get("server_principal_name") or "",
            record.get("application_name") or "", record.get("host_name") or record.get("client_ip") or "",
            record.get("schema_name") or "", record.get("object_name") or "", operation, outcome, record.get("statement") or ""
        )).lastrowid
        group = record.get("sequence_group_id")
        db.execute("INSERT INTO source_records VALUES(?,?,?,?,?,?,?,?,?,?)", (
            key, event_id, source, file_name, record["audit_file_offset"], record.get("sequence_number"),
            serializable(group) if group is not None else "", action, now, raw))
        db.execute("""INSERT INTO source_files VALUES(?,?,?,?) ON CONFLICT(source_id,file_name) DO UPDATE SET
        max_offset=MAX(source_files.max_offset,excluded.max_offset),last_seen=excluded.last_seen""",
        (source, file_name, record["audit_file_offset"], now))
    return True


def poll_once(check_only=False, connect_sql=None):
    source, connection_string, pattern = settings()
    set_state(source, "Connecting", "Opening authenticated SQL Server connection")
    if connect_sql is None:
        import pyodbc
        connect_sql = pyodbc.connect
    # Closing explicitly matters: an ODBC connection context alone does not close it.
    with closing(connect_sql(connection_string, timeout=15, autocommit=True)) as sql:
        sql.timeout = int(os.environ.get("SQL_QUERY_TIMEOUT", "120"))
        with closing(sql.cursor()) as cursor:
            cursor.execute("SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int), CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)), CAST(SERVERPROPERTY('Edition') AS nvarchar(128)), CAST(SERVERPROPERTY('EngineEdition') AS int)")
            major, version, edition, engine = cursor.fetchone()
            if major is None or int(major) < 15 or int(engine) in (5, 6, 8, 11):
                raise ValueError("Target must be an installed SQL Server 2019+ instance; managed Azure services are outside this adapter")
            set_state(source, "Reading", "Reading retained audit files; coverage requires DBA validation", version, edition)
            # No record cap or unsafe offset seek: keep fragments and late records.
            query = "SELECT TOP (1) * FROM sys.fn_get_audit_file(?, NULL, NULL)" if check_only else "SELECT * FROM sys.fn_get_audit_file(?, NULL, NULL)"
            cursor.execute(query, (pattern,))
            columns = [column[0] for column in cursor.description]
            inserted = scanned = 0
            while True:
                batch = cursor.fetchmany(500)
                if not batch:
                    break
                for row in batch:
                    scanned += 1
                    if not check_only:
                        inserted += ingest(source, dict(zip(columns, row)))
            detail = ("Connection and audit-file read succeeded; no records imported" if check_only else f"Read {scanned} records; stored {inserted} new records. Retained-file replay enabled.")
            set_state(source, "Check passed" if check_only else "Last scan succeeded", detail, version, edition, success=not check_only)
            return {"source": source, "version": version, "edition": edition, "scanned": scanned, "inserted": inserted}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check version, connection and ability to read the configured audit files")
    parser.add_argument("--once", action="store_true", help="Import one pass and exit")
    args = parser.parse_args()
    if MODE != "live":
        parser.error("Set AUDIT_MODE=live before collecting SQL Server events")
    initialize_store()
    source, _, _ = settings()
    interval = max(1, int(os.environ.get("COLLECTOR_INTERVAL_SECONDS", "10")))
    while True:
        try:
            print(json.dumps(poll_once(check_only=args.check)), flush=True)
            if not args.check:
                from report_scheduler import run_due_reports
                reporting = run_due_reports()
                if reporting["status"] != "Disabled" and (reporting["status"] == "Error" or reporting.get("generated")):
                    print(json.dumps({"monthly_reporting": reporting}), flush=True)
                if reporting["status"] == "Error" and args.once:
                    raise SystemExit(1)
        except KeyboardInterrupt:
            set_state(source, "Stopped", "Collector stopped by operator")
            return
        except Exception as error:
            # Do not print driver messages: they may contain credentials or query text.
            state = str(error.args[0]) if error.args else ""
            sqlstate = state if len(state) == 5 and state.isalnum() else "n/a"
            detail = f"Collection failed ({type(error).__name__}; SQLSTATE {sqlstate}). Check connection, driver, certificate, permissions and audit-file path. Existing records are retained; retries replay retained files."
            set_state(source, "Error", detail)
            print(detail, flush=True)
            if args.check or args.once:
                raise SystemExit(1)
        if args.check or args.once:
            return
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            set_state(source, "Stopped", "Collector stopped by operator")
            return


if __name__ == "__main__":
    main()
