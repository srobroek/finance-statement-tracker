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
coordinates. Email-statement jobs pass the exact Outlook message ID,
attachment ID, and original attachment filename through that helper. The
content-addressed inbox copy remains private to UID/GID 10002.

Every statement, browser capture, and browser export first runs all canonical
static-rule stages, then history matching, then emits a constrained
`ai_handoff`. Policy definitions and transaction snapshots are deduplicated;
`ai_handoff.requests` contains the exact transaction/policy pairs to answer and
uses `transaction_ref` when one transaction has evolving policy-time context.
A scheduled Sol task may return `ai_responses` with
`-AIResponsesPath`; the container's policy engine validates those proposals
and rebuilds the manifest. The container never calls a model itself.
Durable idempotent results are schema-upgraded from their retained audit
manifest, so response-contract changes do not require changing source identity
or weakening duplicate protection.

`-EvidenceLinksPath` accepts the validated transaction-to-catalogue handoff
created after selective Outlook evidence matching. Links must use a safe
`Finance Evidence/...` relative path and a `sha256:` identity, must target a
transaction in the staged batch, and are written into that transaction's Actual
note before its first commit.
