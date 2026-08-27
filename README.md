# finance statement tracker

This project contains deterministic finance contracts and `n8n` workflows. The
system uses `Actual Budget` as its ledger. The cashback app owns live routing
state. `OneDrive` owns evidence. The parser and rule engine use a small pinned
Python set.

The cashback companion uses SQLite and sends iOS declarative web push messages
through `pywebpush`. Finance separates acquisition from ledger work. Evidence
has its own boundary. Notification state has its own boundary. Each boundary
has a receipt and an owner. The setup guides list the required checks.

Read these guides:

- [`docs/platform-evaluation.md`](docs/platform-evaluation.md)
- [`docs/actual-production.md`](docs/actual-production.md)
- [`docs/cashback-companion-decision.md`](docs/cashback-companion-decision.md)
- [`config/project-acceptance.json`](config/project-acceptance.json)

## included

- ordered static rules with OR groups and AND conditions
- multiple rule actions with stage-aware execution
- a versioned JSON rule model
- a separate AI policy contract
- cashback tier calculations
- cashback cap calculations
- cashback pace and routing calculations
- deterministic email and document matching
- portable `OneDrive` evidence paths
- month-close Markdown with a Mermaid category chart
- a bank adapter API for different statement layouts
- a local CLI for JSON transactions and rules
- account and owner attribution
- category and vendor attribution
- tag attribution
- savings reservation and safe-to-spend calculations
- recurring-subscription detection
- reports grouped by account or owner
- reports grouped by category or vendor
- reports grouped by tag or type
- unit tests for rules and refunds
- unit tests for uplift and routing
- unit tests for savings and reports
- a platform adapter
- an `Actual Budget` import serializer
- an idempotent ledger bootstrap for accounts and categories
- an idempotent ledger bootstrap for tags and payees
- an idempotent ledger bootstrap for rules
- two-phase statement ingestion with a durable run manifest
- read-only ledger snapshots for cashback pace and routing
- a compact live cashback companion in `apps/cashback-control`
- a mobile-first cashback interface for the iPhone 13 Pro Max viewport
- an installable iOS PWA with declarative notifications
- independent dashboard recalculation and stale-ingestion warnings
- Outlook notification adapters
- browser acquisition recipes for ADCB and Emirates Islamic
- browser recipes for FAB and Wio
- browser recipes for Sarwa
- a profile-driven cashback engine with card and currency data
- tier and cap data in the cashback profile
- alert data in the cashback profile

Rules use [`config/static-rule-schema-v1.json`](config/static-rule-schema-v1.json).
`finance_tracker` validates and evaluates each rule. Compatible rules compile into
`Actual Budget`. `rule_sets` provide searchable scopes. One scope is
`LIVE_CASHBACK`.

## bank adapter api

`finance_tracker.statements.BankStatementAdapter` is the extension boundary for
banks. Each adapter detects and parses one layout. It emits
`NormalizedStatement` and `NormalizedStatementTransaction` objects.

The POC includes `emirates_islamic_v1`. It also includes `adcb_v1` and
`wio_credit_v1`. New banks
register one adapter with `StatementAdapterRegistry`. RAKBANK and Standard
Chartered remain placeholders until fixtures pass parser and arithmetic tests.
Supply statement passwords through runtime secrets or an approved credential
store. Never commit them or write them to logs.

`finance_tracker.ingestion.stage_statement` creates a reviewable staging batch.
a statement can be `balance_tied` while `ledger_reconciled` remains false. The
matching workflow can change the latter.

## setup and operations

| surface | procedure owner |
|---|---|
| workflow boundary | [`integrations/n8n/README.md`](integrations/n8n/README.md) |
| credential setup | [`docs/n8n-credential-setup-checklist.md`](docs/n8n-credential-setup-checklist.md) |
| `OneDrive` setup | [`integrations/n8n/setup-workflows/README.md`](integrations/n8n/setup-workflows/README.md) |
| ledger operations | [`docs/actual-production.md`](docs/actual-production.md) |
| backup and restore | [`docs/backup-and-restore.md`](docs/backup-and-restore.md) |
| browser boundary | [`docs/browser-ingestion.md`](docs/browser-ingestion.md) |

### procedure scope

- `ProDex` subscription adapter: workflow boundary
- `ProDex` and Microsoft credential setup
- `OneDrive` root: `OneDrive` setup
- Microsoft OAuth proof: `OneDrive` setup
- ledger and Cashback Control: ledger operations
- rollback: backup and restore
- browser acquisition: separate boundary

## ownership

| owner | responsibility |
|---|---|
| `Actual Budget` | ledger and budgets |
| `n8n` | acquisition and orchestration |
| Cashback Control | routing and cashback state |
| `OneDrive` | evidence and catalog |

The browser never writes to the ledger or Cashback Control. `n8n` validates each
delta. The fenced writer sends each accepted delta to `Actual Budget`.

## boundary map

Each source keeps its original artifact. The staging layer records an immutable
source identity. `n8n` sends accepted deltas to the fenced ledger writer. The
writer records a receipt for each ledger readback. Cashback Control reads only
the fields allowed by its routing contract. `OneDrive` stores the evidence copy
and its checksum.

## data migration

The legacy input contract declares 15 `SPEC_ONLY` tables in
[`integrations/n8n/data-tables.json`](integrations/n8n/data-tables.json).

The migration target has four tables in
[`integrations/n8n/data-table-migration-matrix.json`](integrations/n8n/data-table-migration-matrix.json):

- `finance_ingestion_state`
- `finance_documents`
- `finance_actual_batches`
- `finance_ai_reviews`

The matrix check covers dispositions. It covers target names and bootstrap
exclusion. The matrix records 33 node references. It records 121
write-reference edges. A drifted matrix fails the check.

After source changes, regenerate the checked-in migration matrix from the repository:

```sh
python integrations/n8n/generate_data_table_migration_matrix.py --write
```

```sh
python -m unittest tests.test_data_table_migration_matrix -v
python integrations/n8n/generate_data_table_migration_matrix.py --check
```

`run-four-table-cutover.sh` supports both modes. `DISPOSABLE_ONLY` runs a disposable
check. `PRODUCTION_ONLY` runs production orchestration.

Production mode uses protected values. Provide the receipt inputs.

Name the forward or rollback acknowledgment. The script records a runtime journal.

Cutover status:

- Retained readback proves 19 inactive and unpublished finance workflows.
- Retained readback proves four target tables:
  - `finance_ingestion_state`
  - `finance_documents`
  - `finance_actual_batches`
  - `finance_ai_reviews`
- Production cutover has no receipt here.
- Pending acceptance includes:
  - provider authentication
  - production ingestion
  - replay
  - restart
  - ledger and Cashback readback
  - rollback
- See [`docs/actual-production.md`](docs/actual-production.md) for the ledger guide

Before a disposable forward run, create a receipt directory with mode `0700`.
Place these files in it:

- `finance-data-table-backup-v1.json`
- `data-table-migration-receipt.json`
- `finance-four-table-accepted-identity.json`

Set the required inputs. Keep the Compose project label separate from the
internal Data Table project ID:

```sh
export FINANCE_REPOSITORY_DIR="$PWD"
export FINANCE_N8N_RECEIPT_DIR='<absolute mode-0700 receipt directory>'
export FINANCE_N8N_COMPOSE_PROJECT='<disposable Compose project label>'
export N8N_FINANCE_PROJECT_ID='<internal Data Table project ID>'
export FINANCE_N8N_RUNTIME_MODE=DISPOSABLE_ONLY
```

Select one running `n8n` container by its Compose labels. Reject a wrong project,
service, or state. Save the identity receipt with mode `0600`:

```sh
mapfile -t n8n_candidates < <(
  docker ps \
    --filter "label=com.docker.compose.project=$FINANCE_N8N_COMPOSE_PROJECT" \
    --filter "label=com.docker.compose.service=n8n" \
    --filter status=running \
    --format '{{.ID}}'
)
test "${#n8n_candidates[@]}" -eq 1
FINANCE_N8N_CONTAINER="$(docker inspect -f '{{.Id}}' "${n8n_candidates[0]}")"
container_identity="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}' "$FINANCE_N8N_CONTAINER")"
test "$container_identity" = "$FINANCE_N8N_COMPOSE_PROJECT|n8n|true"
export FINANCE_N8N_CONTAINER
umask 077
docker inspect -f '{{.Id}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}' "$FINANCE_N8N_CONTAINER" > "$FINANCE_N8N_RECEIPT_DIR/finance-n8n-container-identity.txt"
```

The forward gate needs a named operator acknowledgment. The value appears below:

```sh
export FOUR_TABLE_FORWARD_ACK=FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE
integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh forward
```

`run-four-table-cutover.sh` checks source and migration digests. It reads the
four target tables in each phase. It executes workflow 19 twice. Confirm that
the second readback is a no-op.

The rollback gate needs a forward receipt and a named operator acknowledgment.
The value appears below:

```sh
export FOUR_TABLE_ROLLBACK_ACK=FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE
integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh rollback
```

### protected production runner

Use protected inputs for `PRODUCTION_ONLY`. Keep the receipt directory at mode
`0700`. Set the Compose project label separately from the internal Data Table
project ID:

```sh
export FINANCE_REPOSITORY_DIR="$PWD"
export FINANCE_N8N_RECEIPT_DIR='<absolute mode-0700 receipt directory>'
export FINANCE_N8N_COMPOSE_PROJECT='<production Compose project label>'
export N8N_FINANCE_PROJECT_ID='<internal Data Table project ID>'
export FINANCE_N8N_LIVE_EXPORT="$FINANCE_N8N_RECEIPT_DIR/finance-four-table-live-export.json"
export FINANCE_N8N_RUNTIME_MODE=PRODUCTION_ONLY
for file in finance-data-table-backup-v1.json data-table-migration-receipt.json finance-four-table-accepted-identity.json finance-four-table-live-export.json; do
  test "$(stat -c '%a' "$FINANCE_N8N_RECEIPT_DIR/$file")" = 600
done
```

Select one running `n8n` container by its Compose labels. Reject a wrong project,
service, or state. Save the identity receipt with mode `0600`:

```sh
mapfile -t n8n_candidates < <(
  docker ps \
    --filter "label=com.docker.compose.project=$FINANCE_N8N_COMPOSE_PROJECT" \
    --filter "label=com.docker.compose.service=n8n" \
    --filter status=running \
    --format '{{.ID}}'
)
test "${#n8n_candidates[@]}" -eq 1
FINANCE_N8N_CONTAINER="$(docker inspect -f '{{.Id}}' "${n8n_candidates[0]}")"
container_identity="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}' "$FINANCE_N8N_CONTAINER")"
test "$container_identity" = "$FINANCE_N8N_COMPOSE_PROJECT|n8n|true"
export FINANCE_N8N_CONTAINER
umask 077
docker inspect -f '{{.Id}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Running}}' "$FINANCE_N8N_CONTAINER" > "$FINANCE_N8N_RECEIPT_DIR/finance-n8n-container-identity.txt"
```

The selector binds one running `n8n` service to the production Compose project.
The script checks that the internal project ID matches and writes protected
receipts:

- project ID
- source digests
- target tables
- workflow graph

```sh
export FOUR_TABLE_FORWARD_ACK=FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE
integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh forward
forward_receipt="$FINANCE_N8N_RECEIPT_DIR/finance-four-table-runtime-forward.json"
test "$(stat -c '%a' "$forward_receipt")" = 600
jq -e '(.operation == "FORWARD") and (.project_id == env.N8N_FINANCE_PROJECT_ID) and (.action_count == 33) and (.replay_noop == true) and (.readback_verified == true) and (.durable_journal == true) and (.commit_protocol == "postgresql_synchronous_wal")' "$forward_receipt" >/dev/null
```

Runtime output failure starts journal recovery. The recovery writes
`finance-four-table-runtime-forward-recovered.json` and updates
`finance-four-table-runtime-forward.json`.

```sh
test -s "$FINANCE_N8N_RECEIPT_DIR/finance-four-table-runtime-forward-recovered.json"
test -s "$FINANCE_N8N_RECEIPT_DIR/finance-four-table-runtime-forward.json"
```

After both files pass, use the recovered forward receipt for rollback. The rollback
reads the same project ID and export. It reads the protected files:

```sh
export FOUR_TABLE_ROLLBACK_ACK=FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE
integrations/n8n/setup-workflows/runner/run-four-table-cutover.sh rollback
rollback_receipt="$FINANCE_N8N_RECEIPT_DIR/finance-four-table-runtime-rollback.json"
test "$(stat -c '%a' "$rollback_receipt")" = 600
jq -e '(.operation == "ROLLBACK") and (.project_id == env.N8N_FINANCE_PROJECT_ID) and (.action_count == 33) and (.readback_verified == true) and (.durable_journal == true) and (.commit_protocol == "postgresql_synchronous_wal")' "$rollback_receipt" >/dev/null
```

### runtime acceptance boundary

The retained readback proves the 19-workflow and four-table topology. It does not
prove runtime acceptance.

`n8n` export status:

- inactive
- `SPEC_ONLY`
- the acceptance ledger is authoritative

Static exports and local tests do not prove provider authentication, live table
state, ledger or Cashback readback, Cloudflare routes, production identities,
or rollback. The machine-readable
[`config/project-acceptance.json`](config/project-acceptance.json) ledger is the
sole source for current acceptance status.

For semantic acceptance, complete one real production ingestion. Replay the
identical source. Restart `n8n` under control. Check for a no-op replay with matching
ledger readback and cursor state. Cloudflare publication is an optional boundary,
not a semantic acceptance gate. An `Actual` reset is fallback-only and does not
replace the ingestion, replay, or restart evidence.

### platform-owned procedures

The finance checkout does not own the `n8n` platform scripts. The current
platform repository is [`srobroek/n8n-orchestrator`](https://github.com/srobroek/n8n-orchestrator).
Dockge runs its `n8n` stack from `/opt/stacks/finance-n8n`, with the `n8n` service
as the workflow runtime.

The retained ProDex auth state is the protected host file
`/home/ci/.codex-n8n-community/auth.json`. The Dockge `n8n` service mounts the
host directory at `/home/node/.n8n/codex`. Direct n8n workflow W21 (`Subscription
Agent Adapter`) consumes the mounted `auth.json` as the container `node` user.

The retained host owns this file. The host owner and container `node` UID are
different identities. The finance repository stores no auth contents or tokens.

`n8n` owns the encrypted Microsoft OAuth records for `Finance Outlook`
(`BIND_OUTLOOK`) and `Finance OneDrive` (`BIND_ONEDRIVE`). The finance repository
stores only the binding contract. See the
[`n8n credential checklist`](docs/n8n-credential-setup-checklist.md) and the
orchestrator's [`Codex runtime guide`](https://github.com/srobroek/n8n-orchestrator#subscription-backed-agent-runtimes).

- [`backup.sh`](https://github.com/srobroek/n8n-orchestrator/blob/main/scripts/backup.sh)
- [`doctor.sh`](https://github.com/srobroek/n8n-orchestrator/blob/main/scripts/doctor.sh)
- [`restore-disposable.sh`](https://github.com/srobroek/n8n-orchestrator/blob/main/scripts/restore-disposable.sh)
- [`recover-retained-n8n-key.sh`](https://github.com/srobroek/n8n-orchestrator/blob/main/scripts/recover-retained-n8n-key.sh)
- [`cloudflare-publication.md`](https://github.com/srobroek/n8n-orchestrator/blob/main/docs/cloudflare-publication.md)
- [`verify-cloudflare-routes.sh`](https://github.com/srobroek/n8n-orchestrator/blob/main/scripts/verify-cloudflare-routes.sh)

## recreate locally

```powershell
git clone <private-repository-url>
cd Finance-Statement-Tracker
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

PDF extraction is an explicit `n8n` sub-workflow. Install the `statements`
extra for local parser work. Production extraction uses reviewed nodes.

## run

```powershell
python -m unittest discover -s tests -v
python -m finance_tracker.cli demo
python -m finance_tracker.cli browser-adapters-status --sources config\browser-sources.json --adapters-root browser_adapters
```

The `actual-export` and `month-close` commands are not runnable from a fresh
checkout. They need caller-provided canonical transaction and statement-status
JSON files, and this repository does not contain those input fixtures.

Browser acquisition is an alternate source. It is not a second ledger. Provider
recipes describe the authenticated UI path. Official CSV and XLSX files enter
the same staging and review pipeline. Official PDF files use that pipeline too.

This repository has no ingestion bridge or second transaction store. The fixed-purpose
ledger node writes through `@actual-app/api` after validation and review.

## runtime model

The adapter writes records to the ledger through Node API. `n8n` owns Outlook
retrieval and schedule work.

The companion SQLite store owns its cashback mailbox cursor. It owns live state.
`n8n` owns other acquisition cursors. `OneDrive` owns evidence originals. Its
JSON catalog has the same owner.

a notification can update pace or routing. It
can update bucket headroom or warnings.

Each card has a statement job. The job reconciles live state. The
job closes the card period. The job extracts the payment due date.

`config/codex-automations.json` contains local checks. It is not a
production scheduler. `n8n` owns production schedules.

The launcher prompts use runbooks under `agents/automations/`. Audit a local
installation with:

```powershell
python -m finance_tracker.cli automation-audit `
  --manifest .\config\codex-automations.json `
  --project-root . `
  --automation-root "$env:USERPROFILE\.codex\automations"
```

The companion recalculates its dashboard every minute. A separate host timer
probes the ledger and Cashback Control. `n8n` and Postgres use their own health
checks.

Adapters emit review rows. They emit an exact balance check.
Passwords come from runtime secrets or interactive input. A successful parse does
not close a card period. The matching workflow proves the ledger delta. Period
close follows that proof.

See [`AGENTS.md`](AGENTS.md) for extension rules. The guide lists the extension
rules used by this project.

## reusable cashback deployment

Cashback Control accepts a validated profile through
`CASHBACK_PROGRAM_CONFIG_PATH`. The profile defines card names and currency. It
defines tiers and caps. It defines buckets and alerts. It defines routing trees.
See
[`docs/cashback-profile.md`](docs/cashback-profile.md),
[`config/cashback-profile-schema-v1.json`](config/cashback-profile-schema-v1.json),
and [`examples/cashback-profiles/`](examples/cashback-profiles/).
