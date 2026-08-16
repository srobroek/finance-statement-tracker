[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SyncId,
    [string]$ServerUrl = 'http://127.0.0.1:15006',
    [string]$Start = (Get-Date -Day 1 -Format 'yyyy-MM-dd'),
    [string]$End = (Get-Date -Format 'yyyy-MM-dd'),
    [string]$AsOf = (Get-Date -Format 'yyyy-MM-dd'),
    [string]$Config = 'config\actual-bootstrap.json',
    [string]$Snapshot = 'runtime\actual-snapshot-current.json',
    [string]$Dashboard = 'runtime\cashback-dashboard.json',
    [securestring]$Password
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$snapshotPath = Join-Path $repositoryRoot $Snapshot
$dashboardPath = Join-Path $repositoryRoot $Dashboard
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $snapshotPath) | Out-Null

if (-not $Password) {
    $Password = Read-Host 'Actual password' -AsSecureString
}
$credential = [System.Net.NetworkCredential]::new('', $Password)
$oldServer = $env:ACTUAL_SERVER_URL
$oldPassword = $env:ACTUAL_PASSWORD
$oldSyncId = $env:ACTUAL_SYNC_ID

try {
    $env:ACTUAL_SERVER_URL = $ServerUrl
    $env:ACTUAL_PASSWORD = $credential.Password
    $env:ACTUAL_SYNC_ID = $SyncId

    Push-Location (Join-Path $repositoryRoot 'integrations\actual')
    try {
        node .\actualctl.mjs snapshot --start $Start --end $End --result $snapshotPath
        if ($LASTEXITCODE -ne 0) { throw "Actual snapshot failed with exit code $LASTEXITCODE" }
    }
    finally {
        Pop-Location
    }

    Push-Location $repositoryRoot
    try {
        python -m finance_tracker.cli cashback-dashboard --snapshot $snapshotPath --config $Config --output $dashboardPath --as-of $AsOf
        if ($LASTEXITCODE -ne 0) { throw "Cashback calculation failed with exit code $LASTEXITCODE" }
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:ACTUAL_SERVER_URL = $oldServer
    $env:ACTUAL_PASSWORD = $oldPassword
    $env:ACTUAL_SYNC_ID = $oldSyncId
    $credential.Password = ''
}

Write-Output "Cashback dashboard refreshed: $dashboardPath"
