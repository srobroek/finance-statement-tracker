#!/bin/sh
set -eu

immutable=/opt/finance-n8n/custom-extensions/n8n-nodes-finance
mutable_parent=/home/node/.n8n/nodes/node_modules
mutable_link=${mutable_parent}/n8n-nodes-finance
prodex_immutable=/opt/finance-n8n/community-extensions/node_modules/n8n-nodes-prodex
prodex_link=${mutable_parent}/n8n-nodes-prodex

node /opt/finance-n8n/verify-immutable-extension.cjs >/dev/null
mkdir -p "${mutable_parent}" /tmp/finance-ai
chmod 0700 /tmp/finance-ai

ensure_link() {
  link=$1
  target=$2
  if [ -L "${link}" ]; then
    [ "$(readlink "${link}")" = "${target}" ] || {
      echo "FINANCE_EXTENSION_MUTABLE_PATH_REJECTED: unexpected symlink target" >&2
      exit 1
    }
    [ -d "${link}" ] || {
      echo "FINANCE_EXTENSION_MUTABLE_PATH_REJECTED: broken symlink" >&2
      exit 1
    }
  elif [ -e "${link}" ]; then
    echo "FINANCE_EXTENSION_MUTABLE_PATH_REJECTED: expected absent path or exact symlink" >&2
    exit 1
  else
    ln -s "${target}" "${link}"
  fi
}

ensure_link "${mutable_link}" "${immutable}"
ensure_link "${prodex_link}" "${prodex_immutable}"

for forbidden in OPENAI_API_KEY CODEX_API_KEY CODEX_ACCESS_TOKEN; do
  eval "present=\${${forbidden}+yes}"
  [ "${present:-}" != yes ] || {
    echo "COMMUNITY_AI_API_KEY_FORBIDDEN:${forbidden}" >&2
    exit 1
  }
done

exec /docker-entrypoint.sh "$@"
