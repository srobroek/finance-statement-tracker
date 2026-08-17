#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${FINANCE_RUNTIME_DIR:-/opt/stacks/finance-runtime}"
bootstrap_file="${FINANCE_OP_BOOTSTRAP_FILE:-/opt/stacks/finance-runtime/.env.bootstrap}"
template_file="$runtime_dir/.env.tpl"
output_file="$runtime_dir/.env"

if [[ ! -r "$bootstrap_file" ]]; then
  echo "1Password bootstrap file is not readable: $bootstrap_file" >&2
  exit 1
fi
if [[ ! -r "$template_file" ]]; then
  echo "Finance environment template is not readable: $template_file" >&2
  exit 1
fi

set -a
# The FinanceRuntime bootstrap contains only the vault-scoped host service
# account token needed by `op inject`; the rendered file never contains it.
source "$bootstrap_file"
set +a

umask 077
temporary_file="$(mktemp "$runtime_dir/.env.XXXXXX")"
trap 'rm -f "$temporary_file"' EXIT
op inject --force --in-file "$template_file" --out-file "$temporary_file"
chmod 0600 "$temporary_file"
mv -f "$temporary_file" "$output_file"
trap - EXIT
