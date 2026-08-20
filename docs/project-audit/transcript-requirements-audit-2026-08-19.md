# Finance platform transcript requirements audit

Date: 2026-08-19
Companion ledger: `docs/project-audit/transcript-requirements-ledger-2026-08-19.json`

## Verdict

The transcript does not support a claim that the project is finished. It supports
four user-confirmed acceptances, a set of final architecture decisions, and a
large body of repository implementation that is not yet deployed or proven
against the current production data.

The most important distinction is:

- **User-confirmed:** the later cashback routing correction, the compact
  mobile/tabbed presentation, direct and native-trigger web push, and Actual's
  SharedArrayBuffer proxy behavior were observed and accepted by the user.
- **User-reported defective:** FAB/Sarwa account completeness, All Accounts/net
  worth, investment feeds, refund/reward/transfer semantics, canonical notes,
  the actionable review queue, and successful-run task cleanup were explicitly
  reported wrong or missing.
- **Repository/assistant claim only:** the fenced n8n writer, deduplication and
  quarantine, bounded Codex runner, rule ownership, and several recovery
  controls have code/spec/test evidence but no production acceptance.
- **Requested but unverified:** secure PDF extraction, selective evidence,
  budgets, schedules, owners, many reports, production n8n, 1Password runtime
  injection, restore/restart, and the final clean replay.

No production state was changed for this audit.

## Ledger coverage

The machine-readable ledger contains **74** stable requirements:

| Prefix | Workstream | Count |
| --- | --- | ---: |
| `N8N-*` | n8n platform and workflows | 9 |
| `DOC-*` | Outlook, OneDrive and document evidence | 7 |
| `AGENT-*` | AI providers, Codex runner and community-node policy | 5 |
| `ACTUAL-*` | Accounts, ledger, rules, budgets and reports | 21 |
| `CASHBACK-*` | Live cashback companion | 14 |
| `BROWSER-*` | FAB, Sarwa and Amazon acquisition | 4 |
| `AUTO-*` | Scheduling and task lifecycle | 3 |
| `PLATFORM-*` | Containers, CI, secrets, Cloudflare and operations | 7 |
| `DOCS-*` | Documentation, acceptance and public reusability | 4 |

Transcript status counts:

| Status | Count | Meaning |
| --- | ---: | --- |
| `FINAL_DECISION` | 18 | Later user decision is authoritative |
| `USER_CONFIRMED_ACCEPTANCE` | 4 | User observed and accepted the result |
| `USER_REPORTED_DEFECT` | 9 | User explicitly reported wrong/missing behavior |
| `REQUESTED_UNVERIFIED` | 23 | Requested, not user-confirmed complete |
| `ASSISTANT_CLAIM_ONLY` | 6 | Code/docs/assistant claim, no user acceptance |
| `MIXED` | 13 | Accepted/implemented portions plus open portions |
| `SUPERSEDED` | 1 | Notion runtime work removed from scope |

Each JSON entry has the requested fields: `id`, `title`, `requirement`,
`status_from_transcript`, `priority`, `dependencies`, `acceptance_criteria`,
`claimed_evidence`, `contradictions`, and `source_turn_summary`. It also adds an
`expected_state` so the backlog has an unambiguous target.

## Authoritative later decisions and overrides

These overrides must be applied before interpreting older requests:

1. **No Notion.** The user said to remove the dependency entirely with no
   migration. All old Notion layouts, databases, Mermaid and native-chart work
   are superseded. The intent behind categories, rules, budgets, docs and
   dashboards survives in Actual/n8n/OneDrive/cashback, not in Notion.
2. **Actual is the only posted ledger.** SQLite is not a raw shadow ledger.
   Companion SQLite owns live cashback events/periods/alerts/cursors only;
   Postgres owns n8n operational state only.
3. **n8n is the target scheduler/orchestrator.** Existing Codex schedules are a
   transitional state and must remain until issuer-by-issuer cutover gates pass.
   The legacy bridge/SSH submission model is retired and must not return.
4. **Live scan cadence is morning-only.** Hourly, twice-daily and evening scans
   were superseded. RAK is active at 08:05 Dubai; SC is a paused placeholder.
   ADCB, Wio and EI have no live cashback transaction scan.
5. **Statement acquisition and notification acquisition differ.** A monthly
   job selects the latest statement for the expected card cycle; a live scan
   enumerates every supported message since cursor minus overlap.
6. **Card cycles are no longer tentative.** RAK and SC close day 5 and reconcile
   day 6; EI closes at month-end and reconciles day 1; Wio statement ingestion is
   day 3 and Wio is outside cashback.
7. **No provisional/approved cashback UI.** Valid live notifications count
   immediately. Only cashback finalization remains pending until a reconciled
   statement.
8. **EI is statement-only and unlimited.** It has no transaction-email feed,
   no live total/minimum/cap and no daily refund scan. Positive Amazon statement
   credits are refunds unless explicit evidence identifies an Amazon-credit
   reward.
9. **RAK unknown-channel assumption is pragmatic.** Because the observed email
   does not distinguish Apple Pay from physical, non-grocery/dining/travel
   transactions use a configurable Apple Pay/e-wallet default. Manual physical
   corrections were explicitly dropped.
10. **Sarwa refresh is user-assisted.** No stable official API/export or
    unattended browser-session claim has been proven. Persist snapshots, never
    cookies/MFA state.
11. **Repository visibility remains private unless separately changed.** The
    later public-app goal means reusable/publicly deployable code, but it did
    not explicitly reverse the earlier instruction to create a private repo.
12. **Scheduled Codex tasks have no documented ephemeral/no-history mode.** A
    bounded `codex exec --ephemeral` AI call is a different capability. n8n
    routine runs are the durable way to eliminate per-run Codex chat clutter.

## Current evidence boundary

The repository now contains extensive n8n workflow exports, custom-node and PDF
utility code, an isolated Codex runner, schema/config work, FAB/Sarwa account
inventories, wealth contracts, rules, dashboards and tests. That is meaningful
implementation evidence but not deployment evidence.

The current acceptance ledger remains non-final: it contains `BLOCKED`,
`PARTIAL`, `MISSING`, `IMPLEMENTED_NOT_DEPLOYED`, and one
`TESTED_IN_DISPOSABLE` item. In particular:

- deterministic n8n orchestration is still partial;
- the Actual outbox, mail completeness, secrets, Cloudflare route security,
  bounded MCP and resilient document extraction are implemented but not
  deployed;
- FAB/Sarwa completeness and wealth are partial;
- ADCB-at-zero and Actual UI/API parity are blocked;
- the full disposable idempotent rebuild is missing;
- budgets/schedules/owners/reports and selective evidence remain partial.

The working tree also contains substantial uncommitted n8n work. Any completion
or reproducibility claim must be made against a committed SHA and exact image
digest, not the current mutable workspace.

## Prioritized delivery plan

### P0 — Define and preserve the real finance state

1. Freeze production finance writes and capture hash-bound Actual server and
   divergent client snapshots plus manual state.
2. Prove the complete FAB non-credit inventory and source balances; keep the
   legacy workbook excluded.
3. Prove every Sarwa portfolio/position snapshot, obtain reviewed USD/AED FX,
   and define its exact Actual off-budget projection.
4. Prove ADCB closed/historical at AED 0 from issuer/payment evidence.
5. Establish the net-worth equation and exact UI/API identity/balance parity.

### P0 — Fix semantics before any replay

6. Add or retain failing fixtures for positive merchant refunds, issuer
   cashback/rewards, reversals, card payments/transfers, fees, interest and
   adapter-boundary sign normalization.
7. Enforce the minimal tags-first notes contract and remove operational/FX
   clutter from display notes.
8. Complete payee normalization and review-queue invariants.
9. Compile exclusive Actual/upstream rule ownership and require an empty overlap
   report.

### P0 — Prove the new n8n plane in disposable infrastructure

10. Commit/build the exact n8n, custom-node, PDF utility and Codex-runner images.
11. Deploy n8n/Postgres/runners/PDF utility inactive and write-disabled.
12. Prove 1Password cold start/rotation/restore and zero secret leakage.
13. Execute mail failure matrices, document security fixtures, fenced outbox
    kill tests, bounded AI receipts, and Cloudflare/MCP positive and negative
    tests.
14. Keep the transitional Codex tasks active until their corresponding n8n
    workflow passes fixture, shadow, guarded write, readback and three scheduled
    runs.

### P0 — Rebuild safely

15. Generate the complete corpus for cards, all FAB non-credit accounts and
    Sarwa valuation accounts.
16. Replay twice into disposable Actual, preserve manual state, and require zero
    second-run change and zero semantic/note/account/balance violations.
17. Review an exact production delta, perform the guarded write, then verify a
    freshly synced UI equals the API and every source equation.

### P1/P2 — Finish functional coverage

18. Apply user-approved budgets, savings goals, cleanup pools and schedules.
19. Implement owner/person inheritance, shared/property tagging and dashboards.
20. Finish selective warranties/receipts/bills and exact Amazon order splits.
21. Revalidate all Actual dashboards/reports with correct source data and finish
    investment/retirement views.
22. Revalidate cashback weekly pace/history after refactors while preserving the
    user-accepted routing, mobile UI and push behavior.
23. Run backup restore, independent restart, Cloudflare, push and a full real
    issuer-cycle acceptance before calling the system production complete.

## Completion rule

Do not promote an item because an assistant said it was done, a file exists, or
a unit test is green. Promote only when its JSON `acceptance_criteria` are
satisfied with fresh machine-readable evidence and, for user-facing behavior,
fresh UI review. User-confirmed acceptance should be preserved but regression
tested whenever shared engine/config/deployment code changes.
