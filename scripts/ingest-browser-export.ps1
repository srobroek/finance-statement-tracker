[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Provider,
    [Parameter(Mandatory = $true)]
    [string]$DataId,
    [Parameter(Mandatory = $true)]
    [Alias('InputPath')]
    [string]$File,
    [Parameter(Mandatory = $true)]
    [string]$ActualAccount,
    [ValidateSet('STAGE', 'PREFLIGHT', 'COMMIT')]
    [string]$ActualMode = 'STAGE',
    [string]$AIResponsesPath,
    [string]$EvidenceLinksPath,
    [switch]$AIHandoffComplete,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json'),
    [string]$DockerHost,
    [string]$Container
)

$ErrorActionPreference = 'Stop'

$arguments = @{
    InputPath = $File
    Type = 'BROWSER_EXPORT'
    ActualMode = $ActualMode
    Provider = $Provider
    DataId = $DataId
    ActualAccount = $ActualAccount
    SourceKind = 'browser_export'
    DeploymentConfig = $DeploymentConfig
    AIHandoffComplete = $AIHandoffComplete
}
foreach ($optional in @{
    AIResponsesPath = $AIResponsesPath
    EvidenceLinksPath = $EvidenceLinksPath
    DockerHost = $DockerHost
    Container = $Container
}.GetEnumerator()) {
    if (-not [string]::IsNullOrWhiteSpace([string]$optional.Value)) {
        $arguments[$optional.Key] = $optional.Value
    }
}

& (Join-Path $PSScriptRoot 'push-actual-ingestion-job.ps1') @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Guarded browser-export ingestion failed with exit code $LASTEXITCODE"
}
