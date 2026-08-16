[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json')
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'invoke-cashback-endpoint.ps1') -InputPath $InputPath -Endpoint 'reconcile' -DeploymentConfig $DeploymentConfig
if ($LASTEXITCODE -ne 0) {
    throw "Cashback statement reconciliation failed with exit code $LASTEXITCODE"
}
