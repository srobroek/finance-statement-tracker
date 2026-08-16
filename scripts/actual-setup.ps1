[CmdletBinding()]
param(
    [ValidateSet('doctor', 'bootstrap')]
    [string]$Command = 'doctor',
    [string]$ServerUrl = 'http://127.0.0.1:15006',
    [Parameter(Mandatory = $true)]
    [string]$SyncId,
    [string]$Config = (Join-Path $PSScriptRoot '..\config\actual-bootstrap.json'),
    [string]$Result,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bridgeRoot = Join-Path $projectRoot 'integrations\actual'

function Read-PlainSecret([string]$EnvironmentName, [string]$Prompt) {
    $existing = [Environment]::GetEnvironmentVariable($EnvironmentName, 'Process')
    if ($existing) { return $existing }
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$previousServer = $env:ACTUAL_SERVER_URL
$previousSync = $env:ACTUAL_SYNC_ID
$previousPassword = $env:ACTUAL_PASSWORD
try {
    $env:ACTUAL_SERVER_URL = $ServerUrl
    $env:ACTUAL_SYNC_ID = $SyncId
    $env:ACTUAL_PASSWORD = Read-PlainSecret 'ACTUAL_PASSWORD' 'Actual server password'

    $arguments = @('actualctl.mjs', $Command)
    if ($Command -eq 'bootstrap') {
        $arguments += @('--config', (Resolve-Path $Config).Path)
        if ($Apply) { $arguments += '--apply' }
    }
    if ($Result) { $arguments += @('--result', $Result) }
    Push-Location $bridgeRoot
    try { & node @arguments }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Actual command failed with exit code $LASTEXITCODE" }
}
finally {
    $env:ACTUAL_SERVER_URL = $previousServer
    $env:ACTUAL_SYNC_ID = $previousSync
    $env:ACTUAL_PASSWORD = $previousPassword
}
