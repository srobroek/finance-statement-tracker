#!/bin/sh
set -eu

[ "${FINANCE_WF23_ORPHAN_PROCESS_PROOF_ACK:-}" = "PROVE_WF23_EXECUTOR_ABSENT_READ_ONLY" ] || exit 2

# ActiveExecutions is process-local in n8n. WF23 was launched by `docker exec
# ... node -`, so only that short-lived CLI process could own its in-memory
# ActiveExecutions entry. Inspect /proc without printing any command line and
# prove that the exact stdin Node transport no longer exists. This helper does
# not initialize n8n, its database, a workflow, a task runner, or a provider.
found=0
for cmdline in /proc/[0-9]*/cmdline; do
  [ -r "${cmdline}" ] || continue
  first="$(tr '\0' '\n' < "${cmdline}" | sed -n '1p')"
  second="$(tr '\0' '\n' < "${cmdline}" | sed -n '2p')"
  case "${first}" in
    node|*/node) [ "${second}" = "-" ] && found=$((found + 1)) ;;
  esac
done
[ "${found}" = "0" ] || exit 1
printf '%s\n' 'WF23 orphan inactivity verified:{"schema_version":1,"status":"VERIFIED","scope":"WF23_PROCESS_LOCAL_ACTIVE_EXECUTIONS_ABSENCE","stdin_node_processes":0,"n8n_initialized":false,"database_initialized":false,"workflow_loaded":false,"provider_calls":false,"secret_values_recorded":false}'
