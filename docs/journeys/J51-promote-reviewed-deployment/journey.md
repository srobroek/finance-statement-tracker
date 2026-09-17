---
id: J51
title: Promote reviewed deployment
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [release-promotion]
interfaces: [RW-S]
trace: [orc-n2q.379.52, orc-n2q.379.104]
---
# J51 -- Promote reviewed deployment

## Goal
Promote exactly the independently reviewed deployment after all gates pass, with a bounded cutover, readback, and rollback path.

## Preconditions
- P1: Reviewed commit/image/config digests, approvals, tests, backup, target, and exclusive writer lane are bound.
- P2: Existing workflows are quiesced or in the explicitly reviewed inactive state; rollback is recoverable.

## Steps
### S1 -- Verify review boundary {#S1}
- **Do:** Compare target to reviewed head and confirm required tests, security checks, and approvals.
- **Expect:** Exact intended delta and no unrelated work are present.
- **Expect (negative):** Missing approval, drift, or failed gate stops promotion.

### S2 -- Capture pre-state {#S2}
- **Do:** Record protected deployment/runtime identities, health, workflow state, and backup.
- **Expect:** Cutover and compensation are reversible.
- **Expect (negative):** Never promote from a dirty or ambiguous checkout.

### S3 -- Promote {#S3}
- **Do:** Apply only the reviewed artifact through the existing deployment owner.
- **Expect:** Target identity/digest changes exactly as reviewed and no extra service/listener appears.
- **Expect (negative):** No broad rebuild, credential rotation, or unrelated cleanup.

### S4 -- Read back {#S4}
- **Do:** Verify health, workflow activation/write policy, route, semantic readback, and receipt parity.
- **Expect:** Runtime matches the reviewed contract and no unintended financial mutation occurred.
- **Expect (negative):** Any mismatch triggers rollback rather than a retry.

### S5 -- Roll back if required {#S5}
- **Do:** Restore the captured deployment and verify fresh health and identity.
- **Expect:** Previous known-good state is restored and the incident is recorded.
- **Expect (negative):** Do not claim readiness without post-rollback proof when promotion is indeterminate.

## Success criteria
- SC1: S1-S5: Acceptance requires exact reviewed-head parity, independent approval, protected pre/post/rollback receipts, and current runtime readback.

## Known gaps
- G1: The authoritative corpus refs `2cd7612`/`161de41` and current deployment receipts are absent; historical draft evidence must not be treated as final readiness. Trace evidence: `AGENTS.md:29-39,61-67`, `README.md:88-100`, `.github/`, `deploy/`, Beads `orc-n2q.379.52`, and review `orc-n2q.379.104`.

## Delta log
- No behavior delta; structural normalization only.
