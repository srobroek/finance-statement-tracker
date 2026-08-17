[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [Parameter(Mandatory = $true)]
    [ValidateSet('events', 'events/validate', 'ingest-runs', 'ingest-state', 'outlook/messages', 'review-queue', 'reconcile', 'corrections', 'periods/finalize')]
    [string]$Endpoint,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json'),
    [string]$DockerHost,
    [string]$Container
)

$ErrorActionPreference = 'Stop'
$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$payload = Get-Content -Raw -LiteralPath $resolvedInput
if ([string]::IsNullOrWhiteSpace($payload)) {
    throw "Cashback API payload is empty: $resolvedInput"
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
$containerName = if ($Container) { $Container } elseif ($env:FINANCE_CASHBACK_CONTAINER) { $env:FINANCE_CASHBACK_CONTAINER } else { $deployment.cashback_container }
if ($target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
    throw "Invalid SSH target in deployment config: $target"
}
if ($containerName -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') {
    throw "Invalid cashback container name in deployment config: $containerName"
}

$remoteCommand = "sudo docker exec -i $containerName python /app/apps/cashback-control/submit_local.py --endpoint $Endpoint"
$payload | & ssh $target $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "Cashback API endpoint '$Endpoint' failed with exit code $LASTEXITCODE"
}
