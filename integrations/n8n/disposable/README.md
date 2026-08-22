# Disposable n8n runtime fixtures

This directory is test infrastructure, not a deployable workflow set. The
generated workflows are inactive and must only be imported when the harness has
received the exact acknowledgement `DISPOSABLE_ONLY`.

The production gate imports all 19 exports from `../workflows` into an empty
Postgres-backed n8n instance with `--activeState=false`, verifies that none are
published, and executes workflow 19 to create/reuse the Data Tables and seed the
server-owned AI policy contracts. The fixture gate then imports the separate
`generated` directory and executes only the fixed IDs declared in
`fixture-manifest.json` with `n8n execute --id <ID>`.

The harness exercises production workflow 09 through the server-owned
subscription agent adapter (workflow 21). Neither positive wrapper accepts a
model, URL, credential, prompt, or policy hash from its input.

`generate_fixture_workflows.py` derives fixture cores from the production
exports and records exact source and generated hashes. It replaces only the
boundaries that cannot be called in a disposable test without external state:

- Outlook enumeration is replaced with fixed zero, 101-message,
  out-of-order, or failure input.
- The error trigger is replaced with a fixed synthetic failure envelope.
- Recovery's scheduler, OneDrive download, and Actual operations are replaced
  with fixed no-finance-write nodes; the Data Table state machine and the real
  fenced-lease subworkflow remain.
- Negative AI fixtures contain invalid requests that must fail before the
  adapter. The positive Luna wrapper exercises the production workflow's
  fixed subscription-provider boundary with a schema-bound, redacted request
  and no finance write. The positive Sol wrapper is excluded from default
  execution and requires the explicit harness gate
  `DISPOSABLE_ALLOW_SOL_MEDIUM`.

The manifest distinguishes these derived execution receipts from production
provider proof. In particular, an inactive MCP Server Trigger cannot be tested
over its transport without publishing it, and a real Actual recovery write is
intentionally forbidden here. Positive agent receipts prove only the fixed
subscription handoff and checked proposal boundary in the disposable stack.

Regenerate and verify drift with:

```powershell
python integrations/n8n/disposable/generate_fixture_workflows.py --write
python integrations/n8n/disposable/generate_fixture_workflows.py
```
