# Actual production

Actual is the authoritative ledger, budget, rule, schedule, reconciliation, and
reporting application. n8n owns acquisition and orchestration; the cashback app
owns live routing state. No ingestion bridge or second posted-transaction store
exists.

## Runtime topology

| Service | Responsibility | Exposure |
|---|---|---|
| `finance-actual-poc` | Actual server and ledger files | private Docker network |
| `finance-actual-proxy` | SharedArrayBuffer headers | host `127.0.0.1:5006`, Cloudflare Tunnel |
| `finance-cashback-control` | live cashback routing and push | independent stack |
| `finance-n8n` | schedules, ETL, review, and operations | host `172.20.10.20:5678`, Cloudflare Tunnel |
| `finance-n8n-postgres` | n8n workflow and operational state | private to n8n network |

The n8n Actual custom node uses `@actual-app/api` directly over
`finance-actual-poc_default`. Its local Actual cache is inside the persistent
n8n volume. The node accepts typed finance operations only, serializes ledger
writes, verifies imported IDs, and cannot execute arbitrary commands.

## Import gates

1. Archive the immutable source in OneDrive and persist its SHA-256 identity.
2. Parse and normalize without writing to Actual.
3. Apply ordered static rules, history matching, and bounded AI proposals.
4. Require tied statement arithmetic, mapped accounts, stable imported IDs,
   valid notes, and an explicit review result.
5. Preflight against Actual.
6. Write through the direct Actual node and read every imported ID back.
7. Advance the Outlook/browser cursor only after the execution receipt is
   durable in n8n Postgres.
8. Finalize a cashback period only after statement reconciliation succeeds.

Retries reuse the immutable source identity and idempotency key. A failed or
quarantined run never advances a cursor and never creates a balancing entry.

## Cloudflare

- `actual.vxsan.com` routes to `http://127.0.0.1:5006`.
- `n8n.vxsan.com` routes to `http://172.20.10.20:5678`.
- Leave origin Host-header overrides unset.
- Protect browser UIs with interactive AD.
- Use a separate machine-to-machine Access policy for n8n MCP or unattended
  webhook paths. Browser cookies are not service credentials.

| Boundary | Rule |
|---|---|
| Local connector | The finance checkout does not run `cloudflared`. Keep the service stopped and disabled. |
| Tunnel owner | The `Home-beachhead` tunnel owns connector replicas and route identity. |
| Provider contract | [`cloudflare-publication.md`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/docs/cloudflare-publication.md) |
| Provider routes | After Service Auth checks pass, activate routes. |
| Route script | [`verify-cloudflare-routes.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/verify-cloudflare-routes.sh) |
| Route verification | Run the script for positive and negative results. |
| MCP route | `cloudflare-route-security` remains `IMPLEMENTED_NOT_DEPLOYED` in `config/project-acceptance.json`. Keep the route disabled until the connector receipt exists. |

## Schedules

n8n owns the twice-daily/live notification scans and issuer-specific monthly
statement workflows. An overlap cursor catches missed runs. Statement periods
close per card only after the expected statement arrives and reconciles.
Interactive FAB and Sarwa acquisition remains user-assisted. Amazon and other
merchant order evidence uses generic Outlook email enrichment.

## Monitoring

The existing host monitor owns only Actual, its proxy, and cashback. The n8n
stack health-gates n8n on Postgres and checks both services with
[`scripts/doctor.sh`](https://github.com/srobroek/n8n/blob/a3fa5487b250dc46c14ee460a4dc2d34a22c3867/scripts/doctor.sh).
When the n8n MCP acceptance gate passes, query execution state through n8n MCP.
The checked-in application manifest keeps n8n MCP disabled until that gate
passes. Failed runs remain visible; successful run history is pruned by n8n
retention settings.

Current production remains blocked until the fixed-purpose PDF/parser/Actual
custom nodes are implemented, fixture-tested, shadow-run, and promoted through
the acceptance gates in `config/project-acceptance.json`.
