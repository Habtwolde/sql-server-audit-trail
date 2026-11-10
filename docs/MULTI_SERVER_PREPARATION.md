# Preparing SQL sources on multiple Windows hosts

The central Windows server hosts the web app, collector jobs, local CSV reports,
and a SQL Server instance containing the dedicated SqlAuditTrail application DB.
Each remote source SQL Server writes its own native audit files locally. The
collector reads those files through the source SQL instance over ODBC; an SMB
share and Python installation on every source host are not required.

This page covers discovery and folder permissions only. It does not enroll
sources in the app, enable SQL audits, configure networking or SQL Agent, or add
multi-source scheduling. The current collector still uses one source per worker.
The scripts target standalone Windows SQL services; clustered instances require
DBA coordination for active nodes and audit storage.

## 1. Discover locally (no administrative changes)

Run Windows PowerShell 5.1 or newer in the application-package folder on each
source Windows host. Only the scripts need to be copied there, not Python.

```powershell
.\scripts\Discover-SqlInstances.ps1 | Format-Table -AutoSize
```

For an inventory file in an existing writable folder:

```powershell
.\scripts\Discover-SqlInstances.ps1 |
    Export-Csv -Path ".\sql-instances-$env:COMPUTERNAME.csv" -NoTypeInformation -Encoding UTF8
```

Discovery reads Win32_Service and lists Database Engine services only. It also
lists stopped engines. SQLAddress is a proposed host/instance address, not a
validated TCP endpoint. It cannot confirm database names, SQL version, TCP ports,
network reachability, audit scope, or whether the central application can log in.
TcpPort and DatabasesToAudit must be completed by the DBA. No passwords are stored.
Selected defaults to false: discovery does not authorize enrollment.

Administrators with existing PowerShell remoting access can gather multiple
hosts from an administration workstation in one command:

```powershell
$sourceHosts = @('SQLHOST01', 'SQLHOST02')
Invoke-Command -ComputerName $sourceHosts -FilePath .\scripts\Discover-SqlInstances.ps1 |
    Select-Object WindowsServer, InstanceName, SqlAddress, ServiceName, ServiceAccount,
                  ServiceState, ProposedAuditDirectory, TcpPort, DatabasesToAudit, Selected |
    Export-Csv -Path .\sql-source-inventory.csv -NoTypeInformation -Encoding UTF8
```

Replace the example host list. This requires the client's approved WinRM/remoting
configuration and credentials; these scripts do not enable remoting or change
execution policy. If a host fails, retain and resolve its error; the inventory is
incomplete until every intended host is accounted for. Remoting is optional; the
same discovery script can be distributed through the client's management tools.

## 2. Prepare approved folders

After the DBA selects the instances and a suitable local audit drive, run the
folder script in elevated PowerShell **on the relevant source host**. Example:

```powershell
.\scripts\Prepare-SqlAuditFolders.ps1 -InstanceName 'INSTANCE1','INSTANCE2' -AuditRoot 'D:\SqlAudit' -WhatIf
```

Then apply the same selected names/path without -WhatIf:

```powershell
.\scripts\Prepare-SqlAuditFolders.ps1 -InstanceName 'INSTANCE1','INSTANCE2' -AuditRoot 'D:\SqlAudit'
```

For a default SQL instance use `-InstanceName 'MSSQLSERVER'`. Use actual local
instance names, not HOST\INSTANCE addresses or SQL Windows service names. These
scripts support conventional alphanumeric/underscore instance names; if an
installed name is not listed, the DBA must inspect it separately.

The script discovers the selected service's current logon account, resolves it
to a Windows SID and creates one subfolder per selected instance. It adds an
inheritable Modify allowance for that account. It does not reset other access
rules, remove inherited permissions, grant Everyone access, recursively reset
existing protected files, change service accounts or restart SQL Server. A
shared service account will naturally retain access to all folders granted to
that account. The DBA should choose a parent folder whose inherited access is
appropriate for audit evidence. Per-service SID isolation can be configured
separately if required by the client's security standard.

All selected instances, account resolutions and paths are checked before the
first modification. Drive roots, files, junctions and symbolic-link ancestors
are rejected. OS/filesystem errors during apply can still leave earlier folders
prepared; inspect the output and rerun after resolving the failure. Repeated runs
merge the same allowance rather than clearing ACLs. `Prepared` confirms the
folder/ACL operation completed; it does not prove SQL Server has written audit
files. Validate writing when the SQL audit is configured in the next stage.

Validate these scripts first on a representative non-production Windows SQL host.
Their PowerShell syntax was checked during authoring; Windows CIM and ACL
execution cannot be verified from the Linux development environment.

Microsoft references:
- https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-service
- https://learn.microsoft.com/en-us/sql/relational-databases/security/auditing/sql-server-audit-database-engine
