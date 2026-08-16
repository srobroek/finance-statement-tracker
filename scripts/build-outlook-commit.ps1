param(
    [Parameter(Mandatory = $true)]
    [string]$Envelope,
    [Parameter(Mandatory = $true)]
    [string]$ServiceResponse,
    [string]$Output = "runtime/outlook-ingest-run.json"
)

$ErrorActionPreference = "Stop"
python -m finance_tracker.cli outlook-commit-payload `
    --envelope $Envelope `
    --service-response $ServiceResponse `
    --output $Output
if ($LASTEXITCODE -ne 0) { throw "Outlook commit payload validation failed with exit code $LASTEXITCODE" }
