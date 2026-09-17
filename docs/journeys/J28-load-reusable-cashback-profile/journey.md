---
id: J28
title: Load reusable cashback profile
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [cashback-control]
interfaces: [RW-S]
trace: []
---
# J28 -- Load reusable cashback profile

## Goal
As an operator, load a versioned cashback profile so routing and tier calculations use the selected configuration without silently changing ledger data.

## Preconditions
- P1: A named profile is present in the repository configuration and passes schema/semantic validation.
- P2: Cashback control configuration, profile loader, routing engine, and evidence snapshot are the owned surfaces.
- P3: Relevant source evidence: `finance-statement-tracker/docs/cashback-profile.md`, `finance-statement-tracker/docs/cashback-companion-decision.md`.
- P4: The journey uses the exclusive-write-serial lane.

## Steps
### S1 -- Validate profile {#S1}
- **Do:** Select a profile by its stable name/version and validate its currency, dates, tiers, caps, and routing rules.
- **Expect:** A deterministic profile identity and validation result are returned.

### S2 -- Calculate representative input {#S2}
- **Do:** Run the profile-dependent calculation against a disposable representative input.
- **Expect:** The result records profile identity, bucket/tier decision, and source transaction identity.

### S3 -- Reject invalid profile {#S3}
- **Do:** Supply an unknown profile, malformed tier, unsupported currency, or out-of-range cap.
- **Expect (negative):** Validation fails closed before calculation or persistence.

### S4 -- Replay profile calculation {#S4}
- **Do:** Re-run with the same input and profile.
- **Expect (negative):** No duplicate event or ledger mutation is created.

## Success criteria
- SC1: S1-S4: Capture profile digest, pre/post cashback state, calculation receipt, and redacted error output.
- SC2: S1-S4: Acceptance requires deterministic output, no Actual-ledger writes, and a fresh readback proving no duplicate state.

## Known gaps
- G1: The authoritative J01--J51 corpus and original FORMAT/INDEX are unavailable; this reconstruction follows the live Beads contract and current cashback documentation. Execution evidence is intentionally absent and remains a draft gap. Trace evidence: Beads contract `orc-n2q.379.29`.

## Delta log
- No behavior delta; structural normalization only.
