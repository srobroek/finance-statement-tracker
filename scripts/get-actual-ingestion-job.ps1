[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$JobId,
    [switch]$AIHandoffOnly,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json'),
    [string]$DockerHost,
    [string]$Container
)

$ErrorActionPreference = 'Stop'
if ($JobId -notmatch '^[0-9a-f]{24}$') {
    throw 'JobId must be exactly 24 lowercase hexadecimal characters'
}

$defaultDeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json')
$localDeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.local.json')
$requestedDeploymentConfig = (Resolve-Path -LiteralPath $DeploymentConfig).Path
$resolvedDefaultDeploymentConfig = (Resolve-Path -LiteralPath $defaultDeploymentConfig).Path
$effectiveDeploymentConfig = if ($env:FINANCE_DEPLOYMENT_CONFIG) {
    (Resolve-Path -LiteralPath $env:FINANCE_DEPLOYMENT_CONFIG).Path
} elseif ($requestedDeploymentConfig -eq $resolvedDefaultDeploymentConfig -and (Test-Path -LiteralPath $localDeploymentConfig)) {
    (Resolve-Path -LiteralPath $localDeploymentConfig).Path
} else {
    $requestedDeploymentConfig
}
$deployment = Get-Content -Raw -LiteralPath $effectiveDeploymentConfig | ConvertFrom-Json
if ($deployment.schema_version -ne 1) {
    throw 'Deployment config schema_version must be 1'
}
$target = if ($DockerHost) { $DockerHost } elseif ($env:FINANCE_DOCKER_HOST) { $env:FINANCE_DOCKER_HOST } else { $deployment.ssh_target }
$containerName = if ($Container) { $Container } elseif ($env:FINANCE_ACTUAL_INGESTION_CONTAINER) { $env:FINANCE_ACTUAL_INGESTION_CONTAINER } else { $deployment.actual_ingestion_container }
if ($target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
    throw "Invalid SSH target in deployment config: $target"
}
if ($containerName -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') {
    throw "Invalid ingestion container name: $containerName"
}

$remoteCommand = "sudo docker exec $containerName python3 /app/apps/actual-ingestion/submit_local.py --job-id $JobId"
$response = @(& ssh $target $remoteCommand 2>&1)
$remoteExitCode = $LASTEXITCODE
if ($remoteExitCode -ne 0) {
    $detail = ($response | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
    throw "Actual ingestion result retrieval failed with exit code ${remoteExitCode}: $detail"
}

if ($AIHandoffOnly) {
    $job = (($response | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    if ($null -eq $job.ai_handoff) {
        throw "Ingestion job $JobId does not contain an AI handoff"
    }
    $job.ai_handoff | ConvertTo-Json -Depth 30
} else {
    $response
}
