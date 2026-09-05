# Live cashback review runbook

n8n owns live-notification acquisition, scheduling, persistence, and cursor
advancement. This runbook accepts only an explicit n8n review handoff. It must
not initiate a separate mailbox scan or submit an independent ingestion batch.

1. Read the exact source configuration and supplied n8n execution receipt.
   Require the source to be active and the frozen mailbox window, complete
   pagination, message identities, and downstream receipt to be correlated.
2. Review only the unresolved fields allowed by the handoff policy. Preserve
   amounts, dates, card identity, source IDs, deduplication keys, manual locks,
   reward arithmetic, and reconciliation state.
3. Return an empty proposal when the supplied evidence is insufficient. Require
   durable redacted receipts for failed, partial, ambiguous, or review-required
   runs; the deployed retention policy may discard execution history.
4. Return bounded proposals to the exact n8n execution. The workflow and
   companion validate and persist them; this review never advances a cursor.
5. Live notifications may update cashback buckets immediately, but statement
   evidence and successful reconciliation are required to finalize a period.

The retired Codex scan identity remains paused in the automation manifest so
an installation audit can detect a duplicate scheduler. This runbook does not
create, run, or archive a Codex scheduled ingestion task.
