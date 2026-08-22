# Codex agent runner

This private service is the subscription-backed AI execution boundary for n8n.
It is not a finance worker and contains no deterministic ingestion, parsing,
classification, cashback, or Actual logic.

## Runtime boundary

- One endpoint: `POST /v1/jobs/finance-ai-proposal`.
- One health endpoint: `GET /healthz`.
- No host port in production; n8n reaches it as `codex-agent-runner:5090` on an
  internal Docker network.
- The production bearer is rendered from a refs-only 1Password template on the
  host and passed as `RUNNER_BEARER_TOKEN`, matching the proven Bellwether
  service-account deployment pattern. A file source remains supported for
  non-rootless test environments, but configuring both sources fails closed.
  Generate it as printable HTTP-header-safe text with
  `openssl rand -hex 32`; the runner requires exactly 64 lowercase hex
  characters.
- ChatGPT credentials come from a writable bind or named volume at
  `/home/node/.codex`; they are never embedded in the image or workflow.
- `OPENAI_API_KEY`, `CODEX_API_KEY`, and `CODEX_ACCESS_TOKEN` are rejected.
- The pinned Debian CA bundle is copied into the slim runtime and exposed via
  `SSL_CERT_FILE`; without it Codex subscription traffic fails closed with
  `UnknownIssuer` while the local login cache still appears valid.
- Models are fixed by the versioned policy profile: `LUNA_MAX` maps to
  `gpt-5.6-luna`/`max`; `SOL_MEDIUM` maps to `gpt-5.6-sol`/`medium`.
- The runner embeds `generated/ai-policy-contracts.seed.json` and rejects any
  request whose policy hashes, field set, or exact value domains differ from
  that build-time contract. The n8n Data Table is not trusted by itself.
- Codex runs with no shell, an ephemeral session, read-only sandbox, ignored
  rules/user config, disabled code mode, JSONL events, and a fixed output
  schema.

## Build

Use the repository root as Docker build context:

```text
docker build -f services/codex-agent-runner/Dockerfile .
```

Production builds must supply an immutable digest-pinned `NODE_IMAGE`, generate
an SBOM, pass zero-high/critical scanning, and record the resulting image digest
in the orchestrator image lock. The Codex archive version and published SHA-256
are pinned in the Dockerfile.

## Authentication

The first login is interactive:

```text
codex login --device-auth
```

The resulting `auth.json` is a password-equivalent token cache. Mount it only
into this service, keep the directory writable so refresh rotation persists,
and never commit, log, back up without encryption, or expose it to n8n Code
nodes. The runner calls `codex login status` before every proposal and requires
`Logged in using ChatGPT`.

OpenAI's Outlook Email and SharePoint plugins can reuse the same OpenAI login
and server-side app grants in a fresh non-interactive container. The verified
setup and disposable proof are documented in
[`docs/codex-microsoft-connectors.md`](../../docs/codex-microsoft-connectors.md).
The production proposal runner intentionally continues to ignore user config
and disable tools; the connector proof does not silently widen that boundary.

## Tests

```text
npm ci
npm test
```
