# Finance Prefect planning snapshot

## Scope and authority

This local-only snapshot records the approved refresh under audit `orc-n2q.395`. Delivery belongs to epic `orc-n2q.391`. Decision `orc-n2q.391.49` is closed. Short IDs use the form `.N` for `orc-n2q.391.N`. Root IDs remain fully qualified, such as `orc-n2q.390` and `orc-n2q.322`.

Ownership is split between `srobroek/prefect-platform` and `srobroek/prefect-finance`. `finance-statement-tracker` remains the migration and rollback source until separately approved retirement. Cross-repository consumption is immutable and reviewed. The native embedded ledger at `/home/sjors/dev/finance/.beads/embeddeddolt` is the planning authority. It does not approve production actions.

## Ordered plan

This is a planning handover for continued planning, not implementation authorization. Retain independent platform lanes. Use this milestone sequence:

1. Split repositories (`.50`).
2. Record the immutable interface and version receipt (`.53`).
3. Review the interface (`.55`).
4. Complete Finance flows (`.2`).
5. Correct runtime failures.
6. Prove the local end-to-end POC (`.8`).
7. Review the POC (`.26`).
8. Fix specifications (`.9`).
9. Review specifications (`.25`).
10. Retain the existing candidate milestone summary:
    - `.37`
    - `.60`
    - `.38`
    - `.40`
    - `.45`
    Intervening tasks and reviews remain retained.
11. Require recorded human resolutions for the six gates before `.46` acceptance.
12. Validate SharePoint (`.47`) and image publication (`.48`) separately.
13. Deploy, cut over, and issue dual releases only after specification, platform, and Finance acceptance.

The approved plan adds a prerequisite from `orc-n2q.390` to `.47`. Its separate approvals remain intact.

## Approved ledger changes

The manifest contains 13 net new prerequisites:

| Target | New prerequisites |
|---|---|
| `.2` | `.55` |
| `.8` | `.1`, `.6`, `.10`, `.11`, `.12`, `.13`, `.14`, `.16`, `.18` |
| `.9` | `.51`, `.26` |
| `.47` | `orc-n2q.390` |

The two field updates are:

- Update `orc-n2q.391` to name both repositories, preserve `finance-statement-tracker`, and retain SpecKit gates and handover evidence requirements.
- Update `.9` to separate traceability from implementation and runtime acceptance, and link `.26`.

The `.9` criteria retain four evidence areas:

- GitHub release-client credential ownership.
- Per-source writer stop versus shared n8n shutdown.
- Recovery before and after credential revocation.
- Whole-statement failure recovery and replay after interrupted writes.

The criteria do not assume a new journal.

The first comment preserves conflicting `orc-n2q.322` receipts and retains its unresolved evidence.

The second comment preserves blocked `orc-n2q.322.1` requirements and retains all 21 assigned IDs as validation candidates.

The comments map Microsoft behavior in `orc-n2q.157` to `orc-n2q.322` and the Prefect replacement.

The comments change no status, owner, acceptance outcome, deletion, closure, or source activation.

## Package and runtime dependency review

The five criteria updates supplement the earlier two field updates. They do not change the 13 prerequisites or two preserved comments. The table describes the adoption POC before the isolated Redis change. Package ownership remains provisional until `.50`.

| Task | Source-relative evidence and required review |
|---|---|
| `.3` | `prefect-platform/src/prefect_platform/contracts/models.py` and `prefect-platform/src/prefect_platform/finance/models.py` import Pydantic APIs. `prefect-platform/pyproject.toml` declares only Prefect and FastAPI at runtime, so Pydantic is a direct undeclared dependency. Add a compatible direct constraint. |
| `.6` | `prefect-platform/pyproject.toml` requires unversioned Hatchling. Constrain the build backend and record its provenance. The project and `prefect-platform/uv.lock` target Python 3.12. |
| `.10` | `prefect-platform/images/finance-worker/Containerfile` installs from `pyproject.toml` and does not consume `uv.lock`; enforce locked installation or prove equivalence. `prefect-platform/compose.yaml` interpolates raw database credentials; escape reserved characters and test them. The current isolated platform topology excludes Redis; `.10` acceptance records that exclusion. The worker uses `--type process`; [Prefect process-worker pool creation](https://github.com/prefecthq/prefect/blob/main/docs/v3/concepts/workers.mdx) says this mode auto-creates a missing pool, so no extra pool task is justified. |
| `.13` | `prefect-platform/uv.lock` resolves the reviewed versions. Compare installed packages with that lock. Run the existing API/worker smoke. |
| `.43` | `prefect-platform/compose.yaml` and `prefect-platform/images/finance-worker/Containerfile` use mutable image tags. Record immutable manifest and platform digests for PostgreSQL, the Prefect server and worker base, and the built worker. Do not require a Redis digest for the current topology. CI action coverage is already tracked by `.15`. |

The lock records Python 3.12, Prefect 3.4.24, FastAPI 0.115.14, Pydantic 2.13.5, Pydantic-core 2.46.5, and Starlette 0.46.2. Inspected published constraints intersect. Registry metadata sources are [Prefect 3.4.24](https://pypi.org/pypi/prefect/3.4.24/json) and [FastAPI 0.115.14](https://pypi.org/pypi/fastapi/0.115.14/json).

Docker Hub metadata lists PostgreSQL 18.6, Redis 8.2.9, and `prefect:3.4.24-python3.12`, with amd64 and arm64 support. Sources are [PostgreSQL 18.6](https://hub.docker.com/_/postgres/tags?name=18.6), [Redis 8.2.9](https://hub.docker.com/_/redis/tags?name=8.2.9), and [Prefect 3.4.24 Python 3.12](https://hub.docker.com/r/prefecthq/prefect/tags?name=3.4.24-python3.12). Tag existence is not byte identity, runtime acceptance, or vulnerability clearance.

No container or startup test, dependency installation, runtime smoke, immutable-digest proof, or CVE audit was performed. The empty local `prefect-finance` target is not evidence that a remote Finance repository is absent. These findings define acceptance criteria for the existing tasks.

## Isolated Redis exclusion evidence

Code exception `orc-n2q.391.10.1` records that the Redis service and its `depends_on` entry were removed in isolated platform worktree `/home/sjors/tmp/worktrees/finance/omp-agent-orc-n2q.391.10.1`, Redis-removal commit including documentation updates `3e0c55096be7033b373619a7ec651d28f91025c3`. Native `docker-compose config --quiet` passed. `docker-compose config --services` returned `postgres`, `prefect`, `finance-worker-a`, and `finance-worker-b`.

No containers started or deployed. The canonical POC remains preserved. Historical registry lookup of a Redis tag does not establish a current dependency. `.10` acceptance and `.43` digest review therefore use the Redis-excluded topology. `.9` retains its reconciliation comment.

Closed-proof audit `orc-n2q.395.1` is complete. The scan excluded 1,427 events and classified 1,591 of 1,591 non-events. Per-ID artifact: `closed-implementation-proof-20260922.json`.

| Proof category | Count |
|---|---:|
| Verified delivery evidence | 192 |
| Implementation evidence without landing established | 43 |
| Unverified | 337 |
| Superseded or retired | 150 |
| Non-implementation | 869 |
| Substantiated contradictions | 0 |

The scan found no closed implementation tasks in `orc-n2q.391`; its three closed records are decision/planner/review records `.49`, `.78`, and `.79`. Missing proof is not proof of missing code. The 43 implementation-evidenced rows do not all require merging. No provider or runtime retest occurred, and no closed bead was reopened.

## Reconciliation queue

The reconciliation queue belongs to epic `orc-n2q.396` and source audit `orc-n2q.395.1`. Its 11 ready, open, unassigned batches cover 337 unverified records plus 43 records whose landing is unestablished.

| Batch | Domain | Count |
|---|---|---:|
| `orc-n2q.396.1` | Legacy runtime evidence 1/2 | 41 |
| `orc-n2q.396.2` | Legacy runtime evidence 2/2 | 41 |
| `orc-n2q.396.3` | Cashback evidence 1/2 | 39 |
| `orc-n2q.396.4` | Cashback evidence 2/2 | 39 |
| `orc-n2q.396.5` | Tooling and remaining receipts | 16 |
| `orc-n2q.396.6` | Actual evidence 1/2 | 41 |
| `orc-n2q.396.7` | Actual evidence 2/2 | 41 |
| `orc-n2q.396.8` | Microsoft evidence | 14 |
| `orc-n2q.396.9` | Delivery evidence | 32 |
| `orc-n2q.396.10` | Classification and rules evidence | 27 |
| `orc-n2q.396.11` | Journeys and evidence documents | 49 |

Each batch must inline its decisive proof gap, own its result JSON, and track original records without reopening them. Retained obligations must map to a scoped prerequisite or repair, not repeat an unverified claim. Independent review `orc-n2q.396.12` is blocked until all 11 batches complete. Execution has not started. The queue artifact is `reconciliation-queue-20260922.json`.

The queue is independent of unrelated Prefect implementation work and adds no blanket gate. Coverage is 380 unique records, all 380 originals remain closed, six gates remain open, and the verification reports zero cycles.

## Proposed deployment boundary

This is a planning proposal, not an approved topology split. The current Redis-free combined POC has not been split, and no deployment is authorized.

- Platform Compose would own the Prefect API, UI, server, and PostgreSQL.
- Finance Compose would own Finance worker instances and the Finance application image and configuration.
- Platform may own a reusable worker base image.
- Future services would use their own workers and stacks.

Separate stacks could share a private same-host external network or use a private authenticated API endpoint. They must not use cross-project `depends_on`; bounded API readiness and reconnect behavior would replace it.

## Boundaries and unresolved evidence

Do not create duplicate migration or specification beads. Do not add n8n features. Do not assume a new journal. Do not claim unsupported Microsoft closure. No existing implementation task was closed.

The earlier catalog has 337 candidates. This scan has 337 unverified records; these are different populations. The retirement-catalog lane still has 21 candidates. Receipts for `orc-n2q.322` and `orc-n2q.322.1` remain unresolved and conflicting. `orc-n2q.394` retains domain and lease work.

Untracked legacy drafts are distinct from delivered artifacts. This snapshot does not claim that target code is absent globally.

Gates remain open. Runtime reads do not approve live actions.

Production, deployment, cutover, and release remain blocked on the named gates and platform and Finance acceptance.

## Resume point (2026-09-22)

This is a planning handover only. The user accepted the reconciliation queue. Epic `orc-n2q.396` has 11 ready, open, unassigned batches covering 380 records. Review `orc-n2q.396.12` waits for all 11. Execution has not started.

The next planner reads current bead and lease state and uses the existing queue and artifacts. Do not repeat the 3,018-record scan or recreate tasks. Delegate cheap lookup and mechanical work to configured Luna agents. Independent review remains required.

The prior handover and queue are committed at `e8f9d70f`. The proof audit is `14877c4e`. Redis removal is `3e0c55096be7033b373619a7ec651d28f91025c3`. This work is local only. Do not push or merge.

Preserve open branch and worktree `/home/sjors/tmp/worktrees/finance/omp-agent-orc-n2q.395` and Redis worktree `/home/sjors/tmp/worktrees/finance/omp-agent-orc-n2q.391.10.1`. The canonical POC is untouched and unborn. Adopt the recorded snapshot deliberately; do not reset canonical. The embedded store is `/home/sjors/dev/finance/.beads/embeddeddolt`.

Successful checks used direct child-process environment overrides: `BEADS_DOLT_SHARED_SERVER=false`, `BEADS_DOLT_SERVER_MODE=0`, `BEADS_DOLT_AUTO_START=0`, and `BEADS_DOLT_SERVER_USER=beads`, while preserving the session `BEADS_DIR` pin. Bun `$` wrappers retained stale routing in this session, so restarting alone is not a proven fix. Never restart SQL. The separate Compose design remains proposed only. No deployment, provider action, live financial action, or closed-bead reopening is authorized.