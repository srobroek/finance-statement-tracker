# Bellwether 1Password pattern and Codex runner validation

Date: 2026-08-19

## Outcome

The finance orchestration stack now follows Bellwether's proven host-side
1Password service-account pattern without reusing the Bellwether vault or
service account:

1. A rootless host file named `.env.bootstrap`, owned by `ci` and mode `0600`,
   contains only `OP_SERVICE_ACCOUNT_TOKEN`.
2. A versioned `.env.tpl` contains only references to fields in the dedicated
   `FinanceAutomation/n8n-runtime` item.
3. One `op inject` call resolves all platform fields to a temporary file.
4. The renderer validates the complete field set and formats, atomically moves
   the result to a gitignored mode-`0600` `.env`, and unsets the service-account
   token.
5. Compose passes each resolved value only to the service that needs it. The
   1Password service-account token is never passed to a container.

The finance variant improves on Bellwether's current broad rendered environment
by mapping explicit environment names per service. Statement passwords,
Microsoft credentials, and Actual credentials remain encrypted n8n credentials;
they are not platform environment variables.

## Isolated vault contract

Create a new `FinanceAutomation` vault and a read-only service account scoped
only to its `n8n-runtime` item. The required item shape is versioned in the
orchestration repository at `config/onepassword-item.schema.json`. The Codex
runner bearer is exactly 64 lowercase hexadecimal characters. Cloudflare
verification credentials are host-only and are not exposed to a container.

No Bellwether secret value was read, copied, or modified. The Bellwether service
account is vault-scoped and must not be reused for finance automation.

## Subscription-backed Codex boundary

n8n invokes the private `codex-agent-runner:5090` service over an internal
Compose network. The runner executes `codex exec`; Codex does not run as a host
scheduled process. Only the runner mounts `/home/ci/.codex`, and it rejects
`OPENAI_API_KEY`, `CODEX_API_KEY`, and `CODEX_ACCESS_TOKEN`.

Rootless Podman requires `userns_mode: keep-id` so the runner can safely read and
refresh the host login cache. The runtime also requires a writable mode-`1777`
temporary filesystem and a CA bundle. A five-minute ceiling is configured for
bounded Luna/Sol jobs.

## Disposable proof

The disposable CI-host runner confirmed `Logged in using ChatGPT` and returned
three consecutive schema-valid receipts over its private HTTP boundary:

| Policy | Model profile | Result | Receipt |
| --- | --- | --- | --- |
| `classify-unresolved` | Luna / max | Category `Groceries` | `17e558a3-be92-455c-ab0d-87e13798f97a` |
| `recommend-category` | Sol / xhigh | `Home Services` / `Housing` | `310a1c72-bade-48ee-9ac8-fe705cfb69fb` |
| `detect-subscription` | Luna / max | `is_subscription=true` | `06041826-338a-4d78-8cc4-f1bac67e1d21` |

All three responses reported `auth_mode=CHATGPT_SUBSCRIPTION`. No API key was
used. The output schema uses a JSON-encoded `value_json` field because the Codex
CLI Structured Outputs subset rejected the earlier polymorphic `allOf` schema;
the runner parses and validates the typed value against the server-side policy
domain before responding to n8n.

This proves the 1Password-style bearer boundary and subscription-backed runner.
It is not evidence that the full n8n Data Table, Outlook, Postgres, PDF, Actual,
and Cloudflare workflow stack has completed disposable integration testing.

## Verification

- Main Python suite: 321 passed.
- Codex runner: 16 passed.
- Finance custom nodes: 20 passed.
- Actual integration: 44 passed.
- Orchestration platform contract: 15 passed.
- Orchestrator CI for the initial Bellwether-pattern migration: green at
  `https://github.com/srobroek/finance-n8n-orchestrator/actions/runs/32239303585`.

## Promotion requirements

Before production promotion:

1. Create the dedicated vault, item, and read-only service account in one
   batched 1Password administration session.
2. Install only the service-account token in the host bootstrap file.
3. Render and verify the environment on the target host without printing any
   value.
4. Recreate the affected containers and run the security and route checks.
5. Execute the complete disposable n8n/Postgres/PDF/Actual workflow suite.
6. Promote immutable, scanned image digests only after all receipts reconcile.
