[CmdletBinding()]
param(
    [ValidateRange(1, 200)]
    [int]$Limit = 50,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot '..\config\deployment.json')
)

$temporaryPayload = Join-Path ([IO.Path]::GetTempPath()) ("cashback-review-{0}.json" -f [guid]::NewGuid())
try {
    @{ limit = $Limit } | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporaryPayload -Encoding utf8
    & (Join-Path $PSScriptRoot 'invoke-cashback-endpoint.ps1') `
        -InputPath $temporaryPayload `
        -Endpoint 'review-queue' `
        -DeploymentConfig $DeploymentConfig
    if ($LASTEXITCODE -ne 0) {
        throw "Cashback review queue retrieval failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $temporaryPayload -ErrorAction SilentlyContinue
}
