param(
    [Parameter(Mandatory = $true)]
    [string]$Plan,
    [Parameter(Mandatory = $true)]
    [string]$Messages,
    [string]$Output = "runtime/outlook-message-batch.json"
)

$ErrorActionPreference = "Stop"
python -m finance_tracker.cli outlook-envelope `
    --plan $Plan `
    --messages $Messages `
    --output $Output
if ($LASTEXITCODE -ne 0) { throw "Outlook envelope creation failed with exit code $LASTEXITCODE" }
