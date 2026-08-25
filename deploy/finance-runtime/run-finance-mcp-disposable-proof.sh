#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
readonly workflow_id="10000000-0000-4000-8000-000000000015"
readonly workflow_path="finance-operations-v1"
readonly required_ack="ACTIVATE_W15_ONLY"
readonly runtime_scope="${FINANCE_MCP_RUNTIME_SCOPE:-}"
readonly workflow_scope="${FINANCE_MCP_WORKFLOW_SCOPE:-}"
readonly mutation_gate="${FINANCE_MCP_N8N_MUTATION_GATE:-}"
readonly probe_gate="${FINANCE_MCP_PROBE_GATE:-}"
readonly tmp_root="${FINANCE_MCP_PROOF_TMPDIR:-/dev/shm}"
readonly simulated="${FINANCE_MCP_SIMULATED:-false}"

[[ "${FINANCE_N8N_MCP_DISPOSABLE_ACK:-}" == "${required_ack}" ]] || {
  echo "FINANCE_N8N_MCP_DISPOSABLE_ACK=${required_ack} is required" >&2
  exit 1
}
[[ "${FINANCE_MCP_BINDER_VERIFIED:-}" == "VERIFIED" ]] || {
  echo "Inactive W15 binder verification is required before activation" >&2
  exit 1
}
[[ "${runtime_scope}" == "disposable" && "${workflow_scope}" == "W15" ]] || {
  echo "Only disposable W15 scope is accepted" >&2
  exit 1
}
[[ "${N8N_PUBLIC_API_DISABLED:-true}" == "true" ]] || {
  echo "Public n8n API mutation is forbidden" >&2
  exit 1
}
[[ -n "${mutation_gate}" && -x "${mutation_gate}" && ! -d "${mutation_gate}" ]] || {
  echo "Pinned n8n mutation gate is required" >&2
  exit 1
}
[[ -n "${probe_gate}" && -x "${probe_gate}" && ! -d "${probe_gate}" ]] || {
  echo "External MCP probe gate is required" >&2
  exit 1
}

run_root="$(mktemp -d "${tmp_root%/}/finance-mcp-proof.XXXXXX")"
activation_started=false
cleanup_verified=false

run_gate() {
  local action="$1"
  FINANCE_MCP_ACTION="${action}" \
    FINANCE_MCP_WORKFLOW_ID="${workflow_id}" \
    FINANCE_MCP_WORKFLOW_PATH="${workflow_path}" \
    FINANCE_MCP_RUNTIME_SCOPE="disposable" \
    FINANCE_MCP_WORKFLOW_SCOPE="W15" \
    FINANCE_MCP_OUTPUT="${run_root}/${action}.status" \
    "${mutation_gate}" >"${run_root}/${action}.stdout" 2>"${run_root}/${action}.stderr"
}

run_probe() {
  local probe_case="$1" expected_status="$2" observed_status=0
  set +e
  FINANCE_MCP_PROBE_CASE="${probe_case}" \
    FINANCE_MCP_WORKFLOW_PATH="${workflow_path}" \
    FINANCE_MCP_RUNTIME_SCOPE="disposable" \
    "${probe_gate}" >"${run_root}/probe-${probe_case}.stdout" 2>"${run_root}/probe-${probe_case}.stderr"
  observed_status=$?
  set -e
  [[ "${observed_status}" == "${expected_status}" ]] || {
    echo "MCP boundary probe failed: ${probe_case}" >&2
    return 1
  }
}

verify_clean_readback() {
  python3 - "${run_root}/readback-clean.status" <<'PY'
import json
import pathlib
import sys

expected = {
    "status": "CLEAN",
    "scope": "W15_DISPOSABLE_ONLY",
    "counts": {"credentials": 0, "owners": 0, "workflows": 0, "webhooks": 0, "executions": 0},
    "idsRecorded": False,
    "secretValueRecorded": False,
}
try:
    observed = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError) as error:
    raise SystemExit(f"teardown readback is missing or invalid: {error}")
if observed != expected:
    raise SystemExit("teardown readback did not prove the exact zero boundary")
PY
}

cleanup() {
  local status=$?
  local teardown_ok=true
  trap - EXIT INT TERM
  set +e
  if [[ "${activation_started}" == "true" ]]; then
    if ! run_gate deactivate; then teardown_ok=false; status=1; fi
    if ! run_gate unpublish; then teardown_ok=false; status=1; fi
    if ! run_gate remove-webhook; then teardown_ok=false; status=1; fi
    if ! run_gate remove-disposable-rows; then teardown_ok=false; status=1; fi
    if ! run_gate readback-clean || ! verify_clean_readback; then
      teardown_ok=false
      status=1
    fi
    if [[ "${teardown_ok}" == "true" ]]; then cleanup_verified=true; fi
  fi
  if ! rm -f -- "${run_root}"/*; then status=1; fi
  if ! rmdir -- "${run_root}" 2>/dev/null; then status=1; fi
  unset FINANCE_MCP_PROBE_CASE FINANCE_MCP_ACTION FINANCE_MCP_OUTPUT
  if [[ "${cleanup_verified}" != "true" && "${status}" == "0" ]]; then
    status=1
  fi
  if [[ "${status}" == "0" ]]; then
    if [[ "${simulated}" == "true" ]]; then
      printf '%s\n' '{"status":"SIMULATED","runtimeEvidence":false,"scope":"W15_SPEC_ONLY","path_match":true,"mcp_auth":"accepted","negative_probes":"rejected","cleanup":"READBACK_VERIFIED","values":"REDACTED","ids":"REDACTED"}'
    else
      printf '%s\n' '{"status":"VERIFIED","runtimeEvidence":true,"scope":"W15_DISPOSABLE_ONLY","path_match":true,"mcp_auth":"accepted","negative_probes":"rejected","cleanup":"READBACK_VERIFIED","values":"REDACTED","ids":"REDACTED"}'
    fi
  fi
  exit "${status}"
}
trap cleanup EXIT INT TERM

activation_started=true
run_gate activate
run_gate publish
run_gate readback-active
run_probe positive 0
run_probe wrong-mcp-secret 1
run_probe wrong-cloudflare-authority 1
run_probe runner-bearer 1
