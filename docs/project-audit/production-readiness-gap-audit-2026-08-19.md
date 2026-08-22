# Independent production-readiness gap audit — 2026-08-19

Audit revision: `da6b0c1` (subject to the commit-scope note below)

Audit mode: repository and retained-evidence read only

Production writes performed: **none**

## Verdict

The finance tracker is **not ready for production promotion**.

The deterministic core is materially implemented and the current local code
suites are green. Actual note-v2 and transaction semantics have a strong
read-only full-corpus result, the finance custom nodes and bounded Codex runner
pass their local suites, and the n8n exports now form a coherent inactive
19-workflow specification. Those facts do not prove the current bytes in the
target runtime. The remaining blockers cross financial correctness, current
Actual state, credentials, workflow execution, account completeness, recovery,
and public/mobile acceptance.

The most serious financial contradiction is ADCB. A retained production receipt
claims the account was adjusted to AED 0 and closed, but the current issuer
evidence records a non-zero AED -238.32 closing balance and the current proposal
correctly says `BLOCKED_NONZERO_ISSUER_STATEMENT`. That production receipt cannot
serve as acceptance evidence until the live account is read back and reconciled
against later issuer evidence without a synthetic balancing row.

## Status semantics

- **Implemented + verified** means current evidence proves the stated, narrow
  scope. It does not imply production verification unless the scope says so.
- **Implemented-unverified** means code/configuration exists but current runtime,
  external-state, or end-to-end evidence is absent, stale, or commit-mismatched.
- **Missing** means a required implementation or acceptance artifact is absent.
- **Superseded** means the artifact/path is historical and must not be used as
  proof for the current end state.

## Readiness matrix

| Area | Status | Evidence that is currently valid | Gap before production |
| --- | --- | --- | --- |
| Actual adapter/parser core for ADCB, EI and Wio | **Implemented + verified (code/fixtures)** | `config/statement-sources.json`; parser/projection tests; finance-node suite 21/21 | No current n8n execution through archive, extraction, outbox, writer, and Actual readback |
| RAK and Standard Chartered statements | **Missing** | Both are explicit `PLACEHOLDER` sources | Capture real statement/email fixtures, implement adapters, tie balances, and pass replay/reconciliation |
| Actual corpus note-v2 and transaction semantics | **Implemented + verified (read-only corpus)** | `config/evidence/actual-corpus-migration-dry-run-2026-08-19.json`: 45 manifests, 3,927 unique IDs, zero amount/sign mutations, zero note violations; two known EI Amazon credits are refunds | Review 877 semantic/note deltas, 28 manual-category conflicts, and 15 snapshot-missing identities; replay current plan twice in disposable Actual; separately authorize production delta and verify UI/API |
| Actual full import and writer path | **Implemented-unverified** | Exact-state writer, cross-source suppression, manual-state checks, and offline Actual integration pass | No current full corpus replay through the 19-workflow n8n path; no writer-lease concurrency/kill/recovery receipt; no current production UI/API parity |
| Classification/review queue | **Implemented-unverified** | Static/history/scoped-AI ownership and locked-field tests pass | No current full disposable and production exception report proving every unresolved row appears exactly once and every manual override survives |
| Live cashback event store and arithmetic | **Implemented + verified (unit/retained fixture scope)** | Provisional events, dedupe, cursor heartbeat, refunds/reversals, reconciliation/finalization guards, alerts, routing, and push-store tests pass | Current deployed service health/state and n8n cutover were not read back in this audit; statement-only finalization still depends on missing RAK/SC statement sources |
| Live notification sources | **Partial** | RAK is `ACTIVE`; historical cursor receipts show accepted RAK messages; unsupported formats fail closed | SC remains a placeholder; no current source-to-n8n-to-companion execution receipt; EI is intentionally statement-only and Wio is outside live cashback |
| Portable/public cashback profiles | **Implemented + verified (code)** | Versioned schema plus four non-AED example profiles; profile/routing tests pass; CI defines a container matrix | UAE card programme seed remains a POC assumption; no current CI run or deployed fictional-profile receipt proves the current commit |
| PWA/mobile web push | **Implemented-unverified** | Manifest/UI assets and idempotent subscription/candidate/delivery tests exist | No real iPhone install/permission, VAPID delivery, background/lock-screen, stale-feed, weekly pace, history, or close/reset acceptance receipt |
| FAB non-credit inventory | **Implemented-unverified (source and older API receipt)** | Owner-evidenced 2026-08-19 portal inventory lists six accounts/loan; retained receipt says five accounts created and API readback passed | Receipt is from `76cab8d`, UI readback is explicitly pending, and current live API/UI/source balance parity was not independently established |
| Sarwa wealth accounts | **Implemented-unverified** | 2026-08-18 user-assisted capture contains four active Invest/Trade accounts plus a closed account; parsers, stable identities, ownership constraints, and position sidecar tests pass | No FX snapshot, no current Actual accounts/balances, no T1/T2 historical delta, no immutable acceptance bundle, and no UI/API net-worth equation |
| ADCB closed historical account | **Missing safe accepted state** | Latest issuer statement is tied and parsed; code rejects synthetic balancing rows | Current evidence shows issuer closing balance -23,832 fils while an older receipt says production was adjusted to zero/closed. Obtain later issuer zero/closing-payment evidence or retain the non-zero state; inspect current Actual before any repair |
| Budgets, schedules, owners and reports in Actual | **Implemented-unverified** | Portable bootstrap, schedule/payment-reminder, dashboard, tag reporting and mortgage formula tests pass | Values are scaffolding; complete account parity, user-approved budgets, owner allocations, subscriptions/savings/retirement data, and UI report readback are absent |
| Codex schedule contracts | **Implemented + verified (manifest only)** | Six schedules match runbooks and lifecycle tests; configured timings match the stated card cycles | Four legacy tasks remain active and two paused; n8n cutover has not occurred; external exact-ID archive controller remains a desktop-app dependency |
| Evidence search/archive core | **Implemented + verified (hash/file scope), partial semantics** | 67 catalogue entries and 67/67 referenced files exist with matching hashes; 20 entries link to transactions; selective search policy and evidence tests pass | Forty entries use a duplicated `Finance Evidence/Finance Evidence/...` root, violating the shallow path contract; no warranty expiry is populated; property/category/link coverage is incomplete |
| n8n document acquisition/extraction | **Implemented-unverified** | Inactive workflows 13/14, fixed-purpose PDF node, redacted receipt state, and ephemeral-plaintext contract pass structural tests; Outlook and Drive credentials currently report connected | Credential binding, token refresh and canonical-root readback remain pending; there is no current live attachment/HTML, >4 MiB session, encrypted PDF, Linux UDS, plaintext-deletion, archive-hash/readback, or catalogue-link execution receipt |
| n8n workflow specification | **Implemented + verified (static)** | 21 JSON exports are inactive; registry/export bijection, cursor windows, one terminal cursor commit, durable outbox transitions, error redaction, and 17/17 `From list` subworkflow selectors pass tests | Registry correctly remains `SPEC_ONLY`; static graph tests do not prove importability or behavior in the current exact image |
| n8n Data Tables | **Implemented-unverified for current bytes** | v4 contract declares 15 operational tables; every table is connected; generated bootstrap is current; older `fa8fd58` receipt reports 15 tables created | Current workflow 19 and policy seeds changed after that receipt; logical keys are not database uniqueness proof; current seed/readback, concurrency, retention approval, and restart/restore evidence are absent |
| Microsoft OAuth | **Implemented-unverified (connection scope)** | Current n8n operator readback during this audit reports Outlook credential `NcQo00WO7GQ3qYyA` connected with delegated `Mail.Read` and Drive credential `eSnL069pIlzjFj4B` connected with delegated `Files.ReadWrite` | The live project still has a stale 19-workflow set. Exact-current import, durable binding/readback, token refresh, canonical Finance Evidence root readback, and real workflow execution receipts are still pending |
| Subscription community agents | **Implemented-unverified** | Integrity-pinned ProDex 0.5.1 and Claude 0.8.0 register in an older local image; bounded Codex runner suite passes 16/16 | No current exact-image device/subscription login, no Claude no-session proof, no real workflow-09/21 proposal, no malicious negative runtime matrix, and no three consecutive schema-valid receipts |
| Bounded MCP facade | **Implemented-unverified** | Static tests prove fixed operation codes, durable request receipts, and no caller-selected URL/path/credential/write flag | No MCP Server Trigger execution from a real client and no Cloudflare Service Auth route/negative test |
| Runtime images and task runners | **Implemented-unverified for current commit** | Source/integrity pins, immutable extension checks, runner closure, Cloudflared artifact contracts and local image scans exist; focused contract suite passes 64 tests with six Windows symlink skips | Latest local image/CLI receipt is commit `fa8fd58`; current head changes workflows 09/19/21 and AI schemas. Current image builds, scans, SBOMs, protocol smokes, and immutable digests are absent |
| Cloudflare/network/secrets | **Implemented-unverified** | Hardened designs and source-locked Cloudflared artifact exist | Platform status explicitly says compose, restore, routes and MCP facade are unvalidated; dedicated vault rotation/injection, provider egress, Access/Service Auth, origin headers, and two-replica route proof are absent |
| CI and deployment | **Implemented-unverified** | Three workflow definitions cover Python/Actual, cashback profiles/image, finance nodes, PDF utility, bounded runner, task runners and Cloudflared; local suites are green | Branch is two commits ahead of origin; GitHub token is invalid and Actions returned 404, so current remote checks are unknown. No current deployment digest/readback or restore drill exists |

## Detailed evidence and findings

### 1. Actual ingestion, semantics, notes and refunds

The current implementation correctly separates source direction/topic from
category. Amounts are immutable after adapter normalization; manual fields and
protected AI fields are locked; transfers, rewards, refunds, reversals, fees and
interest have explicit regressions. The corpus planner is dry-run-only and has
no Actual connection or apply mode.

The 2026-08-19 corpus evidence is strong for the proposed state:

- 45 manifests and 3,927 unique stable identities;
- 3,927 amount/sign comparisons with zero mutations;
- 3,927 canonical note checks with zero violations and no routine source,
  message, original-currency, FX, technical, or derived cashback clutter;
- 3,400 purchases, 47 refunds, 5 reversals, 115 explicit rewards, 120
  transfers, 207 fees, 3 incomes, and 30 unresolved deposit credits;
- the EI 355-fils and 257-fils Amazon rows are refunds;
- plain `Amazon credit` text is not enough to establish a reward;
- the snapshot plan is `DRY_RUN_ONLY`, has 400 changes, preserves 28 manual
  categories, and reports 15 corpus identities absent from the snapshot.

This is not production verification. The retained production full-ingestion
audit was generated on 2026-08-18 before the note-v2/corpus commit. Its clean
3,912-row readback proves the older import, not the 400 proposed current
changes. Current UI samples and API parity remain absent.

The active statement registry covers ADCB, EI, and Wio. RAK and SC are explicit
placeholders, so their monthly n8n workflows cannot close periods. This is a
fail-closed implementation, but the missing adapters are production blockers.

### 2. Cashback live pipeline, configuration, reusability and push

The companion correctly owns notification cursor/routing state while Actual
owns posted transactions. Tests prove provisional notifications affect buckets
immediately, cursor heartbeats are independent of event count, finalization
requires statement evidence plus Actual verification, refunds/reversals reduce
spend, and corrections cannot alter protected financial facts.

Only the RAK notification source is active. SC has no sender, subject, fixture,
or parser. Historical RAK receipts are useful operational evidence, but they
predate the n8n design and cannot prove the current n8n acquisition path.

The profile abstraction is genuinely portable at code level: four non-AED
examples exercise flat, tiered, rotating, and requirements-driven portfolios.
The deployment workflow defines a container matrix for them. Live programme
values in `config/cashback-programs.json` remain explicitly unverified POC seed
data under `AGENTS.md`, so they cannot be promoted merely because routing tests
pass.

Push has durable/idempotent server primitives and UI assets, but no retained
real-device acceptance. A production gate must include iPhone PWA installation,
notification permission, VAPID delivery in foreground/background/locked state,
dedupe, acknowledgement, stale-feed episodes, weekly pace, routing change,
period history, and close/reset behavior.

### 3. FAB, Sarwa, ADCB and wealth

FAB has the best source evidence: the 2026-08-19 portal capture asserts a
complete six-item non-credit inventory and exact signed balances. A production
receipt claims guarded creation/reconciliation and API readback, but its source
commit is `76cab8d` and UI authentication was pending. A receipt test proves the
file is well-formed and hash-bound; it does not independently prove current
external state.

Sarwa has a fresh 2026-08-18 visible capture with USD 1,571,611.22 overall as of
2026-08-17, four active asset accounts/products, and one closed account. It
retains no browser session. However, there is no FX snapshot anywhere under
`runtime` or `config`, no current Actual projection/readback, and no required
wealth acceptance bundle. The read-only verifier therefore correctly returns
`BLOCKED / EVIDENCE_BUNDLE_UNAVAILABLE`.

ADCB is contradictory and must be treated as a financial incident/gap:

1. `config/evidence/adcb-closed-card-status-2026-08-19.json` records a tied
   issuer closing balance of -23,832 fils and status
   `BLOCKED_NONZERO_ISSUER_STATEMENT`.
2. `config/proposals/actual-accounts-fab-sarwa.json` forbids writes and lists
   `ISSUER_CLOSING_BALANCE_NOT_ZERO`.
3. `config/evidence/production-account-reconciliation-receipt-2026-08-19.json`
   nevertheless says three reconciliation adjustments were applied, ADCB was
   set to zero and closed, and only API readback passed.

Do not infer that the account should be reopened or altered. First obtain a
fresh read-only Actual API/UI snapshot and later issuer evidence. Then either
prove a real closing payment/zero statement or document the correct retained
non-zero closed/historical treatment. No synthetic balance row may substitute
for issuer evidence.

### 4. Schedules and evidence downloads

The Codex schedule manifest matches the required timezone/cycles: RAK and SC
close on day 5/reconcile day 6, EI closes month-end/reconciles day 1, and Wio
runs day 3. Four legacy tasks are active and two are paused. n8n workflows are
all inactive and have not cut over, so disabling the legacy tasks now would
create a scheduling gap.

The evidence archive is real and hash-consistent: all 67 catalogue paths exist
and match their stored SHA-256. It includes statements, bills, receipts,
bookings, policies, orders and claim documents, with 20 transaction-linked
entries. Coverage and path hygiene remain incomplete:

- 40 catalogue entries/files use `Finance Evidence/Finance Evidence/...`;
- no entry has `warranty_expiry`;
- no entry carries property/unit metadata and only three carry a category;
- n8n OAuth/download/extraction/readback has not executed on current bytes.

The duplicated root is not a missing-file problem, but it violates the required
shallow `Finance Evidence/YYYY/MM/vendor-slug/` contract and should be migrated
with hash-preserving catalogue updates before promotion.

### 5. n8n workflows, Data Tables, OAuth and community agents

Static state is coherent and conservative:

- 19 workflows, all inactive and tagged setup-required;
- 15 Data Tables with explicit retention/idempotency/concurrency contracts;
- all declared tables are referenced by connected executable nodes;
- all 17 Execute Sub-workflow nodes use n8n `From list` mode, stable workflow
  IDs, and readable cached names;
- one fenced Actual writer path and explicit PREPARED → ACTUAL_OBSERVED →
  VERIFIED → COMMITTED transitions;
- bounded proposal-only AI and MCP inputs;
- no Execute Command, SSH bridge, arbitrary path, or caller-selected write flag.

The current registry intentionally records zero import, fixture, disposable and
production validations. A retained local receipt at `fa8fd58` proves an exact
n8n 2.36.2 image registered eight extension nodes/three credentials, imported
19 inactive workflows, and created 15 tables with no production mounts or
writes. It is not current-byte proof: after `fa8fd58`, workflows 09, 19 and 21,
the AI proposal schema, policy seed, platform bootstrap manifest and fixtures
changed.

Microsoft consent has progressed beyond the checked-in checklist. During this
audit the current n8n operator reported Outlook credential `NcQo00WO7GQ3qYyA`
as `Account connected` with delegated `Mail.Read`, and Drive credential
`eSnL069pIlzjFj4B` as `Account connected` with delegated `Files.ReadWrite`.
That is useful current external-state evidence but not yet a durable runtime
receipt. The older disposable receipt explicitly had no credentials mounted,
the repository checklist remains unchecked, and the live project still has a
stale 19-workflow set. Production still requires exact-current workflow import,
credential-ID binding/readback, token refresh, canonical Finance Evidence root
readback, and real Outlook/OneDrive workflow execution. Root creation is the
only planned Drive mutation if the canonical root is absent; no delete or
broad/send permission is authorized.

Community packages are pinned and registered in the older image, and the
separate bounded Codex runner now passes its 16 tests. Neither provider is
runtime accepted: there is no ProDex device-login readback, no Claude
subscription/no-session proof, no current workflow 09→21 execution, and no
three consecutive schema-valid proposal receipts. The MCP facade likewise has
only static proof, not a real client or Service Auth route test.

### 6. Runtime, Cloudflare, tests, deployment and CI

Local verification performed in this audit:

| Command | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | **PASS** — 378 tests, 6 skipped |
| `npm test` in `integrations/actual` | **PASS** — 48 tests |
| `npm test` in `packages/n8n-nodes-finance` | **PASS** — 21 tests plus build |
| `npm test` in `services/codex-agent-runner` | **PASS** — 16 tests |
| Focused n8n/image/runner/Cloudflared/deployment Python suite | **PASS** — 64 tests, 6 Windows symlink skips |
| `python scripts/verify-project-acceptance.py` | **PASS only for ledger schema validity** — 26 requirements; it does not execute their verifiers |
| `python scripts/verify-wealth-acceptance.py --bundle runtime/audit/wealth-acceptance-evidence.json` | **BLOCKED** — evidence bundle unavailable; no writes allowed |
| PDF utility standalone tests | **NOT RUNNABLE LOCALLY** — `pikepdf` absent in this Windows environment; Linux/container CI remains required |

The repository defines strong CI jobs for tests, immutable images, Trivy,
SBOMs, protocol smokes and digest receipts. Current CI state is unknown: the
branch is two commits ahead of origin, the configured GitHub token is invalid,
and a read-only Actions query returned 404. A workflow definition is not a
successful run.

Cloudflare and production runtime remain unverified. The checked-in platform
status explicitly says compose, disposable restore, Cloudflare routes and MCP
facade are false/unvalidated. There is no current receipt for dedicated vault
injection/rotation, network isolation and provider-only egress, independent
Actual/cashback/n8n restarts, backup restoration, Access/Service Auth, or both
hostname routes on the existing two-replica tunnel.

## Superseded or historical evidence

These artifacts remain useful history but cannot prove the current end state:

| Artifact/path | Why superseded |
| --- | --- |
| `runtime/audit/production-post-replay-audit.json` | Clean 2026-08-18 production replay predates note-v2, corpus semantics, account scope, and current n8n workflows |
| `runtime/n8n-orchestrator-repo/docs/receipts/disposable-n8n-fa8fd58-20260819/*` | Proves local image/import/bootstrap for `fa8fd58`; current head changed three workflows and their AI/bootstrap contracts |
| `config/evidence/production-account-reconciliation-receipt-2026-08-19.json` as ADCB acceptance | Contradicted by current non-zero issuer evidence and blocked proposal; UI readback pending |
| Legacy HTTP/SSH/finance-worker compatibility concepts | Correctly absent under the greenfield n8n architecture; do not restore them as shortcuts |
| Legacy Codex schedules as target architecture | Still required operationally until n8n cutover, but superseded after—and only after—current n8n schedules have durable runtime receipts |

## Required promotion sequence

### P0 — correctness and current-state proof

1. Freeze one exact clean commit and bind every receipt to it. Push it and
   require current CI, scans, SBOMs and immutable image digests to pass.
2. Read back current Actual API and authenticated UI before any mutation.
   Resolve the ADCB zero/closed contradiction and preserve history.
3. Review the 877 corpus deltas, 28 manual-category conflicts, and 15
   cross-source-suppressed identities. Replay the exact 3,927-row corpus twice
   in disposable Actual with zero second-run delta and exact manual-state diff.
4. Create the wealth acceptance bundle: current FAB inventory, Sarwa T1/T2,
   contemporaneous FX, archive hashes, Actual API/UI, and exact signed net-worth
   equation. The read-only verifier must pass every required wealth gate.
5. Capture and implement real RAK and SC statement adapters. Keep their close
   and finalization paths blocked until statement evidence and reconciliation
   succeed.

### P0 — orchestration and security

6. Build the exact current custom n8n/PDF/runner/Cloudflared images; scan,
   generate SBOMs, publish immutable digests, and generate a current lock.
7. Complete least-privilege Outlook/OneDrive and statement/Actual credentials
   in disposable with the user present. Verify scopes without exposing secrets.
8. Import all 19 workflows inactive, place folders, bootstrap all 15 current
   tables/seeds, and read them back exactly. Execute the resilience/security
   fixture matrix, including zero/101-page/late-order/pagination failure,
   duplicate delivery, writer concurrency, kill-after-Actual, restart, backup,
   and networkless restore.
9. Prove Codex and Claude subscription paths through workflows 09/21 with fixed
   provider/model policy, no API-key/tool/write/session escape, malicious input
   negatives, and three consecutive schema-valid receipts per enabled path.
10. Prove the bounded MCP facade from a real client behind Cloudflare Service
    Auth, with rejected arbitrary operations and no instance-wide MCP exposure.

### P1 — usability and operations

11. Repair the duplicated evidence root and fill required property/category/
    warranty/link metadata without changing file hashes. Exercise plain,
    encrypted, large, HTML-body and non-PDF evidence flows end to end.
12. Run real-device PWA/push acceptance and a deployed portable profile matrix.
    Verify programme seed assumptions with current issuer terms before relying
    on live routing.
13. Populate budgets, schedules, owners and reports only after account and
    transaction parity. Obtain explicit user approval for budget values.
14. Validate the existing Cloudflare tunnel, origin headers, Access policies,
    two replicas, independent service restarts, backup/restore and monitoring.
15. Cut over n8n schedules atomically, then disable legacy Codex schedules and
    verify durable receipts plus lifecycle cleanup without losing a run.

## Final acceptance rule

Production promotion remains prohibited until every P0 item is bound to the
same exact commit/image set and has current disposable plus required production
readback evidence. Passing unit tests, retaining an older receipt, or failing to
find another mismatch is not sufficient proof.
