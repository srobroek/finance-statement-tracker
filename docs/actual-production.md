# ledger operations

`Actual Budget` is the posted finance ledger. `n8n` acquires source files and
runs orchestration. Cashback Control owns routing state. `OneDrive` owns source
evidence. No second posted-transaction store exists.

## runtime topology

| service | responsibility | exposure |
|---|---|---|
| `finance-actual-poc` | ledger files and the `Actual` server | private Docker network |
| `finance-actual-proxy` | shared-array-buffer headers | `127.0.0.1:5006` and tunnel |
| `finance-cashback-control` | cashback routing and push state | separate stack |
| `finance-n8n` | schedules and workflow work | `172.20.10.20:5678` and tunnel |
| `finance-n8n-postgres` | workflow state | private n8n network |

`n8n` uses a custom node with `@actual-app/api` over
`finance-actual-poc_default`. `n8n` stores its node cache in the persistent
volume. Typed finance operations pass through the custom node. It serializes
ledger writes. It reads each imported ID back. It cannot run arbitrary commands.

## import gates

Use this order for an import:

1. archive the source in `OneDrive` and save its SHA-256 identity
2. parse and normalize without a ledger write
3. apply static rules and history matching
4. review bounded AI proposals
5. check statement arithmetic and account mapping
6. preflight the `Actual` target
7. write through the custom node
8. read every imported ID back
9. advance a cursor only after a durable receipt exists

When statement reconciliation succeeds, close the Cashback period. A failed or
quarantined run keeps its cursor unchanged. It cannot create a balancing entry.

## acceptance ledger

The [project acceptance ledger](../config/project-acceptance.json) is the source
for runtime acceptance. The `cashback-reusability-mobile-push` entry is the
Cashback gate. Its checkout status is `PARTIAL`. Its blockers are
`FICTIONAL_PORTFOLIO_MATRIX_REQUIRED` and `MOBILE_PUSH_ACCEPTANCE_REQUIRED`.
Run its declared verifier from the repository root:

```sh
python scripts/verify-project-acceptance.py --require cashback-reusability-mobile-push
```

The ledger entry records the verifier and its evidence. A service health check
does not establish workflow acceptance.

## semantic acceptance

The semantic gate requires one real production ingestion through the import gates
above. Record each field in its terminal receipt:

- source SHA-256
- normalized batch
- imported IDs
- cursor before and after

Replay the identical source. Do a controlled `n8n` restart. When all checks pass,
accept the run:

- replay is a no-op
- the cursor is unchanged
- ledger readback matches the first receipt

Cloudflare publication is not part of this semantic gate. Treat its connector
and authorization checks as a separate optional boundary.

## runtime status

| surface | status |
|---|---|
| `Cashback Control` runtime | `READY` |
| `W20` workflow integration | `DEFERRED` |
| `W02` workflow integration | `DEFERRED` |
| `W03` workflow integration | `DEFERRED` |
| shared ingestion authority | `OPEN` |

The table records the current runtime split. The acceptance ledger remains the
source for production promotion. The `cashback-reusability-mobile-push` entry
still reports its own gate status and blockers.

When its named verifier and runtime readback pass, a production gate opens. A
container health response does not prove a workflow result. The ledger stores the
gate status and verifier. Its entry records blockers and evidence for review.
When either declared blocker remains, the Cashback entry stays `PARTIAL`. The
operator records each command result with the same checkout and ledger source,
binding the status to the target service and review time.

The shared ingestion authority stays `OPEN` until the semantic receipt binds each
owner.

## cloudflare target

Cloudflare is optional. The user waived its publication and has not verified it. The
checkout has no current provider authority. It has no provider readback.
Cloudflare stays outside semantic acceptance. These mappings describe a target,
not a deployment receipt:

- `actual.vxsan.com` targets `http://127.0.0.1:5006`
- `n8n.vxsan.com` targets `http://172.20.10.20:5678`
- origin host overrides stay unset
- browser UIs use interactive access
- machine access uses a separate service policy

The local checkout does not run `cloudflared`. Keep routes disabled until provider
readback proves:

- connector identity
- route identity
- origin reachability
- service authorization

Use the pinned platform contract:
[`cloudflare-publication.md`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/docs/cloudflare-publication.md).
Use its route check:
[`verify-cloudflare-routes.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/verify-cloudflare-routes.sh).
The ledger keeps `cloudflare-route-security` at `IMPLEMENTED_NOT_DEPLOYED` until
that readback exists.

## schedules

`n8n` owns these jobs:

- notification scans
- issuer statement jobs

Missed runs use an overlap cursor. Each card has a job.

For each card, `n8n` checks:

- statement arrival
- reconciliation success

When both checks pass, close the card period. The job records the payment due
date.

FAB and Sarwa acquisition stays user-assisted. Amazon order evidence uses
Outlook email enrichment. Browser credentials stay outside unattended jobs.

## monitoring

The host monitor checks `Actual`, its proxy, and Cashback Control. The `n8n`
stack checks Postgres and uses the pinned `doctor.sh` procedure. Durable redacted
receipts are the execution record because the platform disables successful and
failed execution history.

Static exports and local tests do not prove provider authentication, production
identity, ledger readback, Cashback readback, routes, or rollback.

`n8n` has no production cutover guide in this checkout. The checkout has no
production receipt. `run-four-table-cutover.sh` supports `PRODUCTION_ONLY` and
`DISPOSABLE_ONLY`.

`PRODUCTION_ONLY` requires protected receipt inputs, an exact project ID, and a
named forward or rollback acknowledgment. The script records a durable runtime
journal. When output fails, it recovers a forward receipt.

The documented invocation is disposable. Do not treat its fixtures as retained-
state migration inputs.
