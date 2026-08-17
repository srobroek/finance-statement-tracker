# Production readiness audit

Date: 2026-08-17

This audit distinguishes implemented behavior from behavior that can only be validated after a bank supplies a real message, statement, or portal export. “Waiting for evidence” is intentionally fail-closed and does not authorize synthetic production data.

| Capability | State | Production evidence |
|---|---|---|
| Actual-first architecture | Ready | Actual owns posted ledger and budgets; Cashback Control owns provisional live routing state; OneDrive owns evidence. No Notion runtime or migration dependency remains. |
| Bank-neutral statement contract | Ready | Emirates Islamic, ADCB, and Wio adapters emit the same normalized statement model and pass parser/arithmetic tests. RAKBANK and Standard Chartered are explicit non-importing placeholders. |
| Hourly Outlook acquisition | Ready | Sol task scans from the durable cursor minus overlap, submits exact Outlook objects, and commits the cursor only after the companion acknowledges the run. Latest successful cursor: `2026-08-17T14:07:37.571291Z`. |
| Live notification parsing | Partially evidenced | Real RAKBANK messages are parsing and deduplicating in production. ADCB authorization mail is supported conservatively. Standard Chartered and Emirates Islamic notification formats require representative messages before adapters can be enabled. |
| Static classification | Ready | AutoCat-style OR-of-AND rules, stages, priorities, multiple actions, stop semantics, locks, and scoped `LIVE_CASHBACK` membership pass regression tests. |
| AI enrichment | Ready | Sol receives only unresolved, policy-allowed fields after deterministic stages. Protected facts, arithmetic, source identity, reconciliation, and locked values cannot be modified. |
| Actual guarded commit | Ready | Two real Emirates Islamic statements were preflighted, committed or verified by stable imported IDs, and replayed idempotently with zero duplicates. |
| Statement reconciliation | Ready; awaiting current-cycle evidence | Matching, variance handling, acknowledgement, authoritative row replacement, and finalization gates pass tests. No current cashback period is finalized because no matching current-cycle statement has arrived. |
| Cashback routing | Ready | Live provisional events update tier, bucket headroom, weekly pace, warnings, and dynamic recommendations immediately. Four unrelated fictional programme profiles pass Linux deployment tests. |
| Mobile PWA and push | Ready | Dashboard is installable, responsive, and uses profile-derived card labels. Bucket, target, routing-change, and stale-feed pushes are deduplicated; native delivery reached both registered endpoints. |
| Scheduled operations | Ready | One hourly ingest and five card/source-specific month-start statement jobs are active. Card close remains statement- and reconciliation-gated; the legacy daily close is paused. |
| Browser acquisition | Ready for staged capture; awaiting a real raw export | Provider/data recipes, official-export parsers, review gates, and the standard Actual handoff pass tests. The available legacy CSVs are derived normalized outputs, not authoritative raw bank exports, and correctly fail closed. |
| Evidence archive | Ready | Exact Outlook message/attachment identity, content hash, OneDrive archive path, and catalogue linkage are proven with real Emirates Islamic statements. |
| Credentials | Ready | Runtime secrets are injected from the dedicated 1Password-backed environment; statement and application passwords are absent from source and logs. |
| Backup and recovery | Ready | Daily quiesced backup checksum passed. Five-minute watchdog recovered a deliberately stopped cashback service without restarting Actual or ingestion. |
| Reproducible deployment | Ready | Commit `0d2d4c4` passed 168 Python tests, 10 Node tests, offline Actual integration, image builds, and fictional profiles. Both GitHub Actions pipelines published GHCR images, fetched the exact deployment SHA with native Git, independently recreated their owned container, and completed live verification. Production containers carry the matching OCI revision label. |

## Evidence still required

1. A real RAKBANK monthly statement fixture to implement and validate its statement adapter.
2. A real Standard Chartered statement fixture and, separately, a representative transaction-notification message if live SC ingestion is desired.
3. A genuine raw portal CSV/XLSX/PDF export for a production browser-ingestion run. Derived output from the legacy application is not an acceptable substitute.
4. Issuer terms for every configured cashback programme, including eligibility, tier thresholds, caps, exclusions, and reset dates. Current programme seed data remains `TENTATIVE`.
These items do not justify weakening validation. Until the corresponding evidence exists, the affected adapter, browser import, or finalization step must remain non-importing and retryable.
