param(
    [Parameter(Mandatory = $true)]
    [string]$IngestState,
    [Parameter(Mandatory = $true)]
    [string]$RunUpperBound,
    [string]$Output = "runtime/outlook-scan-plan.json",
    [string]$IngestionConfig = "config/ingestion.json"
)

$ErrorActionPreference = "Stop"
python -m finance_tracker.cli outlook-scan-plan `
    --ingestion-config $IngestionConfig `
    --ingest-state $IngestState `
    --run-upper-bound $RunUpperBound `
    --output $Output
if ($LASTEXITCODE -ne 0) { throw "Outlook scan planning failed with exit code $LASTEXITCODE" }
