---
id: J46
title: Inject runtime secrets
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [runtime-secrets]
interfaces: [RW-O]
trace: [orc-n2q.379.47, orc-n2q.379.99]
---
# J46 -- Inject runtime secrets

## Goal
Inject only the approved runtime secret values into the existing execution boundary without persisting, printing, or widening their scope.

## Preconditions
- P1: The target runtime and exact workflow/run are identified.
- P2: MFA/OTP remains a user boundary; the operator has approved vault scope and a protected pre-state receipt.
- P3: The exclusive write lane, rollback/compensation path, and redacted receipt location are available.
- P4: n8n credential/runtime configuration, finance host environment, Codex runner, and protected audit receipt. This is RW-O: secret injection is a consequential write; all inspection-only checks must prove no mutation.

## Steps
### S1 -- Bind target and pre-state {#S1}
- **Do:** Capture non-secret identity, current binding, scope, and protected pre-state.
- **Expect:** Exactly the named target is bound.
- **Expect (negative):** No secret value is exposed.
- **Expect (negative):** Missing identity, stale pre-state, or ambiguous scope stops before mutation.

### S2 -- Obtain secret {#S2}
- **Do:** Read the approved secret through the runtime credential authority, in memory only.
- **Expect:** The value is available only to the bounded operation.
- **Expect (negative):** Do not copy it into Git, logs, artifacts, shell history, or chat.

### S3 -- Inject {#S3}
- **Do:** Apply the reviewed binding through the existing writer in the exclusive lane.
- **Expect:** Only the named runtime field changes and the operation emits a redacted receipt.
- **Expect (negative):** No new listener, credential, runner, or duplicate service is created.

### S4 -- Read back {#S4}
- **Do:** Re-read identity, presence/type, scope, and permissions without reading the value.
- **Expect:** Binding is present at the intended surface and unrelated state is unchanged.
- **Expect (negative):** Any mismatch fails closed and invokes the reviewed rollback.

### S5 -- Roll back if required {#S5}
- **Do:** With explicit approval, restore the captured binding or remove the operation-created value; verify afterward.
- **Expect:** Pre-state identity and health are restored.
- **Expect (negative):** Never retry with broader privileges or print the secret.

## Success criteria
- SC1: S1-S5: Acceptance requires redacted pre/post receipts, exact-scope parity, and no secret plaintext.

## Known gaps
- G1: Authoritative J01-J51 corpus refs `2cd7612`/`161de41` are absent in this checkout; this draft is reconstructed from the Beads contract and current source. No production execution is claimed. Trace evidence: `AGENTS.md:9-13,27-36`, `README.md:84-100`, `config/codex-automations.json`, `finance_tracker/automation_manifest.py`, Beads `orc-n2q.379.47`, and review `orc-n2q.379.99`.

## Delta log
- No behavior delta; structural normalization only.
