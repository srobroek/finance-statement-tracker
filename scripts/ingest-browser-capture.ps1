[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Capture,
    [string]$ServerUrl = 'http://127.0.0.1:15006',
    [Parameter(Mandatory = $true)]
    [string]$SyncId,
    [string]$Config = (Join-Path $PSScriptRoot '..\config\actual-bootstrap.json'),
    [string]$Rules = (Join-Path $PSScriptRoot '..\config\static-rules.seed.json'),
    [string]$History,
    [string]$AiPolicies,
    [string]$AiProvider,
    [string]$StatementAdapter,
    [string]$StatementPasswordEnv = 'STATEMENT_PASSWORD',
    [switch]$Commit,
    [switch]$ApproveReviewedRows,
    [switch]$SkipCashbackReconciliation
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bridgeRoot = Join-Path $projectRoot 'integrations\actual'
$capturePath = (Resolve-Path $Capture).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$safeStem = [IO.Path]::GetFileNameWithoutExtension($capturePath) -replace '[^A-Za-z0-9_-]', '-'
$runRoot = Join-Path $projectRoot "runtime\browser-runs\$stamp-$safeStem"
$manifest = Join-Path $runRoot 'browser-run.json'
$result = Join-Path $runRoot 'actual-import-result.json'
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null

function Read-PlainSecret([string]$EnvironmentName, [string]$Prompt) {
    $existing = [Environment]::GetEnvironmentVariable($EnvironmentName, 'Process')
    if ($existing) { return $existing }
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$pythonArguments = @(
    '-m', 'finance_tracker.cli', 'browser-capture-export',
    '--input', $capturePath,
    '--config', (Resolve-Path $Config).Path,
    '--output', $manifest
)
if ($Rules) { $pythonArguments += @('--rules', (Resolve-Path $Rules).Path) }
if ($History) { $pythonArguments += @('--history', (Resolve-Path $History).Path) }
if ($AiPolicies -or $AiProvider) {
    if (-not $AiPolicies -or -not $AiProvider) {
        throw 'AI enrichment requires both -AiPolicies and -AiProvider'
    }
    $pythonArguments += @('--ai-policies', (Resolve-Path $AiPolicies).Path)
    $pythonArguments += @('--ai-provider', (Resolve-Path $AiProvider).Path)
}
& python @pythonArguments
if ($LASTEXITCODE -ne 0) { throw "Browser capture staging failed with exit code $LASTEXITCODE" }

$run = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
if ($run.staging_status -eq 'ROUTE_TO_STATEMENT_PIPELINE') {
    $downloadedPath = [string]$run.artifact.local_path
    if (-not [IO.Path]::IsPathRooted($downloadedPath)) {
        $downloadedPath = Join-Path (Split-Path -Parent $capturePath) $downloadedPath
    }
    $statementArguments = @{
        Pdf = (Resolve-Path $downloadedPath).Path
        ServerUrl = $ServerUrl
        SyncId = $SyncId
        Config = $Config
        Rules = $Rules
        StatementPasswordEnv = $StatementPasswordEnv
        Commit = $Commit
        SkipCashbackReconciliation = $SkipCashbackReconciliation
    }
    if ($History) { $statementArguments.History = $History }
    if ($AiPolicies) { $statementArguments.AiPolicies = $AiPolicies }
    if ($AiProvider) { $statementArguments.AiProvider = $AiProvider }
    if ($StatementAdapter) { $statementArguments.Adapter = $StatementAdapter }
    & (Join-Path $PSScriptRoot 'ingest-statement-to-actual.ps1') @statementArguments
    if ($LASTEXITCODE -ne 0) { throw "Statement pipeline failed with exit code $LASTEXITCODE" }
    Write-Output "Browser capture manifest: $manifest"
    return
}

if ($run.staging_status -eq 'ACCOUNT_REVIEW_REQUIRED') {
    Write-Output "Account snapshot staged for review; no balance transaction was created."
    Write-Output "Browser capture manifest: $manifest"
    return
}
if (@($run.import_blockers).Count -gt 0 -or @($run.envelopes).Count -eq 0) {
    throw "Browser capture cannot be imported: $(@($run.import_blockers) -join '; ')"
}
if ($Commit -and $run.review_count -gt 0 -and -not $ApproveReviewedRows) {
    throw 'Reviewed-row acknowledgement is required: rerun with -ApproveReviewedRows after inspecting the manifest'
}

$previousServer = $env:ACTUAL_SERVER_URL
$previousSync = $env:ACTUAL_SYNC_ID
$previousActualPassword = $env:ACTUAL_PASSWORD
try {
    $env:ACTUAL_SERVER_URL = $ServerUrl
    $env:ACTUAL_SYNC_ID = $SyncId
    $env:ACTUAL_PASSWORD = Read-PlainSecret 'ACTUAL_PASSWORD' 'Actual server password'
    $nodeArguments = @('actualctl.mjs', 'import', '--input', $manifest, '--result', $result)
    if ($Commit) { $nodeArguments += '--commit' }
    Push-Location $bridgeRoot
    try { & node @nodeArguments }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Actual import failed with exit code $LASTEXITCODE" }
    Write-Output "Browser capture manifest: $manifest"
    Write-Output "Import result: $result"
}
finally {
    $env:ACTUAL_SERVER_URL = $previousServer
    $env:ACTUAL_SYNC_ID = $previousSync
    $env:ACTUAL_PASSWORD = $previousActualPassword
}
