[CmdletBinding()]
param(
    [string]$SourceConfig = (Join-Path $PSScriptRoot '..\config\full-restage-sources.json'),
    [string]$EvidenceCatalogue = (Join-Path $PSScriptRoot '..\Finance Evidence\catalogue.json'),
    [string]$OutputRoot = (Join-Path $PSScriptRoot '..\runtime\full-restage'),
    [string]$AIResponsesRoot,
    [switch]$AIHandoffComplete,
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$configuration = Get-Content -Raw -LiteralPath (Resolve-Path -LiteralPath $SourceConfig) | ConvertFrom-Json
if ($configuration.schema_version -ne 'full-restage-sources-v1') {
    throw 'Unsupported full-restage source schema'
}
$allowedCards = @($configuration.statement_cards | ForEach-Object { [string]$_ })
$catalogue = @(Get-Content -Raw -LiteralPath (Resolve-Path -LiteralPath $EvidenceCatalogue) | ConvertFrom-Json)
$runId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$runRoot = Join-Path $OutputRoot $runId
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$pushScript = Join-Path $PSScriptRoot 'push-actual-ingestion-job.ps1'
$results = [Collections.Generic.List[object]]::new()
$seenHashes = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

function Assert-InRepository([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $prefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source escapes repository root: $resolved"
    }
    return $resolved
}

function Get-RepositoryRelativePath([string]$Path) {
    $resolved = Assert-InRepository $Path
    $prefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    return $resolved.Substring($prefix.Length).Replace('\', '/')
}

function Submit-StagingJob {
    param(
        [string]$Id,
        [string]$InputPath,
        [string]$Type,
        [string]$CardCode,
        [string]$SourceMessageId,
        [string]$SourceAttachmentId
    )
    $safeId = $Id -replace '[^A-Za-z0-9._-]+', '-'
    $parameters = @{
        InputPath = (Assert-InRepository $InputPath)
        Type = $Type
        ActualMode = 'STAGE'
    }
    if ($CardCode) { $parameters.CardCode = $CardCode }
    if ($SourceMessageId) { $parameters.SourceMessageId = $SourceMessageId }
    if ($SourceAttachmentId) { $parameters.SourceAttachmentId = $SourceAttachmentId }
    if ($AIResponsesRoot) {
        $responsePath = Join-Path $AIResponsesRoot "$safeId.json"
        if (Test-Path -LiteralPath $responsePath) {
            $parameters.AIResponsesPath = (Assert-InRepository $responsePath)
        }
    }
    if ($AIHandoffComplete) { $parameters.AIHandoffComplete = $true }
    if ($PlanOnly) {
        $results.Add([ordered]@{
            id = $Id
            type = $Type
            card_code = $CardCode
            source = Get-RepositoryRelativePath $InputPath
            source_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $InputPath).Hash.ToLowerInvariant()
            job_id = $null
            status = 'PLANNED'
            staging_status = $null
            review_count = $null
            ai_request_count = 0
            ai_response_count = 0
            ai_handoff_complete = $false
            manifest_path = $null
            result_file = $null
        })
        return
    }
    $raw = @(& $pushScript @parameters)
    $parsed = ConvertFrom-Json -InputObject (($raw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine)
    $resultPath = Join-Path $runRoot "$safeId.json"
    [IO.File]::WriteAllText(
        $resultPath,
        (($parsed | ConvertTo-Json -Depth 30) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    $results.Add([ordered]@{
        id = $Id
        type = $Type
        card_code = $CardCode
        source = Get-RepositoryRelativePath $InputPath
        source_sha256 = $parsed.source_sha256
        job_id = $parsed.job_id
        status = $parsed.status
        staging_status = $parsed.staging_status
        review_count = $parsed.review_count
        ai_request_count = $parsed.ai_request_count
        ai_response_count = $parsed.ai_response_count
        ai_handoff_complete = $parsed.ai_handoff_complete
        manifest_path = $parsed.manifest_path
        result_file = Get-RepositoryRelativePath $resultPath
    })
}

foreach ($row in $catalogue | Where-Object {
    $_.document_type -eq 'statement' -and $allowedCards -contains [string]$_.card_code
} | Sort-Object statement_date, card_code) {
    $hash = [string]$row.sha256
    if (-not $seenHashes.Add($hash)) { continue }
    $sourcePath = Join-Path $repositoryRoot ([string]$row.relative_path)
    Submit-StagingJob `
        -Id ("statement-{0}-{1}" -f $row.card_code, $row.statement_date) `
        -InputPath $sourcePath `
        -Type 'STATEMENT_PDF' `
        -CardCode ([string]$row.card_code) `
        -SourceMessageId ([string]$row.message_id) `
        -SourceAttachmentId ([string]$row.attachment_id)
}

foreach ($source in @($configuration.browser_sources)) {
    Submit-StagingJob `
        -Id ([string]$source.id) `
        -InputPath (Join-Path $repositoryRoot ([string]$source.path)) `
        -Type ([string]$source.type) `
        -CardCode '' `
        -SourceMessageId '' `
        -SourceAttachmentId ''
}

$totalAIRequests = 0
foreach ($result in $results) {
    $totalAIRequests += [int]$result.ai_request_count
}
$summary = [ordered]@{
    schema_version = 'full-restage-result-v1'
    run_id = $runId
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    status = if ($PlanOnly) {
        'PLANNED'
    } elseif (@($results | Where-Object { $_.status -ne 'STAGED' }).Count) {
        'FAIL'
    } else {
        'STAGED'
    }
    source_count = $results.Count
    ai_request_count = $totalAIRequests
    ai_response_count = [int](($results | ForEach-Object { [int]$_.ai_response_count } | Measure-Object -Sum).Sum)
    ai_handoff_complete = -not (@($results | Where-Object { -not $_.ai_handoff_complete }).Count)
    results = @($results)
}
$summaryPath = Join-Path $runRoot 'summary.json'
[IO.File]::WriteAllText(
    $summaryPath,
    (($summary | ConvertTo-Json -Depth 20) + [Environment]::NewLine),
    [Text.UTF8Encoding]::new($false)
)
$summary | ConvertTo-Json -Depth 20
