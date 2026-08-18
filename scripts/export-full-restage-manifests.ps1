[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunRoot,
    [string]$OutputRoot,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json'),
    [string]$DockerHost,
    [string]$Container
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resolvedRunRoot = (Resolve-Path -LiteralPath $RunRoot).Path
$summaryPath = Join-Path $resolvedRunRoot 'summary.json'
$summary = Get-Content -Raw -LiteralPath (Resolve-Path -LiteralPath $summaryPath) | ConvertFrom-Json
if ($summary.schema_version -ne 'full-restage-result-v1') {
    throw 'Unsupported full-restage result schema'
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
$target = if ($DockerHost) { $DockerHost } elseif ($env:FINANCE_DOCKER_HOST) { $env:FINANCE_DOCKER_HOST } else { $deployment.ssh_target }
$containerName = if ($Container) { $Container } elseif ($env:FINANCE_ACTUAL_INGESTION_CONTAINER) { $env:FINANCE_ACTUAL_INGESTION_CONTAINER } else { $deployment.actual_ingestion_container }
if ($target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') { throw "Invalid SSH target: $target" }
if ($containerName -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { throw "Invalid container: $containerName" }

if (-not $OutputRoot) { $OutputRoot = Join-Path $resolvedRunRoot 'manifests' }
$resolvedOutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$prefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not ($resolvedOutputRoot + [IO.Path]::DirectorySeparatorChar).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputRoot must be inside the repository: $resolvedOutputRoot"
}
New-Item -ItemType Directory -Path $resolvedOutputRoot -Force | Out-Null

$exported = [Collections.Generic.List[object]]::new()
foreach ($result in @($summary.results)) {
    $jobId = [string]$result.job_id
    $manifestPath = [string]$result.manifest_path
    if ($jobId -notmatch '^[0-9a-f]{24}$' -or $manifestPath -ne "/var/lib/finance-ingestion/jobs/$jobId/manifest.json") {
        throw "Unsafe manifest identity for source $($result.id)"
    }
    $remoteCommand = "sudo docker exec $containerName cat '$manifestPath'"
    $response = @(& ssh $target $remoteCommand 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Manifest export failed for ${jobId}: $($response -join [Environment]::NewLine)"
    }
    $raw = ($response | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
    $manifest = ConvertFrom-Json -InputObject $raw
    $safeId = ([string]$result.id) -replace '[^A-Za-z0-9._-]+', '-'
    $targetPath = Join-Path $resolvedOutputRoot "$safeId.json"
    [IO.File]::WriteAllText(
        $targetPath,
        (($manifest | ConvertTo-Json -Depth 50) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    $exported.Add([ordered]@{
        id = [string]$result.id
        job_id = $jobId
        record_count = @($manifest.envelopes | ForEach-Object { @($_.records) }).Count
        output = $targetPath.Substring($prefix.Length).Replace('\', '/')
    })
}
[ordered]@{
    schema_version = 'full-restage-manifest-export-v1'
    run_id = [string]$summary.run_id
    manifest_count = $exported.Count
    manifests = @($exported)
} | ConvertTo-Json -Depth 10
