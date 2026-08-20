# Full Project Gap Audit and Recovery Plan

Date: 2026-08-19

## Decision summary

The project is not production-complete. Deterministic ingestion libraries, the
rule engine, direct Actual integration, cashback companion, evidence catalogue,
Docker deployment, and most dashboard scaffolding exist and have meaningful
automated coverage. The legacy HTTP Actual bridge referenced by earlier audits
was retired on 2026-08-19 and is not a supported runtime path. The previous
full-ingestion result was nevertheless too narrow: it proved parity for the
manifests and accounts already in scope, not that the scope represented the
user's complete finances.

The immediate recovery scope for accounts is deliberately limited to:

- every FAB non-credit-card account found directly in the FAB portal;
- every Sarwa Invest and Trade portfolio found directly in Sarwa;
- the existing credit-card accounts already in Actual.

The legacy Google workbook must not be used to infer or recreate additional accounts.

ADCB Credit Card is closed and historical-only. It must retain its transaction history, be excluded from active-card routing/payment recommendations, and reconcile to an evidenced AED 0 balance. No synthetic balancing transaction may be invented to force that result.

No production data was changed during this audit.

## Implementation progress after the audit

The machine-readable acceptance ledger is `config/project-acceptance.json`. It
is deliberately fail-closed: a requirement may be implemented in source and
still block production until deployment, corpus replay, source acquisition, or
readback evidence exists.

As of the latest implementation pass:

- account completeness, interactive Sarwa wealth snapshots, FX contracts,
  closed-ADCB-at-zero validation, locked transaction semantics, minimal notes
  v2, classification exception reporting, and manual-state preservation are
  implemented and covered by regression tests;
- the existing Sarwa capture parses and reconciles exactly to USD 1,571,611.22;
- the full restage planner identifies 45 unique source artifacts;
- 42 live manual category/payee differences would be overwritten by an
  unguarded rebuild and therefore now block replacement;
- no production finance rows, accounts, balances, or dashboards were changed;
- the disposable full-corpus replay, fresh FAB inventory, fresh Sarwa/FX
  projection, ADCB zero readback, and guarded production delta remain open.

## Audit basis

The audit combined four independent evidence lanes:

1. A complete scan of the current project conversation: 1,297 user/assistant text records across 223 user messages.
2. Repository inspection of configuration, adapters, rules, tests, manifests, dashboard definitions, evidence policies, and readiness documents.
3. Read-only production inspection of Actual, the cashback companion, browser captures, and installed Codex automations.
4. Official OpenAI documentation for Scheduled Tasks/Codex chat archival behavior.

Statuses below mean:

- **Verified complete**: current repository or production evidence demonstrates the requested outcome.
- **Partial**: useful implementation exists, but the requested outcome or its acceptance coverage is incomplete.
- **Missing/defective**: not implemented, not deployed, or contradicted by current evidence.
- **Superseded**: replaced by a later architectural decision.

## Current hard facts

### Actual accounts and balances

- Production Actual contains seven active accounts: six credit cards and `FAB Elite Gold Current · 2001`.
- No Sarwa account exists in Actual.
- No other FAB non-credit-card account exists in Actual.
- The FAB browser capture records AED 225,011.45 as the balance of account 2001 on 2026-08-18.
- The latest 100 captured FAB transactions net to AED 109,956.72, exactly the balance produced by the ingestion API. The AED 115,054.73 difference is a **derived balance adjustment**, not an evidenced opening balance. It must be recalculated after acquiring the maximum available history and replaced by a dated source opening balance whenever one becomes available.
- A live Actual client showed AED 106,638.72 for the same FAB account, exactly AED 3,318 below the API state. This client was missing a captured 2026-08-17 credit.
- Aggregate state is divergent: one live UI showed AED 88,534.11, while the synchronized ingestion API totalled AED 53,207.37. The earlier approximately -AED 76,000 view was not reproducible. None of these totals is an acceptable net-worth figure while accounts, opening balances, and client synchronization are incomplete.

### Sarwa

The authenticated Sarwa capture exists and totals USD 1,571,611.22:

- Personal Investments: USD 1,249,330.82;
- rekening ouders: USD 98,819.13;
- shared airbnb returns: USD 15,872.67;
- Sarwa Trade: USD 207,588.60;
- Sarwa Classic: USD 0.

The capture includes positions, units, allocation, market value, and cash. It is currently stranded: there is no registered `sarwa_holdings_v1` parser, no valuation store, no Actual account bootstrap, and no replay/completeness test. Black Swan Protection's USD 1.3 million is insurance coverage and must not be treated as a net-worth asset.

### Notes, tags, and classification

- Current server snapshots no longer contain legacy `source:statement`, `currency:`, `original:`, `message:`, `#browser-import`, or `#primary` values.
- The user-visible screenshot does contain those values. This proves either stale/divergent client data or a different local Actual file; it must be resolved by transaction ID, not dismissed.
- The current note contract still intentionally emits `FX:` details on 311 rows and emits operational `#cashback-*` tags. Both are more verbose/redundant than the requested display-note policy.
- Production contains positive Emirates Islamic Amazon entries such as AED 3.55 and AED 2.57 categorized as Online Shopping and tagged as EI cashback. They are ordinary merchant credits/refunds. EI Amazon cashback is settled as Amazon credits, so statement-side positive Amazon entries must not be inferred as cashback.
- At least 41 positive credits remain in ordinary expense categories.
- The live UI reports 2,186 uncategorized transactions.
- Six utility transactions contain mutually contradictory `#home` and `#rental` tags.
- Forty-seven transactions are in `Needs Review` without the note tags used by the review dashboard, so the queue can be bypassed.

### Automations

- Six installed tasks match `config/codex-automations.json`: four active and two Standard Chartered placeholders paused.
- The only active daily live scan is RAKBANK at 08:05 using Luna Max.
- Two consecutive successful RAKBANK runs committed and verified their cursors, then called the app archive function. Both archive calls hung for minutes and the tasks remain visible.
- The problem is not missing prompt instructions; it is an unconfirmed app/tool lifecycle operation.
- Official documentation describes run chats for standalone scheduled tasks and manual archival, but does not document an ephemeral/no-history run mode or guarantee automatic archival. Cloud/web tasks are not a drop-in replacement because the current workflow uses local repository code, private SSH, and the companion service.

## Capability matrix

| Area | Status | Audit verdict |
| --- | --- | --- |
| Actual-first architecture; no Notion runtime dependency | Verified complete | Current architecture is coherent. |
| Separate cashback companion | Verified complete | Live operational state remains separate from the authoritative Actual ledger. |
| Statement-agnostic normalized ingestion contract | Partial | Core abstraction exists; RAK and SC statement adapters still need real representative statements. |
| AutoCat-style staged static rule engine | Verified complete | Ordered groups, actions, locks, and AI boundary exist and are tested. |
| AI fallback after deterministic/history rules | Partial | Policy exists, but category/review invariants and end-to-end outcomes need coverage. |
| Actual category learning disabled | Verified complete | Correct for the owned rules pipeline. |
| Historical statement replay and deduplication | Partial | Row parity is strong, but scope/completeness and semantic correctness are not. |
| FAB non-credit-card accounts | Missing/defective | Only account 2001 exists; its opening balance is missing. Other FAB accounts must be inventoried from the portal. |
| Sarwa accounts and investment values | Missing | Capture exists; parser, store, accounts, scheduled refresh, and validation do not. |
| Correct net worth / All Accounts total | Missing/defective | Missing accounts, missing opening balance, and client/API divergence invalidate it. |
| Canonical minimal notes with tags prepended | Partial | Server cleanup exists, but FX/reward-tag policy is too verbose and the visible client is divergent. |
| Refund/credit/cashback/transfer topic rules | Missing/defective | EI Amazon and generic positive merchant credits are misclassified. |
| Payee normalization | Partial | Foundation exists; many merchants remain concatenated or unresolved. |
| Rental/shared/property tagging | Partial | Requested tags exist, but home/rental exclusivity is broken. |
| Evidence catalogue and selective attachments | Partial | Catalogue and documents exist; warranty acquisition and end-to-end linkage are incomplete. |
| Cashback routing UI and deterministic profiles | Verified complete with regression recheck | Mobile UI, routing, profile configuration, and push were accepted; weekly pacing/history should be revalidated. |
| Dashboards and reports | Partial | Seven dashboards/12 reports exist; wealth/net-worth/person views lack valid source data. |
| Budgets and schedules | Partial | Recommendations exist, but `budget_months` and `schedules` remain empty in production bootstrap. |
| Account/person ownership | Missing | No owner field/tag contract or owner dashboards are populated. |
| Production CI/GHCR/Docker | Verified complete with ops follow-up | Deployment works; restart monitoring and a restore drill remain open. |
| Successful-run chat archival | Missing/defective | Reproduced twice; archival hangs and remains unconfirmed. |
| Ephemeral/cloud scheduled execution | Unsupported as a current assumption | No documented ephemeral mode; cloud cannot directly use the local/private workflow. |
| Notion dashboards/config/rules | Superseded | Must not be reintroduced. |

## Recovery plan

## Adversarial review decision and hard guardrails

An independent red-team review returned **NO-GO for production writes as originally sequenced** and conditional approval for read-only acquisition, schema design, fixtures, and test scaffolding. The following amendments are mandatory:

1. Treat the FAB AED 115,054.73 difference as a replaceable derived adjustment, with formula, capture ID, as-of timestamp, covered transaction window, and confidence. Older history must reduce or replace it without double-counting.
2. Keep Sarwa provider-native USD snapshots as the wealth source of truth. Define versioned USD/AED FX snapshots, rounding tolerance, freshness, and exact net-worth inclusion before projecting one aggregate AED valuation per portfolio into Actual.
3. Sarwa refresh is explicitly interactive/user-assisted browser capture, matching FAB and Amazon. Preserve the pluggable provider boundary and explicit stale indicator, but do not pursue unattended Sarwa sessions or persist browser cookies/MFA state.
4. Create failing regression fixtures before implementing semantic changes. Read-only phases may emit captures, proposed manifests, deltas, and exception reports only.
5. Inventory manual Actual state—overrides, splits, reconciliations, transfer/schedule links, custom notes, and post-import corrections—before any rebuild. Manual state must survive by stable identity or block the write.
6. Normalize debit/credit signs at the source adapter boundary. Static/AI rules may classify a transaction topic but can never change an amount.
7. Back up and compare the divergent client and server before clearing any client state. Prove file ID, account IDs, transaction IDs, sync state, timestamps, and absence of unique local edits.
8. Complete a disposable restore drill before any destructive or replacement production operation.
9. Use stable provider account identities/fingerprints plus last four, type, and currency; display names are not identifiers and full account numbers are never stored.
10. Run an automation lifecycle feasibility experiment before committing to a parent/controller design. If archival cannot be confirmed through supported capabilities, document the limitation instead of claiming a workaround.

### Phase 0 — Freeze and define acceptance

1. Do not perform another production clean replay yet.
2. Export and hash a fresh server snapshot plus the divergent local client/file before clearing or resyncing anything.
3. Inventory manual versus system-managed state for every transaction field, split, reconciliation, transfer link, schedule link, note, and correction.
4. Define an account-completeness manifest with stable provider identity/fingerprint, last four, type, currency, owner if known, balance source, and as-of timestamp.
5. Define net worth mathematically: included assets/liabilities, sign conventions, base currency, FX source/timestamp, freshness, and as-of alignment.
6. Limit the new account scope to FAB non-credit-card accounts and Sarwa portfolios, per the latest instruction.
7. Explicitly include or exclude every captured Sarwa account, including zero/closed accounts, with a reason and closed status.
8. Add a client/API synchronization gate: the same file ID, account IDs, transaction IDs, and balances must be visible through the production UI and ingestion API.
9. Mark ADCB Credit Card closed/historical, require an AED 0 reconciled balance from source/payment history, and exclude it from active routing.

Exit gate: the expected account list and balance semantics are explicit and testable before any write.

### Phase 1 — Inventory FAB directly

1. Re-open the authenticated FAB portal with user-assisted login/MFA.
2. Capture every non-credit-card account's exact name, type, last four digits, currency, current/available balance, and balance timestamp.
3. Download the largest official transaction export/history available for every account.
4. Preserve the portal capture using the versioned browser-capture schema.
5. Prefer a dated statement/source opening balance for account 2001. If unavailable after maximum-history acquisition, propose a replaceable derived adjustment equal to source balance minus covered normalized activity; record the exact formula and evidence.
6. Do not invent accounts or use the legacy workbook as an account source.

Exit gate: each FAB non-card account independently reconciles from opening balance plus imported activity to the portal balance.

### Phase 2 — Implement the Sarwa wealth feed

1. Implement a pluggable interactive `SarwaProvider` capture contract and register `sarwa_holdings_v1` as a deterministic parser.
2. Require user-assisted login/MFA for each acquisition and persist only the normalized capture—not cookies, browser session state, passwords, or MFA material.
3. Introduce a versioned `wealth_snapshot_v1` contract containing provider, stable portfolio identity, currency, as-of time, total value, cash, positions, units, instrument identity, ticker/exchange, price/market value, corporate-action context, contributions/withdrawals where available, and source identity.
4. Introduce versioned FX snapshots with provider, pair, timestamp, rate, precision, freshness, and source identity.
5. Store immutable snapshots in a dedicated wealth snapshot store; do not encode positions as fake spending transactions.
6. Prove positions plus cash reconcile to the provider portfolio total without double-counting.
7. Create a proposed off-budget Actual account for each included Sarwa Invest/Trade portfolio according to the completeness manifest.
8. Project one aggregate AED valuation per included portfolio using the exact wealth and FX snapshots; keep position-level reporting outside ordinary Actual transactions.
9. Represent valuation changes using a tested adjustment strategy that preserves history and does not pollute ordinary income/expense reporting.
10. Exclude insurance coverage from net worth.
11. Implement a user-assisted refresh workflow with a visible as-of timestamp and stale-data warning.
12. Restrict retirement formulas to an explicit investment-account allowlist; never query every off-budget account.

Exit gate: the Sarwa sum, positions, and per-portfolio values match the source capture and survive an idempotent replay.

### Phase 3 — Fix transaction topic semantics before categorization

1. Add a deterministic topic stage before ordinary category/reward rules: purchase, refund, reversal, card payment/transfer, cashback/reward, fee, interest, and Amazon credit.
2. Make transaction direction and account type primary evidence. A positive merchant credit in an expense context defaults to refund unless explicit evidence identifies transfer or reward.
3. Restrict the EI Amazon cashback rule to actual reward evidence; never apply it to every EI Amazon transaction.
4. Remove `#cashback-ei_amazon` from ordinary statement refunds.
5. Normalize transaction signs from source debit/credit semantics and account type in the adapter, then add generic and issuer-specific topic/transfer classification with paired-account reconciliation where both sides are present.
6. Ensure later rules cannot overwrite a locked transaction topic.
7. Generate a proposed backfill and exception report for all positive credits in expense categories; do not write it to production in this phase.

Exit gate: the two known Amazon credits and the full positive-credit corpus are correctly classified, with fixture-backed evidence.

### Phase 4 — Enforce the display-note contract

1. Define one canonical grammar: normalized semantic tags first, then concise human-meaningful text/evidence links only.
2. Remove legacy source labels, message hashes, parser bookkeeping, redundant evidence words, `#browser-import`, and `#primary` from display notes.
3. Remove routine original-currency/amount text from display notes. Preserve technical FX data in manifests/evidence metadata, not the ordinary transaction note.
4. Stop exposing derived cashback bucket state as redundant ledger tags unless the tag is independently useful for reporting.
5. Normalize tag case and ordering; deduplicate tags.
6. Enforce mutual exclusivity for `#home` versus `#rental`; rental rows receive both `#rental` and exactly one `#rental:<unit>` tag.
7. Make `Needs Review` category and review tags mutually reinforcing so dashboards cannot miss review items.
8. Compare screenshot rows to server rows by stable transaction ID and generate an unsynced-difference report. Clear/resync a client only after proving it contains no unique state.

Exit gate: every production note passes the canonical parser, forbidden-token scan, ordering check, exclusivity rules, and a user-visible UI spot check.

### Phase 5 — Expand ingestion and account tests

Add tests that fail on the defects found in this audit:

1. Expected-account inventory equality, not just accounts seen in manifests.
2. Portal/source balance = evidenced opening balance or derived adjustment + normalized transactions = Actual API balance = production UI balance.
3. Sarwa portfolio totals, positions, snapshot idempotency, and stale/as-of handling.
4. Generic positive merchant credit/refund behavior.
5. EI Amazon refund versus Amazon-credit reward behavior.
6. Card-payment transfer direction/sign and paired transfer behavior.
7. Minimal note grammar, forbidden tokens, prepended tags, deduplication, and stable output.
8. Home/rental tag exclusivity and unit tagging.
9. Review-category/review-tag invariants.
10. Investment dashboard allowlist and liability exclusion.
11. Browser-captured accounts surviving a clean rebuild.
12. Evidence linkage and exact Amazon order splitting only when amounts reconcile.
13. Manual-state preservation across delta repair or rebuild.
14. Stable account identity despite display-name changes.
15. FX conversion, rounding, stale rates, missing prices, and authoritative-versus-estimated investment values.
16. Classification coverage: every row is resolved or appears in one explicit review queue; broad rules are tested against false positives.
17. Closed-account invariants: ADCB retains history, has no post-close activity, is excluded from active routing, and reconciles to AED 0 without a fabricated adjustment.

Exit gate: both Python and Node suites pass with these new failure cases represented.

### Phase 6 — Rebuild and reconcile Actual

1. Create verified backups, record hashes, and complete a disposable restore drill.
2. Generate manifests for the complete scoped corpus: existing cards, all FAB non-card accounts, and all Sarwa valuation accounts.
3. Apply fixed topic rules, normalization, static rules, history matching, scoped AI, property projection, and canonical notes in the documented order.
4. Replay into a disposable Actual file first.
5. Audit identities, amounts, dates, payees, categories, notes, transfers, account balances, wealth values, and evidence links.
6. Replay a second time and require zero duplicates/changes.
7. Generate and review an exact production delta plus manual-state preservation report. Prefer a guarded delta repair unless a clean replacement is proven lossless.
8. Only then perform the guarded production operation.
9. Open the production UI after a fresh sync and compare it against the API snapshot.

Exit gate: no missing/unexpected accounts, balance differences, note violations, semantic refund errors, untracked rows, or client/API divergence.

### Phase 7 — Complete finance functionality previously left as proposals

1. Review and apply user-approved category budgets; keep unapproved values as proposals.
2. Add Actual schedules for evidenced recurring utilities, subscriptions, mortgage, Sarwa transfers, and card payments using date/amount ranges where appropriate.
3. Implement owner/person configuration and inherited transaction tagging/reporting.
4. Finish warranties and high-value purchase-evidence acquisition and verify files against the catalogue.
5. Revalidate shared-expense, rental, review, wealth, retirement, spending, and Sankey dashboards using the corrected data.
6. Finish real RAK and SC statement adapters when representative statements are available; keep SC tasks paused until then.

Exit gate: production configuration, dashboards, and reports match their versioned definitions and have valid inputs.

### Phase 8 — Repair scheduled-task lifecycle

1. Stop treating a dispatched archive request as success.
2. Run a small feasibility experiment that proves thread identification, archive completion, archived-state confirmation, and failure visibility.
3. Replace the daily standalone RAK run with a recurring schedule in one dedicated operations task/chat only if the experiment proves chat reuse; retain failed-run notifications and suppress routine success clutter.
4. Add a parent/controller only if supported tooling proves it can identify, archive, and verify the exact completed child task.
5. Do not move to cloud tasks merely to hide chats. Cloud tasks still have history and cannot directly access the current local project/private SSH topology.
6. Keep monthly issuer reconciliation jobs separate where their audit history is materially useful.
7. Add an automation lifecycle audit covering schedule, timezone, model, prompt/source contract, cursor, notification policy, completion state, and archived state.

Exit gate: three consecutive successful daily runs create no visible task clutter, while a forced failure remains visible and actionable.

### Phase 9 — Operational acceptance

1. Perform a service restart and verify Actual and cashback health.
2. Perform a real backup restore drill in a disposable location.
3. Verify Cloudflare routes, SharedArrayBuffer headers, push delivery, and independent service restart behavior.
4. Observe one complete statement/reconciliation cycle for every active issuer.
5. Regenerate the production-readiness report and mark stale documents as superseded.

Exit gate: the system passes a full cycle without manual data repair or hidden divergence.

## Definition of done

The project is complete only when all of these are true simultaneously:

- every FAB non-credit-card account and every intended Sarwa portfolio exists exactly once;
- ADCB Credit Card is closed/historical and reconciles to AED 0 from evidenced activity;
- FAB and Sarwa balances reconcile to timestamped source evidence;
- Actual UI and ingestion API show the same file, transactions, account balances, and net worth;
- the All Accounts total is explainable and reproducible;
- positive credits are correctly separated into refunds, transfers, rewards, reversals, fees, and interest;
- EI Amazon statement credits are not mislabeled as cashback;
- all notes follow the minimal tags-first grammar and contain no forbidden operational metadata;
- all payees/categories/tags either resolve or appear in an explicit review queue;
- the clean corpus replays twice without drift or duplicates;
- investment and retirement dashboards use real Sarwa values and exclude unrelated liabilities;
- budgets/schedules/owner reporting that are claimed as active are actually populated in Actual;
- evidence files and catalogue entries reconcile;
- successful daily automation runs do not accumulate visible chats, while failures remain auditable;
- backup restore, service restart, and at least one real issuer cycle are verified.

## Priority order

1. Account inventory and balance semantics.
2. FAB opening balance and client/API synchronization.
3. Sarwa snapshot feed and accounts.
4. Refund/credit/cashback/transfer semantics.
5. Minimal notes and tag invariants.
6. Expanded acceptance tests.
7. Disposable rebuild, then production rebuild.
8. Budgets, schedules, owners, evidence completion, and dashboard revalidation.
9. Automation chat lifecycle and operational acceptance.
