#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
readonly item_reference="op://FinanceRuntime/Finance Statement Tracker Runtime/finance_n8n_mcp_bearer"
readonly op_bin="${OP_BIN:-op}"
readonly codex_bin="${CODEX_BIN:-codex}"
readonly template_root="${FINANCE_MCP_LAUNCH_TMPDIR:-/dev/shm}"

is_sensitive_variable() {
  case "$1" in
    FINANCE_N8N_MCP_BEARER|OP_SESSION|OP_SESSION_*|OP_CONNECT_TOKEN|OP_SERVICE_ACCOUNT_TOKEN|OP_*TOKEN*|*BEARER*|*TOKEN*|*SECRET*|*PASSWORD*|*API_KEY*|*ACCESS_KEY*|*PRIVATE_KEY*|*CREDENTIAL*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

reject_parent_secrets() {
  local variable
  while IFS='=' read -r variable _; do
    if is_sensitive_variable "${variable}"; then
      echo "Secret-bearing parent variable is not accepted: ${variable}" >&2
      return 1
    fi
  done < <(env)
}

reject_parent_secrets || exit 1
[[ "${1:-}" == "codex" ]] || { echo "The child must be the approved Codex binary" >&2; exit 1; }
shift
[[ -x "$(command -v "${op_bin}" || true)" ]] || { echo "Approved 1Password CLI is unavailable" >&2; exit 1; }
case "${op_bin}" in
  op|op.exe|*/op|*/op.exe) ;;
  *) echo "OP_BIN must identify the approved 1Password CLI" >&2; exit 1 ;;
esac
case "${codex_bin}" in
  codex|*/codex|codex-cli|*/codex-cli) ;;
  *) echo "CODEX_BIN must identify the approved Codex binary" >&2; exit 1 ;;
esac
codex_path="$(command -v "${codex_bin}" || true)"
readonly codex_path
[[ -x "${codex_path}" ]] || { echo "Approved Codex binary is unavailable" >&2; exit 1; }
[[ "$(stat -fc '%T' "${template_root}")" == "tmpfs" ]] || {
  echo "Launcher template root must be tmpfs" >&2
  exit 1
}

run_root="$(mktemp -d "${template_root%/}/finance-mcp-launch.XXXXXX")"
env_file="${run_root}/finance.env"
umask 077
printf 'FINANCE_N8N_MCP_BEARER=%s\n' "${item_reference}" >"${env_file}"
chmod 0600 "${env_file}"
if [[ "$(stat -c '%a' "${env_file}")" != "600" ]]; then
  rm -f -- "${env_file}"
  rmdir -- "${run_root}" 2>/dev/null || true
  echo "Runtime env template must be mode 0600" >&2
  exit 1
fi

child_status=0
# shellcheck disable=SC2329 # invoked indirectly by the EXIT/INT/TERM traps
cleanup() {
  local status=$?
  local session_variable
  local sensitive_variable
  if [[ "${status}" == "0" && "${child_status}" != "0" ]]; then
    status="${child_status}"
  fi
  trap - EXIT INT TERM
  unset FINANCE_N8N_MCP_BEARER OP_SESSION OP_CONNECT_TOKEN OP_SERVICE_ACCOUNT_TOKEN N8N_ENCRYPTION_KEY
  while IFS= read -r session_variable; do
    unset "${session_variable}"
  done < <(compgen -v OP_SESSION_)
  while IFS= read -r sensitive_variable; do
    if is_sensitive_variable "${sensitive_variable}"; then
      unset "${sensitive_variable}"
    fi
  done < <(compgen -v)
  rm -f -- "${env_file}"
  rmdir -- "${run_root}" 2>/dev/null || true
  printf '{"status":"%s","secret":"REDACTED","parent_environment":"SCRUBBED"}\n' \
    "$([[ "${status}" == "0" ]] && echo VERIFIED || echo FAILED)" >&2
  exit "${status}"
}
trap cleanup EXIT INT TERM

# `op run --env-file` resolves the reference in its own short-lived child
# environment. The template contains only an op:// reference and is removed.
set +e
"${op_bin}" run --env-file="${env_file}" -- "${codex_path}" "$@"
child_status=$?
set -e
exit "${child_status}"
