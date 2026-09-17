---
id: J08
title: Generate reports/dashboards
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RO]
trace: []
---
# J08 -- Generate reports/dashboards

## Goal
As an operator, generate deterministic finance reports or dashboards from a selected snapshot, with explicit output destination and no source mutation.

## Preconditions
- P1: Select source snapshot, account scope, period, report configuration, and output destination.
- P2: Confirm destination is non-source/non-production and redact sensitive fields.
- P3: Surfaces include reporting/dashboard CLI or scripts, `finance_tracker/reports.py`, statement/transaction readers, report templates, and output artifacts.

## Steps
### S1 -- Validate inputs {#S1}
- **Do:** Validate source snapshot, schema, and report parameters.
- **Expect:** Invalid/missing inputs fail before generation.
### S2 -- Generate output {#S2}
- **Do:** Generate report/dashboard into declared destination.
- **Expect:** Output path, timestamp, source identity, and hash are recorded.
- **Expect (negative):** Source corpus, ledger, provider, and configuration are not modified.
### S3 -- Repeat generation {#S3}
- **Do:** Re-run with identical inputs.
- **Expect:** Content is deterministic or differences are explained by timestamps/metadata.
### S4 -- Inspect report {#S4}
- **Do:** Inspect output for completeness, redaction, and unsupported assertions.
- **Expect:** Gaps are labeled; no live-date claim is made from stale data.

## Success criteria
- SC1: S1 rejects invalid or missing inputs before generation.
- SC2: S2 emits a traceable output without modifying source or configuration.
- SC3: S3-S4 establish repeat equivalence or explain metadata-only differences and label gaps.

## Known gaps
- G1: Canonical report route/output contract and authoritative corpus are unavailable; status remains draft. Beads: `orc-n2q.379.9` author J08, review `orc-n2q.379.61`, fix `orc-n2q.379.319`. Source refs: `finance_tracker/reports.py`, report/dashboard scripts/templates, and corpus readers.

## Delta log
- No behavior delta; structural normalization only.
