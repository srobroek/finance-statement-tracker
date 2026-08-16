# Finance production stack

Deploy `compose.yaml` as the Dockge-compatible stack `finance-actual-poc`.
Despite the historical stack name, this is the production topology:

- `finance-actual-poc`: the digest-pinned Actual 26.8.1 ledger service,
  private to the Compose network;
- `finance-actual-proxy`: the digest-pinned non-root Nginx edge on host port
  `5006`, providing the browser isolation headers Actual requires; and
- `finance-cashback-control`: the non-root live cashback event store and
  dashboard on host port `5010`.

Actual data is bind-mounted at `./data`; cashback operational state is mounted
at `./cashback-data`. The stack-local `.env` is required and must contain a
strong `CASHBACK_INGEST_TOKEN`. It is not tracked or included in backups. Store
the Actual application password and ingest token in the approved password
manager.

The cashback service is published by `.github/workflows/cashback-image.yml` to
`ghcr.io/srobroek/finance-statement-tracker-cashback-control`. The production
stack follows the `main` image tag and uses `pull_policy: always`, so Dockge's
Update action (or `docker compose pull` followed by `docker compose up -d`)
pulls the most recent tested image. The workflow also publishes immutable
`sha-<commit>` tags for rollback. Do not maintain a second generated Compose
file; `docker compose config` is the reproducible rendered form.

The GHCR package inherits the repository's visibility. If it is private, log
the container host in to `ghcr.io` once with a token that has `read:packages`.

Deploy and verify:

```bash
cd /opt/stacks/finance-actual-poc
sudo docker compose pull cashback-control
sudo docker compose up -d cashback-control
sudo docker compose ps
curl -fsSI http://127.0.0.1:5006/ | grep -E 'Cross-Origin-(Embedder|Opener)-Policy'
curl -fsS http://127.0.0.1:5010/api/health
```

Use `scripts/actual-setup.ps1` and `scripts/ingest-statement-to-actual.ps1` for
authenticated Actual operations. Both default to planning/preflight; writes
require their explicit apply/commit switches. See `docs/actual-production.md`
and `docs/backup-and-restore.md` for the operating and recovery procedures.
