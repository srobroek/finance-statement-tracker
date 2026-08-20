# Statement-cycle review runbook

n8n owns the schedule and deterministic workflow. Use this runbook only for an
AI proposal/review handoff or an operator-triggered replay; do not search email
or submit an ingestion job independently.

1. Read the n8n execution receipt and resolve the exact statement source,
   archived OneDrive identity, SHA-256, message/attachment IDs, card, and period.
2. Refuse any handoff whose deterministic stages have not tied statement
   arithmetic, mapped the account, and finalized source direction/topic.
3. Answer each bounded AI request once. Propose only allowed categories, tags,
   vendors, or evidence policies. Never change amounts, dates, source IDs,
   account/card identity, reconciliation, reward arithmetic, or dedupe keys.
4. Use an empty proposal when evidence is insufficient. Any unresolved required
   field or low-confidence result remains in review.
5. For selective evidence, require strong transaction facts. Archive confirmed
   documents under the standard OneDrive path and return only their immutable
   evidence identities and catalogue links.
6. Resume the exact n8n execution/sub-workflow with the proposal set. Do not
   reconstruct the source or call Actual directly.
7. Require n8n preflight, direct Actual-node write, imported-ID readback, and a
   durable Postgres receipt before the source cursor advances.
8. Reconcile cashback notifications to the statement. Finalize the card period
   only after discrepancies are resolved; then open the next period.
9. Use the statement's due date when present. Configured offsets are forecasts.

Failed, partial, quarantined, or review-required executions remain visible in
n8n. Successful execution retention is controlled by n8n; this runbook does not
create or archive a Codex scheduled task.
