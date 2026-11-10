#Requires -Version 5.1
<#
.SYNOPSIS
Lists local SQL Server Database Engine services without changing configuration.
.DESCRIPTION
Run on each source Windows host, directly or through approved PowerShell remoting.
Returns objects for display or Export-Csv. Does not connect to SQL, discover TCP
ports, enumerate databases, create folders, change ACLs, or restart services.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z]:\\')]
    [string]$AuditRoot = 'C:\SqlAudit'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$services = @(Get-CimInstance -ClassName Win32_Service | Where-Object {
    $_.Name -eq 'MSSQLSERVER' -or $_.Name -match '^MSSQL\$[A-Za-z0-9_]+$'
})
foreach ($service in ($services | Sort-Object Name)) {
    $instance = if ($service.Name -eq 'MSSQLSERVER') { 'MSSQLSERVER' } else { $service.Name.Substring(6) }
    $address = if ($instance -eq 'MSSQLSERVER') { $env:COMPUTERNAME } else { '{0}\{1}' -f $env:COMPUTERNAME, $instance }
    [PSCustomObject]@{
        WindowsServer = $env:COMPUTERNAME
        InstanceName = $instance
        SqlAddress = $address
        ServiceName = $service.Name
        ServiceAccount = $service.StartName
        ServiceState = $service.State
        ProposedAuditDirectory = Join-Path -Path $AuditRoot -ChildPath $instance
        TcpPort = ''
        DatabasesToAudit = ''
        Selected = 'false'
    }
}
if ($services.Count -eq 0) {
    Write-Warning 'No local SQL Server Database Engine services were found.'
}
