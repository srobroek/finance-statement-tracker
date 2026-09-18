---
id: J13
title: Apply budgets/savings/cleanup pools
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [finance-core]
interfaces: [RW-S]
trace: []
---
# J13 -- Apply budgets/savings/cleanup pools

## Goal
A finance operator previews and installs the repository's Actual automation definitions on an approved disposable target. The CLI reports `status: "applied"` for the reviewed changes. The command does not execute a monthly budget or cleanup.

## Preconditions
- P1: Use `config/actual-budget-automation.json` with Actual server version `26.8.1`. Create every category named by the config.
- P2: Use the exclusive RW-S write lane and record the disposable target's identity.
- P3: Capture a protected pre-state before any write. Do not apply until a reviewed restore procedure can return the target to that pre-state.
- P4: Record approval for the exact S2 preview on the recorded target.
- P5: Set `ACTUAL_SERVER_URL`, `ACTUAL_PASSWORD`, and `ACTUAL_SYNC_ID` for the recorded target.

## Steps
### S1 -- Inspect the automation definitions {#S1}
- **Do:** Inspect `config/actual-budget-automation.json` and record its SHA-256 hash.
- **Expect:** The file declares schema `actual-budget-automation-v1` and Actual version `26.8.1`.
- **Expect:** The file defines category templates and cleanup roles.
- **Expect (negative):** The file contains no execution fixture.

### S2 -- Preview the definition changes {#S2}
- **Do:** Run `node integrations/actual/actualctl.mjs budget-automation --config config/actual-budget-automation.json`.
- **Expect:** The JSON result reports `status: "planned"`, `required_actual_version: "26.8.1"`, and a `changes` array.
- **Expect (negative):** The command does not install definitions. Opening the budget can refresh the local Actual cache, so cache files are not no-mutation evidence.

### S3 -- Approve and install the definitions {#S3}
- **Do:** After P1-P5 hold, run `ALLOW_ACTUAL_WRITES=true node integrations/actual/actualctl.mjs budget-automation --config config/actual-budget-automation.json --apply`.
- **Expect:** The JSON result reports `status: "applied"`, `required_actual_version: "26.8.1"`, and the applied `changes`.
- **Expect (negative):** Applying definitions does not execute a monthly budget or cleanup. The result is not a durable receipt and contains no target or object IDs.

## Success criteria
- SC1: S1-S2 use the reviewed config hash and return `status: "planned"` without installing definitions.
- SC2: S3 runs only after P1-P5 hold and returns `status: "applied"` for the approved `changes`.
- SC3: No step claims to execute monthly budgeting, savings allocation, or cleanup pools.

## Known gaps
- G1: The repository has no command or fixture for month execution, amount allocation, or cleanup-pool execution.
- G2: The apply command has no durable receipt, target or object IDs, automatic readback, or rollback operation.
- G3: S3 remains blocked until the RW-S profile includes reviewed procedures for capture and verified restoration.

## Delta log
