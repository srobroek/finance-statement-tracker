#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${FINANCE_RUNTIME_DIR:-/opt/stacks/finance-runtime}"
bootstrap_file="${FINANCE_OP_BOOTSTRAP_FILE:-/opt/stacks/finance-runtime/.env.bootstrap}"
template_file="$runtime_dir/.env.tpl"
output_file="$runtime_dir/.env"

if [[ ! -f "$bootstrap_file" || -L "$bootstrap_file" || ! -r "$bootstrap_file" ]]; then
  echo "1Password bootstrap file is not a readable regular file: $bootstrap_file" >&2
  exit 1
fi
bootstrap_owner="$(stat -c '%u' -- "$bootstrap_file")"
bootstrap_mode="$(stat -c '%a' -- "$bootstrap_file")"
if [[ "$bootstrap_owner" != "$(id -u)" || "$bootstrap_mode" != 600 ]]; then
  echo "1Password bootstrap file has unsafe ownership or mode: $bootstrap_file" >&2
  exit 1
fi
if [[ ! -r "$template_file" ]]; then
  echo "Finance environment template is not readable: $template_file" >&2
  exit 1
fi

mapfile -t bootstrap_lines < "$bootstrap_file"
if [[ "${#bootstrap_lines[@]}" != 1 || "${bootstrap_lines[0]}" == *$'\r'* ]]; then
  echo "1Password bootstrap file must contain exactly one assignment: $bootstrap_file" >&2
  exit 1
fi

bootstrap_line="${bootstrap_lines[0]}"
if [[ ! "$bootstrap_line" =~ ^[[:blank:]]*OP_SERVICE_ACCOUNT_TOKEN[[:blank:]]*=[[:blank:]]*(.*)$ ]]; then
  echo "1Password bootstrap file contains an unsupported assignment: $bootstrap_file" >&2
  exit 1
fi
bootstrap_value="${BASH_REMATCH[1]}"
while [[ "$bootstrap_value" == *[[:blank:]] ]]; do
  bootstrap_value="${bootstrap_value::-1}"
done

case "$bootstrap_value" in
  \'*\')
    bootstrap_token="${bootstrap_value:1:${#bootstrap_value}-2}"
    [[ "$bootstrap_token" != *"'"* ]] || {
      echo "1Password bootstrap file has malformed quoting: $bootstrap_file" >&2
      exit 1
    }
    ;;
  \"*\")
    bootstrap_token="${bootstrap_value:1:${#bootstrap_value}-2}"
    [[ "$bootstrap_token" != *'"'* ]] || {
      echo "1Password bootstrap file has malformed quoting: $bootstrap_file" >&2
      exit 1
    }
    ;;
  *)
    bootstrap_token="$bootstrap_value"
    ;;
esac

if [[ ! "$bootstrap_token" =~ ^[A-Za-z0-9._:/+,%@=-]+$ ]]; then
  echo "1Password bootstrap file contains an unsafe token value: $bootstrap_file" >&2
  exit 1
fi

# The FinanceRuntime bootstrap contains only the vault-scoped host service
# account token needed by `op inject`; the rendered file never contains it.
export OP_SERVICE_ACCOUNT_TOKEN="$bootstrap_token"

umask 077
temporary_file="$(mktemp "$runtime_dir/.env.XXXXXX")"
trap 'rm -f "$temporary_file"' EXIT
op inject --force --in-file "$template_file" --out-file "$temporary_file"
chmod 0600 "$temporary_file"
mv -f "$temporary_file" "$output_file"
trap - EXIT
