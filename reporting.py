"""Shared CSV snapshots for manual and automatic monthly reports."""
import csv
import hashlib
import io
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from security import csv_cell
from storage import REPORT_DIR, connect


SCHEDULE_ACTOR = "system:monthly-scheduler"


def month_bounds(period):
    start = datetime.strptime(period, "%Y-%m").replace(tzinfo=timezone.utc)
    if start.strftime("%Y-%m") != period or start.year >= 9999:
        raise ValueError("Invalid month")
    end = datetime(start.year + (start.month == 12),
                   1 if start.month == 12 else start.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def generate_monthly_report(period, created_by, scheduled=False, now=None):
    """Create one immutable snapshot; scheduled periods are unique across workers.

    The SQLite write lock covers allocation, snapshot, file publication and the
    scheduled-period marker. An interrupted run can leave an unreferenced file;
    retrying replaces that orphan before committing a report entry.
    """
    start, end = month_bounds(period)
    now = now or datetime.now(timezone.utc)
    published = temporary = None
    try:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if scheduled:
                existing = db.execute(
                    "SELECT report_id FROM scheduled_reports WHERE period=?", (period,)
                ).fetchone()
                if existing:
                    return {"report_id": existing[0], "period": period, "created": False}
            rows = db.execute(
                "SELECT e.*, s.source_id, s.file_name, s.file_offset, s.sequence_number, "
                "s.sequence_group_id, s.action_id, s.collected_at "
                "FROM audit_events e LEFT JOIN source_records s ON s.event_id=e.id "
                "WHERE e.event_time >= ? AND e.event_time < ? ORDER BY e.event_time, e.id",
                (start.isoformat(), end.isoformat())
            ).fetchall()
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM reports WHERE period=?", (period,)
            ).fetchone()[0]
            report_id = db.execute(
                "INSERT INTO reports(period,version,created_at,created_by,event_count,file_path,sha256,status) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (period, version, now.astimezone(timezone.utc).isoformat(), created_by,
                 len(rows), "", "", "Pending")
            ).lastrowid
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["Event ID","Event Time UTC","Server","Database","Login","Effective User",
                             "Application","Client Host","Schema","Object","Operation","Outcome",
                             "SQL Statement","Source ID","Audit File","File Offset","Sequence Number",
                             "Sequence Group","Action ID","Collected At UTC"])
            columns = ("id","event_time","server_name","database_name","login_name","effective_user",
                       "application_name","client_host","schema_name","object_name","operation","outcome",
                       "statement","source_id","file_name","file_offset","sequence_number",
                       "sequence_group_id","action_id","collected_at")
            for row in rows:
                writer.writerow([csv_cell(row[key]) for key in columns])
            data = output.getvalue().encode("utf-8-sig")
            digest = hashlib.sha256(data).hexdigest()
            file_path = REPORT_DIR / f"audit-{period}-v{version:03}.csv"
            with tempfile.NamedTemporaryFile(dir=REPORT_DIR, prefix=".report-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, file_path)
            temporary = None
            published = file_path
            db.execute("UPDATE reports SET file_path=?,sha256=? WHERE id=?",
                       (str(file_path), digest, report_id))
            if scheduled:
                db.execute("INSERT INTO scheduled_reports(period,report_id) VALUES(?,?)",
                           (period, report_id))
        return {"report_id": report_id, "period": period, "version": version,
                "records": len(rows), "created": True}
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if published is not None:
            published.unlink(missing_ok=True)
        raise
