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

- **Stable ID:** J28
- **Profile:** RW-S
- **Beads contract:** `orc-n2q.379.29`
- **Lane:** exclusive-write-serial
- **Status:** draft (behavioral validation not performed)

## Goal
As an operator, load a versioned cashback profile so routing and tier calculations use the selected configuration without silently changing ledger data.

## Preconditions and surfaces
- A named profile is present in the repository configuration and passes schema/semantic validation.
- Cashback control configuration, profile loader, routing engine, and evidence snapshot are the owned surfaces.
- Relevant source evidence: `finance-statement-tracker/docs/cashback-profile.md`, `finance-statement-tracker/docs/cashback-companion-decision.md`.

## Steps
1. **Do:** Select a profile by its stable name/version and validate its currency, dates, tiers, caps, and routing rules. **Expect:** A deterministic profile identity and validation result are returned.
2. **Do:** Run the profile-dependent calculation against a disposable representative input. **Expect:** The result records profile identity, bucket/tier decision, and source transaction identity.
3. **Do (negative):** Supply an unknown profile, malformed tier, unsupported currency, or out-of-range cap. **Expect:** Validation fails closed before calculation or persistence.
4. **Do (negative):** Re-run with the same input and profile. **Expect:** No duplicate event or ledger mutation is created.

## Evidence and acceptance
- Capture profile digest, pre/post cashback state, calculation receipt, and redacted error output.
- Acceptance requires deterministic output, no Actual-ledger writes, and a fresh readback proving no duplicate state.

## Known gaps
The authoritative J01--J51 corpus and original FORMAT/INDEX are unavailable; this reconstruction follows the live Beads contract and current cashback documentation. Execution evidence is intentionally absent and remains a draft gap.
