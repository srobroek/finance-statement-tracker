# Transaction ingestion contract

n8n parses and classifies each archived notification with the canonical Python
parser and configuration. Cashback receives one normalized event per request;
raw messages and HTML stay in the acquisition/archive workflow.

All endpoints require the existing ingest bearer credential. The request limit
remains 1 MB.

1. `POST /api/ingest/transaction` accepts exactly `source`, `completed_at`,
   `cursor`, and `event` (one canonical notification event). `cursor` must equal
   the frozen scan completion timestamp. It returns persistence counters and a
   `service_receipt` containing `receipt_id`, `receipt_sha256`, event IDs/digests,
   scan fields, `state`, and `receipt_kind: TRANSACTION`. Replaying an event does
   not insert another transaction. Child receipts cannot advance a cursor.
2. After every archived message has a disposition, `POST /api/ingest/receipt`
   accepts `source`, `completed_at`, `cursor`, `scanned_count`, `accepted_count`,
   `ignored_count`, `review_count`, `receipts`, and `message_dispositions`.
   `receipts` contains only each child's `receipt_id` and `receipt_sha256`.
   Each disposition has a unique `message_id` and `status`. `ACCEPTED` requires
   `source_event_id` equal to `message_id + ':0'`. `IGNORED` and `REVIEW` require
   a nonempty `reason` and no event ID. Review here means the parser could not
   produce a valid event; an accepted event's `review_required` flag does not
   change its accepted disposition. Counts must reconcile exactly. Accepted
   IDs must match persisted child receipts and unchanged stored transactions.
   The final receipt digest binds the sorted complete disposition list.
3. Submit the final `service_receipt` and matching source/completion/cursor/
   scanned/accepted fields to the existing `POST /api/ingest-runs` endpoint.
   Only this step commits the cursor and successful-check timestamp.

Both new endpoints return `cursor_candidate` and `cursor_committed: false`.
A zero-message scan uses zero counts and empty receipt/disposition lists; its
final commit is a successful heartbeat even though no transaction was added.
The workflow must compare dispositions against its independently verified
archive inventory before requesting the final receipt. Cashback cannot infer
mailbox completeness from transactions alone.

If any request fails, leave the cursor unchanged and replay the frozen scan.
Already accepted transactions remain visible and replay idempotently. A reused
source ID with changed economics, missing child receipt, duplicate message,
changed stored event, or wrong scan binding fails closed.
