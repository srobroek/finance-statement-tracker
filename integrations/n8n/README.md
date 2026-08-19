# Finance n8n workflows

This directory contains sanitized, importable n8n workflow JSON for the finance
system. n8n owns schedules, acquisition, orchestration, retries, and execution
visibility. Actual remains the authoritative ledger, the cashback app remains
the live routing store, and OneDrive remains the evidence archive. n8n stores
workflow and operational state in its private Postgres container.

## Design rules

- Workflows are inactive after import.
- Every financial stage is a visible node or sub-workflow.
- Prefer official nodes. The only planned custom nodes are fixed-purpose PDF
  unlock, issuer parser, and Actual writer operations that n8n cannot express
  safely with standard nodes.
- Never use Execute Command, SSH, arbitrary filesystem paths, caller-selected
  shell arguments, or embedded credentials.
- The Outlook trigger is not the accounting cursor. Every live/monthly workflow
  includes a scheduled cursor-minus-overlap recovery path.
- No cursor advances until the original is archived, the downstream write is
  verified, and the durable receipt exists.
- AI returns proposals only and is never required for deterministic staging.
- Transaction-alert sweeps and statement/document acquisition are separate
  sub-workflows. Live mail never traverses attachment or PDF processing.
- All companion calls use an n8n HTTP Header Auth credential. Tokens are never
  embedded in expressions, workflow JSON, or model-visible tool inputs.
- Actual writes use the reviewed fixed-purpose custom node directly. There is
  no HTTP ingestion bridge, SSH hop, or generic command runner.

## Import

The separate `finance-n8n-orchestrator` repository mounts this `workflows`
directory read-only at `/workflows`. Its import helper imports every JSON file
inactive. After import:

1. bind the Outlook and OneDrive credentials;
2. seed the source/rule/cursor Data Tables from versioned configuration;
3. install the reviewed finance custom nodes;
4. run fixtures manually;
5. enable only the MCP-safe workflows listed in `pipeline-registry.json`;
6. publish and activate one issuer only after its cutover gates pass.

## Codex

The public/default AI provider is the official n8n OpenAI integration with a
structured schema. Subscription-backed Codex remains provider-optional. The
current `n8n-nodes-prodex` package is not installed in production because its
token export and in-process shell-capable agent collapse the n8n trust boundary.
If Codex subscription execution is required, use a narrow fixed-policy runner or
a reviewed future provider that supports ephemeral, read-only structured runs.

Codex can still invoke and observe bounded workflows through n8n's built-in MCP
server. In particular, `OUTLOOK_FINANCE_ACQUISITION` is reusable from monthly
pipelines and as an MCP operation; `FINANCE_OPERATIONS_STATUS` provides run
state without exposing unrestricted mutation workflows.
