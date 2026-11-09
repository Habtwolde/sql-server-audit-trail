-- Run in the dedicated, empty SqlAuditTrail database, never a business database.
-- Also applied idempotently by initialize_store(). No GO: executed via ODBC.
IF OBJECT_ID(N'dbo.audit_events', N'U') IS NULL
CREATE TABLE dbo.audit_events (
 id bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
 event_time varchar(40) NOT NULL,
 server_name nvarchar(128) NOT NULL, database_name nvarchar(128) NOT NULL,
 login_name nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
 effective_user nvarchar(128) NOT NULL, application_name nvarchar(max) NOT NULL,
 client_host nvarchar(max) NOT NULL, schema_name nvarchar(128) NOT NULL,
 object_name nvarchar(128) NOT NULL, operation nvarchar(128) NOT NULL,
 outcome nvarchar(128) NOT NULL, statement nvarchar(max) NOT NULL
);
IF OBJECT_ID(N'dbo.reports', N'U') IS NULL
CREATE TABLE dbo.reports (
 id bigint IDENTITY(1,1) NOT NULL PRIMARY KEY, period varchar(7) NOT NULL,
 version int NOT NULL, created_at varchar(40) NOT NULL,
 created_by nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
 event_count bigint NOT NULL, file_path nvarchar(max) NOT NULL,
 sha256 varchar(64) NOT NULL, status varchar(16) NOT NULL DEFAULT 'Pending',
 CONSTRAINT UQ_report_version UNIQUE(period,version)
);
IF OBJECT_ID(N'dbo.scheduled_reports', N'U') IS NULL
CREATE TABLE dbo.scheduled_reports (
 period varchar(7) NOT NULL PRIMARY KEY,
 report_id bigint NOT NULL UNIQUE REFERENCES dbo.reports(id)
);
IF OBJECT_ID(N'dbo.report_scheduler_state', N'U') IS NULL
CREATE TABLE dbo.report_scheduler_state (
 id int NOT NULL PRIMARY KEY CHECK(id=1), status nvarchar(128) NOT NULL,
 last_attempt varchar(40) NOT NULL, last_success varchar(40), detail nvarchar(max) NOT NULL
);
IF OBJECT_ID(N'dbo.report_reviews', N'U') IS NULL
CREATE TABLE dbo.report_reviews (
 id bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
 report_id bigint NOT NULL REFERENCES dbo.reports(id),
 reviewer nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
 decision varchar(16) NOT NULL, comment nvarchar(max) NOT NULL,
 decided_at varchar(40) NOT NULL
);
IF OBJECT_ID(N'dbo.app_users', N'U') IS NULL
CREATE TABLE dbo.app_users (
 username nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
 password_hash nvarchar(max) NOT NULL, role varchar(32) NOT NULL
);
IF OBJECT_ID(N'dbo.collector_state', N'U') IS NULL
CREATE TABLE dbo.collector_state (
 source_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
 last_attempt varchar(40), last_success varchar(40), status nvarchar(128) NOT NULL,
 detail nvarchar(max) NOT NULL, server_version nvarchar(128), edition nvarchar(128)
);
IF OBJECT_ID(N'dbo.source_records', N'U') IS NULL
CREATE TABLE dbo.source_records (
 record_key varchar(64) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,
 event_id bigint NOT NULL REFERENCES dbo.audit_events(id),
 source_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
 file_name nvarchar(260) COLLATE Latin1_General_100_BIN2 NOT NULL,
 file_offset bigint, sequence_number bigint, sequence_group_id nvarchar(128),
 action_id nvarchar(128), collected_at varchar(40) NOT NULL, raw_json nvarchar(max) NOT NULL
);
IF OBJECT_ID(N'dbo.source_files', N'U') IS NULL
CREATE TABLE dbo.source_files (
 source_id nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
 file_name nvarchar(260) COLLATE Latin1_General_100_BIN2 NOT NULL,
 max_offset bigint, last_seen varchar(40) NOT NULL,
 PRIMARY KEY(source_id,file_name)
);
IF OBJECT_ID(N'dbo.repository_identity', N'U') IS NULL
CREATE TABLE dbo.repository_identity (mode varchar(8) NOT NULL);
IF NOT EXISTS(SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.audit_events') AND name='event_time_idx')
CREATE INDEX event_time_idx ON dbo.audit_events(event_time,id);
IF NOT EXISTS(SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.audit_events') AND name='login_time_idx')
CREATE INDEX login_time_idx ON dbo.audit_events(login_name,event_time,id);
IF NOT EXISTS(SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.source_records') AND name='source_event_idx')
CREATE INDEX source_event_idx ON dbo.source_records(event_id);
