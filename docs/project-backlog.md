# Finance platform orchestrated backlog

Generated: 2026-08-20
Tasks: **77**
Verified: **0**

This backlog merges the full transcript requirements ledger with the independent implementation audit.
Repository code, tests and historical user acceptance remain unverified until the task's current acceptance criteria and live readback pass.

## Status summary

| Status | Count |
| --- | ---: |
| `BLOCKED` | 3 |
| `IMPLEMENTED_UNVERIFIED` | 18 |
| `NOT_STARTED` | 1 |
| `PARTIAL` | 54 |
| `SUPERSEDED` | 1 |

## Evidence snapshot

Progress recorded: **2026-08-20** at `6a4595c`.
Evidence commits: `6a4595c`, `1636e0e`, `3484c82`, `a07c5ab`, `4a30c7f`, `8149f42`, `b07c410`, `00491aa`, `c8d4f7c`, `3a6acc6`, `da6b0c1`, `b845ccf`, `fa8fd58`

- The reviewed inactive promotion at finance commit 00491aa and orchestrator commit c8d4f7c verified 21 workflows, zero active, zero published, eight folders, 63 tag edges, four Outlook bindings, and 13 OneDrive bindings without changing credential ciphertext or calling providers.
- Official n8n DataTableService readback verified all 15 exact operational Data Tables, seven AI policy rows, nine commit-bound configuration rows, and preservation of the existing source-cursor row; no direct SQL bootstrap or finance-system write was used.
- External, unversioned host evidence at /opt/disposable/finance-n8n/20260819155134/receipts/microsoft-oauth-8149f42f2694-20260820T011501Z-failure.json (SHA-256 d85a54134152493150b2618beffa25434e79e4160001f1d8ebdf14ce1ca88148) reports bounded Outlook and OneDrive first reads and future token expiries. The repository does not contain that receipt, and no n8n restart or second-read persistence proof exists.
- WF23 is reported stranded as one inactive, unpublished setup workflow with one soft-deleted running execution row. The external observed boundary is temporarily 22 workflows, zero active, zero published, 22 placements, and 66 tag edges; the later exact cleanup receipt path and SHA-256 are pending.
- The setup-only OneDrive root workflow remains manual-only and outside the regular 21-workflow corpus. Its attempted live run failed at a clean restored boundary; exact create-or-reuse and root-folder readback remain unproven.
- The host runtime registers pinned ProDex 0.5.1 and Claude 0.8.0 community packages, eight nodes, and three credential types. Subscription authentication and schema-bound execution remain unproven.
- Outlook filtering, attachment preservation, and original-email JSON archival are repository-tested. Inline-email PDF rendering still needs a fixed local renderer and runtime proof.
- Claude cache discovery succeeded; Codex startup failed because the pinned CLI cannot parse the shared newer agent-role configuration. ProDex needs an isolated compatible authentication home or device login before structured execution can be claimed.
- b845ccf records a guarded 45-manifest, 3,927-identity Actual dry run with 400 proposed exact-state changes, zero amount mutations, 28 preserved manual-category conflicts, and 15 snapshot-absent identities. No disposable Actual replay or production write occurred.
- The independent 3a6acc6 readiness audit still keeps the ADCB issuer-balance contradiction, duplicated Finance Evidence paths, missing warranty and property metadata, and missing fresh wealth and FX evidence open. Production activation and finance writes remain forbidden pending their separate acceptance gates.
- One batched 1Password update to FinanceAutomation/n8n-runtime preserved existing fields, concealed the secret, and left Bellwether untouched. Retained Postgres rotation and n8n key recovery completed; cold-start injection, persistence/log scanning, and backup restore remain open.

## Latest orchestration overrides

- Manual workflow-layout optimization is removed. Use **Tidy Workflow**; clear code, node names, notes, sections and folders remain required.
- Execute Sub-workflow selectors should use **From list** when the target is available.
- The finance workflows must live under the sole top-level `Finance` application root, with role-based child folders and peer application roots left untouched.
- The 15-table Data Table contract is a review baseline. The unapproved direction is four domain tables; n8n owns generic execution/failures, Cloudflare access logs, optional Langfuse sanitized traces, and cashback companion period close.
- Community agent candidates are pinned to `n8n-nodes-prodex@0.5.1` and `@ggomez91npm/n8n-nodes-claude-code@0.8.0`; neither is production-approved until disposable registration, isolation, authentication and structured-output proof passes.
- The backlog intentionally has no automatically promoted `VERIFIED` tasks.

## Ordered executable queue

| # | ID | Owner | Priority | Status | Next action |
| ---: | --- | --- | --- | --- | --- |
| 1 | `N8N-012` | `n8n_workflows` | P0 | `PARTIAL` | Execute the exact cleanup gate and retain the later content-addressed cleanup receipt, then proceed directly to Microsoft restart/second-read proof; folder and canvas polish are last. |
| 2 | `N8N-003` | `n8n_workflows` | P0 | `PARTIAL` | After N8N-012 cleanup, run the guarded n8n restart and second-read proof without making hierarchy work depend on a new token-expiry wait. |
| 3 | `DOC-001` | `document_evidence` | P0 | `PARTIAL` | After Microsoft restart/second-read proof, import and run inactive WF22 once, retain the redacted exact-root receipt, then proceed with archive normalization; defer final folder polish. |
| 4 | `N8N-005` | `n8n_workflows` | P0 | `IMPLEMENTED_UNVERIFIED` | Run concurrent and kill-at-boundary disposable tests for the fenced Actual outbox and prove one row, one verified commit and one cursor advance. |
| 5 | `N8N-011` | `n8n_workflows` | P0 | `PARTIAL` | Review the 15-to-4 machine map and WF20 artifact gates; do not create generic receipt, period-close or provider-circuit tables. |
| 6 | `N8N-006` | `n8n_workflows` | P0 | `IMPLEMENTED_UNVERIFIED` | Keep all workflows inactive while completing disposable double replay and reviewed finance-write acceptance. |
| 7 | `AGENT-003` | `agent_runtime` | P0 | `PARTIAL` | Run one schema-bound proposal per provider after isolated authentication succeeds. |
| 8 | `N8N-001` | `n8n_workflows` | P0 | `IMPLEMENTED_UNVERIFIED` | Remove the exact WF23 orphan, then complete the guarded Microsoft restart and second-read proof while all workflows remain inactive. |
| 9 | `N8N-002` | `n8n_workflows` | P1 | `PARTIAL` | Implement only functional parameter ownership and workflow semantics for MVP now; defer six-folder, Canvas Group, sticky and Tidy polish until after double replay and activation gates. |
| 10 | `N8N-008` | `n8n_workflows` | P1 | `IMPLEMENTED_UNVERIFIED` | Authenticate each isolated subscription and execute one schema-bound proposal per provider. |
| 11 | `DOCS-002` | `documentation_acceptance` | P0 | `IMPLEMENTED_UNVERIFIED` | Review this generated backlog as the orchestration source, then require --check and focused tests on every backlog change. |
| 12 | `PLATFORM-003` | `platform_operations` | P0 | `IMPLEMENTED_UNVERIFIED` | Run one disposable cold start and restore with persistence, execution, log, and backup scans. |
| 13 | `PLATFORM-006` | `platform_operations` | P0 | `PARTIAL` | Complete current-byte disposable replay and restore evidence while production remains untouched. |
| 14 | `BROWSER-002` | `browser_acquisition` | P0 | `IMPLEMENTED_UNVERIFIED` | Prove current Actual API and authenticated UI contain exactly the complete FAB non-credit inventory with source-matching signed balances. |
| 15 | `BROWSER-003` | `browser_acquisition` | P0 | `PARTIAL` | Acquire fresh interactive holdings and FX, run disposable idempotent projection, then guardedly create/read back each included off-budget portfolio account. |
| 16 | `ACTUAL-021` | `actual_finance` | P0 | `BLOCKED` | Resolve the ADCB issuer evidence contradiction before changing account state. |
| 17 | `ACTUAL-003` | `actual_finance` | P0 | `BLOCKED` | Obtain fresh wealth inputs and authenticated UI access, then prove exact parity. |
| 18 | `ACTUAL-010` | `actual_finance` | P0 | `IMPLEMENTED_UNVERIFIED` | Review exceptions and double replay the guarded plan in disposable Actual. |
| 19 | `ACTUAL-011` | `actual_finance` | P0 | `IMPLEMENTED_UNVERIFIED` | Complete conflict review and disposable exact-state replay. |
| 20 | `ACTUAL-006` | `actual_finance` | P0 | `IMPLEMENTED_UNVERIFIED` | Compile the current canonical rules into the ownership manifest, require an empty overlap report, and run Actual/canonical evaluator parity fixtures. |
| 21 | `DOC-002` | `document_evidence` | P0 | `PARTIAL` | Exercise plain/encrypted live documents, verify ephemeral plaintext deletion, catalogue hashes, selective store/do-not-store policy, and transaction linkage. |
| 22 | `DOC-007` | `document_evidence` | P0 | `IMPLEMENTED_UNVERIFIED` | Exercise plain/encrypted live documents, verify ephemeral plaintext deletion, catalogue hashes, selective store/do-not-store policy, and transaction linkage. |
| 23 | `ACTUAL-019` | `actual_finance` | P0 | `BLOCKED` | Execute the current corpus twice through the inactive n8n writer. |
| 24 | `PLATFORM-004` | `platform_operations` | P0 | `PARTIAL` | Record immutable image digests, verify independent restarts, execute restore drill, validate tunnel origin/headers and AD/Service Auth boundaries, and deploy reviewed n8n candidate. |
| 25 | `AUTO-001` | `automation_cutover` | P0 | `PARTIAL` | Prove one issuer schedule end to end while inactive. |
| 26 | `N8N-010` | `n8n_workflows` | P3 | `PARTIAL` | After the functional MVP and activation gates, apply and independently verify the six-folder Finance/Global tree and Canvas documentation contract without touching unrelated applications. |
| 27 | `AUTO-003` | `automation_cutover` | P1 | `PARTIAL` | Complete n8n schedule runtime proof, cut over atomically, disable legacy Codex schedules, and confirm successful runs create durable receipts without new chats. |
| 28 | `ACTUAL-001` | `actual_finance` | P0 | `PARTIAL` | Enumerate every posted identity across Actual and operational stores and prove Actual is the sole posted ledger. |
| 29 | `ACTUAL-002` | `actual_finance` | P0 | `PARTIAL` | Capture fresh Sarwa holdings and FX before any guarded Actual account change. |
| 30 | `ACTUAL-004` | `actual_finance` | P0 | `PARTIAL` | Run each in-scope provider through immutable archive, n8n validation, reviewed delta, fenced writer, and Actual readback; add representative RAK/SC statement fixtures. |
| 31 | `ACTUAL-005` | `actual_finance` | P0 | `PARTIAL` | Populate only after account/UI parity; obtain approved budget values and verify every named report against authoritative source data. |
| 32 | `ACTUAL-017` | `actual_finance` | P0 | `PARTIAL` | Acquire fresh interactive holdings and FX, run disposable idempotent projection, then guardedly create/read back each included off-budget portfolio account. |
| 33 | `AGENT-001` | `agent_runtime` | P0 | `PARTIAL` | Execute the deterministic-first n8n path and prove AI is invoked only for explicitly unresolved fields. |
| 34 | `AUTO-002` | `automation_cutover` | P0 | `PARTIAL` | After OAuth binding, execute bounded expected-cycle polling and retain cursor, no-statement, and statement-found receipts. |
| 35 | `BROWSER-001` | `browser_acquisition` | P0 | `PARTIAL` | Run each in-scope provider through immutable archive, n8n validation, reviewed delta, fenced writer, and Actual readback; add representative RAK/SC statement fixtures. |
| 36 | `CASHBACK-001` | `cashback_companion` | P0 | `PARTIAL` | Enumerate every posted identity across Actual and operational stores and prove Actual is the sole posted ledger. |
| 37 | `CASHBACK-003` | `cashback_companion` | P0 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 38 | `CASHBACK-004` | `cashback_companion` | P0 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 39 | `CASHBACK-005` | `cashback_companion` | P0 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 40 | `CASHBACK-006` | `cashback_companion` | P0 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 41 | `CASHBACK-007` | `cashback_companion` | P0 | `IMPLEMENTED_UNVERIFIED` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 42 | `CASHBACK-012` | `cashback_companion` | P0 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 43 | `CASHBACK-013` | `cashback_companion` | P0 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 44 | `CASHBACK-014` | `cashback_companion` | P0 | `PARTIAL` | Finish disposable semantic replay before live cashback acceptance. |
| 45 | `N8N-004` | `n8n_workflows` | P0 | `PARTIAL` | Run the sole writer in disposable Actual and compare every economic field and balance. |
| 46 | `PLATFORM-001` | `platform_operations` | P0 | `PARTIAL` | Run the independent restart, protected-route, and restore acceptance matrix. |
| 47 | `ACTUAL-007` | `actual_finance` | P1 | `PARTIAL` | Run classification audit over the full disposable corpus and authenticated production readback; resolve owner/property/payee exceptions. |
| 48 | `ACTUAL-008` | `actual_finance` | P1 | `PARTIAL` | Run classification audit over the full disposable corpus and authenticated production readback; resolve owner/property/payee exceptions. |
| 49 | `ACTUAL-009` | `actual_finance` | P1 | `PARTIAL` | Run classification audit over the full disposable corpus and authenticated production readback; resolve owner/property/payee exceptions. |
| 50 | `ACTUAL-012` | `actual_finance` | P1 | `PARTIAL` | Run classification audit over the full disposable corpus and authenticated production readback; resolve owner/property/payee exceptions. |
| 51 | `ACTUAL-013` | `actual_finance` | P1 | `PARTIAL` | Populate only after account/UI parity; obtain approved budget values and verify every named report against authoritative source data. |
| 52 | `ACTUAL-014` | `actual_finance` | P1 | `PARTIAL` | Populate only after account/UI parity; obtain approved budget values and verify every named report against authoritative source data. |
| 53 | `ACTUAL-016` | `actual_finance` | P1 | `PARTIAL` | Populate only after account/UI parity; obtain approved budget values and verify every named report against authoritative source data. |
| 54 | `ACTUAL-018` | `actual_finance` | P1 | `IMPLEMENTED_UNVERIFIED` | Populate only after account/UI parity; obtain approved budget values and verify every named report against authoritative source data. |
| 55 | `ACTUAL-020` | `actual_finance` | P1 | `PARTIAL` | Run classification audit over the full disposable corpus and authenticated production readback; resolve owner/property/payee exceptions. |
| 56 | `AGENT-002` | `agent_runtime` | P1 | `PARTIAL` | Prove server-owned normal and exception model routing, including the gated high-reasoning path, in disposable n8n. |
| 57 | `BROWSER-004` | `browser_acquisition` | P1 | `PARTIAL` | Run each in-scope provider through immutable archive, n8n validation, reviewed delta, fenced writer, and Actual readback; add representative RAK/SC statement fixtures. |
| 58 | `CASHBACK-002` | `cashback_companion` | P1 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 59 | `CASHBACK-008` | `cashback_companion` | P1 | `IMPLEMENTED_UNVERIFIED` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 60 | `CASHBACK-009` | `cashback_companion` | P1 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 61 | `CASHBACK-010` | `cashback_companion` | P1 | `PARTIAL` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 62 | `CASHBACK-011` | `cashback_companion` | P1 | `IMPLEMENTED_UNVERIFIED` | Recheck public/mobile health, push triggers, weekly pace, period history/reset, fictional profiles, and n8n-fed live events after deployment cutover. |
| 63 | `DOC-003` | `document_evidence` | P1 | `PARTIAL` | Exercise plain/encrypted live documents, verify ephemeral plaintext deletion, catalogue hashes, selective store/do-not-store policy, and transaction linkage. |
| 64 | `DOC-004` | `document_evidence` | P1 | `PARTIAL` | Review the 28 preserved manual-category conflicts and 15 corpus identities absent from the snapshot, replay the guarded plan in disposable Actual, then separately authorize any production delta and verify authenticated UI samples. |
| 65 | `DOC-005` | `document_evidence` | P1 | `PARTIAL` | Exercise plain/encrypted live documents, verify ephemeral plaintext deletion, catalogue hashes, selective store/do-not-store policy, and transaction linkage. |
| 66 | `DOC-006` | `document_evidence` | P1 | `PARTIAL` | Run each in-scope provider through immutable archive, n8n validation, reviewed delta, fenced writer, and Actual readback; add representative RAK/SC statement fixtures. |
| 67 | `DOCS-001` | `documentation_acceptance` | P1 | `PARTIAL` | Implement and prove: Fresh clone runbook. |
| 68 | `N8N-007` | `n8n_workflows` | P1 | `PARTIAL` | Run the disposable operations and error-workflow negative matrix and retain redacted durable receipts. |
| 69 | `PLATFORM-002` | `platform_operations` | P1 | `IMPLEMENTED_UNVERIFIED` | Run exact image build and provenance attestation in CI and compare the deployed digest. |
| 70 | `PLATFORM-005` | `platform_operations` | P1 | `IMPLEMENTED_UNVERIFIED` | Record immutable image digests, verify independent restarts, execute restore drill, validate tunnel origin/headers and AD/Service Auth boundaries, and deploy reviewed n8n candidate. |
| 71 | `ACTUAL-015` | `actual_finance` | P2 | `PARTIAL` | Populate only after account/UI parity; obtain approved budget values and verify every named report against authoritative source data. |
| 72 | `AGENT-004` | `agent_runtime` | P2 | `PARTIAL` | Run protected-field and automatic-write attacks and prove every AI result remains a review-only proposal. |
| 73 | `AGENT-005` | `agent_runtime` | P2 | `PARTIAL` | Prove identical provider boundaries in the disposable runtime. |
| 74 | `DOCS-003` | `documentation_acceptance` | P2 | `NOT_STARTED` | Implement and prove: Public-safe examples. |
| 75 | `N8N-009` | `n8n_workflows` | P2 | `IMPLEMENTED_UNVERIFIED` | Run allowed and malicious MCP facade requests with instance MCP disabled and prove zero unauthorized writes. |
| 76 | `PLATFORM-007` | `platform_operations` | P2 | `PARTIAL` | Implement and prove: Private repo visibility verified. |

## Tasks by workstream

### AI providers and agent execution

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `AGENT-001` | `agent_runtime` | `ACTUAL-005`, `ACTUAL-010` | None | `PARTIAL` | PARTIAL / REPOSITORY_GREEN_SUBSCRIPTION_EXECUTION_PENDING; 3 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: runner contract tests plus isolated structured-output receipt review | AI runs only after deterministic rules and history matching |
| `AGENT-002` | `agent_runtime` | `AGENT-003` | None | `PARTIAL` | PARTIAL / REPOSITORY_GREEN_SUBSCRIPTION_EXECUTION_PENDING; 3 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: runner contract tests plus isolated structured-output receipt review | Luna normal path and gated Sol exception path |
| `AGENT-003` | `agent_runtime` | `PLATFORM-003`, `N8N-007` | None | `PARTIAL` | PARTIAL / HOST_RUNNER_HEALTH_VERIFIED_PROVIDER_AUTH_BLOCKED; 7 files, 5 tests; live readback: recorded; 4 acceptance checks; validator: runner contract tests plus isolated structured-output receipt review | Ephemeral bounded Codex proposal runner |
| `AGENT-004` | `agent_runtime` | `AGENT-001` | `ACTUAL-009` | `PARTIAL` | PARTIAL / REPOSITORY_GREEN_SUBSCRIPTION_EXECUTION_PENDING; 3 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: runner contract tests plus isolated structured-output receipt review | AI category and rule recommendations remain proposals |
| `AGENT-005` | `agent_runtime` | `AGENT-003`, `N8N-008` | None | `PARTIAL` | PARTIAL / HOST_REGISTRATION_VERIFIED_AUTH_STRUCTURED_EXECUTION_PENDING; 7 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: runner contract tests plus isolated structured-output receipt review | Community AI nodes are optional, pinned pilots only |

### Actual accounts, ledger, rules, budgets and reports

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `ACTUAL-001` | `actual_finance` | `N8N-004` | None | `PARTIAL` | PARTIAL / NOT_RUNTIME_VERIFIED; 3 files, 1 tests; live readback: recorded; 3 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Actual is the sole authoritative finance ledger |
| `ACTUAL-002` | `actual_finance` | `BROWSER-002`, `BROWSER-003` | `ACTUAL-003` | `PARTIAL` | PARTIAL / FAB_RECEIPT_ONLY_SARWA_FX_PARITY_PENDING; 8 files, 5 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Complete account inventory for FAB non-credit accounts and Sarwa portfolios |
| `ACTUAL-003` | `actual_finance` | `ACTUAL-002`, `PLATFORM-006` | `BROWSER-002`, `BROWSER-003` | `BLOCKED` | PARTIAL / TESTS_GREEN_LIVE_UI_API_PARITY_BLOCKED; 11 files, 8 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Explainable balances, net worth, and UI/API parity |
| `ACTUAL-004` | `actual_finance` | `DOC-002` | `ACTUAL-019` | `PARTIAL` | PARTIAL / ADAPTER_TESTED_NOT_END_TO_END; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Statement-agnostic bank adapter contract |
| `ACTUAL-005` | `actual_finance` | None | `ACTUAL-006` | `PARTIAL` | PARTIAL / CONFIG_SCAFFOLD_ONLY; 4 files, 1 tests; live readback: recorded; 5 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | AutoCat-style ordered static rules |
| `ACTUAL-006` | `actual_finance` | `ACTUAL-005`, `N8N-002` | None | `IMPLEMENTED_UNVERIFIED` | PARTIAL / CONFIG_SCAFFOLD_ONLY; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Exclusive rule ownership between Actual and upstream evaluator |
| `ACTUAL-007` | `actual_finance` | `ACTUAL-005`, `ACTUAL-020` | None | `PARTIAL` | PARTIAL / MIXED: CONFIG_SCAFFOLD_ONLY, SCAFFOLD_VERIFIED; 7 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Payee and transaction normalization preserves originals |
| `ACTUAL-008` | `actual_finance` | `AGENT-001` | `ACTUAL-020` | `PARTIAL` | PARTIAL / SCAFFOLD_VERIFIED; 3 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Scoped AI classification and tagging |
| `ACTUAL-009` | `actual_finance` | `AGENT-004` | `ACTUAL-013` | `PARTIAL` | PARTIAL / MIXED: CONFIG_SCAFFOLD_ONLY, SCAFFOLD_VERIFIED; 7 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Category and subcategory management |
| `ACTUAL-010` | `actual_finance` | `ACTUAL-004` | `ACTUAL-019` | `IMPLEMENTED_UNVERIFIED` | IMPLEMENTED_NOT_DEPLOYED / FULL_CORPUS_DRY_RUN_VERIFIED_RUNTIME_REPLAY_PENDING; 11 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Correct purchase, refund, reward, transfer, fee and interest semantics |
| `ACTUAL-011` | `actual_finance` | `ACTUAL-010` | `ACTUAL-019` | `IMPLEMENTED_UNVERIFIED` | IMPLEMENTED_NOT_DEPLOYED / FULL_CORPUS_DRY_RUN_VERIFIED_RUNTIME_REPLAY_PENDING; 12 files, 3 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Canonical minimal tags-first display notes |
| `ACTUAL-012` | `actual_finance` | `ACTUAL-005`, `DOC-005` | `ACTUAL-016` | `PARTIAL` | PARTIAL / MIXED: CONFIG_SCAFFOLD_ONLY, SCAFFOLD_VERIFIED; 7 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Rental, shared and owner/person tagging |
| `ACTUAL-013` | `actual_finance` | `ACTUAL-009`, `ACTUAL-014` | None | `PARTIAL` | PARTIAL / CONFIG_SCAFFOLD_ONLY; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Modern envelope budgets, savings goals, and cleanup pools |
| `ACTUAL-014` | `actual_finance` | `DOC-003`, `ACTUAL-010` | None | `PARTIAL` | PARTIAL / CONFIG_SCAFFOLD_ONLY; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Evidence-backed schedules and reminders |
| `ACTUAL-015` | `actual_finance` | `ACTUAL-005`, `ACTUAL-014` | None | `PARTIAL` | PARTIAL / CONFIG_SCAFFOLD_ONLY; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Mortgage account and IPMT/PPMT split |
| `ACTUAL-016` | `actual_finance` | `ACTUAL-002`, `ACTUAL-009`, `ACTUAL-012`, `ACTUAL-013` | None | `PARTIAL` | PARTIAL / CONFIG_SCAFFOLD_ONLY; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Comprehensive filterable dashboards and saved reports |
| `ACTUAL-017` | `actual_finance` | `BROWSER-003`, `ACTUAL-003` | None | `PARTIAL` | PARTIAL / MIXED: CONFIG_SCAFFOLD_ONLY, FIXTURE_AND_CAPTURE_VERIFIED; 8 files, 4 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Investment and retirement reporting from real Sarwa values |
| `ACTUAL-018` | `actual_finance` | `ACTUAL-006` | None | `IMPLEMENTED_UNVERIFIED` | PARTIAL / CONFIG_SCAFFOLD_ONLY; 4 files, 1 tests; live readback: recorded; 3 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Disable Actual automatic category learning |
| `ACTUAL-019` | `actual_finance` | `ACTUAL-002`, `ACTUAL-010`, `ACTUAL-011`, `PLATFORM-006` | None | `BLOCKED` | PARTIAL / GUARDED_PLAN_VERIFIED_DOUBLE_REPLAY_PENDING; 4 files, 4 tests; live readback: recorded; 5 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | Full clean, idempotent corpus replay and reconciliation |
| `ACTUAL-020` | `actual_finance` | `ACTUAL-008`, `ACTUAL-016` | None | `PARTIAL` | PARTIAL / MIXED: CONFIG_SCAFFOLD_ONLY, SCAFFOLD_VERIFIED; 7 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | One complete review queue |
| `ACTUAL-021` | `actual_finance` | `ACTUAL-010`, `ACTUAL-019` | None | `BLOCKED` | PARTIAL / ISSUER_BALANCE_CONTRADICTION_OPEN; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: full Python suite plus disposable Actual and authenticated UI/API readback | ADCB is closed, historical, and reconciled to zero |

### Outlook, OneDrive and document evidence

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `DOC-001` | `document_evidence` | `DOC-007`, `PLATFORM-003`, `N8N-003` | None | `PARTIAL` | PARTIAL / REPOSITORY_TESTED_ROOT_SETUP_LIVE_RUN_PENDING; 7 files, 3 tests; live readback: recorded; 4 acceptance checks; validator: evidence tests plus immutable archive and readback receipt review | Immutable OneDrive evidence archive |
| `DOC-002` | `document_evidence` | `PLATFORM-003`, `N8N-008` | None | `PARTIAL` | PARTIAL / MIXED: DESIGN_ONLY_FOR_CURRENT_STACK, UNIT_VERIFIED_ONLY; 8 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: evidence tests plus immutable archive and readback receipt review | Local secure PDF unlock and extraction |
| `DOC-003` | `document_evidence` | `DOC-001`, `AGENT-001` | None | `PARTIAL` | PARTIAL / UNIT_VERIFIED_ONLY; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: evidence tests plus immutable archive and readback receipt review | Selective receipts, bills, warranties, and purchase-proof acquisition |
| `DOC-004` | `document_evidence` | `DOC-001`, `ACTUAL-011` | None | `PARTIAL` | PARTIAL / MIXED: FULL_CORPUS_DRY_RUN_VERIFIED, UNIT_VERIFIED_ONLY; 16 files, 3 tests; live readback: recorded; 4 acceptance checks; validator: evidence tests plus immutable archive and readback receipt review | Transaction-to-email and document links |
| `DOC-005` | `document_evidence` | `DOC-002` | `ACTUAL-012` | `PARTIAL` | PARTIAL / UNIT_VERIFIED_ONLY; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: evidence tests plus immutable archive and readback receipt review | Utility bill extraction and property matching |
| `DOC-006` | `document_evidence` | `BROWSER-004`, `ACTUAL-019` | None | `PARTIAL` | PARTIAL / MIXED: ADAPTER_TESTED_NOT_END_TO_END, UNIT_VERIFIED_ONLY; 8 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: evidence tests plus immutable archive and readback receipt review | Amazon order enrichment and evidence-backed split |
| `DOC-007` | `document_evidence` | None | `N8N-003` | `IMPLEMENTED_UNVERIFIED` | PARTIAL / UNIT_VERIFIED_ONLY; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: evidence tests plus immutable archive and readback receipt review | Source deduplication, quarantine, and immutable receipts |

### browser acquisition

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `BROWSER-001` | `browser_acquisition` | `DOC-001` | `N8N-005` | `PARTIAL` | PARTIAL / ADAPTER_TESTED_NOT_END_TO_END; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: capture-schema tests plus immutable source and guarded-ingestion receipt review | User-assisted browser acquisition pipeline |
| `BROWSER-002` | `browser_acquisition` | `BROWSER-001` | `ACTUAL-002` | `IMPLEMENTED_UNVERIFIED` | PARTIAL / MIXED: ADAPTER_TESTED_NOT_END_TO_END, REPOSITORY_AND_RECEIPT_ONLY; 8 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: capture-schema tests plus immutable source and guarded-ingestion receipt review | FAB complete non-credit inventory and history |
| `BROWSER-003` | `browser_acquisition` | `BROWSER-001` | `ACTUAL-017` | `PARTIAL` | PARTIAL / MIXED: ADAPTER_TESTED_NOT_END_TO_END, FIXTURE_AND_CAPTURE_VERIFIED; 8 files, 4 tests; live readback: recorded; 5 acceptance checks; validator: capture-schema tests plus immutable source and guarded-ingestion receipt review | Sarwa provider-native holdings and wealth snapshots |
| `BROWSER-004` | `browser_acquisition` | `BROWSER-001` | `DOC-006` | `PARTIAL` | PARTIAL / ADAPTER_TESTED_NOT_END_TO_END; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: capture-schema tests plus immutable source and guarded-ingestion receipt review | Amazon authenticated order capture |

### cashback companion

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `CASHBACK-001` | `cashback_companion` | `ACTUAL-001`, `PLATFORM-001` | None | `PARTIAL` | PARTIAL / MIXED: HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST, NOT_RUNTIME_VERIFIED; 8 files, 3 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Cashback is a separate live companion service |
| `CASHBACK-002` | `cashback_companion` | `PLATFORM-002` | `DOCS-003` | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Fully configuration-driven public/reusable cashback engine |
| `CASHBACK-003` | `cashback_companion` | `N8N-003`, `CASHBACK-004` | None | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Valid live transactions enter buckets immediately without approval |
| `CASHBACK-004` | `cashback_companion` | `ACTUAL-004`, `AUTO-002` | None | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 5 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Statement reconciliation finalizes and resets each card period |
| `CASHBACK-005` | `cashback_companion` | `CASHBACK-004` | `AUTO-001` | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Correct card cycles and payment due semantics |
| `CASHBACK-006` | `cashback_companion` | `N8N-003` | `AUTO-001` | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Live source scope and cadence |
| `CASHBACK-007` | `cashback_companion` | `CASHBACK-002` | `CASHBACK-010` | `IMPLEMENTED_UNVERIFIED` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Dynamic route graph considers all card and bucket conditions |
| `CASHBACK-008` | `cashback_companion` | `CASHBACK-007` | None | `IMPLEMENTED_UNVERIFIED` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Mobile-first compact tabbed UI |
| `CASHBACK-009` | `cashback_companion` | `CASHBACK-004`, `CASHBACK-005` | None | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Card positions, tier progress, history and expected cashback |
| `CASHBACK-010` | `cashback_companion` | `CASHBACK-005`, `CASHBACK-007` | None | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Weekly pace and under/over warnings |
| `CASHBACK-011` | `cashback_companion` | `CASHBACK-010`, `PLATFORM-004` | None | `IMPLEMENTED_UNVERIFIED` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Declarative web push for important routing events |
| `CASHBACK-012` | `cashback_companion` | `CASHBACK-002`, `CASHBACK-006` | None | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | RAKBANK channel and programme assumptions |
| `CASHBACK-013` | `cashback_companion` | `CASHBACK-002`, `CASHBACK-007` | None | `PARTIAL` | PARTIAL / HISTORICALLY_ACCEPTED_CURRENTLY_UNREACHABLE_FROM_AUDIT_HOST; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | Standard Chartered tier, bucket and FX logic |
| `CASHBACK-014` | `cashback_companion` | `ACTUAL-010`, `CASHBACK-004` | None | `PARTIAL` | PARTIAL / SEMANTICS_DRY_RUN_VERIFIED_LIVE_FINALIZATION_PENDING; 16 files, 4 tests; live readback: recorded; 4 acceptance checks; validator: cashback regression suite plus live companion reconciliation receipt review | EI Amazon is unlimited and statement-only |

### documentation and public reusability

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `DOCS-001` | `documentation_acceptance` | `PLATFORM-002` | None | `PARTIAL` | NOT_ASSESSED / NOT_VERIFIED; 0 files, 0 tests; live readback: none; 4 acceptance checks; validator: backlog generator check plus independent acceptance-evidence audit | Portable agent and operator documentation |
| `DOCS-002` | `documentation_acceptance` | `ACTUAL-019`, `N8N-006`, `PLATFORM-006` | None | `IMPLEMENTED_UNVERIFIED` | MISSING / REPOSITORY_GREEN_RUNTIME_PROMOTION_PENDING; 3 files, 3 tests; live readback: recorded; 4 acceptance checks; validator: backlog generator check plus independent acceptance-evidence audit | Machine-readable acceptance ledger distinguishes claims from proof |
| `DOCS-003` | `documentation_acceptance` | `CASHBACK-002`, `ACTUAL-004`, `PLATFORM-003` | None | `NOT_STARTED` | NOT_ASSESSED / NOT_VERIFIED; 0 files, 0 tests; live readback: none; 4 acceptance checks; validator: backlog generator check plus independent acceptance-evidence audit | Reusable configuration, examples and deployment guide |
| `DOCS-004` | `documentation_acceptance` | `ACTUAL-001` | None | `SUPERSEDED` | NOT_ASSESSED / NOT_VERIFIED; 0 files, 0 tests; live readback: none; 3 acceptance checks; validator: backlog generator check plus independent acceptance-evidence audit | Notion architecture and UI work are explicitly superseded |

### n8n platform and workflows

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `N8N-001` | `n8n_workflows` | `N8N-005`, `N8N-006`, `PLATFORM-001`, `AGENT-003` | None | `IMPLEMENTED_UNVERIFIED` | DEPLOYED_INACTIVE / RUNTIME_INACTIVE_IMPORT_BINDING_SCHEMA_AND_HEALTH_VERIFIED; 10 files, 7 tests; live readback: recorded; 4 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | n8n is the sole production scheduler and orchestrator |
| `N8N-002` | `n8n_workflows` | `N8N-001`, `N8N-008` | None | `PARTIAL` | DEPLOYED_INACTIVE / RUNTIME_SIBLING_PLACEMENT_VERIFIED_TARGET_APPLICATION_HIERARCHY_PENDING; 7 files, 3 tests; live readback: recorded; 17 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Workflow stages remain visible and composable |
| `N8N-003` | `n8n_workflows` | `N8N-004`, `DOC-007`, `N8N-012` | None | `PARTIAL` | DEPLOYED_INACTIVE / MICROSOFT_FIRST_READ_AND_REFRESH_OBSERVED_RESTART_PERSISTENCE_PENDING; 8 files, 4 tests; live readback: recorded; 4 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Cursor-safe Outlook acquisition |
| `N8N-004` | `n8n_workflows` | None | `ACTUAL-001`, `CASHBACK-001` | `PARTIAL` | PARTIAL / RUNTIME_EXACT_SCHEMA_AND_SEED_READBACK_VERIFIED_ACTUAL_READBACK_PENDING; 9 files, 4 tests; live readback: recorded; 4 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Postgres and Data Tables hold operational state only |
| `N8N-005` | `n8n_workflows` | `PLATFORM-003`, `DOC-001` | `ACTUAL-019` | `IMPLEMENTED_UNVERIFIED` | PARTIAL / MIXED: REPOSITORY_GREEN_HOST_EVIDENCE_EXTERNAL_ACCEPTANCE_PENDING, REPOSITORY_GREEN_RUNTIME_PROMOTION_PENDING; 7 files, 4 tests; live readback: recorded; 4 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Single fenced Actual writer and recoverable outbox |
| `N8N-006` | `n8n_workflows` | `N8N-005`, `N8N-011`, `PLATFORM-006` | `N8N-001` | `IMPLEMENTED_UNVERIFIED` | DEPLOYED_INACTIVE / REGULAR_CORPUS_INACTIVE_VERIFIED_TRANSIENT_WF23_CLEANUP_PENDING; 9 files, 6 tests; live readback: recorded; 5 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Production workflows start inactive and write-disabled |
| `N8N-007` | `n8n_workflows` | `N8N-003`, `DOC-007`, `PLATFORM-003` | None | `PARTIAL` | PARTIAL / REPOSITORY_GREEN_HOST_EVIDENCE_EXTERNAL_ACCEPTANCE_PENDING; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Bounded operations and error workflows |
| `N8N-008` | `n8n_workflows` | `PLATFORM-002` | None | `IMPLEMENTED_UNVERIFIED` | DEPLOYED_INACTIVE / HOST_REGISTRATION_VERIFIED_AUTH_EXECUTION_PENDING; 8 files, 3 tests; live readback: recorded; 4 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Native-node-first and reviewed extension policy |
| `N8N-009` | `n8n_workflows` | `PLATFORM-004`, `N8N-007` | None | `IMPLEMENTED_UNVERIFIED` | PARTIAL / REPOSITORY_GREEN_HOST_EVIDENCE_EXTERNAL_ACCEPTANCE_PENDING; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Bounded MCP facade |
| `N8N-010` | `n8n_workflows` | `N8N-006`, `AUTO-001` | `N8N-002`, `N8N-003`, `N8N-012`, `N8N-011`, `DOC-001`, `PLATFORM-001` | `PARTIAL` | DESIGN_RECORDED_UNIMPLEMENTED / DESIGN_RECORDED_UNIMPLEMENTED; 3 files, 2 tests; live readback: recorded; 10 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Application and shared-scope folder hierarchy |
| `N8N-011` | `n8n_workflows` | `N8N-005` | `N8N-004`, `N8N-007`, `N8N-009`, `AGENT-003`, `DOC-007`, `PLATFORM-006` | `PARTIAL` | PARTIAL / FIFTEEN_TABLE_BASELINE_VERIFIED_MINIMALITY_AND_MIGRATION_UNPROVEN; 6 files, 2 tests; live readback: recorded; 11 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Minimize Data Tables to durable finance-domain state |
| `N8N-012` | `n8n_workflows` | None | `N8N-003`, `N8N-006`, `N8N-010`, `DOC-001`, `PLATFORM-001` | `PARTIAL` | IMPLEMENTED_NOT_EXECUTED / EXACT_CLEANUP_GATE_COMMITTED_EXTERNAL_RECEIPT_PENDING; 3 files, 1 tests; live readback: recorded; 5 acceptance checks; validator: n8n contract tests plus exact disposable execution receipt review | Exact transient WF23 cleanup |

### platform, security and deployment

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `PLATFORM-001` | `platform_operations` | `PLATFORM-002` | `PLATFORM-006` | `PARTIAL` | PARTIAL / CI_HOST_N8N_RUNTIME_HEALTH_AND_INACTIVE_DEPLOYMENT_VERIFIED; 8 files, 5 tests; live readback: recorded; 4 acceptance checks; validator: platform tests plus image, deploy, restart, security, and restore receipt review | Separate containerized services on the CI host |
| `PLATFORM-002` | `platform_operations` | `PLATFORM-003` | `DOCS-001` | `IMPLEMENTED_UNVERIFIED` | DEPLOYED_INACTIVE / HOST_IMAGE_RUNNING_LOCAL_SCAN_SBOM_VERIFIED_CI_ATTESTATION_PENDING; 8 files, 4 tests; live readback: recorded; 4 acceptance checks; validator: platform tests plus image, deploy, restart, security, and restore receipt review | CI builds pinned images and supports reproducible deployment |
| `PLATFORM-003` | `platform_operations` | None | `PLATFORM-006` | `IMPLEMENTED_UNVERIFIED` | DEPLOYED_INACTIVE / DEDICATED_VAULT_AND_ROTATION_VERIFIED_COLD_START_RESTORE_PENDING; 6 files, 3 tests; live readback: recorded; 4 acceptance checks; validator: platform tests plus image, deploy, restart, security, and restore receipt review | Dedicated 1Password vault and runtime secret injection |
| `PLATFORM-004` | `platform_operations` | `PLATFORM-001`, `PLATFORM-003` | None | `PARTIAL` | PARTIAL / CI_DEFINED_LIVE_STATE_UNVERIFIED; 5 files, 2 tests; live readback: recorded; 5 acceptance checks; validator: platform tests plus image, deploy, restart, security, and restore receipt review | Cloudflare route and authentication matrix |
| `PLATFORM-005` | `platform_operations` | `PLATFORM-004` | None | `IMPLEMENTED_UNVERIFIED` | PARTIAL / CI_DEFINED_LIVE_STATE_UNVERIFIED; 5 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: platform tests plus image, deploy, restart, security, and restore receipt review | Actual reverse proxy supplies SharedArrayBuffer headers |
| `PLATFORM-006` | `platform_operations` | `PLATFORM-001`, `PLATFORM-003` | None | `PARTIAL` | PARTIAL / HOST_INACTIVE_DEPLOY_AND_HEALTH_VERIFIED_RESTORE_PENDING; 12 files, 8 tests; live readback: recorded; 5 acceptance checks; validator: platform tests plus image, deploy, restart, security, and restore receipt review | Backups, restore drills, restart health and deployment receipts |
| `PLATFORM-007` | `platform_operations` | `CASHBACK-002`, `DOCS-003` | None | `PARTIAL` | NOT_ASSESSED / NOT_VERIFIED; 0 files, 0 tests; live readback: none; 4 acceptance checks; validator: platform tests plus image, deploy, restart, security, and restore receipt review | Repository privacy versus public deployability is explicit |

### scheduling and task lifecycle

| ID | Owner | Strict dependencies | Related tasks | Status | Acceptance evidence and validator | Title |
| --- | --- | --- | --- | --- | --- | --- |
| `AUTO-001` | `automation_cutover` | `CASHBACK-005`, `CASHBACK-006`, `N8N-001`, `N8N-006` | None | `PARTIAL` | PARTIAL / INACTIVE_N8N_IMPORT_VERIFIED_CUTOVER_PENDING; 7 files, 3 tests; live readback: recorded; 4 acceptance checks; validator: automation lifecycle tests plus target scheduler and legacy-task readback | Issuer-specific production schedule matrix |
| `AUTO-002` | `automation_cutover` | `N8N-003`, `DOC-002` | `CASHBACK-004` | `PARTIAL` | PARTIAL / MIXED: LEGACY_TASKS_AUDITED_N8N_CUTOVER_PENDING, REPOSITORY_GREEN_HOST_EVIDENCE_EXTERNAL_ACCEPTANCE_PENDING; 7 files, 2 tests; live readback: recorded; 4 acceptance checks; validator: automation lifecycle tests plus target scheduler and legacy-task readback | Monthly statement acquisition polls the expected cycle |
| `AUTO-003` | `automation_cutover` | `N8N-001`, `N8N-007` | None | `PARTIAL` | PARTIAL / LEGACY_TASKS_AUDITED_N8N_CUTOVER_PENDING; 4 files, 1 tests; live readback: recorded; 4 acceptance checks; validator: automation lifecycle tests plus target scheduler and legacy-task readback | Successful-run chat lifecycle does not create clutter |

## Validation

Regenerate and validate deterministically:

```powershell
python scripts/generate-project-backlog.py --check
python -m unittest tests.test_project_backlog -v
```
