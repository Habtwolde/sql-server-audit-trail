# Local report storage on the on-premises Windows server

## Configure persistent paths

The application stores CSV reports on the same Windows server as the web app and collector. The default is instance/reports/live inside the application directory. For a new production installation, provision these folders and set:

```dotenv
AUDIT_DB_PATH='C:\ProgramData\SqlAuditTrail\Data\audit-live.sqlite3'
AUDIT_REPORT_DIR='C:\ProgramData\SqlAuditTrail\Reports'
```

The service account needs Modify permission on both directories, including child files (SQLite creates supporting journal files). Restrict access to authorized operators. Browser users download reports through authenticated application routes and do not need Windows folder access.

Use the same configuration for the web and collector processes. Restart both after changes. These are application storage paths, separate from the native SQL Server Audit directory C:\SqlAudit.

## Check and download

```powershell
python report_storage.py --status
```

This prints the configured local directory without connecting to SQL Server or generating reports. It is a path diagnostic, not a file-integrity or permission test.

Generate a report and download its CSV. Downloads verify the file against its recorded SHA-256 digest. A modified file is rejected with an unavailable response; a missing file returns 404. Restore missing files from the approved backup. Approval/rejection changes the database decision history, not the CSV bytes.

Changing AUDIT_REPORT_DIR affects new reports only. Existing report rows continue using their recorded paths. The application does not move or delete existing files. Changing AUDIT_DB_PATH selects a different repository: do not point it at an empty location during an upgrade unless intentionally starting a new repository.

## Upgrade existing installations

1. Stop the web and collector processes and take a consistent backup of the database and reports.
2. Update the code and install requirements.txt. Do not overwrite the private .env or change existing data paths.
3. Remove obsolete remote-storage configuration entries from the private .env. Storage selection is no longer configurable; only local files are used. Any historical report whose local copy is missing must be restored to its recorded path before it can be downloaded.
4. Replace the old storage-status command with python report_storage.py --status. Remove any old upload command from operator scripts.
5. Restart the web and collector, verify existing report downloads, and generate/review a new report.

Existing databases may retain unused tables from earlier releases. They are not read, updated or deleted by this version. Audit evidence, report versions, users, approvals and scheduler state remain in place. Fresh databases create only the tables needed for local operation. A fresh virtual environment installed from requirements.txt avoids retaining unused dependencies from an older installation.

## Backup and retention

Back up the SQLite repository and report folders together using the client's backup system. For a simple consistent backup, stop both application processes and ensure no other application writer is running before copying the Data and Reports directories; then restart and verify collection. An online procedure requires a SQLite-aware backup and coordinated report-file capture.

Keep a protected backup off the application server and test restoration, including report downloads and approval history. Protect configuration secrets separately. Native audit-file backup and rollover are DBA responsibilities. Restoring the application does not recover source audit files that have already rolled over.

All report versions are retained; there is no automatic deletion or backup job in this application. Monitor free disk space and agree retention with the client. A hash detects changed report bytes but does not make storage immutable or replace backups.
