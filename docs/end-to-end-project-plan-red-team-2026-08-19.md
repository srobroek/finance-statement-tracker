# Adversarial review: end-to-end finance platform plan

Date: 2026-08-19  
Decision: **NO-GO**  
Reviewed plan SHA-256: `AC066C2B2F61FFC4AAD25BBB5F990E1438294D7137252FA065BEE183E7C33533`

## Decision

The target architecture is directionally sound—Actual as the posted ledger,
the cashback companion for live routing, n8n for orchestration, Postgres for
n8n state, and OneDrive for source evidence—but the current plan cannot safely
authorize implementation or production writes. It contains a legacy/greenfield
contradiction, assumes safety properties that Postgres and an in-process mutex
do not provide, and treats workflow sketches and declarative documentation as
runtime evidence.

The project may proceed with repository-only implementation after the plan is
amended. It must not deploy n8n, expose MCP, retire Codex schedules, or mutate
production finance data until findings 1–11 below are closed. Findings 12–15
must be closed before end-to-end completion is claimed.

## Evidence inspected

- `docs/end-to-end-project-plan-2026-08-19.md`
- `config/project-acceptance.json`
- `AGENTS.md`
- `integrations/n8n/pipeline-registry.json`
- all 15 exports under `integrations/n8n/workflows/`
- `integrations/n8n/data-tables.json` and `tests/test_n8n_workflows.py`
- the local checkout of the separate orchestrator repository under
  `runtime/n8n-orchestrator-repo/`
- current source, automation, account, wealth, document, and deployment
  configuration
- existing runtime audit/restage artifacts named by the acceptance ledger
- current official n8n, Actual, Cloudflare, and 1Password documentation linked
  in the findings below

## Highest-impact findings and mandatory amendments

### 1. P0 — The plan is not greenfield

**Finding.** The plan retains `finance-actual-ingestion` as an active fallback
until Phase 7 and makes absence of port 5020 a late cutover gate. `AGENTS.md`
still sends browser artifacts through a “standard Actual bridge,” and other
repository documentation/comments still describe bridge behavior. This directly
contradicts the user's instruction to drop the ingestion bridge and work as a
greenfield environment.

**Required amendment.** Define “greenfield” precisely: no bridge code, image,
container, port, credential, workflow dependency, compatibility layer, or
migration of bridge state. Capture a read-only forensic baseline and source
artifacts, then stop/remove the legacy runtime in Phase 0. Retain only immutable
audit/source evidence. If this creates an ingestion gap, declare a maintenance
window; do not preserve the bridge as a rollback path. Replace every bridge
reference in `AGENTS.md`, docs, tests, and comments with the direct fixed-purpose
n8n Actual node/subworkflow.

**Acceptance evidence.** A repository-wide zero-match search for bridge/service/
port-5020 references except an explicitly labelled historical decision record;
host container/network/port/credential inventory proving absence; and an n8n
dependency graph with no bridge endpoint.

### 2. P0 — The n8n specification is internally inconsistent and not executable

**Finding.** The plan says 14 exports, but 15 exist. The registry still says
`regular-sqlite-wal-external-runners`, and `data-tables.json` still says
`n8n-data-tables-on-pooled-wal-sqlite`, while the target is Postgres. Phase 2
proposes `financeStatement`, `financeRules`, and `financeActual`; exports instead
reference the nonexistent `financeTransform` and `actualBudget` types. The stock
n8n image contains none of the three referenced custom types. Current tests only
parse JSON and inspect strings; they do not import or execute the exports.

**Required amendment.** Add a Phase 0 contract freeze: one canonical node
package/name/version, JSON Schemas for every inter-node payload, a registry status
of `SPEC_ONLY` until real import succeeds, a Postgres-correct state declaration,
and a stable subworkflow-ID resolution strategy. Add a Dockerfile/lockfile and
CI that builds the exact custom image before platform deployment.

**Acceptance evidence.** Pinned-container `n8n import:workflow` of all exports,
node-type resolution with no unknown nodes, credential/Data Table binding in a
disposable instance, publish validation, and one real fixture execution per
non-placeholder workflow.

### 3. P0 — Current mail flows can silently lose data or skip state advancement

**Finding.** A zero-message subworkflow yields zero items, so downstream heartbeat,
cursor, and “statement missing” logic may never execute even though n8n marks the
path successful. Monthly workflows use a single seven-day window and no durable
statement cursor/catch-up loop. Attachment acquisition is limited to 100 messages,
does not deterministically select the latest eligible statement, and has no
explicit pagination exhaustion proof. The archive path expression can lose
`onedrive_parent_id` between Outlook/attachment nodes. A run delayed over seven
days can miss a statement permanently.

**Required amendment.** Make empty scans first-class results; freeze
`run_upper_bound`; enumerate every page; prove complete enumeration; aggregate to
exactly one result item; commit the cursor only after archive, downstream durable
write, and readback; and write an empty heartbeat. Monthly schedules must poll a
bounded cycle window until the expected statement arrives or a deadline alert is
raised. Resolve source contract and archive destination from trusted configuration,
not transient item fields.

**Acceptance evidence.** Automated cases for zero messages, 101+ messages,
pagination failure, delayed run beyond seven days, out-of-order arrivals, duplicate
message/attachment/hash, multiple statement candidates, no attachment, late
statement, and failure immediately before/after cursor commit. Each case must
prove exact scanned counts and no skipped window.

### 4. P0 — “Durable receipts” are not durable and error handling is detached

**Finding.** `Immutable Acquisition Receipt`, `Daily Scan Receipt`, `Durable
Pipeline Receipt`, `Typed Extraction Receipt`, and wealth staging are Set nodes;
they persist nothing. Only the error flow contains a Data Table upsert, but no
workflow has `settings.errorWorkflow` configured. The three declared Data Tables
do not include source cursors, acquisition receipts, archive identities, import
outbox entries, reconciliation receipts, or workflow configuration versions.

**Required amendment.** Define and bootstrap versioned Postgres-backed tables for
source cursors, source/archive receipts, document state, pipeline runs, Actual
outbox/import verification, reconciliations, config versions, circuit breakers,
and redacted failures. Attach the error workflow to every production workflow.
Every terminal success/failure must perform and verify a real upsert; Set nodes
may only shape the returned response.

**Acceptance evidence.** Schema migration/drift test, database uniqueness and
upsert tests, restart recovery from each nonterminal state, error-workflow receipt
for every workflow, and direct Postgres/Data Table readback after successful and
failed executions.

### 5. P0 — Postgres does not make Actual writes concurrency-safe

**Finding.** The compose file allows four production executions. A mutex in one
custom-node process does not cover manual executions, process restarts, future
replicas, or another Actual API client. Postgres cannot atomically commit a local
Actual client write, a cashback close, a cursor, and an n8n receipt. Actual's API
works on a locally cached budget copy, so cache lifecycle and synchronization are
part of correctness, not an implementation detail. Official n8n documentation
also states regular-mode concurrency is unlimited unless explicitly configured.

**Required amendment.** Start with global production concurrency `1`; reject
production mutation from manual/MCP execution; route every Actual write through
one writer subworkflow; and implement a durable outbox state machine such as
`PREPARED → ACTUAL_OBSERVED → VERIFIED → COMMITTED`. Use a process-independent
lease/fencing mechanism, deterministic `imported_id`, pre/post sync, `shutdown()`
in `finally`, and recovery that observes an already-written Actual ID rather than
writing again. Treat cashback close as a separate idempotent step after verified
Actual readback.

**Acceptance evidence.** Concurrent schedule/manual attempts, kill/restart at
every state boundary, stale-cache and simultaneous-browser sync tests, duplicate
replay, and readback proving one Actual row and one cursor advancement. Reference:
[n8n concurrency control](https://docs.n8n.io/hosting/scaling/concurrency-control/)
and [Actual API local-cache model](https://actualbudget.org/docs/api/).

### 6. P0 — The plan can apply classification rules twice

**Finding.** The shared workflow applies `financeRules` before the Actual writer,
while Actual `importTransactions` runs Actual rules and reconciliation again. The
plan also intends to compile representable rules into Actual. Without an explicit
ownership boundary, normalization, category, tags, transfers, or notes can be
changed twice after source semantics were supposedly locked. Actual's API defaults
`reimportDeleted` to `true` unless explicitly overridden.

**Required amendment.** Choose one evaluation path per rule. Representable
AutoCat-style pre/default/post rules should execute in Actual and be parity-tested;
only non-representable deterministic rules execute before import. No overlapping
action may exist in both engines. Define rule traces, stage ordering, regex/one-of
semantics, append/prepend behavior, locked fields, manual overrides, AI proposal
boundaries, category-creation recommendations, and disabling of unwanted Actual
learning. Require `reimportDeleted:false`, deterministic imported IDs, returned
error inspection, and post-import invariant checks.

**Acceptance evidence.** Canonical-rule-to-Actual compiler parity fixtures,
double-run tests, known refund/reward/transfer fixtures, pre/default/post trace,
zero locked-field mutations, and a production dry-run/readback diff. Reference:
[Actual transaction import behavior](https://actualbudget.org/docs/api/reference/).

### 7. P0 — Current 1Password/env design persists secrets and conflicts with n8n security defaults

**Finding.** `render-env.sh` resolves 1Password references into a persistent
`.env`, and sources a host `.env.bootstrap` as shell code. Docker environment
values can be inspected on the host. Meanwhile workflows rely on `$env`, but n8n
2.x blocks node access to environment variables by default; disabling that guard
would expose the n8n encryption key and Postgres password to workflow expressions.
The service-account bootstrap, vault scope, rotation, file ownership, and restore
key procedure are not defined.

**Required amendment.** Use a dedicated `FinanceAutomation` vault and least-
privilege service account. Batch-fetch once per deployment, but inject via
`op run` and/or mounted `/run/secrets`/supported `_FILE` variables; do not render
resolved secrets to a durable `.env`. Keep statement passwords and service tokens
in n8n encrypted credentials, not workflow-readable environment. Keep
`N8N_BLOCK_ENV_ACCESS_IN_NODE=true` and move nonsecret source configuration into
versioned/Data Table state. Document the root bootstrap credential, permissions,
rotation, and recovery of the stable `N8N_ENCRYPTION_KEY` separately from DB
backups.

**Acceptance evidence.** Secret scan of Git, compose render, container inspect,
execution payloads, logs, and backups; successful 1Password noninteractive cold
start; credential decrypt after disposable restore; rotation drill; and proof no
workflow can read an unrelated secret. Reference:
[1Password runtime injection](https://developer.1password.com/docs/cli/secrets-scripts)
and [n8n encryption-key requirements](https://docs.n8n.io/hosting/securing/encryption-key-rotation/).

### 8. P0 — Proposed instance-level MCP exposure is both ineligible and overpowered

**Finding.** Three of the four allowlisted exports have only Manual/Execute
Workflow triggers. n8n only allows **published workflows with webhook, form,
schedule, or chat triggers** to be exposed through instance MCP, so they are
ineligible. Instance MCP is beta and also exposes workflow/data-table management
capabilities; `search_workflows` can preview every workflow visible to the n8n
user, and all clients share that user's enabled workflow surface. The acquisition
workflow is not bounded: its caller supplies `folder_id`, sender, subject, time
window, and effectively the scan contract.

**Required amendment.** Do not enable instance MCP by default. Prefer a dedicated
MCP Server Trigger/facade exposing only fixed operation codes resolved against
trusted server-side source contracts. If instance MCP is retained, prove the
edition supports a dedicated least-privilege n8n user/token, remove build/edit/
Data Table scopes, add eligible triggers, and negative-test every tool. No model
may supply mailbox folders, arbitrary senders/subjects, OneDrive destinations,
URLs, credentials, Actual IDs, or commit flags.

**Acceptance evidence.** Tool enumeration from the real client, least-privilege
scope dump, successful bounded call, rejected arbitrary folder/URL/write calls,
and access-log attribution/revocation. Reference:
[n8n instance MCP eligibility and scope](https://docs.n8n.io/connect/connect-to-n8n-mcp-server/).

### 9. P0 — Cloudflare publication is assumed rather than designed

**Finding.** `http://127.0.0.1:5678` is valid only when `cloudflared` runs in the
host network namespace; if the tunnel is containerized, loopback points to the
tunnel container. The plan does not prove tunnel topology, forwarded headers,
cache bypass, WebSockets, Microsoft OAuth callbacks, Access policy precedence,
or how a client supplies both Cloudflare service-token headers and n8n's bearer
token. UI AD, MCP, OAuth callbacks, and externally invoked webhooks have different
authentication needs and should not inherit one ambiguous path policy.

**Required amendment.** Add an explicit route matrix. Recommended: interactive
`n8n.vxsan.com` behind AD; a separate `n8n-mcp.vxsan.com` behind Service Auth plus
n8n authentication; and no public webhook hostname until a source-specific
authenticated webhook exists. Record where `cloudflared` runs and use host
loopback or Docker DNS accordingly. Configure and verify `WEBHOOK_URL`,
`N8N_EDITOR_BASE_URL`, `N8N_PROXY_HOPS`, forwarded headers, no-cache rules,
WebSockets, callback URLs, Access policy order, token rotation, and origin firewall.

**Acceptance evidence.** Cloudflare route/policy export with secrets redacted;
host/container topology; browser AD login; WebSocket workflow updates; OAuth
credential callback; MCP request carrying both auth layers; unauthenticated denial;
and origin unreachable from LAN. References:
[Tunnel routing](https://developers.cloudflare.com/tunnel/routing/) and
[Access service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/).

### 10. P0 — Sensitive PDFs/text can persist in failed n8n executions

**Finding.** The plan promises ephemeral decrypted data, but every export saves
error execution data as `all`, and the compose file globally saves all failed
execution data. A failure after decryption/extraction can therefore persist PDF
binary or statement text in Postgres/binary storage. Success pruning after seven
days does not satisfy “never persist.”

**Required amendment.** Set failed execution payload retention to `none` for
document/statement workflows and persist only a separately redacted failure
receipt. Explicitly drop decrypted binary/text before any branch that may wait or
fail. Prohibit pinning sensitive execution data and manual saved executions in
production. Define binary-volume cleanup and a forensic policy that never copies
raw decrypted content.

**Acceptance evidence.** Induced failure after decryption followed by direct
inspection of Postgres, execution API/UI, binary volume, logs, and backups proving
absence of decrypted bytes/text/password; only hashes and redacted error class may
remain.

### 11. P0 — Untrusted PDF parsing shares the n8n credential boundary

**Finding.** Running QPDF and parsers inside the credential-bearing n8n container
means a malicious/corrupt statement can crash or exploit the orchestration process.
External task runners isolate Code nodes, not arbitrary custom-node subprocesses.
The plan lists size/page/time limits but no OS-level CPU/memory/PID/network/filesystem
sandbox or parser supply-chain gate.

**Required amendment.** Execute QPDF/parsing in an unprivileged, read-only,
networkless sandbox or narrowly scoped document-utility sidecar with tmpfs,
seccomp/AppArmor, CPU/memory/PID/time limits, fixed arguments, and no n8n/Actual/
OneDrive credentials. This is a document utility, not a finance worker. Pin QPDF
and parser images/packages by digest and generate an SBOM/security scan.

**Acceptance evidence.** Encrypted/plain/corrupt/polyglot/oversize/decompression-
bomb fixtures, timeout/OOM isolation, network-denial test, fixed-command negative
tests, image digest/SBOM/CVE gate, and proof a parser crash does not restart n8n or
expose credentials.

### 12. P1 — The acceptance ledger cannot prove completion

**Finding.** `actual-authoritative-ledger` is `VERIFIED` using architecture docs
and configuration, not runtime ledger enumeration. One evidence path,
`config/evidence-policies.json`, does not exist. The cited full-restage summary is
`PLANNED`, not executed. Evidence entries lack environment identity, Actual sync
ID, commit SHA, verifier command, result hash, timestamp/expiry, or independent
review. Broad phrases such as “healthy,” “all dashboards,” and “three successful
runs” are not machine-verifiable and can pass on empty/no-op runs.

**Required amendment.** Replace status prose with a machine-readable traceability
ledger containing requirement, authoritative verifier, exact command/test, expected
invariants, environment/sync identity, artifact URI+SHA256, observed timestamp,
expiry, reviewer, and dependencies. Downgrade every unsupported `VERIFIED` row.
Add missing gates for secrets, Cloudflare/MCP, cursor completeness, Actual writer
recovery, OneDrive retention, custom image supply chain, cashback reuse/mobile/
push, investment feeds, and net-worth sign/parity.

**Acceptance evidence.** A schema-validated ledger with no missing files, no
expired evidence, no document-only proof for runtime claims, and a generated
requirement-by-requirement report whose verifier fails on empty/no-op executions.

### 13. P1 — Production mutation is sequenced before disposable proof

**Finding.** Phases 4–6 describe creating accounts, correcting ADCB, cleaning the
corpus, and populating budgets/schedules/reports before Phase 7 performs the first
complete disposable replay and exact production-delta review. A general sentence
about root approval does not prevent an agent from interpreting those earlier
phases as authorization to write production. UI/API divergence and 42 unresolved
manual-field diffs increase the risk.

**Required amendment.** Split Phases 4–6 into **capture/plan only** and move every
Actual mutation to production promotion after: backup restore, custom-node parity,
double replay, manual-state fingerprint review, and exact delta approval. Resolve
UI/API file identity before any new account or transaction write. Never clear a
client cache until local-only state is exported and diffed.

**Acceptance evidence.** Write-disabled phase receipts; exact proposed account/
transaction/budget/schedule/report delta; disposable double replay; reviewed 42-
field conflict disposition; and a production write log mapping every changed ID to
an approved delta and post-write readback.

### 14. P1 — Cashback completion/reusability requirements are under-specified

**Finding.** The plan still calls live events “provisional,” despite the product
decision that valid notifications count immediately and require no approval; only
final cashback reconciliation waits for a statement. It does not explicitly gate
the config-driven public card/bucket/tier/rule/decision-tree model, fictional-card
scenario tests, mobile-first viewport, period history selector, weekly pace logic,
PWA installation, or native declarative web-push behavior. Postgres cursor ownership
also conflicts with `AGENTS.md`, which assigns live cashback cursors to the companion.

**Required amendment.** State the UX semantics precisely: accepted live events
affect routing immediately; reconciliation state is internal; no provisional/
approval UI exists; statement evidence alone finalizes/resets a period. Choose one
authoritative cursor per source. Add a public, versioned schema for arbitrary cards,
tiers, caps, buckets, cycle dates, categories, routing priorities, alerts, and issuer
adapters—no user-specific executable constants. Include EI unlimited/stale behavior,
RAK/SC cycles, weekly expected-vs-actual pace, routing-change/bucket-full/close-window
push, history selection, and mobile layouts.

**Acceptance evidence.** Schema/secret scan; at least three materially different
fictional portfolios; exhaustive routing oracle tests including over-cap and tier-
chasing cases; iPhone 13 Pro Max viewport screenshots with no clipping; PWA install;
foreground/background push matrix through Cloudflare; and statement-close/reset
replay with prior-period history preserved.

### 15. P1 — Wealth/net-worth and the remaining finance feature inventory are incomplete

**Finding.** The plan provides a one-time Sarwa capture but not the requested richer
investment value feeds, valuation history, price/FX provenance, stale behavior, or
refresh cadence. It does not make the reported negative all-accounts value an
explicit acceptance target. Broad Phase 6 wording also fails to enumerate required
AutoCat/AI review behavior, owners, rental tags, evidence links/search, schedules,
modern budgets/cleanup, mortgage IPMT/PPMT, tag reporting, subscriptions/bills,
savings, property, retirement/SWR, and Sankey/trend reports.

**Required amendment.** Add an exact feature inventory and separate acceptance IDs.
For current account scope, use only authenticated FAB non-credit accounts and Sarwa
portfolios—never the workbook. Define holdings snapshot, immutable provider capture,
price/FX source+timestamp, valuation equation, stale threshold, interactive refresh,
historical series, and Actual off-budget representation without inventing cashflow.
Make `All Accounts`/net-worth parity and sign correctness explicit. Expand planning
acceptance into named budgets, schedules, owner/property tags, review queues, evidence
links, and saved reports with query equations.

**Acceptance evidence.** Fresh FAB inventory and maximum history; fresh Sarwa
holdings plus independently sourced FX/price snapshot; position+cash-to-provider and
provider-to-Actual equations; UI/API identical account set and signed balances;
ADCB closed at exact AED 0; stale/fresh valuation tests; and a report inventory whose
values reconcile to saved Actual queries.

## Required phase reorder

The implementation plan should be amended to use this order:

1. **Specification and safety freeze:** correct greenfield scope, acceptance
   schema, source contracts, state ownership, threat model, baseline, backups,
   and removal of the legacy runtime.
2. **Build artifacts:** TypeScript custom nodes, isolated PDF utility, schemas,
   fixtures, CI-built digest-pinned image, SBOM, and importable workflow exports.
3. **Disposable platform:** n8n + Postgres + runners + Data Tables + 1Password
   injection + Cloudflare route tests, with all workflows inactive/write-disabled.
4. **Executable workflow validation:** mail recovery, archive, document, rules,
   AI proposal, Actual outbox, cashback, errors, MCP facade, and failure-injection
   tests.
5. **Source capture and delta design only:** FAB, Sarwa, ADCB, corpus cleanup,
   budgets, schedules, owners, and reports; no production mutation.
6. **Disposable double replay:** full corpus twice, manual-state diff, balance/
   net-worth equations, dashboard reconciliation, restart/restore tests.
7. **Controlled production promotion:** one source/delta at a time, readback,
   real scheduled runs, then retire the matching Codex task.
8. **Observation and release:** real statement cycles, notifications, public
   fictional-profile clean install, operational docs, and final evidence audit.

## GO criteria

The plan becomes **GO for production implementation** only when:

- findings 1–11 have explicit amendments in the main plan;
- `config/project-acceptance.json` is replaced/upgraded with the verifiable
  evidence contract from finding 12;
- every workflow is labelled honestly (`SPEC_ONLY`, `TESTED_IN_DISPOSABLE`,
  `SHADOW`, or `PRODUCTION`) and no sketch is called implemented;
- all Phase 4–6 actions are explicitly write-disabled until disposable replay;
- the root agent records acceptance of this review or documents a reasoned,
  evidence-backed rejection for each finding.

Until then, repository-only work is permitted, but deployment, MCP exposure,
schedule cutover, and production finance mutation are **NO-GO**.
