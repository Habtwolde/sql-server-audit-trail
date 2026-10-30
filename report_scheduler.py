"""Monthly reporting inside the collector process; no external scheduler needed."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from reporting import SCHEDULE_ACTOR, generate_monthly_report, month_bounds
from storage import MODE, connect, initialize_store


def schedule_settings():
    flag = os.environ.get("REPORT_SCHEDULE_ENABLED", "false").lower()
    if flag not in ("true", "false"):
        raise ValueError("REPORT_SCHEDULE_ENABLED must be true or false")
    if flag == "false":
        return None
    start = os.environ.get("REPORT_SCHEDULE_START_MONTH", "")
    month_bounds(start)
    delay = int(os.environ.get("REPORT_SCHEDULE_DELAY_HOURS", "1"))
    if not 0 <= delay <= 168:
        raise ValueError("Report delay must be between 0 and 168 hours")
    return start, delay


def due_periods(now=None):
    settings = schedule_settings()
    if settings is None:
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Scheduler clock must include a timezone")
    period, delay = settings
    periods = []
    with connect() as db:
        completed = {r[0] for r in db.execute("SELECT period FROM scheduled_reports")}
    while True:
        _, end = month_bounds(period)
        if end + timedelta(hours=delay) > now:
            break
        if period not in completed:
            periods.append(period)
        period = end.strftime("%Y-%m")
    return periods


def set_reporting_state(status, detail, now):
    with connect() as db:
        db.execute(
            "INSERT INTO report_scheduler_state(id,status,last_attempt,last_success,detail) "
            "VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status,last_attempt=excluded.last_attempt,"
            "last_success=COALESCE(excluded.last_success,report_scheduler_state.last_success),"
            "detail=excluded.detail",
            (status, now.isoformat(), now.isoformat() if status == "Ready" else None, detail)
        )


def run_due_reports(now=None):
    """Call after a successful full collection scan. Failed snapshots retry next cycle."""
    now = now or datetime.now(timezone.utc)
    try:
        if schedule_settings() is None:
            return {"status": "Disabled", "generated": []}
        if MODE != "live":
            raise ValueError("Automatic reporting requires live mode")
        generated = []
        for period in due_periods(now):
            result = generate_monthly_report(period, SCHEDULE_ACTOR, scheduled=True, now=now)
            if result["created"]:
                generated.append(result)
        set_reporting_state("Ready", f"Generated {len(generated)} report(s) this cycle. Reports await review.", now)
        return {"status": "Ready", "generated": generated}
    except Exception as error:
        # Do not expose connection strings, captured SQL or filesystem details.
        detail = f"Automatic reporting failed ({type(error).__name__}). Check report settings and storage; retry on next successful scan."
        set_reporting_state("Error", detail, now)
        return {"status": "Error", "detail": detail}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show configuration and due months without generating reports")
    group.add_argument("--run-due", action="store_true", help="Collect audit records, then generate due monthly reports")
    args = parser.parse_args()
    initialize_store()
    try:
        settings = schedule_settings()
        if args.status:
            print(json.dumps({"enabled": settings is not None, "timezone": "UTC",
                              "start_month": settings[0] if settings else None,
                              "delay_hours": settings[1] if settings else None,
                              "due_months": due_periods()}))
            return
        if MODE != "live":
            raise ValueError("Live mode required")
        if settings is None:
            print(json.dumps({"status": "Disabled", "generated": []}))
            return
        from collector import poll_once
        print(json.dumps({"collection": poll_once()}), flush=True)
        result = run_due_reports()
        print(json.dumps({"monthly_reporting": result}), flush=True)
        if result["status"] == "Error":
            raise SystemExit(1)
    except Exception as error:
        print(f"Report command failed ({type(error).__name__}); check configuration, collection and storage.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
