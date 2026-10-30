# SQL Server Audit Trail

Live SQL Server Audit reader and web application for SQL Server **2019 and newer**. This is the first real-integration build, not a certified production release. It reads actual SQL Server audit files over ODBC, stores evidence separately from the demo, and produces monthly CSV reports for review.

**Start here: [Windows / live SQL Server setup](docs/LIVE_SETUP.md).**

## Current capabilities

- SQL Server version/edition detection and a connection/read diagnostic.
- Windows integrated authentication or SQL login for the collector connection.
- Independent polling worker; no DML or DDL triggers.
- Persistent record identities and atomic storage prevent duplicate imports on replay/restart.
- Original audit metadata, SQL text/fragments, account, effective user, timestamp, and available object/client information.
- Live audit view, collection status, evidence detail, monthly CSV export and approval history.
- Separate live and demo databases; live mode never seeds sample events.
- Password-based application accounts, roles, CSRF protection, and a separate report approver.
- Waitress web entry point for Windows/Linux; container definitions for on-premises/EC2 trials.

## Important boundaries

- Windows authentication for the **SQL connection** is implemented. Windows single sign-on for **web users** is a separate, pending integration. The web app currently uses local password accounts.
- The evidence store is currently SQLite on one host. A separate SQL Server/PostgreSQL audit repository is pending. Do not share its file across hosts or a network share.
- The worker replays retained files and deduplicates them. Per-file offsets are persisted for diagnostics, not used to skip records. This prioritizes safe replay in the integration build; performance must be assessed before production.
- SQL Server Audit `succeeded` is labelled as a permission-check result. It does not establish statement completion or transaction commit.
- SQL text can span multiple records. Fragments are retained with sequence/group information; automatic statement reassembly is pending. CSV counts are record counts, not unique statement counts.
- Native auditing only records configured actions. Stored-procedure ownership chaining, dynamic SQL, MERGE/TRUNCATE, denied operations and server/database scope require explicit coverage testing. SELECT is not enabled by the supplied configuration.
- No automatic detection of every lost/deleted audit file or unaudited operation is claimed. First import includes retained history; deleted history cannot be recovered. Audit disablement, queue/disk failures and retention require operational monitoring.
- Monthly CSV reports support manual generation and opt-in automatic scheduling inside the collector, including catch-up and duplicate prevention. See [automatic reporting setup](docs/LIVE_SETUP.md#automatic-monthly-csv-reports-no-task-scheduler). Reports still require human review. Local CSV storage is configurable, with optional retried S3 history. See [report storage](docs/REPORT_STORAGE.md).
- All editions of SQL Server 2019+ are the compatibility target, not a completed test matrix. No real SQL Server instance or Windows SSO host was available during development. Docker files have not yet been built in the development environment.

## Run after configuration

```powershell
pip install -r requirements.txt
python manage.py create-user reviewer --role Reviewer
python manage.py create-user approver --role Approver
python collector.py --check
python collector.py --once
python run_web.py
```

Run `python collector.py` in another terminal for continuous collection. Both processes load `.env`. See the setup guide for prerequisites and the DBA scripts. Open http://127.0.0.1:5000.

## Automated checks

```powershell
python -m unittest discover -s tests -v
```

These checks use a temporary repository and mocked ODBC responses; they validate application behavior, not native SQL Server capture completeness. `sql/test_scenarios.sql` is a manual test-database script and is never run automatically.

## Files

- `collector.py`: SQL Server Audit reader and polling process.
- `storage.py`: shared local repository and mode separation.
- `app.py`, `security.py`: web interface, authentication and permissions.
- `manage.py`: create application accounts without command-line passwords.
- `sql/`: DBA setup/permissions and manual test scenarios.
- `.env.example`: configuration template; never commit your `.env`.

To revisit the demo, set `AUDIT_MODE=demo` in `.env` and restart the web process. The original `instance/audit-demo.sqlite3` is preserved. Do not run the live collector in demo mode. Old demo report paths remain usable if their files still exist.
