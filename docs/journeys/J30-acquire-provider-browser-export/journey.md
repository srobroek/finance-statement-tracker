---
id: J30
title: Acquire provider browser export
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
<!-- slopvac-allow: rule=ste-nouns.multiword-noun-too-long reason=identifier-fidelity -->
# J30 -- Acquire provider browser export

## Goal
Acquire a provider export as immutable evidence. Prove that another user cannot reuse the run's authorization or browser session.

## Preconditions
- P1: An owner-authorized provider session is available. This journey never guesses or enters credentials.
- P2: The provider contract identifies these reviewed controls:
  - export operation, endpoint, and returned identifiers
  - authorization revocation endpoint and logout
  - session invalidation and quarantine endpoint
- P3: The n8n contract identifies these controls:
  - authorized runtime identity
  - execution cancellation endpoint
  - receipt quarantine
- P4: The operator can query each state outside the acquisition session:
  - provider authorization and browser authentication
  - n8n execution
  - document and receipt
- P5: Browser acquisition and archive writes use the `exclusive-write-serial` lane.
- P6: Evidence comes from `docs/browser-ingestion.md`, `docs/full-ingestion-validation.md`, and Beads contract `orc-bi1a`.

## Steps
### S1 -- Record capture and rollback scope {#S1}
- **Do:**
  1. Capture fresh identifiers for the browser context and session.
  2. Read provider authorization and export state.
  3. Read existing document and receipt state.
  4. Record the reviewed operations and runtime identity from P2-P3.
- **Expect:** The redacted pre-state distinguishes each existing object from objects that this run can create.
- **Expect (negative):** Writes need every value from P2-P4.

### S2 -- Approve the write scope {#S2}
- **Do:** At the point of risk, get explicit approval for:
  - source and provider
  - export identifier
  - planned receipt change
- **Expect:** The approval limits the run to one provider export and one matching receipt.
- **Expect (negative):** Do not use open-ended approval.

### S3 -- Acquire the immutable export {#S3}
- **Do:** Use only the reviewed export operation. Hash the artifact and record the identifiers that the provider and n8n return.
- **Expect:** The manifest records these values:
  - source identity and capture time
  - artifact SHA-256
  - `source_code`
  - `source_message_id` or `source_attachment_id`
  - `archive_receipt_id` and `run_id`
  - `execution_id`
- **Expect (negative):** Do not infer, replace, or broaden a returned identifier.

### S4 -- Deduplicate the artifact {#S4}
- **Do:** Submit the same artifact through the reviewed operation.
- **Expect:** Deduplication resolves to the same source identity and artifact SHA-256.
- **Expect (negative):** The operation must not create a duplicate. This applies to the evidence object and receipt.

### S5 -- Verify that the archive matches {#S5}
- **Do:** Read the archived evidence through an independent archive query.
- **Expect:** The result matches the manifest identity and artifact SHA-256.
- **Expect (negative):** The acquisition must not write financial product data or ledger entries.

### S6 -- End the authenticated session {#S6}
- **Do:** On success, failure, or interruption:
  1. Revoke the provider authorization for this run.
  2. Log out.
  3. Close and dispose the headed page and browser context.
  4. Invalidate the captured session.
- **Expect:** The provider reports no authorization for the run. The browser reports a closed context and an invalid session.
- **Expect (negative):** Always clean up. An acquisition result cannot bypass cleanup.

### S7 -- Cancel and quarantine run-created state {#S7}
- **Do:** On success, failure, or interruption:
  1. Use the authorized runtime identity.
  2. Cancel the exact nonterminal `execution_id`.
  3. Quarantine only the run-created export or document.
  4. Quarantine its matching durable receipt.
- **Expect:** Match the target with these values:
  - `source_code`
  - `source_message_id` or `source_attachment_id`
  - source SHA-256
  - `archive_receipt_id` and `run_id`
- **Expect (negative):** Never modify an existing or foreign object. Never delete the durable receipt.

### S8 -- Prove teardown and rollback {#S8}
- **Do:** Query each state independently:
  1. provider authorization
  2. browser authentication
  3. n8n execution
  4. document
  5. durable receipt
- **Expect:** The evidence proves each result:
  - The provider reports revoked authorization.
  - Unauthenticated browser access fails.
  - The execution is `CANCELLED` or terminal.
  - The target document is `QUARANTINED`.
  - The receipt is `QUARANTINED` or has a redacted terminal marker for cleanup.
  - Every identity and hash matches the manifest.
- **Expect (negative):** Closing the window proves none of these results:
  - logout or revocation
  - session invalidation
  - execution cancellation
  - document or receipt quarantine

### S9 -- Fail closed on incomplete cleanup {#S9}
- **Do:** When an identity mismatches or a cleanup query fails, preserve redacted evidence and stop.
- **Expect:** The run records `FAILED` or `INCONCLUSIVE`. Handoff and promotion remain blocked.
- **Expect (negative):** After incomplete cleanup, do not claim success.

## Success criteria
- SC1: S1-S5 produce one manifest. Its source identifiers and SHA-256 match the independent archive result.
- SC2: S6-S8 prove each result:
  - revoked authorization and an invalid session
  - denied unauthenticated access
  - a canceled or terminal execution
  - quarantined run-created document and receipt state
- SC3: S1-S9 create none of these side effects:
  - duplicate evidence object
  - financial product or ledger write
  - durable receipt deletion
  - mutation of an existing or foreign object
- SC4: S9 blocks handoff and promotion after:
  - missing prerequisites
  - identity mismatch
  - cleanup failure
  - failed readback

## Known gaps
- G1: Validation requires the provider operations and authorized identities in P2-P3. It also requires n8n cancellation and receipt quarantine. A missing value blocks all writes. The journey stays in draft.

## Delta log
- None.
