# Finance production stack

Deploy `compose.yaml` as the Dockge-compatible stack `finance-actual-poc`.
Despite the historical stack name, this is the production topology:

- `finance-actual-poc`: the digest-pinned Actual 26.8.1 ledger service,
  private to the Compose network;
- `finance-actual-proxy`: the digest-pinned non-root Nginx edge on host port
  `5006`, providing the browser isolation headers Actual requires.

Actual data is bind-mounted at `./data`. Cashback operational state remains in
`/opt/stacks/finance-actual-poc/cashback-data` for backward-compatible storage,
but its service is owned by the independent `/opt/stacks/finance-cashback`
project.

Actual and its Nginx proxy are the only services in this Compose project.
Cashback is an independent project under `deploy/cashback`. n8n owns ingestion
orchestration in its own stack and reaches Actual over the private Docker
network through a fixed-purpose custom node using `@actual-app/api`.

The GHCR package inherits the repository's visibility. If it is private, log
the container host in to `ghcr.io` once with a token that has `read:packages`.

Deploy and verify:

```bash
cd /opt/stacks/finance-actual-poc
sudo docker compose up -d actual actual-proxy
sudo docker compose ps
curl -fsSI http://127.0.0.1:5006/ | grep -E 'Cross-Origin-(Embedder|Opener)-Policy'
```

Use `scripts/actual-setup.ps1` for declarative bootstrap operations. All new
statement and browser imports enter the inactive-first n8n workflows; there is
no HTTP ingestion bridge or SSH upload wrapper. Bootstrap defaults to planning,
and writes require explicit mode plus the production write gate. See
`docs/actual-production.md` and `docs/backup-and-restore.md` for the operating
and recovery procedures.
