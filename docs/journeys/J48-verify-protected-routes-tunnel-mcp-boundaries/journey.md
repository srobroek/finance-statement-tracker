---
id: J48
title: Verify protected routes/tunnel/MCP boundaries
version: 1
status: draft
last_reviewed: 2026-09-17
actors: [finance-operator]
surfaces: [protected-routes]
interfaces: [RO]
trace: [orc-n2q.379.49, orc-n2q.379.101]
---
## Goal
Read-only verify that public routes, tunnel ownership, retained services, and MCP/tool boundaries resolve to the intended authorities without mutation.

## Preconditions
- Approved read-only host/provider access and a non-sensitive identity inventory are available.
- No deployment, restart, credential change, or route mutation is permitted.

## Steps
### S1 — Inspect authority {#S1}
- **Do:** Read tunnel/service ownership, connector identity, DNS/HTTPS behavior, and retained-host topology.
- **Expect:** Each route has one evidenced owner and origin boundary.
- **Expect-negative:** Do not infer authority from a redirect, stale local connector, or ambient DNS.

### S2 — Verify route {#S2}
- **Do:** Perform bounded metadata/readback checks through the public route and intended origin.
- **Expect:** Access behavior, TLS, and origin classification are recorded without secrets.
- **Expect-negative:** No login reset, route edit, restart, or deployment occurs.

### S3 — Verify MCP boundary {#S3}
- **Do:** Confirm tool/resource identity and allowed invocation boundary from configuration.
- **Expect:** MCP calls remain scoped and no alternate shell/tool path is substituted.
- **Expect-negative:** Do not execute write-capable tools or emit credentials.

### S4 — Classify gaps {#S4}
- **Do:** Record current proof, historical-only evidence, and blockers separately.
- **Expect:** An unresolved provider authority remains a blocker, not a pass.
- **Expect-negative:** No mutation is used to “prove” reachability.

## Evidence and acceptance
Evidence: `AGENTS.md:27-36`; `README.md:77-99`; `config/codex-automations.json`; `.mcp.json`/tool configuration where present; Beads `orc-n2q.379.49`, review `orc-n2q.379.101`. Acceptance requires current read-only route, origin, ownership, and MCP-boundary receipts.

## Known blockers
No authoritative J01-J51 corpus or current provider receipt is present here. This remains a draft and makes no live-route claim.
