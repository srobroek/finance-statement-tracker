---
id: J42
title: Inspect n8n workflows/Data Tables
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [operations-operator]
surfaces: [n8n-orchestration]
interfaces: [RO]
trace: [orc-n2q.379.43, orc-n2q.379.199, 4f115c64351b24554ec9b2ada6b0166786fda727]
---
# J42 -- Inspect n8n workflows/Data Tables

## Goal
Inspect the relevant n8n workflows and Data Tables to understand configuration and state without changing them.

## Preconditions
- P1: Read-only credentials and the target workflow/table identifiers are available.
- P2: The operator records a before snapshot and uses a read-only session.

## Steps
### S1 -- Locate workflow {#S1}
- **Do:** Search by stable workflow identifier and inspect its status, version, nodes, and connections.
- **Expect:** The requested workflow is identified unambiguously.
- **Expect (negative):** No activation or save occurs.

### S2 -- Inspect Data Tables {#S2}
- **Do:** Read relevant table schema, row counts, and keyed values needed for diagnosis.
- **Expect:** Values are attributable to a table/version snapshot.
- **Expect:** Secrets are redacted.
- **Expect (negative):** Data remains unchanged.

### S3 -- Compare and report {#S3}
- **Do:** Compare workflow references with table state and record discrepancies.
- **Expect:** Findings identify exact workflow/table references and preserve a post-snapshot proving no mutation.

## Success criteria
- SC1: S1-S3 yield a reproducible, read-only configuration report with no activation, save, or row mutation.

## Known gaps
- G1: n8n access and current table snapshots are unavailable; runtime inspection is intentionally unclaimed.

## Delta log
- No behavior delta; structural normalization only.
