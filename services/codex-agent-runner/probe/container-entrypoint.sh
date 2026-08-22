#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' 'PROBE_STAGE=codex_install'
npm install --global "@openai/codex@${CODEX_PROBE_VERSION}" >/dev/null

printf '%s\n' 'PROBE_STAGE=marketplace_provenance'
command -v git >/dev/null || {
  printf '%s\n' 'PROBE_FAILURE=git_unavailable'
  exit 30
}
git -C /home/node/.codex/.tmp/plugins rev-parse --verify HEAD >/dev/null || {
  printf '%s\n' 'PROBE_FAILURE=marketplace_sha'
  exit 30
}
if [[ "$(git -C /home/node/.codex/.tmp/plugins rev-parse HEAD)" != "$(tr -d '\r\n' < /home/node/.codex/.tmp/plugins.sha)" ]]; then
  printf '%s\n' 'PROBE_FAILURE=marketplace_sha_mismatch'
  exit 30
fi

printf '%s\n' 'PROBE_STAGE=plugin_install'
codex plugin add outlook-email@openai-curated --json >/tmp/outlook-plugin.json
codex plugin add sharepoint@openai-curated --json >/tmp/sharepoint-plugin.json

printf '%s\n' 'PROBE_STAGE=plugin_discovery'
codex plugin list --json >/tmp/plugins.json
node - <<'NODE'
const plugins = require('/tmp/plugins.json');
const enabled = new Set(plugins.installed.filter((plugin) => plugin.enabled).map((plugin) => plugin.name));
if (!(enabled.has('outlook-email') && enabled.has('sharepoint') && enabled.size === 2)) {
  console.error(`PROBE_FAILURE=plugin_discovery enabled=${[...enabled].sort().join(',') || 'none'}`);
  process.exit(31);
}
console.log('PROBE_PLUGIN_READY=true');
NODE

printf '%s\n' 'PROBE_STAGE=connector_calls'
if ! codex exec \
  --ephemeral \
  --skip-git-repo-check \
  --ignore-rules \
  --sandbox read-only \
  --json \
  --output-last-message /tmp/final.txt \
  "${CODEX_PROBE_PROMPT}" >/tmp/events.jsonl; then
  printf '%s\n' 'PROBE_FAILURE=codex_exec'
  exit 32
fi

if [[ "$(tr -d '\r\n' </tmp/final.txt)" != 'CONTAINER_CONNECTOR_OK=true' ]]; then
  printf '%s\n' 'PROBE_FAILURE=final_response'
  exit 33
fi

node - <<'NODE'
const fs = require('fs');
const tools = new Set(
  fs.readFileSync('/tmp/events.jsonl', 'utf8')
    .trim()
    .split(/\n/)
    .filter(Boolean)
    .map(JSON.parse)
    .filter((event) =>
      event.item?.type === 'mcp_tool_call'
      && event.item.server === 'codex_apps'
      && event.item.status === 'completed')
    .map((event) => event.item.tool),
);
const outlook = tools.has('microsoft_outlook_email.get_profile');
const sharepoint = tools.has('microsoft_sharepoint.get_profile');
if (!(outlook && sharepoint)) {
  console.error(`PROBE_FAILURE=tool_evidence outlook=${outlook} sharepoint=${sharepoint}`);
  process.exit(34);
}
console.log('PROBE_TOOL_EVIDENCE=true');
NODE

printf '%s\n' 'CONTAINER_CONNECTOR_OK=true'
