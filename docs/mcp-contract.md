# Finance MCP contract

The finance application exports one bounded n8n MCP Server Trigger at
`/mcp/finance-operations-v1`. Cloudflare Service Auth protects the edge and a
dedicated application bearer protects the n8n trigger. Instance-level n8n MCP
remains disabled.

The checked-in contract is [config/mcp-facade.json](../config/mcp-facade.json).
The exported workflow is
[15-finance-mcp-facade.json](../integrations/n8n/workflows/15-finance-mcp-facade.json).

| External tool | Input passed to the fixed workflow | Mode |
| --- | --- | --- |
| `finance.status.v1` | `finance.status` | Read-only status |
| `finance.document.request.v1` | `document.request` | Document request |
| `finance.reviewed-artifact.handoff.v1` | `artifact.submit_reviewed` | Reviewed artifact handoff |

Tool inputs contain fixed operation codes and server-owned identifiers. Callers
cannot supply URLs, filesystem paths, credentials, workflow IDs, data-table IDs,
mailbox queries, commands, or activation flags. The workflow remains inactive
until a reviewed disposable proof supplies the required acknowledgment and
teardown readback.

The application manifest records the contract path, SHA-256 digest, route,
authentication boundary, and exact external tool names. Tests extract the tool
names and input mappings from the exported workflow and compare them with the
manifest and contract file.
