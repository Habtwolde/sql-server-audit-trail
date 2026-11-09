# SQL Server application database

The Python web app, collector and reporting commands share a dedicated SQL Server
database when `AUDIT_STORE_CONNECTION_STRING` is non-empty. An invalid connection
fails; it never silently falls back to SQLite. Without this setting, legacy SQLite
stores remain supported so existing installs are not automatically switched.

SQL Server Developer edition is a development/test environment. Production must
use an appropriately licensed SQL Server edition. Installing Developer does not
migrate the old Express databases, logins or audit configurations.

## Local development setup

Create `SqlAuditTrail` on the installed Developer instance. In the project `.env`:

```dotenv
AUDIT_STORE_CONNECTION_STRING="DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost\MSSQLSERVER02;DATABASE=SqlAuditTrail;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;APP=SQLAuditTrailStore"
```

This setting is independent of `SQL_CONNECTION_STRING`, `SQL_SOURCE_ID` and
`SQL_AUDIT_FILE_PATTERN`, which identify the SQL Server **source being audited**.
Keep the original source ID when replaying its old files; changing it creates new
identities. To audit the new Developer instance, configure its audit and test
business database separately and give it a new source ID. Do not audit application
repository DML into the same collection stream: that can create a feedback loop.

The current source adapter still reads one source per worker/configuration. This
change does not add a 40-instance inventory, worker pool or incremental reader.
Do not claim 40-instance capacity without those changes and workload testing.

## Verify before switching

Run from the project with the virtual environment active:

```powershell
python -m pip install -r requirements.txt
python init_store.py
python tests/run_sqlserver.py
```

`init_store.py` creates the schema idempotently and shows backend, database, mode
and row counts without starting the app. `sql/application_schema.sql` is the same
schema and can be applied by a DBA in SSMS in the dedicated database.

The integration runner creates a uniquely named `SqlAuditTrail_test_*` database,
runs the live workflow tests using SQL Server storage, and removes only that test
database. The current Windows development user needs CREATE/DROP DATABASE rights
for this test. It does not read the source SQL audit files; audit reads are mocked.
Run a real source audit check and event demonstration separately after switching.

## Preserve existing data

1. Stop the old web app and all collectors/scheduled jobs. Keep them stopped until
   migration and configuration checks finish.
2. Back up `.env`, the SQLite repository (using the SQLite backup API), and CSVs.
3. Keep `AUDIT_REPORT_DIR` pointing to the existing report directory. Migration
   preserves absolute report paths; it does not move CSVs.
4. Set `AUDIT_STORE_CONNECTION_STRING` and keep the same `AUDIT_MODE` and
   `FLASK_SECRET_KEY`. Do not create app users in the destination before migration.
5. Run `python init_store.py`; all four displayed row counts must be zero.
6. Migrate using the actual SQLite file/backup path:

```powershell
python migrate_store.py --from-sqlite "C:\path\to\audit-live.sqlite3.backup-YYYYMMDD-HHMMSS"
```

The command opens SQLite read-only, validates integrity, foreign keys and mode,
refuses a non-empty target, and copies all application tables in one SQL Server
transaction. It preserves IDs, password hashes, source identities, report versions,
paths, hashes, decisions, scheduler history and collector status. Each table is
verified with an order-independent row count/content checksum before commit.
A second run refuses to append or duplicate data. Legacy unused archive tables
are not migrated. Schema and mode identity can remain after a failed migration;
application rows roll back. Driver details are deliberately not printed.

7. Run `python init_store.py` again and check migrated counts.
8. Start `python run_web.py`. Test existing login, Full record, CSV download,
   report version generation and approval with a different user.
9. Run `python collector.py --check`, then `python collector.py --once` and check
   the dashboard. The latter performs collection **and** the due-report check.
10. Start exactly one intended collector arrangement, then verify restart/replay
    does not duplicate records. Back up the new SQL database and report files.

For a genuinely fresh deployment, skip migration and create accounts with
`python manage.py create-user admin --role Administrator` (password prompted).

## SQL Server Agent

CmdExec can run the virtual-environment Python directly:

```bat
"C:\SqlAuditTrail\.venv\Scripts\python.exe" "C:\SqlAuditTrail\collector.py" --once
```

This one-pass command checks due monthly reports after successful collection.
`python report_scheduler.py --run-due` also collects and checks reporting, even
when automatic report generation is disabled. Schedule only one arrangement.
The continuous `python collector.py` is for a long-running service, not a recurring
job expected to finish. Keep the web process as its own Windows service.

## Permissions and operation

Integrated authentication uses the Windows identity running each process or Agent
proxy, not the person logged into the website. Provision that identity in the
central database with default schema `dbo`. For runtime it needs SELECT, INSERT,
UPDATE and DELETE on the application tables and permission to use the application
lock. First initialization needs schema/DDL rights; alternatively a DBA applies
the schema and mode identity first. Do not grant sysadmin to the runtime service.
Source audit-read permissions are separate. The web/collector identity also needs
report-folder access. Use trusted TLS certificates in production.

SQL Server now holds the repository; CSVs still live in the report directory.
Use SQL Server database backups, including log backups if FULL recovery is chosen,
and test restoration with the matching CSVs. Do not copy MDF/LDF files as a live
backup. For rollback before new writes, stop processes and remove the new setting
to return to the retained SQLite backup. After new writes, reverting loses access
to that newer history; reconcile data first.

This first backend preserves the original global application write-lock behavior
for correctness. Report snapshots still load all rows into memory and hold a write
lock through file publication. Retained-file scans, per-record connections,
retention, 40-source orchestration and production performance remain separate work.
