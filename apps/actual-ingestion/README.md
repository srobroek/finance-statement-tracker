# Actual ingestion worker

This container stages evidence-backed PDF statements and browser captures into
auditable Actual import manifests. It includes the deterministic Python
normalizers and the official `@actual-app/api` Node bridge.

`POST /api/jobs` accepts `STATEMENT_PDF`, `BROWSER_CAPTURE`, or
`BROWSER_EXPORT`. Source files must already exist under the persistent
`inbox/` directory and requests use relative paths only. `actual_mode` is one
of `STAGE`, `PREFLIGHT`, or `COMMIT`; production writes additionally require
`ALLOW_ACTUAL_WRITES=true` and the Actual runtime credentials.

RAKBANK and Standard Chartered statement definitions are intentionally
non-importing placeholders until real PDF fixtures are captured and parser
tests pass. Synthetic fixtures are test evidence only and never activate a
placeholder adapter.

The service is deliberately host-local on the Docker server. Scheduled Codex
jobs upload one evidence file and submit one deterministic job through
`scripts/push-actual-ingestion-job.ps1`; deployment details come from
`config/deployment.json`, so automation does not hard-code SSH or container
coordinates.
