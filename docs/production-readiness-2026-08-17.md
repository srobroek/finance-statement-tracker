# Production readiness audit

Date: 2026-08-17

This audit distinguishes implemented behavior from behavior that can only be validated after a bank supplies a real message, statement, or portal export. “Waiting for evidence” is intentionally fail-closed and does not authorize synthetic production data.

| Capability | State | Production evidence |
|---|---|---|
| Actual-first architecture | Ready | Actual owns posted ledger and budgets; Cashback Control owns live routing state; OneDrive owns evidence. No Notion runtime or migration dependency remains. |
| Bank-neutral statement contract | Ready | Emirates Islamic, ADCB, and Wio adapters emit the same normalized statement model and pass parser/arithmetic tests. RAKBANK and Standard Chartered are explicit non-importing placeholders. |
| Morning Outlook acquisition | Ready | The active Luna/max RAKBANK task runs at 08:05 Dubai, scans from the durable cursor minus overlap, submits exact Outlook objects, and commits the cursor only after the companion acknowledges the run. This recovers missed schedule intervals without a second daily scan. Standard Chartered's 08:25 task remains paused pending a real message contract. |
| Live notification parsing | Partially evidenced | Real RAKBANK messages are parsing and deduplicating in production. Standard Chartered has a separate paused placeholder task. Emirates Islamic, ADCB, and Wio are intentionally absent from live scanning. |
| Static classification | Ready | AutoCat-style OR-of-AND rules, stages, priorities, multiple actions, stop semantics, locks, and scoped `LIVE_CASHBACK` membership pass regression tests. |
| AI enrichment | Ready | Sol receives only unresolved, policy-allowed fields after deterministic stages. Policy trigger fields and transaction/card conditions prevent tag-only, subscription, evidence, or cashback calls from running outside their scope. A completed handoff must answer every emitted transaction/policy request. Protected facts, arithmetic, source identity, reconciliation, and locked values cannot be modified. |
| Actual guarded commit | Ready | Two real Emirates Islamic statements and a real 20-row Wio credit statement were preflighted, committed or verified by stable imported IDs, and replayed idempotently with zero duplicates. The Wio readback contains 20 cleared transactions, 20 unique imported IDs, no uncategorized rows, and an AED 274.40 credit balance. |
| Statement reconciliation | Ready; awaiting current-cycle evidence | Matching, variance handling, acknowledgement, authoritative row replacement, and finalization gates pass tests. No current cashback period is finalized because no matching current-cycle statement has arrived. |
| Cashback routing | Ready | Valid live notifications update tier, bucket headroom, weekly pace, warnings, and dynamic recommendations immediately. EI is configured as unlimited 6% Amazon cashback with statement-only totals. Four unrelated fictional programme profiles pass Linux deployment tests. |
| Mobile PWA and push | Ready | Dashboard is installable, responsive, and uses profile-derived card labels. Bucket, target, routing-change, and stale-feed pushes are deduplicated; native delivery reached both registered endpoints. |
| Scheduled operations | Ready | One daily morning RAKBANK ingest and card/source-specific statement jobs are configured. Standard Chartered's morning and monthly tasks remain prepared but paused until real formats are verified. The six exact task contracts and two shared runbooks are versioned and can be audited against the installed Codex automations. Card close remains statement- and reconciliation-gated; the legacy daily close is paused. |
| Browser acquisition | Ready; real export staged, commit awaiting review | The original ADCB portal CSV at legacy `_source/adcb_may_jun.csv` produced the same SHA-256 (`32444d7848209c69842e83caeb89fbb273fa46b3064625444576517786a310dc`) locally and in the production container. Both parsed 303/303 rows with zero import blockers. Static rules cleared 11 deterministic cashback/payment credit flags, leaving one genuinely ambiguous credit for review. Production job `6f30c9019002819fe37227f5` proves upload, content-addressing, parsing, account mapping, rule staging, and durable job persistence without writing Actual. |
| Evidence archive | Ready | Exact Outlook identity, redacted content snapshots, content hashes, OneDrive archive paths, and catalogue linkage are proven with real Emirates Islamic statements plus two SmartHotel receipts and one SAS booking linked to six exact Wio rows. Vendor-only candidates still fail closed. |
| Credentials | Ready | Runtime secrets are injected from the dedicated 1Password-backed environment; statement and application passwords are absent from source and logs. |
| Backup and recovery | Ready | Daily quiesced backup checksum passed. Five-minute watchdog recovered a deliberately stopped cashback service without restarting Actual or ingestion. |
| Reproducible deployment | Ready | The current release passed 195 Python tests, 10 Node tests, offline Actual integration, image builds, and fictional profiles. Both GitHub Actions pipelines publish immutable GHCR images, fetch the exact deployment SHA with native Git, independently recreate their owned container, and complete live verification. Production containers must carry the matching OCI revision label. |

## Evidence still required

1. A real RAKBANK monthly statement fixture to implement and validate its statement adapter.
2. A real Standard Chartered statement fixture and, separately, a representative transaction-notification message if live SC ingestion is desired.
3. Owner review/correction of the one ambiguous credit in the staged real ADCB browser export before any browser-originated Actual commit. The raw-export acquisition and production staging path are now evidenced.
4. Revalidation after any issuer programme change. The current RAKBANK World, Standard Chartered Platinum X, and Emirates Islamic Amazon configurations are versioned from the cardholder-confirmed terms recorded on 2026-08-17; they are configuration, not immutable issuer facts.
These items do not justify weakening validation. Until the corresponding evidence exists, the affected adapter, browser import, or finalization step must remain non-importing and retryable.
