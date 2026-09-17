# production deployment

Deploy `compose.yaml` as the Dockge-compatible stack `finance-actual-poc`.
Despite the historical stack name, this is the production topology:

<!--slopvac-allow: rule=ste-practices.false-friend-term reason=identifier-fidelity -->
- `finance-actual`: the digest-pinned Actual 26.8.1 ledger service is private
  to the Compose network.
- `finance-actual-proxy`: Nginx serves the browser isolation headers at port
  `5006`. The image uses a digest pin. The process has no root privileges.

<!--slopvac-allow: rule=ste-practices.false-friend-term reason=identifier-fidelity -->
Actual data is bind-mounted at `./data`. Cashback stores operational state in
`/opt/stacks/finance-actual-poc/cashback-data` for backward-compatible storage.
The independent `/opt/stacks/finance-cashback` project owns the Cashback service.

<!--slopvac-allow: rule=ste-practices.false-friend-term reason=identifier-fidelity -->
Actual and its Nginx proxy are the only services in this Compose project.
Cashback is an independent project under `deploy/cashback`. n8n owns ingestion
orchestration in its own stack.
<!--slopvac-allow: rule=ste-practices.false-friend-term reason=identifier-fidelity -->
It reaches Actual over the private Docker
network through a fixed-purpose custom node using `@actual-app/api`.

The GHCR package inherits the repository's visibility. If it is private, log
the container host in to `ghcr.io` once with a token with `read:packages`
permission.

Deploy:

```bash
cd /opt/stacks/finance-actual-poc
sudo docker compose up -d actual actual-proxy
sudo docker compose ps
curl -fsSI http://127.0.0.1:5006/ | grep -E 'Cross-Origin-(Embedder|Opener)-Policy'
```

Use `scripts/actual-setup.ps1` for declarative bootstrap operations. All new
statement and browser imports enter the inactive-first n8n workflows. The
deployment has no HTTP ingestion bridge or SSH upload wrapper. Bootstrap
defaults to planning. Write operations use explicit mode and the production
write gate. See
`docs/actual-production.md` and `docs/backup-and-restore.md` for the operating
and recovery procedures.
