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
## Goal
As an operator, generate deterministic finance reports or dashboards from a selected snapshot, with explicit output destination and no source mutation.

## Preconditions
- Select source snapshot, account scope, period, report configuration, and output destination.
- Confirm destination is non-source/non-production and redact sensitive fields.

## Surfaces
Reporting/dashboard CLI or scripts, `finance_tracker/reports.py`, statement/transaction readers, report templates, and output artifacts.

## Steps
1. **Do:** Validate source snapshot, schema, and report parameters. **Expect:** Invalid/missing inputs fail before generation.
2. **Do:** Generate the report/dashboard into the declared destination. **Expect:** Output path, timestamp, source identity, and hash are recorded. **Expect-negative:** Source corpus, ledger, provider, and configuration are not modified.
3. **Do:** Re-run with identical inputs. **Expect:** Content is deterministic or differences are explained by timestamps/metadata.
4. **Do:** Inspect output for completeness, redaction, and unsupported assertions. **Expect:** Gaps are labeled; no live-date claim is made from stale data.

## Evidence and trace
- Beads: `orc-n2q.379.9` (author J08), review `orc-n2q.379.61`, fix `orc-n2q.379.319`.
- Source trace: `finance_tracker/reports.py`, report/dashboard scripts/templates, and corpus readers.

## Definition-of-ready audit
- [x] Stable ID/title/profile, input/output contract, deterministic generation, no-mutation checks.
- [x] Failure, stale-data, redaction, and repeat-equivalence branches.
- [x] Beads and source traces recorded.
- [ ] Canonical report route/output contract and authoritative corpus unavailable; status is draft.
