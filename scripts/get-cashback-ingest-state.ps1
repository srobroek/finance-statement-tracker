[CmdletBinding()]
param(
    [string]$Source = 'outlook',
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json')
)

$temporaryPayload = Join-Path ([IO.Path]::GetTempPath()) ("cashback-ingest-state-{0}.json" -f [guid]::NewGuid())
try {
    @{ source = $Source } | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporaryPayload -Encoding utf8
    & (Join-Path $PSScriptRoot 'invoke-cashback-endpoint.ps1') `
        -InputPath $temporaryPayload `
        -Endpoint 'ingest-state' `
        -DeploymentConfig $DeploymentConfig
    if ($LASTEXITCODE -ne 0) {
        throw "Cashback ingest-state retrieval failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $temporaryPayload -ErrorAction SilentlyContinue
}
