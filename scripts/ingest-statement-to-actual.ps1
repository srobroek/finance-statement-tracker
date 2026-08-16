[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Pdf,
    [string]$ServerUrl = 'http://127.0.0.1:15006',
    [Parameter(Mandatory = $true)]
    [string]$SyncId,
    [string]$Config = (Join-Path $PSScriptRoot '..\config\actual-bootstrap.json'),
    [string]$Rules = (Join-Path $PSScriptRoot '..\config\static-rules.seed.json'),
    [string]$History,
    [string]$AiPolicies,
    [string]$AiProvider,
    [string]$Adapter,
    [string]$StatementPasswordEnv = 'STATEMENT_PASSWORD',
    [switch]$Commit,
    [switch]$SkipCashbackReconciliation
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bridgeRoot = Join-Path $projectRoot 'integrations\actual'
$pdfPath = (Resolve-Path $Pdf).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$safeStem = [IO.Path]::GetFileNameWithoutExtension($pdfPath) -replace '[^A-Za-z0-9_-]', '-'
$runRoot = Join-Path $projectRoot "runtime\actual-runs\$stamp-$safeStem"
$manifest = Join-Path $runRoot 'statement-run.json'
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

$previousServer = $env:ACTUAL_SERVER_URL
$previousSync = $env:ACTUAL_SYNC_ID
$previousActualPassword = $env:ACTUAL_PASSWORD
$previousStatementPassword = [Environment]::GetEnvironmentVariable($StatementPasswordEnv, 'Process')
try {
    if (-not $previousStatementPassword) {
        [Environment]::SetEnvironmentVariable(
            $StatementPasswordEnv,
            (Read-PlainSecret $StatementPasswordEnv 'Statement PDF password'),
            'Process'
        )
    }
    $pythonArguments = @(
        '-m', 'finance_tracker.cli', 'actual-statement-export',
        '--pdf', $pdfPath,
        '--config', (Resolve-Path $Config).Path,
        '--output', $manifest,
        '--password-env', $StatementPasswordEnv
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
    if ($Adapter) { $pythonArguments += @('--adapter', $Adapter) }
    & python @pythonArguments
    if ($LASTEXITCODE -ne 0) { throw "Statement export failed with exit code $LASTEXITCODE" }

    $env:ACTUAL_SERVER_URL = $ServerUrl
    $env:ACTUAL_SYNC_ID = $SyncId
    $env:ACTUAL_PASSWORD = Read-PlainSecret 'ACTUAL_PASSWORD' 'Actual server password'
    $nodeArguments = @('actualctl.mjs', 'import', '--input', $manifest, '--result', $result)
    if ($Commit) { $nodeArguments += '--commit' }
    Push-Location $bridgeRoot
    try { & node @nodeArguments }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Actual import failed with exit code $LASTEXITCODE" }
    $importResult = Get-Content -Raw -LiteralPath $result | ConvertFrom-Json
    if ($Commit -and $importResult.status -ne 'committed') {
        throw "Actual did not report a committed import; cashback reconciliation was not attempted"
    }
    if ($Commit -and -not $SkipCashbackReconciliation) {
        $runManifest = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
        $reconcileIndex = 0
        foreach ($reconciliation in @($runManifest.cashback_reconciliation)) {
            $reconcileIndex += 1
            $reconciliationPath = Join-Path $runRoot "cashback-reconciliation-$reconcileIndex.json"
            $reconciliation | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $reconciliationPath -Encoding utf8
            & (Join-Path $PSScriptRoot 'reconcile-cashback-statement.ps1') -InputPath $reconciliationPath
            if ($LASTEXITCODE -ne 0) {
                throw "Cashback reconciliation failed after the Actual commit"
            }
        }
    }
    Write-Output "Run manifest: $manifest"
    Write-Output "Import result: $result"
}
finally {
    $env:ACTUAL_SERVER_URL = $previousServer
    $env:ACTUAL_SYNC_ID = $previousSync
    $env:ACTUAL_PASSWORD = $previousActualPassword
    [Environment]::SetEnvironmentVariable($StatementPasswordEnv, $previousStatementPassword, 'Process')
}
