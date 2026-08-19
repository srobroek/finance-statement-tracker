#!/bin/sh
set -eu

immutable=/opt/finance-n8n/custom-extensions/n8n-nodes-finance
mutable_parent=/home/node/.n8n/nodes/node_modules
mutable_link=${mutable_parent}/n8n-nodes-finance

node /opt/finance-n8n/verify-immutable-extension.cjs >/dev/null
mkdir -p "${mutable_parent}"
if [ -L "${mutable_link}" ]; then
  [ "$(readlink "${mutable_link}")" = "${immutable}" ] || {
    echo "FINANCE_EXTENSION_MUTABLE_PATH_REJECTED: unexpected symlink target" >&2
    exit 1
  }
  [ -d "${mutable_link}" ] || {
    echo "FINANCE_EXTENSION_MUTABLE_PATH_REJECTED: broken symlink" >&2
    exit 1
  }
elif [ -e "${mutable_link}" ]; then
  echo "FINANCE_EXTENSION_MUTABLE_PATH_REJECTED: expected absent path or exact symlink" >&2
  exit 1
else
  ln -s "${immutable}" "${mutable_link}"
fi

exec /docker-entrypoint.sh "$@"
