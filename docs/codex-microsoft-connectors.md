# Microsoft connectors for Codex

When Codex needs direct read-only access to Microsoft 365, use OpenAI's Outlook
Email and SharePoint plugins. The plugins do not use n8n's native Outlook and
OneDrive credentials.

The Codex plugins need no project-specific Entra application. The user grants
each OpenAI app access through the normal OpenAI connection flow. OpenAI stores
the app OAuth tokens server-side. The local `auth.json` only authenticates
Codex to the user's OpenAI account. A fresh `codex exec` process can reuse the
app grants without receiving a Microsoft access token or refresh token.

The grant is durable, not permanent. OpenAI refreshes access in the background,
but the user or administrator can revoke consent, conditional-access policy can
change, and Microsoft can invalidate the session. A revoked connection requires
the user to reconnect it. Do not use app passwords. Microsoft Graph uses OAuth,
and app passwords do not provide a safer refresh mechanism.

OpenAI documents the app connection model and server-side token protection in
[Apps in ChatGPT](https://help.openai.com/en/articles/11487775-connectors-in)
and [OAuth security for apps](https://help.openai.com/en/articles/11509118-admin-controls-security-and-compliance-in-connectors-enterprise-edu-and-team).
Microsoft documents refresh-token behavior in
[Refresh tokens in the Microsoft identity platform](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens).

## One-time user setup

1. Connect Outlook Email and SharePoint in the OpenAI app settings and complete
   Microsoft consent.
2. Sign the trusted host into Codex with ChatGPT authentication:

   ```text
   codex login --device-auth
   ```

3. Install only the required curated plugins in the dedicated Codex home:

   ```text
   codex plugin add outlook-email@openai-curated --json
   codex plugin add sharepoint@openai-curated --json
   ```

4. Confirm `codex plugin list --json` reports exactly those two plugins enabled.

Do not copy Microsoft tokens into any of these locations:

- `.env`.
- n8n.
- Git.
- Logs.
- container images.

Treat the Codex home as a credential store. Keep `auth.json` at mode `0600`.
Keep the directory writable for OpenAI login rotation. Mount it only into the
bounded runner.

## Non-interactive container proof

The proof performs these actions in a fresh Debian container:

1. Install the pinned Codex CLI.
2. Install only the two curated plugins.
3. Call each plugin's read-only `get_profile` tool.
4. Check that the JSONL stream contains both completed tool events.

If rootless Podman cannot create its socket from the calling sandbox, start the
bounded proof service from the trusted host shell:

```text
services/codex-agent-runner/scripts/start-podman-proof-service.sh
```

Run the proof against that socket:

```text
PODMAN_PROOF_SOCKET=/tmp/sjors-podman-codex/podman.sock \
  services/codex-agent-runner/scripts/probe-microsoft-connectors-container.sh
```

The successful output ends with these lines:

```text
PROBE_PLUGIN_READY=true
PROBE_TOOL_EVIDENCE=true
CONTAINER_CONNECTOR_OK=true
```

The script copies the Codex login into a mode-`0600` temporary directory. Its
exit trap force-removes the proof container and deletes the temporary directory
on success or failure. The script requires the curated marketplace checkout to
match its recorded commit. It rejects an image without Git.

The proof confirms reuse by an ordinary non-interactive Codex process. The
production proposal runner still launches Codex with user configuration and
tools disabled. Enabling mail enrichment there is a separate security change.
That change needs:

- A dedicated minimal Codex home.
- Exact tool allowlists.
- Bounded search inputs.
- Output-schema validation.
- an independently reviewed promotion gate.

## n8n authentication boundary

n8n continues to use its encrypted native Microsoft Outlook and OneDrive
credentials for deterministic acquisition and evidence storage. Those
credentials are not exported to Codex, and the OpenAI app grants are not copied
into n8n.

Every Outlook and OneDrive node must stop on error and route failures to the
shared operations error workflow. The workflow classifies these failures as
unavailable authentication:

- Microsoft `401` or `403` responses.
- OAuth errors.
- Missing credentials.
- expired or revoked tokens.

The shared error workflow writes this fixed receipt:

```text
provider_code=MICROSOFT_GRAPH
error_class=MICROSOFT_AUTH_UNAVAILABLE
error_message_redacted=Microsoft authentication unavailable; reconnect the credential.
```

Stop before any archive, enrichment, cursor, or ledger mutation. Keep provider
responses, email addresses, and token material out of the receipt.

## Troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| `enabled=none` | The plugins were not installed in this Codex home. | Run both `codex plugin add` commands. |
| `local curated marketplace sha is not available` | The curated checkout or `.tmp/plugins.sha` is missing. | Recreate the dedicated Codex home from a trusted installed Codex profile. |
| `git_unavailable` | The container image cannot validate marketplace provenance. | Use `node:24-bookworm`, not the slim image. |
| `codex_exec` or a failed profile call | OpenAI login or Microsoft app authorization is unavailable. | Check `codex login status`, then reconnect the affected OpenAI app interactively. |
| n8n emits `MICROSOFT_AUTH_UNAVAILABLE` | A native n8n Microsoft credential cannot authenticate. | Reconnect that n8n credential before replaying the failed execution. |
