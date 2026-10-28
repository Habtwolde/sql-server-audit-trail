# First live connection on your Windows PC

## 1. Update and install

Stop the old app with Ctrl+C. From the repository folder in PowerShell:

```powershell
git pull
pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the generated value into `FLASK_SECRET_KEY` in `.env`. Do not overwrite an existing `.env`; merge changes instead. Use your virtual environment if you created one.

Install **Microsoft ODBC Driver 18 for SQL Server** on the machine running the collector. The Python `pyodbc` package does not install Microsoft's driver. Official download: https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server

## 2. Ask the DBA to prepare a test database and audit

Use a disposable database on SQL Server 2019+ first. Record the instance address, version/edition, database name and Windows or SQL identity to run the collector.

The DBA creates a directory on the SQL Server host and grants the **SQL Server service account** write access. An example is `C:\SqlAudit\`. This is not a path on your PC unless SQL Server is on your PC.

Review `sql/setup_audit.sql`. Use SSMS **Query > SQLCMD Mode**, prepend:

```sql
:setvar AuditDirectory "C:\SqlAudit\"
:setvar AuditDatabase "YourTestDatabase"
```

Then execute the script. Or use `sqlcmd -b` and supply the same variables with `-v`. SQLCMD expansion is intended for trusted DBA input, not web-submitted values. The setup script exits on errors and does not replace an existing audit. If it stops after partial setup, inspect the created objects before continuing; do not blindly rerun it.

Each monitored database needs its own database audit specification. The server specification captures database/server DDL; the database specification captures direct INSERT/UPDATE/DELETE and schema/database-object changes. The application imports every record in the configured audit files, including audit-control records, so evidence is not silently discarded.

`ON_FAILURE=CONTINUE` preserves workload availability but can lose events if auditing fails. The DBA must review that policy and audit-file retention before production. The supplied limit is 100 files of 256 MB; workload volume determines how much history that retains. Keep files longer than your longest expected collector outage.

## 3. Give the dedicated collector identity access

The collector account is independent of the web reviewer accounts. For Windows authentication, run the collector process under the intended Windows service/test account. The connection uses that identity.

The DBA must create the login and review `sql/collector_permissions.sql`, setting `CollectorLogin` through SQLCMD mode:

```sql
:setvar CollectorLogin "DOMAIN\svc_sql_audit"
```

Direct `sys.fn_get_audit_file` access requires **CONTROL SERVER on SQL Server 2019**, or **VIEW SERVER SECURITY AUDIT on SQL Server 2022+**. The 2019 grant is broad; it is not a least-privilege design. A signed stored-procedure interface may be appropriate for production but is not implemented in this adapter. No permission changes are performed automatically by the app.

Microsoft reference: https://learn.microsoft.com/en-us/sql/relational-databases/system-functions/sys-fn-get-audit-file-transact-sql

## 4. Configure `.env`

- Keep `AUDIT_MODE=live`.
- Set `SQL_SOURCE_ID` to a unique, stable name for this server/audit stream.
- Replace the server address in `SQL_CONNECTION_STRING`.
- Use `Trusted_Connection=yes` for Windows integrated authentication, or the SQL-login example for a server with mixed-mode authentication.
- Set `SQL_AUDIT_FILE_PATTERN` to the DBA's directory and audit filename pattern, e.g. `C:\SqlAudit\AppAuditTrail_*.sqlaudit`.

Encryption and certificate validation are enabled in the example. A server certificate trusted by your PC and matching the configured DNS name is required. For a disposable local test only, you can explicitly set `TrustServerCertificate=yes`; restore certificate validation for shared deployments. A named instance may require SQL Browser or an explicit TCP port. Test TCP reachability and firewall rules if connection fails.

Secrets remain in the ignored `.env` file. Do not send passwords in chat, screenshots, or GitHub issues. Restrict file access to the service operator.

## 5. Create real application accounts

```powershell
python manage.py create-user reviewer --role Reviewer
python manage.py create-user approver --role Approver
```

Passwords are prompted without displaying them or storing them in command history. These are application accounts, not SQL Server logins. Web Windows SSO is not implemented yet; local password accounts allow testing with real data now.

## 6. Check, collect and open the app

```powershell
python collector.py --check
python collector.py --once
python run_web.py
```

`--check` verifies the version and audit-file read access without importing records. `--once` imports retained audit records once. In a second terminal, run `python collector.py` to poll continuously. It keeps running without an open browser. Visit http://127.0.0.1:5000 and sign in.

The first import can include historical records that still exist in the configured files. Subsequent scans replay retained files and deduplicate. There is no cursor seek that skips the rest of an audit buffer. Per-record insertion plus its replay identity is atomic. A failed scan can be retried safely, provided the source files remain available.

The displayed last-success status is historical. If the worker is stopped, its last-attempt time stops advancing. This release does not certify audit coverage or detect every audit-disablement/retention gap. Do not approve a report as complete without comparing it against known test operations and the source audit configuration.

## 7. Validate with real SQL

Use `sql/test_scenarios.sql` in a disposable audited database under distinct test logins. Confirm SQL text, account, effective user, time, database/object and source metadata appear in the UI and CSV. Test collector restart and replay without duplicate records.

The included script covers direct INSERT/UPDATE/DELETE, ALTER, dynamic SQL, MERGE, TRUNCATE and rollback examples. Separately test stored procedures/ownership chaining, EXECUTE AS, failed permissions, concurrent short statements and SQL longer than one audit record. Native auditing can report multiple actions per statement and may contain parameterized SQL. Preserve action IDs and fragments; do not assume one row equals one committed statement.

Reports use UTC calendar months. Excel-safe CSV escaping prefixes potentially active spreadsheet cells with an apostrophe; the source SQL is preserved unchanged in stored evidence. Approval decisions are tied to a report version and one person cannot approve a report they generated.

## On-premises and EC2 trial deployment

On Windows, the same Python commands work under a service account; configure the web process and collector as separate managed services. Waitress replaces Flask's development server. Configure HTTPS/reverse proxy, restricted inbound access, backups and process supervision before shared access.

The Linux container definitions include ODBC Driver 18. For a test using SQL authentication:

```text
docker compose build
docker compose run --rm web python manage.py create-user reviewer --role Reviewer
docker compose run --rm web python manage.py create-user approver --role Approver
docker compose run --rm collector python collector.py --check
docker compose up -d
```

The web port is bound to loopback. Use a deliberate HTTPS gateway/private network setup for remote access. Container Windows integrated authentication requires Kerberos/domain configuration; it does not inherit the Windows host's logged-in user. Never copy `.env` into an image. Web and collector share one local volume; do not scale them across hosts with SQLite.

## Remaining production work

Windows web SSO; separate database repository; automatic monthly scheduling and S3 archive; efficient incremental scanning under high load; fragment assembly; gap/retention monitoring; and a verified version/edition/workload compatibility matrix. These are explicit release gates, not assumed capabilities.
