[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json')
)

$ErrorActionPreference = 'Stop'
$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$payload = Get-Content -Raw -LiteralPath $resolvedInput
if ([string]::IsNullOrWhiteSpace($payload)) {
    throw "Cashback event payload is empty: $resolvedInput"
}

$decoded = $payload | ConvertFrom-Json
if ($decoded.PSObject.Properties.Name -contains 'events') {
    $payload = $decoded.events | ConvertTo-Json -Depth 20 -Compress
}
if ($payload -eq 'null' -or [string]::IsNullOrWhiteSpace($payload)) {
    throw "Cashback event batch contains no events: $resolvedInput"
}

$temporaryPayload = Join-Path ([IO.Path]::GetTempPath()) ("cashback-events-{0}.json" -f [guid]::NewGuid())
try {
    Set-Content -LiteralPath $temporaryPayload -Value $payload -Encoding utf8
    & (Join-Path $PSScriptRoot 'invoke-cashback-endpoint.ps1') -InputPath $temporaryPayload -Endpoint 'events' -DeploymentConfig $DeploymentConfig
    if ($LASTEXITCODE -ne 0) {
        throw "Cashback event ingestion failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $temporaryPayload -ErrorAction SilentlyContinue
}
