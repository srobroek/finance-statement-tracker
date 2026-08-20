# Finance tracker implementation status

Date: 2026-08-20
Audited committed baseline: `6a4595caf8e9da0970e93aea8c32cc8fa0e4dcda`
Production writes performed by this audit: **none**

> Current status artifact: this document and
> [`implementation-status.json`](implementation-status.json) were refreshed from
> the committed baseline and current repository tests. Host-only `/opt/disposable`
> receipts are explicitly external and unversioned; they do not become repository
> evidence or restart proof merely because their path and hash are recorded.
> [`project-backlog.md`](../project-backlog.md) remains the authoritative execution
> queue.

## Verdict

The repository is green but not ready for production promotion. The current
Python suite and Codex runner contracts pass. The live critical boundary remains
operational: one transient WF23 workflow must be removed exactly before the
Microsoft restart/second-read proof and OneDrive-root setup. Folder and canvas
polish are explicitly deferred until after the functional MVP gates. The Microsoft first reads refreshed both
credentials, but the failure receipt is external and no n8n restart plus second
read proves persistence. Older production and replay artifacts do not certify
the expanded FAB/Sarwa scope or the current end-to-end finance path.

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
| Browser adapter foundation | ADCB, EI, FAB, Amazon, Wio, and Sarwa report ready in the registry and browser tests pass. | RAK/SC statement adapters and the greenfield n8n end-to-end route remain missing. |
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

## Current test and host evidence

| Command | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | **PASS** — 435 passed, 8 skipped at the refreshed baseline. |
| `python -m unittest tests.test_project_backlog -v` | **PASS** — 20/20 at the refreshed baseline. |
| `npm test --prefix integrations/actual` | **PASS** — 48/48. |
| `npm test --prefix services/codex-agent-runner` | **PASS** — 16/16 in the retained current evidence. |
| Microsoft host run | **PARTIAL** — external failure receipt records first Outlook and OneDrive reads and refreshed expiries; exact cleanup receipt and restart/second-read proof are pending. |

## Production blockers in execution order

1. Remove the exact WF23 orphan and retain a later cleanup receipt proving the
   reviewed inactive project boundary.
2. Prove Microsoft persistence through n8n restart and bounded Outlook/OneDrive
   second reads without waiting for token expiry.
3. Run WF22 create-or-reuse for the OneDrive `Finance Evidence` root with exact readback.
4. Prove WF20 Actual fencing, immutable verification-artifact binding and recovery.
5. Independently review and migrate the 15-to-4 Data Table design: ingestion state,
   merged documents, Actual batches with immutable OneDrive verification
   artifacts, and review-only AI state. Generic execution/failure observability
   belongs to n8n, access logs to Cloudflare, optional sanitized agent traces to
   Langfuse, and cashback period close to the companion.
6. Pass complete disposable double replay and reviewed promotion/activation gates.
7. Apply the final Finance/Global folder, Canvas Group and sticky-note polish.
8. Acquire fresh interactive Sarwa holdings and a versioned FX snapshot.
9. Prove the complete FAB non-credit and Sarwa account set through both Actual
   API and authenticated UI, including signed balances and a consistent sync
   file identity.
10. Obtain issuer evidence for the ADCB closing payment and prove AED 0 without
   a synthetic balancing row.
11. Generate note-v2 manifests and a full positive-credit/classification
   exception report; resolve manual-state conflicts before any replacement.
12. Run the exact current n8n image and complete corpus twice in disposable
   Actual, including crash, concurrency, cursor, secret, PDF, restart, and
   restore drills.
13. Review the receipts, promote immutable image digests, cut schedules from
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
