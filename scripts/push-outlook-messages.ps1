[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json')
)

& (Join-Path $PSScriptRoot 'invoke-cashback-endpoint.ps1') `
    -InputPath $InputPath `
    -Endpoint 'outlook/messages' `
    -DeploymentConfig $DeploymentConfig
if ($LASTEXITCODE -ne 0) {
    throw "Outlook message ingestion failed with exit code $LASTEXITCODE"
}
