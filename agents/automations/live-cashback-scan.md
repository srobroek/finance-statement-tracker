# Live cashback scan runbook

Use this runbook only for one configured transaction-notification source. The scheduled prompt supplies the source code and durable cursor name.

1. Read `AGENTS.md`, `config/deployment.json`, `config/ingestion.json`, `config/transaction-email-sources.json`, `config/cashback-programs.json`, and the `LIVE_CASHBACK` subset of `config/static-rules.seed.json`.
2. Resolve the exact source-code row. If it is not `ACTIVE`, stop without searching Outlook, calling the companion, or advancing a cursor.
3. Freeze `run_upper_bound` before mailbox access. Fetch the source-specific companion state with `scripts/get-cashback-ingest-state.ps1`, then build the authoritative cursor-minus-overlap window with `scripts/plan-outlook-ingestion.ps1`.
4. Search only the configured Outlook folder, sender allowlist, subject contract, and frozen `receivedDateTime` window. Follow pagination and retain every exact matching Outlook object. Never substitute a current-day, newest-message, or fixed-hours scan.
5. Do not search for statements, card payments, general refunds, receipts, bills, warranties, purchase evidence, reminders, or unrelated mail. Live scanning exists only to update cashback routing.
6. Build the retryable envelope with `scripts/build-outlook-envelope.ps1` and submit it with `scripts/push-outlook-messages.ps1`. The companion owns parsing, the live static-rule subset, identity deduplication, immediate bucket updates, routing, and alerts. Do not run full-budget AI, evidence discovery, or Actual import.
7. Only after acknowledged persistence, build the commit payload with `scripts/build-outlook-commit.ps1`, submit it with `scripts/push-cashback-ingest-run.ps1`, and verify that the source cursor equals the frozen upper bound. A verified empty scan still records a heartbeat and advances the cursor. Preserve retry artifacts and do not commit after any partial failure.
8. After every verification passes, call `codex_app__set_thread_archived` with `archived=true` and omit `threadId`. Do not call it for a failed, partial, or blocked run.
