"""Copy a stopped SQLite application repository into an empty SQL Server store."""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from storage import BACKEND, MODE, begin_write, connect, initialize_store

# Parent tables precede children. Identifiers are fixed, never user input.
TABLES = {
    "app_users": ("username,password_hash,role", False),
    "audit_events": ("id,event_time,server_name,database_name,login_name,effective_user,application_name,client_host,schema_name,object_name,operation,outcome,statement", True),
    "reports": ("id,period,version,created_at,created_by,event_count,file_path,sha256,status", True),
    "source_records": ("record_key,event_id,source_id,file_name,file_offset,sequence_number,sequence_group_id,action_id,collected_at,raw_json", False),
    "source_files": ("source_id,file_name,max_offset,last_seen", False),
    "report_reviews": ("id,report_id,reviewer,decision,comment,decided_at", True),
    "scheduled_reports": ("period,report_id", False),
    "report_scheduler_state": ("id,status,last_attempt,last_success,detail", False),
    "collector_state": ("source_id,last_attempt,last_success,status,detail,server_version,edition", False),
}


def fingerprint(rows):
    """Order-independent, bounded-memory count and SHA-256 multiset checksum."""
    count = total = xor = 0
    for row in rows:
        digest = int.from_bytes(hashlib.sha256(json.dumps(
            list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8")).digest(), "big")
        count += 1
        total = (total + digest) % (1 << 256)
        xor ^= digest
    return count, total, xor


def migrate(path):
    if BACKEND != "sqlserver":
        raise ValueError("Set AUDIT_STORE_CONNECTION_STRING before migrating")
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("BEGIN")  # One consistent read snapshot; workers must stay stopped.
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        if source.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite foreign-key check failed")
        if source.execute("SELECT mode FROM repository_identity").fetchall() != [(MODE,)]:
            raise ValueError("Source mode does not match AUDIT_MODE")
        initialize_store()
        with connect() as target:
            begin_write(target)
            for table in TABLES:
                if target.execute(f"SELECT COUNT(*) FROM dbo.{table}").fetchone()[0]:
                    raise ValueError("Destination is not empty; migration aborted")
            counts = {}
            for table, (columns, identity) in TABLES.items():
                if identity:
                    target.execute(f"SET IDENTITY_INSERT dbo.{table} ON")
                try:
                    query = f"SELECT {columns} FROM {table}"
                    rows = source.execute(query)
                    count = 0
                    for row in rows:
                        target.execute(f"INSERT INTO dbo.{table}({columns}) VALUES(" +
                                       ",".join("?" for _ in row) + ")", row)
                        count += 1
                    expected = fingerprint(source.execute(query))
                    actual = fingerprint(target.execute(f"SELECT {columns} FROM dbo.{table}"))
                    if actual != expected:
                        raise ValueError(f"Verification failed for {table}")
                    counts[table] = count
                finally:
                    if identity:
                        target.execute(f"SET IDENTITY_INSERT dbo.{table} OFF")
        return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-sqlite", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps({"migrated": migrate(args.from_sqlite)}))
    except Exception as error:
        # Driver messages can contain data or credentials.
        print(f"Migration failed ({type(error).__name__}). No application rows were committed. Check source mode, empty destination, schema compatibility and permissions.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
