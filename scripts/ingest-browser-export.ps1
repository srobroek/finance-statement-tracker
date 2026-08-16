[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Provider,
    [Parameter(Mandatory = $true)]
    [string]$DataId,
    [Parameter(Mandatory = $true)]
    [string]$File,
    [Parameter(Mandatory = $true)]
    [string]$ActualAccount,
    [Parameter(Mandatory = $true)]
    [string]$SyncId,
    [string]$ServerUrl = 'http://127.0.0.1:15006',
    [string]$Sources = (Join-Path $PSScriptRoot '..\config\browser-sources.json'),
    [string]$AdaptersRoot = (Join-Path $PSScriptRoot '..\browser_adapters'),
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
$sourcePath = (Resolve-Path $File).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$safeStem = [IO.Path]::GetFileNameWithoutExtension($sourcePath) -replace '[^A-Za-z0-9_-]', '-'
$capturePath = Join-Path $projectRoot "runtime\browser-captures\$stamp-$safeStem.json"
New-Item -ItemType Directory -Path (Split-Path -Parent $capturePath) -Force | Out-Null

& python -m finance_tracker.cli browser-export-file `
    --provider $Provider `
    --data-id $DataId `
    --file $sourcePath `
    --sources (Resolve-Path $Sources).Path `
    --actual-account $ActualAccount `
    --adapters-root (Resolve-Path $AdaptersRoot).Path `
    --output $capturePath
if ($LASTEXITCODE -ne 0) { throw "Browser export parsing failed with exit code $LASTEXITCODE" }

$arguments = @{
    Capture = $capturePath
    ServerUrl = $ServerUrl
    SyncId = $SyncId
    Config = $Config
    Rules = $Rules
    StatementPasswordEnv = $StatementPasswordEnv
    Commit = $Commit
    ApproveReviewedRows = $ApproveReviewedRows
    SkipCashbackReconciliation = $SkipCashbackReconciliation
}
if ($History) { $arguments.History = $History }
if ($AiPolicies) { $arguments.AiPolicies = $AiPolicies }
if ($AiProvider) { $arguments.AiProvider = $AiProvider }
if ($StatementAdapter) { $arguments.StatementAdapter = $StatementAdapter }
& (Join-Path $PSScriptRoot 'ingest-browser-capture.ps1') @arguments
if ($LASTEXITCODE -ne 0) { throw "Browser capture ingestion failed with exit code $LASTEXITCODE" }

Write-Output "Browser capture: $capturePath"
