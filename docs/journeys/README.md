---
config: user-journeys/1
reporter: local
reporter_labels: []
fix_loop: dispatch-coder
fix_loop_max_iterations: 3
runs_keep: 20
---

# Finance user journeys

This directory defines 51 finance tracker journeys. `FORMAT.md` specifies their structure, and `INDEX.md` routes changes to affected journeys.

Run the structural checks from the repository root:

```bash
python3 docs/journeys/journeys.py lint docs/journeys
python3 docs/journeys/journeys.py index docs/journeys
```

## Interface profiles

Use the selected profile's side-effect boundary. Follow the exact command and evidence source cited by the journey.

### RO

- kind: cli
- exclusive: false
- Install the Python package with `python -m pip install -e .`.
- Launch repository CLI checks with `python -m finance_tracker.cli <command>` as documented in the root `README.md`.
- Use read-only API, UI, and n8n inspection surfaces when the journey names them.
- Reset is not applicable because this profile forbids product-state mutation. Discard temporary evidence created outside the checkout after the run.

### RW-O

- kind: api
- exclusive: true
- Use one exclusive writer for the target named by the journey.
- For Actual, launch the configured stack with `cd deploy/actual-poc && sudo docker compose up -d actual actual-proxy`.
- For Cashback Control, launch the configured stack with `cd deploy/cashback && docker compose up -d`.
- Before the first write, capture a protected pre-state.
- On failure, run the journey's reviewed compensation or recovery command. Verify the result with a fresh readback.
- Use a production target only after the journey records its identity and the user approves the exact consequential action.

### RW-S

- kind: api
- exclusive: true
- Use one exclusive writer and a disposable or explicitly approved target.
- Launch Actual and Cashback Control with the commands listed in the RW-O profile when those surfaces are required.
- Before mutation, get the journey's backup or pre-state receipt.
- Reset with the documented restore or inverse operation. Compare a fresh readback with the captured pre-state.
- Do not substitute deletion, a new transaction, or an unreviewed compensating write for the specified rollback.

The n8n disposable fixtures live under `integrations/n8n/disposable/`. Regenerate them. Then verify them:

```bash
python integrations/n8n/disposable/generate_fixture_workflows.py --write
python integrations/n8n/disposable/generate_fixture_workflows.py
```

Before import, keep the fixture exports inactive and provide the `DISPOSABLE_ONLY` acknowledgment.

## Surface map

| path glob | surfaces |
|---|---|
| `finance_tracker/**` | finance-core, cashback-control, evidence-ingestion |
| `integrations/actual/**` | finance-core, n8n-orchestration |
| `integrations/n8n/**` | evidence-ingestion, n8n-orchestration, runtime-secrets, service-recovery |
| `packages/n8n-nodes-finance/**` | finance-core, n8n-orchestration |
| `apps/cashback-control/**` | cashback-control, service-recovery, backup-restore |
| `browser_adapters/**` | evidence-ingestion |
| `config/**` | finance-core, cashback-control, evidence-ingestion, runtime-secrets |
| `deploy/actual-poc/**` | deployment, protected-routes, service-recovery, backup-restore |
| `deploy/cashback/**` | deployment, protected-routes, service-recovery, backup-restore |
| `Dockerfile*` | container-images, release-promotion |
| `.github/workflows/**` | container-images, release-promotion |
| `docs/backup-and-restore.md` | backup-restore |
| `docs/journeys/**` | release-promotion |

## Intent-evidence sources

Use merged pull requests and their commits as intent evidence. Find operating constraints in:

- `README.md` and `AGENTS.md`;
- `docs/` and `config/`;
- `integrations/n8n/`.

Do not treat an unmerged plan, an open issue, or a validation result as proof that behavior changed intentionally.

## Findings and retention

Append findings to `TRACKER.md` using the local reporter format in `FORMAT.md`. Stop the dispatch-coder fix loop after three iterations. Retain the newest 20 run files per journey. Get human approval before pruning.