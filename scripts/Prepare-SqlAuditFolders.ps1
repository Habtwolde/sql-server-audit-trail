#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
Creates audit folders and grants Modify to the selected local SQL service accounts.
.DESCRIPTION
Accepts SQL instance names, not Windows service names or remote server names.
Use MSSQLSERVER for a default instance. All names/accounts/paths are validated
before changes. Supports -WhatIf. Run locally on each source Windows host.
Does not configure SQL audits, start services, install Python, or configure jobs.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [ValidatePattern('^[A-Za-z][A-Za-z0-9_]{0,15}$')]
    [string[]]$InstanceName,

    [ValidatePattern('^[A-Za-z]:\\')]
    [string]$AuditRoot = 'C:\SqlAudit'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-OrdinaryDirectoryPath {
    param([string]$Path)
    $current = $Path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Expected ordinary directories, without junctions or symbolic links: $current"
            }
        }
        $parent = Split-Path -Path $current -Parent
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

$rootPath = [IO.Path]::GetFullPath($AuditRoot)
if ($rootPath -eq [IO.Path]::GetPathRoot($rootPath)) {
    throw 'AuditRoot must be a dedicated folder, not a drive root.'
}
Assert-OrdinaryDirectoryPath -Path $rootPath
$services = @(Get-CimInstance -ClassName Win32_Service)
$plan = @()
foreach ($instance in ($InstanceName | Select-Object -Unique)) {
    $serviceName = if ($instance -eq 'MSSQLSERVER') { 'MSSQLSERVER' } else { 'MSSQL$' + $instance }
    $service = @($services | Where-Object { $_.Name -eq $serviceName })
    if ($service.Count -ne 1) { throw "Local Database Engine service not found: $serviceName" }
    $account = $service[0].StartName
    switch ($account) {
        'LocalSystem' { $account = 'NT AUTHORITY\SYSTEM' }
        'NT AUTHORITY\LocalService' { $account = 'NT AUTHORITY\LOCAL SERVICE' }
        'NT AUTHORITY\NetworkService' { $account = 'NT AUTHORITY\NETWORK SERVICE' }
    }
    if ($account.StartsWith('.\')) { $account = $env:COMPUTERNAME + $account.Substring(1) }
    $identity = New-Object System.Security.Principal.NTAccount($account)
    $sid = $identity.Translate([System.Security.Principal.SecurityIdentifier])
    $folder = Join-Path -Path $rootPath -ChildPath $instance
    Assert-OrdinaryDirectoryPath -Path $folder
    $plan += [PSCustomObject]@{
        WindowsServer = $env:COMPUTERNAME
        InstanceName = $instance
        ServiceName = $service[0].Name
        ServiceAccount = $account
        ServiceSid = $sid
        AuditDirectory = $folder
    }
}

foreach ($entry in $plan) {
    $status = 'Preview'
    if ($PSCmdlet.ShouldProcess($entry.AuditDirectory, "Create audit folder and grant Modify to $($entry.ServiceAccount)")) {
        # Recheck immediately before changing existing filesystem objects.
        Assert-OrdinaryDirectoryPath -Path $entry.AuditDirectory
        New-Item -Path $entry.AuditDirectory -ItemType Directory -Force | Out-Null
        $acl = Get-Acl -LiteralPath $entry.AuditDirectory
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $entry.ServiceSid,
            [System.Security.AccessControl.FileSystemRights]::Modify,
            ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit),
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $entry.AuditDirectory -AclObject $acl
        $status = 'Prepared'
    }
    [PSCustomObject]@{
        WindowsServer = $entry.WindowsServer
        InstanceName = $entry.InstanceName
        ServiceAccount = $entry.ServiceAccount
        AuditDirectory = $entry.AuditDirectory
        Status = $status
    }
}
