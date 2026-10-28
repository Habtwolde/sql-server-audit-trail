:ON ERROR EXIT
/* Run by a DBA after creating a dedicated Windows or SQL login.
   SQLCMD variable: CollectorLogin, e.g. DOMAIN\svc_sql_audit.
   This script deliberately requires explicit DBA execution. The application
   never grants itself permissions or creates audits.
*/
USE [master];
GO
DECLARE @Grant nvarchar(max);
IF CAST(SERVERPROPERTY('ProductMajorVersion') AS int) >= 16
    SET @Grant = N'GRANT VIEW SERVER SECURITY AUDIT TO ' + QUOTENAME(N'$(CollectorLogin)');
ELSE
BEGIN
    /* SQL Server 2019 fn_get_audit_file requires CONTROL SERVER. This is broad.
       Consider a DBA-designed signed-module interface instead for production;
       the current collector uses the direct function and does not implement it.
    */
    SET @Grant = N'GRANT CONTROL SERVER TO ' + QUOTENAME(N'$(CollectorLogin)');
END;
EXEC sys.sp_executesql @Grant;
GO
