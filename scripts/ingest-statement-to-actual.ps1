[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Alias('InputPath')]
    [string]$Pdf,
    [Parameter(Mandatory = $true)]
    [string]$CardCode,
    [ValidateSet('STAGE', 'PREFLIGHT', 'COMMIT')]
    [string]$ActualMode = 'STAGE',
    [string]$SourceMessageId,
    [string]$SourceAttachmentId,
    [ValidateSet('outlook_attachment', 'manual_statement')]
    [string]$SourceKind = 'outlook_attachment',
    [string]$Adapter,
    [string]$PasswordEnv,
    [string]$AIResponsesPath,
    [string]$EvidenceLinksPath,
    [switch]$AIHandoffComplete,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json'),
    [string]$DockerHost,
    [string]$Container
)

$ErrorActionPreference = 'Stop'

if ($SourceKind -eq 'outlook_attachment') {
    if ([string]::IsNullOrWhiteSpace($SourceMessageId)) {
        throw 'Outlook statements require -SourceMessageId'
    }
    if ([string]::IsNullOrWhiteSpace($SourceAttachmentId)) {
        throw 'Outlook statements require -SourceAttachmentId'
    }
}

$arguments = @{
    InputPath = $Pdf
    Type = 'STATEMENT_PDF'
    ActualMode = $ActualMode
    CardCode = $CardCode
    SourceKind = $SourceKind
    DeploymentConfig = $DeploymentConfig
    AIHandoffComplete = $AIHandoffComplete
}
foreach ($optional in @{
    SourceMessageId = $SourceMessageId
    SourceAttachmentId = $SourceAttachmentId
    Adapter = $Adapter
    PasswordEnv = $PasswordEnv
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
    throw "Guarded statement ingestion failed with exit code $LASTEXITCODE"
}
