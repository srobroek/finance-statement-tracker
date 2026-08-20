# n8n to Codex execution decision

Date: 2026-08-19  
Status: accepted implementation constraint, pending disposable runtime proof

## Decision

n8n owns the schedule, durable state, idempotency, retries, validation, and
application of AI proposals. A private, single-purpose Codex runner container
executes the subscription-backed Codex CLI and returns proposal-only structured
output to an authenticated n8n HTTP Request node.

The runner is not a deterministic finance worker. It has no Actual, Outlook,
OneDrive, browser, PDF, Postgres, or cashback credentials and contains no
statement parsing or finance rule engine. It accepts one fixed operation and
cannot accept a caller-selected model, prompt, command, path, URL, provider, or
credential.

## Why not the native n8n AI Agent today

- The AI Agent is a Tools Agent: it requires a chat-model connection and at
  least one tool. The finance enrichment stage needs bounded structured
  proposals rather than autonomous tool selection.
- Current official n8n OpenAI credentials document API-key authentication.
  That would use the separately billed OpenAI API, contrary to the explicit
  requirement to use the owner's ChatGPT subscription.
- Upstream n8n PR `n8n-io/n8n#29184` proposes ChatGPT/OpenAI Account device
  authentication for the OpenAI Chat Model, but it remains open and unmerged as
  of this decision. It is not a production dependency.

## Why not Execute Command

The n8n Execute Command node is disabled by default from n8n 2.0 because it
permits arbitrary shell execution. In Docker it runs inside the n8n container,
which would require installing Codex and mounting the reusable ChatGPT auth
cache into the main orchestration process. That expands the blast radius and
weakens the workflow boundary.

## Invocation contract

1. n8n derives a policy ID, model profile, allowed fields, allowed values, and
   redacted context from versioned configuration.
2. n8n hashes and durably records the request before dispatch.
3. The HTTP Request node posts the bounded envelope over the internal Docker
   network to `codex-agent-runner:5090` with a bearer credential.
4. The runner verifies its embedded generated policy contract, exact field and
   value domains, local config/schema hashes, ChatGPT login, policy model
   mapping, and request limits. A narrowed or broadened n8n Data Table row is
   rejected.
5. The runner invokes an exact argument array with no shell: ephemeral session,
   read-only sandbox, rules ignored, code mode disabled, fixed Luna/max or
   Sol/medium model profile, JSONL events, and a fixed JSON output schema.
6. The runner and n8n independently validate identity hashes, proposal fields,
   value constraints, model, reasoning effort, authentication mode, duplicates,
   and protected fields.
7. n8n persists the proposal receipt. Deterministic downstream logic decides
   whether a proposal is accepted, reviewed, or rejected.

## Future simplification gate

Replace the private runner with the native OpenAI Chat Model plus n8n structured
output only after all of these are proven in a stable n8n release:

- ChatGPT/OpenAI Account authentication is officially merged and documented.
- Luna and Sol are selectable with the required reasoning efforts.
- Strict output schemas and proposal identity fields are preserved.
- Subscription authentication refresh survives restart and backup/restore.
- Negative tests prove no fallback to API-key billing.

## Primary sources

- n8n AI Agent: <https://github.com/n8n-io/n8n-docs/blob/main/docs/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent/README.md>
- n8n OpenAI credentials: <https://github.com/n8n-io/n8n-docs/blob/main/docs/integrations/builtin/credentials/openai.md>
- n8n Execute Command: <https://github.com/n8n-io/n8n-docs/blob/main/docs/integrations/builtin/core-nodes/n8n-nodes-base.executecommand/README.md>
- Proposed n8n OpenAI Account authentication: <https://github.com/n8n-io/n8n/pull/29184>
- OpenAI Codex authentication: <https://learn.chatgpt.com/docs/auth>
- OpenAI Codex non-interactive mode: <https://learn.chatgpt.com/docs/non-interactive-mode>
