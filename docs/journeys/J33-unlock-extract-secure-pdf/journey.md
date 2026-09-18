---
id: J33
title: Unlock/extract secure PDF
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [evidence-ingestion]
interfaces: [RW-O]
trace: []
---
# J33 -- Unlock/extract secure PDF

## Goal
As a finance operator, approve a fixed scope and unlock a protected statement PDF. Hand one complete package to the designated parser. Done means that cleanup removes all decrypted material. Fresh readback must prove that the encrypted source remains unchanged.

## Preconditions
- P1: The encrypted source PDF is archived, and its authorized unlock material is available through the protected runtime boundary.
- P2: The operator can identify the approved extractor, the designated parser principal, and the retention policy without viewing statement contents.
- P3: The extraction lane can record source and artifact hashes, access modes, and page counts in a redacted receipt.
- P3a: The receipt can also record approval and cleanup evidence.
- P3b: The receipt can record readback results and the compensation outcome.
- P4: The journey uses the exclusive-write-serial lane.
- P5: Evidence references are `finance-statement-tracker/docs/full-ingestion-validation.md` and `finance-statement-tracker/docs/wealth-ingestion.md`.

## Steps
### S1 -- Verify source and pre-state {#S1}
- **Do:** Read the archived source metadata and calculate its digest. Verify that no decrypted workspace or parser handoff exists.
- **Expect:** The pre-state receipt identifies one archive location and one source digest.
- **Expect:** The receipt also records the source byte count and retention policy.
- **Expect (negative):** The source bytes and archive metadata remain unchanged.
- **Expect (negative):** Parser state remains unchanged. The cursor and ledger also remain unchanged.

### S1a -- Approve the fixed extraction scope {#S1a}
- **Do:** Review the fixed plan described by P3 and P3a. Before extraction starts, approve its exact plan digest.
- **Expect:** The receipt binds one operator approval to the immutable plan.
- **Expect:** The approval time precedes the extractor start time.
- **Expect (negative):** The extractor starts only after approval. A scope or plan change requires a new review.

### S2 -- Extract in a restricted temporary workspace {#S2}
- **Do:** Create an owner-only temporary workspace and verify its access modes. Run the approved extractor through the protected unlock boundary.
- **Expect:** The redacted receipt binds the approved plan to the source digest and extractor identity.
- **Expect:** The receipt records output hashes and page counts. It also records completeness checks, access modes, and the outcome.
- **Expect:** Decrypted bytes exist only from extractor start until parser acknowledgment or failure cleanup, whichever occurs first.
- **Expect (negative):** Unlock material and decrypted text never appear in command output or logs.
- **Expect (negative):** Financial content never appears in telemetry or the receipt.

### S2a -- Publish one complete parser handoff {#S2a}
- **Do:** Stage the verified extraction outside parser-readable space, then publish the complete package to the designated parser principal.
- **Expect:** Access evidence identifies the designated parser principal and proves that it received one package whose hashes and page count match the receipt.
- **Expect (negative):** The parser cannot read staging files or incomplete output.
- **Expect (negative):** The parser cannot read another source's output. After the extractor revokes access, it cannot read the package.

### S2b -- Read back and clean the successful run {#S2b}
- **Do:** After parser acknowledgment, revoke handoff access and delete all decrypted artifacts. Read the archive and filesystem state again.
- **Expect:** A fresh archive read returns the original digest.
- **Expect:** A fresh workspace and handoff readback finds zero decrypted artifacts.
- **Expect:** The encrypted source and redacted receipt follow their configured retention policies.
- **Expect:** Decrypted material has no retention.
- **Expect (negative):** Cleanup does not delete or rewrite the encrypted source.
- **Expect (negative):** No plaintext remains available to the extractor or parser principal.

### S3 -- Compensate a failed extraction {#S3}
- **Do:** Compensate if the unlock material is wrong or the input is malformed.
- **Do:** After incomplete extraction, readback mismatch, or handoff failure, start compensation.
- **Do:** Revoke parser access. Delete all decrypted artifacts.
- **Expect:** The redacted failure receipt records the failed check and compensation actions.
- **Expect:** The receipt reports zero accepted handoffs and zero cursor advances.
- **Expect (negative):** No partial package reaches the parser.
- **Expect (negative):** The source and archive metadata remain unchanged. The cursor and ledger also remain unchanged.

### S3a -- Prove the post-failure state {#S3a}
- **Do:** After compensation completes, do a fresh archive readback and workspace listing. Check handoff access and redaction again.
- **Expect:** The source digest matches the pre-state, and the decrypted artifact count is zero.
- **Expect:** The extractor denies parser access. The receipt reports zero accepted handoffs.
- **Expect (negative):** After the failed run, logs and telemetry contain no unlock material.
- **Expect (negative):** They also contain no decrypted text or financial content.

### S4 -- Replay extraction {#S4}
- **Do:** Re-run the same source through a newly reviewed and approved fixed plan.
- **Expect:** The replay preserves the source identity and records whether the extracted artifact identity matches the prior run.
- **Expect (negative):** No duplicate downstream import, cursor advance, or retained decrypted artifact occurs.

## Success criteria
- SC1: S1-S1a record one fixed plan and one approval before extraction. Each plan change requires another approval.
- SC2: S2-S2a prove owner-only temporary access and a lifecycle-bounded decrypted lifetime.
- SC3: S2a proves that the parser can access one complete verified package only.
- SC4: S2b and S3-S3a prove that the source digest is unchanged and the decrypted artifact count is zero.
- SC5: S2b and S3-S3a prove revoked parser access, zero cursor advances, and zero ledger mutations.
- SC6: S2-S3a redaction checks find zero protected values in logs, telemetry, and receipts.
- SC7: S4 preserves one source identity and one accepted downstream import. It retains zero decrypted artifacts.

## Known gaps
- G1: No execution receipt proves the approval or access-control requirements.
- G1a: No execution receipt proves the redaction, cleanup, or readback requirements.
- G1b: No execution receipt proves the no-partial-handoff requirement.
- G1c: Validation must produce this evidence before the journey can leave draft status. Trace evidence: Beads contract `orc-n2q.379.34`.

## Delta log
- No behavior delta. This change corrects readiness details.
