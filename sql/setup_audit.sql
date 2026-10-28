:ON ERROR EXIT
/* DBA setup. Run in SSMS SQLCMD mode or sqlcmd with these variables:
   AuditDirectory = C:\SqlAudit\   (existing directory on the SQL Server host)
   AuditDatabase = name of the business/test database to monitor
   The SQL Server SERVICE account needs write access to AuditDirectory.
   Review capacity/retention first. This script does not create any triggers.
   Execute once on a test instance first. Do not reuse an audit name already in use.
*/
USE [master];
GO
IF CAST(SERVERPROPERTY('ProductMajorVersion') AS int) < 15
    THROW 50000, 'SQL Server 2019 or newer is required.', 1;
IF EXISTS (SELECT 1 FROM sys.server_audits WHERE name=N'AppAuditTrail')
    THROW 50001, 'AppAuditTrail already exists. Inspect its configuration before changing it.', 1;
GO
CREATE SERVER AUDIT [AppAuditTrail]
TO FILE (FILEPATH = N'$(AuditDirectory)', MAXSIZE = 256 MB, MAX_ROLLOVER_FILES = 100)
WITH (QUEUE_DELAY = 1000, ON_FAILURE = CONTINUE);
GO
CREATE SERVER AUDIT SPECIFICATION [AppAuditTrailServerSpec]
FOR SERVER AUDIT [AppAuditTrail]
ADD (DATABASE_CHANGE_GROUP),
ADD (SERVER_OBJECT_CHANGE_GROUP),
ADD (SERVER_PRINCIPAL_CHANGE_GROUP),
ADD (AUDIT_CHANGE_GROUP)
WITH (STATE = ON);
GO
USE [$(AuditDatabase)];
GO
CREATE DATABASE AUDIT SPECIFICATION [AppAuditTrailDatabaseSpec]
FOR SERVER AUDIT [AppAuditTrail]
ADD (INSERT ON DATABASE::[$(AuditDatabase)] BY [public]),
ADD (UPDATE ON DATABASE::[$(AuditDatabase)] BY [public]),
ADD (DELETE ON DATABASE::[$(AuditDatabase)] BY [public]),
ADD (SCHEMA_OBJECT_CHANGE_GROUP),
ADD (DATABASE_OBJECT_CHANGE_GROUP),
ADD (DATABASE_PRINCIPAL_CHANGE_GROUP)
WITH (STATE = ON);
GO
USE [master];
ALTER SERVER AUDIT [AppAuditTrail] WITH (STATE = ON);
GO
SELECT name, is_state_enabled FROM sys.server_audits WHERE name=N'AppAuditTrail';
SELECT name, status_desc, audit_file_path FROM sys.dm_server_audit_status WHERE name=N'AppAuditTrail';
GO
/* For each additional database, run only the database audit specification block
   with that database's name. New databases are not automatically enrolled.
   ON_FAILURE=CONTINUE keeps the workload available if audit writing fails, but
   can lose evidence. A DBA must select/approve the final fail-open/fail-closed
   policy and monitor audit status/disk capacity. No zero-loss guarantee here.
*/
