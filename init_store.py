"""Initialize the configured application repository without starting the web app."""
import json
from storage import BACKEND, MODE, connect, initialize_store


def main():
    try:
        initialize_store()
        with connect() as db:
            counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("audit_events", "app_users", "reports", "report_reviews")}
            database = db.execute("SELECT DB_NAME()").fetchone()[0] if BACKEND == "sqlserver" else "SQLite"
        print(json.dumps({"backend": BACKEND, "database": database, "mode": MODE, "counts": counts}))
    except Exception as error:
        print(f"Repository initialization failed ({type(error).__name__}); check configuration, driver, database permissions and repository mode.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
