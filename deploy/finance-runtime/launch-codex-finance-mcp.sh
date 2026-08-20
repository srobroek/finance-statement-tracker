#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
readonly item_reference="op://FinanceRuntime/Finance Statement Tracker Runtime/finance_n8n_mcp_bearer"
readonly op_bin="${OP_BIN:-op}"
readonly codex_bin="${CODEX_BIN:-codex}"

if [[ "${FINANCE_N8N_MCP_BEARER+x}" == "x" ]]; then
  echo "Parent FINANCE_N8N_MCP_BEARER must be unset" >&2
  exit 1
fi
[[ "$#" -gt 0 ]] || { echo "An approved Codex child command is required" >&2; exit 1; }
[[ -x "$(command -v "${op_bin}" || true)" ]] || { echo "Approved 1Password CLI is unavailable" >&2; exit 1; }
if [[ "${1}" == "codex" ]]; then
  shift
  set -- "${codex_bin}" "$@"
fi

child_status=0
# shellcheck disable=SC2329 # invoked indirectly by the EXIT/INT/TERM traps
cleanup() {
  local status="${child_status}"
  local session_variable
  trap - EXIT INT TERM
  unset FINANCE_N8N_MCP_BEARER OP_SESSION N8N_ENCRYPTION_KEY
  while IFS= read -r session_variable; do
    unset "${session_variable}"
  done < <(compgen -v OP_SESSION_)
  export FINANCE_N8N_MCP_BEARER=""
  unset FINANCE_N8N_MCP_BEARER
  printf '{"status":"%s","secret":"REDACTED","parent_environment":"SCRUBBED"}\n' \
    "$([[ "${status}" == "0" ]] && echo VERIFIED || echo FAILED)" >&2
  exit "${status}"
}
trap cleanup EXIT INT TERM

# `op run --env` resolves the reference in its own short-lived child environment.
# The reference is safe to place in argv; the resolved value is never there.
set +e
"${op_bin}" run --env "FINANCE_N8N_MCP_BEARER=${item_reference}" -- "$@"
child_status=$?
set -e
exit "${child_status}"
