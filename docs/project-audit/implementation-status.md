# Finance tracker implementation status

Date: 2026-08-19
Audited revision: `f4436d8` plus a large uncommitted n8n/agent refactor
Production writes performed by this audit: **none**

> Baseline snapshot: the test and worktree findings below describe the audited
> revision at collection time. Current status is the generated
> [`project-backlog.md`](../project-backlog.md), whose progress overlay records
> later contract, corpus, disposable-runtime, OAuth, and independent-audit evidence.

## Verdict

The repository is not ready for production promotion. Important deterministic
components exist and are well tested, but the current shared worktree is not
green: the Python suite has one n8n bootstrap failure and the subscription
adapter has two envelope-contract failures. The n8n refactor is also uncommitted and has
not been executed end to end in a disposable runtime. Older production and
replay artifacts do not certify these changed bytes or the expanded FAB/Sarwa
account scope.

The machine-readable backlog is
[`implementation-status.json`](implementation-status.json). Its IDs use stable
domain prefixes (`architecture.`, `accounts.`, `transactions.`,
`orchestration.`, `ingestion.`, `cashback.`, `automation.`, `security.`,
`deployment.`, `actual.`, and `verification.`) and expose the requested fields:
`implemented_state`, `verification_state`, `evidence_paths`, `tests`,
`live_readback`, `last_verified`, `remaining_work`, `risk`, and `blocked_by`.

## Highest-confidence implemented foundations

| Area | Evidence-backed result | Boundary |
| --- | --- | --- |
| Actual integration library | 48/48 Node tests pass, including guarded account reconciliation, rules, notes, dashboards, budgets, schedules, and preservation primitives. | Passing libraries are not proof of current production state. |
| Transaction semantics | Direction/topic locking, EI Amazon refund behavior, generic positive merchant credits, immutable amounts, and ADCB closing-payment transfer behavior have regression coverage. | Not deployed or full-corpus audited. |
| Note contract v2 | Tags-first minimal grammar, removal of routine FX/source/message/derived-cashback clutter, evidence formatting, and technical-tag rejection are tested in Python and Node. | Existing production/UI rows are not proven migrated. |
| FAB/Sarwa inventory model | Stable redacted identities and source captures exist; account, wealth, and acceptance validators have tests. | Fresh Sarwa/FX and authenticated Actual UI/API readback remain open. |
| Browser adapter foundation | ADCB, EI, FAB, Wio, and Sarwa report ready in the registry and browser tests pass. Generic merchant order enrichment uses email evidence. | RAK/SC statement adapters and the greenfield n8n end-to-end route remain missing. |
| Cashback companion | Configurable app, separate compose, CI image flow, deterministic routing, and push foundations exist. | Current public health/mobile/push and n8n live-feed cutover were not independently revalidated. |

## Current contradictions and stale evidence

1. `config/project-acceptance.json` correctly remains fail-closed, but some
   repository receipts sound stronger than its gates. In particular, the FAB
   reconciliation receipt records API success while authenticated UI readback
   is still pending.
2. The ADCB reconciliation plan/receipt targets a closed zero balance, yet the
   acceptance contract still lacks issuer-evidenced closing payment history.
   Zero must not be achieved with an invented balancing row.
3. Older production rebuild and note-cleanup audits predate note contract v2,
   semantic corrections, expanded FAB/Sarwa accounts, and the current n8n
   refactor. They are historical evidence, not present-tense acceptance.
4. The previous live Actual snapshot enumerated seven accounts and had no Sarwa
   accounts. Repository proposals and receipts do not substitute for a fresh
   API plus authenticated UI equality check.
5. Legacy Codex schedules are documented and self-archive remains unreliable;
   n8n workflow exports are intentionally inactive. Therefore automation
   ownership has not yet cut over.

## Test evidence collected in this audit

| Command | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | **FAIL** — 351 run, 350 passed, 1 failed, 6 skipped. Failure: n8n platform-bootstrap seed/readback assertion. |
| `python -m unittest tests.test_n8n_workflows -v` | **FAIL** — 36 run, 35 passed, 1 failed (same bootstrap contract mismatch). |
| `npm test --prefix integrations/actual` | **PASS** — 48/48. |
| Subscription adapter contract suite | **FAIL** — 14/16 passed; both failures are `agent_provider` envelope mismatches. |
| HTTPS read-only health probes | **UNVERIFIED** — the audit host failed before HTTP with Windows Schannel `SEC_E_NO_CREDENTIALS`; this is not evidence that either service is down. |

## Production blockers in execution order

1. Stabilize and commit the n8n subscription-adapter refactor; regenerate contracts and make
   every suite green.
2. Acquire fresh interactive Sarwa holdings and a versioned FX snapshot.
3. Prove the complete FAB non-credit and Sarwa account set through both Actual
   API and authenticated UI, including signed balances and a consistent sync
   file identity.
4. Obtain issuer evidence for the ADCB closing payment and prove AED 0 without
   a synthetic balancing row.
5. Generate note-v2 manifests and a full positive-credit/classification
   exception report; resolve manual-state conflicts before any replacement.
6. Run the exact current n8n image and complete corpus twice in disposable
   Actual, including crash, concurrency, cursor, secret, PDF, restart, and
   restore drills.
7. Review the receipts, promote immutable image digests, cut schedules from
   legacy Codex tasks to n8n, and perform authenticated live readback.

## Requirement traceability

The transcript-derived baseline is
[`docs/full-project-gap-audit-and-recovery-plan-2026-08-19.md`](../full-project-gap-audit-and-recovery-plan-2026-08-19.md).
It states that 1,297 conversation records across 223 user messages were scanned,
but no separate immutable raw transcript requirements artifact was found in the
repository. This audit therefore uses that document and
`config/project-acceptance.json` as the traceability inputs while treating every
assistant completion claim as unverified unless backed by current code, tests,
runtime evidence, or readback.

## Proof index

- Acceptance ledger: `config/project-acceptance.json`
- Account inventory: `config/account-completeness.json`
- FAB capture: `config/evidence/browser-captures/fab-non-credit-inventory-2026-08-19.json`
- Sarwa capture: `runtime/browser-captures/sarwa-holdings-2026-08-18.json`
- Account reconciliation artifacts: `runtime/account-reconciliation-production-2026-08-19.json` and `config/evidence/production-account-reconciliation-receipt-2026-08-19.json`
- Notes and semantics: `config/actual-note-contract.json`, `finance_tracker/actual_notes.py`, `finance_tracker/transaction_semantics.py`
- Browser status: `runtime/browser-adapters-status.json`
- n8n registry/workflows: `integrations/n8n/pipeline-registry.json` and `integrations/n8n/workflows/`
- Legacy automation audit: `docs/automation-lifecycle-audit-2026-08-19.md`
- CI/deployment: `.github/workflows/cashback-image.yml`, `.github/workflows/phase1-finance-artifacts.yml`, `deploy/actual-poc/compose.yaml`, and `deploy/cashback/compose.yaml`
