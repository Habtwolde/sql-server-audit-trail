/* Manually run ONLY in a disposable, already-audited test database.
   Run under two distinct test logins to verify account attribution.
   Existing objects are never replaced by this script.
*/
USE [AuditTrailTest];
GO
IF OBJECT_ID(N'dbo.AuditTrailProbe', N'U') IS NOT NULL
    THROW 50010, 'AuditTrailProbe exists; use a clean disposable test database.', 1;
CREATE TABLE dbo.AuditTrailProbe (Id int PRIMARY KEY, Value nvarchar(100));
INSERT dbo.AuditTrailProbe VALUES (1, N'initial');
UPDATE dbo.AuditTrailProbe SET Value=N'updated' WHERE Id=1;
ALTER TABLE dbo.AuditTrailProbe ADD Note nvarchar(100) NULL;
BEGIN TRANSACTION;
UPDATE dbo.AuditTrailProbe SET Value=N'rolled back' WHERE Id=1;
ROLLBACK TRANSACTION;
EXEC sp_executesql N'UPDATE dbo.AuditTrailProbe SET Note=N''dynamic SQL'' WHERE Id=1;';
MERGE dbo.AuditTrailProbe AS target
USING (VALUES(1, N'merged'), (2, N'new')) AS src(Id, Value)
ON target.Id=src.Id
WHEN MATCHED THEN UPDATE SET Value=src.Value
WHEN NOT MATCHED THEN INSERT(Id, Value) VALUES(src.Id, src.Value);
DELETE dbo.AuditTrailProbe WHERE Id=2;
TRUNCATE TABLE dbo.AuditTrailProbe;
DROP TABLE dbo.AuditTrailProbe;
/* Acceptance also requires a separate test matrix for stored procedures with
   ownership chaining/EXECUTE AS, denied operations, concurrent short statements,
   long statements/fragments, reconnect/restart, rollover, audit disablement,
   disk failure and monthly boundaries. Do not certify full coverage from this
   small smoke script. Permission-check success does not establish commit.
*/
