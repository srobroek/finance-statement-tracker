[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [Parameter(Mandatory = $true)]
    [ValidateSet('STATEMENT_PDF', 'BROWSER_CAPTURE', 'BROWSER_EXPORT')]
    [string]$Type,
    [ValidateSet('STAGE', 'PREFLIGHT', 'COMMIT')]
    [string]$ActualMode = 'STAGE',
    [string]$CardCode,
    [string]$Adapter,
    [string]$Provider,
    [string]$DataId,
    [string]$ActualAccount,
    [string]$SourceMessageId,
    [string]$SourceAttachmentId,
    [string]$SourceKind,
    [string]$AIResponsesPath,
    [switch]$AIHandoffComplete,
    [string]$PasswordEnv = 'STATEMENT_PASSWORD',
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json'),
    [string]$DockerHost,
    [string]$Container
)

$ErrorActionPreference = 'Stop'
$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$deployment = Get-Content -Raw -LiteralPath (Resolve-Path -LiteralPath $DeploymentConfig) | ConvertFrom-Json
if ($deployment.schema_version -ne 1) {
    throw 'Deployment config schema_version must be 1'
}
$target = if ($DockerHost) { $DockerHost } elseif ($env:FINANCE_DOCKER_HOST) { $env:FINANCE_DOCKER_HOST } else { $deployment.ssh_target }
$containerName = if ($Container) { $Container } elseif ($env:FINANCE_ACTUAL_INGESTION_CONTAINER) { $env:FINANCE_ACTUAL_INGESTION_CONTAINER } else { $deployment.actual_ingestion_container }
if ($target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
    throw "Invalid SSH target in deployment config: $target"
}
if ($containerName -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') {
    throw "Invalid ingestion container name in deployment config: $containerName"
}
if ($ActualMode -eq 'COMMIT' -and $env:ALLOW_ACTUAL_WRITES -ne 'true') {
    throw 'COMMIT requires ALLOW_ACTUAL_WRITES=true in the calling environment'
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedInput).Hash.ToLowerInvariant()
$extension = [IO.Path]::GetExtension($resolvedInput).ToLowerInvariant()
if ($extension -notmatch '^\.[a-z0-9]{1,8}$') {
    throw "Unsafe or missing input extension: $extension"
}
$remoteName = "$hash$extension"
$remoteTemporary = "/tmp/finance-ingest-$remoteName"

& scp -- $resolvedInput "${target}:$remoteTemporary"
if ($LASTEXITCODE -ne 0) {
    throw "Artifact upload failed with exit code $LASTEXITCODE"
}
$installCommand = "sudo install -o 10002 -g 10002 -m 0600 '$remoteTemporary' '/opt/stacks/finance-actual-poc/ingestion-data/inbox/$remoteName' && rm -f '$remoteTemporary'"
& ssh $target $installCommand
if ($LASTEXITCODE -ne 0) {
    throw "Artifact installation failed with exit code $LASTEXITCODE"
}

$job = [ordered]@{
    type = $Type
    actual_mode = $ActualMode
    source_path = $remoteName
    source_filename = [IO.Path]::GetFileName($resolvedInput)
    ai_handoff_complete = $AIHandoffComplete.IsPresent
}
$resolvedSourceKind = if ($SourceKind) {
    $SourceKind
} elseif ($Type -eq 'STATEMENT_PDF') {
    'outlook_attachment'
} elseif ($Type -eq 'BROWSER_EXPORT') {
    'browser_export'
} else {
    'browser_capture'
}
foreach ($property in @{
    card_code = $CardCode
    adapter = $Adapter
    provider = $Provider
    data_id = $DataId
    actual_account = $ActualAccount
    source_message_id = $SourceMessageId
    source_attachment_id = $SourceAttachmentId
    source_kind = $resolvedSourceKind
    password_env = $PasswordEnv
}.GetEnumerator()) {
    if (-not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        $job[$property.Key] = $property.Value
    }
}
if ($AIResponsesPath) {
    $resolvedAIResponses = (Resolve-Path -LiteralPath $AIResponsesPath).Path
    $rawAIResponses = Get-Content -Raw -LiteralPath $resolvedAIResponses
    if (-not $rawAIResponses.TrimStart().StartsWith('[')) {
        throw 'AIResponsesPath must contain one JSON array'
    }
    $aiResponses = @($rawAIResponses | ConvertFrom-Json)
    $job.ai_responses = @($aiResponses)
}
$payload = $job | ConvertTo-Json -Compress
$remoteCommand = "sudo docker exec -i $containerName python3 /app/apps/actual-ingestion/submit_local.py"
$payload | & ssh $target $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "Actual ingestion job failed with exit code $LASTEXITCODE"
}
