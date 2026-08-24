#!/usr/bin/env bash
set -euo pipefail

[[ "${FINANCE_MICROSOFT_OAUTH_PROOF_ACK:-}" == "RUN_TRANSIENT_WF23_ONLY" ]] || { echo "Set FINANCE_MICROSOFT_OAUTH_PROOF_ACK=RUN_TRANSIENT_WF23_ONLY" >&2; exit 1; }
[[ "$(id -u)" != "0" ]] || { echo "Run as the rootless stack owner, not root" >&2; exit 1; }

readonly source_sha256="879d637a5ad71e5a35ec8a90001d33c00067e05115a3bcdd28a80a9191c7224e"
readonly prior_promoted_commit="00491aae2ab43c486f3a9b4a62ce3ba5e63032f6"
readonly expected_project="${FINANCE_N8N_COMPOSE_PROJECT:-}"
readonly expected_project_id="${N8N_FINANCE_PROJECT_ID:-}"
readonly finance_repo="${FINANCE_REPOSITORY_DIR:-}"
readonly stack_dir="${FINANCE_N8N_STACK_DIR:-}"
readonly compose_file_input="${FINANCE_N8N_COMPOSE_FILE:-}"
readonly env_file="${FINANCE_N8N_DEPLOYMENT_ENV_FILE:-}"
readonly receipt_root="${FINANCE_N8N_RECEIPT_DIR:-}"
readonly recovery_receipt="${FINANCE_N8N_RECOVERY_RECEIPT:-}"
readonly n8n_service="${FINANCE_N8N_N8N_SERVICE:-n8n}"
readonly task_runners_service="${FINANCE_N8N_TASK_RUNNERS_SERVICE:-task-runners}"
readonly workflow_id="10000000-0000-4000-8000-000000000023"
readonly companion_setup_id="10000000-0000-4000-8000-000000000022"
readonly workflow_name="Finance · Microsoft OAuth Refresh Proof · Manual Read Only"
readonly folder_id="f1000000-0000-4000-8000-000000000191"
readonly folder_name="Shared"
readonly global_folder_id="f1000000-0000-4000-8000-000000000190"
readonly preflight_mode="${FINANCE_MICROSOFT_OAUTH_PROOF_PREFLIGHT:-false}"
readonly expected_finance_commit="${FINANCE_REPOSITORY_COMMIT:-}"
readonly expected_orchestrator_commit="${ORCHESTRATOR_REPOSITORY_COMMIT:-}"
readonly runner_dir="${finance_repo}/integrations/n8n/setup-workflows/runner"
readonly source_file="${finance_repo}/integrations/n8n/setup-workflows/23-microsoft-oauth-refresh-proof.json"

[[ "${expected_project}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{1,62}$ ]] || { echo "FINANCE_N8N_COMPOSE_PROJECT is required and invalid" >&2; exit 1; }
[[ -n "${expected_project_id}" ]] || { echo "N8N_FINANCE_PROJECT_ID is required" >&2; exit 1; }
[[ "${expected_project_id}" =~ ^[A-Za-z0-9_-]{8,64}$ ]] || { echo "N8N_FINANCE_PROJECT_ID is invalid" >&2; exit 1; }
[[ "${expected_finance_commit}" =~ ^[0-9a-f]{40}$ ]] || { echo "Exact finance commit required" >&2; exit 1; }
[[ "${expected_orchestrator_commit}" =~ ^[0-9a-f]{40}$ ]] || { echo "Exact orchestrator commit required" >&2; exit 1; }
[[ "${preflight_mode}" == "true" || "${preflight_mode}" == "false" ]] || { echo "FINANCE_MICROSOFT_OAUTH_PROOF_PREFLIGHT must be true or false" >&2; exit 1; }
[[ -d "${finance_repo}/.git" && -d "${stack_dir}/.git" ]] || { echo "Required source repositories are missing" >&2; exit 1; }
[[ -f "${env_file}" && ! -L "${env_file}" && "$(stat -c '%a' "${env_file}")" == "600" ]] || { echo "Mode-0600 deployed runtime environment required" >&2; exit 1; }
[[ -f "${source_file}" && ! -L "${source_file}" ]] || { echo "Regular WF23 source required" >&2; exit 1; }
[[ -n "${compose_file_input}" ]] || { echo "FINANCE_N8N_COMPOSE_FILE is required" >&2; exit 1; }
[[ -n "${recovery_receipt}" && "${recovery_receipt}" = /* ]] || { echo "FINANCE_N8N_RECOVERY_RECEIPT must be an absolute path" >&2; exit 1; }
[[ -f "${recovery_receipt}" && ! -L "${recovery_receipt}" && "$(stat -c '%a' "${recovery_receipt}")" == "600" ]] || { echo "Mode-0600 Postgres recovery receipt required" >&2; exit 1; }

compose_file="${compose_file_input}"
[[ "${compose_file}" = /* ]] || compose_file="${stack_dir}/${compose_file}"
[[ -f "${compose_file}" && ! -L "${compose_file}" ]] || { echo "Deployed Compose file is missing" >&2; exit 1; }
[[ -n "${receipt_root}" && "${receipt_root}" = /* ]] || { echo "FINANCE_N8N_RECEIPT_DIR must be an absolute path" >&2; exit 1; }

[[ -z "$(git -C "${finance_repo}" status --porcelain)" ]] || { echo "Finance repository must be completely clean" >&2; exit 1; }
[[ -z "$(git -C "${stack_dir}" status --porcelain --untracked-files=no)" ]] || { echo "Orchestrator repository has tracked changes" >&2; exit 1; }
[[ "$(git -C "${finance_repo}" rev-parse HEAD)" == "${expected_finance_commit}" ]] || { echo "Finance commit mismatch" >&2; exit 1; }
[[ "$(git -C "${stack_dir}" rev-parse HEAD)" == "${expected_orchestrator_commit}" ]] || { echo "Orchestrator commit mismatch" >&2; exit 1; }
git -C "${finance_repo}" merge-base --is-ancestor "${prior_promoted_commit}" "${expected_finance_commit}" || { echo "Finance commit does not descend from promoted corpus" >&2; exit 1; }
[[ "$(sha256sum "${source_file}" | awk '{print $1}')" == "${source_sha256}" ]] || { echo "WF23 source SHA-256 mismatch" >&2; exit 1; }
for helper in bind-microsoft-oauth-refresh-proof.py validate_microsoft_oauth_refresh_evidence.py build_microsoft_oauth_failure_receipt.py parse_n8n_redacted_wrapper_output.py parse_wf23_execution_output.py n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs n8n-cli-wf23-direct-transport-probe.cjs n8n-cli-microsoft-oauth-metadata-readback.cjs n8n-cli-finance-data-table-digest.cjs n8n-cli-remove-transient-microsoft-oauth-refresh-proof.cjs; do
  [[ -f "${runner_dir}/${helper}" && ! -L "${runner_dir}/${helper}" ]] || { echo "Reviewed WF23 runner helper missing" >&2; exit 1; }
done

cd "${stack_dir}"
export COMPOSE_PROJECT_NAME="${expected_project}"
export COMPOSE_ENV_FILES="${env_file}"
unset N8N_ENCRYPTION_KEY
compose=(docker compose --project-name "${expected_project}" --file "${compose_file}" --env-file "${env_file}")
install -d -m 0700 "${receipt_root}"

container_for_service() {
  local service="$1" ids
  ids="$("${compose[@]}" ps -q "${service}")"
  [[ "${ids}" =~ ^[0-9a-f]{12,64}$ ]] || return 1
  [[ "$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}' "${ids}")" == "${expected_project}|${service}" ]] || return 1
  printf '%s' "${ids}"
}

postgres_container_from_recovery_receipt() {
  python3 - "${recovery_receipt}" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        receipt = json.load(stream)
except (OSError, json.JSONDecodeError):
    raise SystemExit("Postgres recovery receipt is unreadable")

if receipt.get("schema_version") != 1 or receipt.get("purpose") != "N8N_RECOVERY_PRESTATE_RECEIPT_V1":
    raise SystemExit("Postgres recovery receipt schema mismatch")
container_id = receipt.get("postgres", {}).get("container_id")
if not isinstance(container_id, str) or not re.fullmatch(r"[0-9a-f]{64}", container_id):
    raise SystemExit("Postgres recovery receipt identity is invalid")
print(container_id)
PY
}

health_check() {
  local container="$1"
  [[ "$(docker inspect -f '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container}")" == "true|healthy" ]]
}

n8n_container="$(container_for_service "${n8n_service}")" || { echo "Deployed n8n container resolution failed" >&2; exit 1; }
task_runners_container="$(container_for_service "${task_runners_service}")" || { echo "Deployed task-runners container resolution failed" >&2; exit 1; }
postgres_container="$(postgres_container_from_recovery_receipt)" || { echo "Deployed Postgres recovery identity unavailable" >&2; exit 1; }
health_check "${n8n_container}" || { echo "Deployed n8n health failed" >&2; exit 1; }
[[ "$(docker inspect -f '{{.Id}}|{{.State.Running}}' "${postgres_container}")" == "${postgres_container}|true" ]] || { echo "Recovered Postgres container identity or state mismatch" >&2; exit 1; }
[[ "$(docker inspect -f '{{.State.Running}}' "${task_runners_container}")" == "true" ]] || { echo "Deployed task-runners container is not running" >&2; exit 1; }

n8n_runtime_env="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${n8n_container}")"
postgres_user="${N8N_POSTGRES_USER:-$(awk -F= '$1 == "DB_POSTGRESDB_USER" { print substr($0, index($0, "=") + 1); exit }' <<<"${n8n_runtime_env}")}"
postgres_database="${N8N_POSTGRES_DATABASE:-$(awk -F= '$1 == "DB_POSTGRESDB_DATABASE" { print substr($0, index($0, "=") + 1); exit }' <<<"${n8n_runtime_env}")}"
[[ "${postgres_user}" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ && "${postgres_database}" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || { echo "Deployed Postgres database identity is invalid" >&2; exit 1; }
docker exec "${postgres_container}" pg_isready -U "${postgres_user}" -d "${postgres_database}" >/dev/null || { echo "Recovered Postgres readiness failed" >&2; exit 1; }
[[ "$(docker exec -i "${postgres_container}" psql -v ON_ERROR_STOP=1 -At -U "${postgres_user}" -d "${postgres_database}" -c "select current_database()||'|'||current_user;")" == "${postgres_database}|${postgres_user}" ]] || { echo "Recovered Postgres database identity mismatch" >&2; exit 1; }

direct_transport_probe() {
  local raw expected
  expected='WF23 direct transport probe verified:{"schema_version":1,"status":"VERIFIED","scope":"DIRECT_EXECUTE_INSTANCE_TRANSPORT","execute_instance_resolved":true,"instance_log_override_invoked":true,"workflow_loaded":false,"workflow_executed":false,"provider_calls":false,"database_initialized":false,"raw_irun_persisted":false,"provider_response_logged":false,"secret_values_recorded":false}'
  raw="$(timeout --foreground --signal=TERM --kill-after=30s 120s docker exec -i -e FINANCE_WF23_TRANSPORT_PROBE_ACK=READ_ONLY_DIRECT_EXECUTE_INSTANCE "${n8n_container}" node - < "${runner_dir}/n8n-cli-wf23-direct-transport-probe.cjs" 2>/dev/null | head -c 4097)" || return 1
  [[ "${raw}" == "${expected}" ]]
}

direct_transport_probe || { echo "WF23 direct execution transport probe failed before metadata/provider access" >&2; exit 1; }

task_runner_control_preflight() {
  timeout --foreground --signal=TERM --kill-after=5s 15s docker exec "${n8n_container}" node -e \
    'if (process.env.N8N_RUNNERS_MODE !== "external" || process.env.N8N_RUNNERS_BROKER_LISTEN_ADDRESS !== "0.0.0.0" || !process.env.N8N_RUNNERS_AUTH_TOKEN) process.exit(1); fetch("http://127.0.0.1:5679/healthz").then(response => { if (!response.ok) process.exit(1); }).catch(() => process.exit(1));' \
    >/dev/null 2>&1 || return 1
  timeout --foreground --signal=TERM --kill-after=5s 15s docker exec "${task_runners_container}" node -e \
    'fetch("http://127.0.0.1:5680/healthz").then(response => { if (!response.ok) process.exit(1); }).catch(() => process.exit(1));' \
    >/dev/null 2>&1 || return 1
}

task_runner_control_preflight || { echo "Deployed n8n/task-runner control path unavailable" >&2; exit 1; }

psql_scalar() { docker exec -i "${postgres_container}" psql -v ON_ERROR_STOP=1 -At -U "${postgres_user}" -d "${postgres_database}" -c "$1"; }
project_state() { psql_scalar "select count(*)||'|'||count(*) filter (where w.active)||'|'||count(*) filter (where w.\"activeVersionId\" is not null) from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id where s.\"projectId\"='${expected_project_id}';"; }
mapped_count() { psql_scalar "select count(*) from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id join folder f on f.id=w.\"parentFolderId\" where s.\"projectId\"='${expected_project_id}' and f.\"projectId\"='${expected_project_id}';"; }
tag_edge_count() { psql_scalar "select count(*) from workflows_tags wt join shared_workflow s on s.\"workflowId\"=wt.\"workflowId\" join tag_entity t on t.id=wt.\"tagId\" where s.\"projectId\"='${expected_project_id}' and t.name in ('finance','setup-required','inactive');"; }
folder_hierarchy() { psql_scalar "select count(*) from folder shared join folder global_folder on global_folder.id=shared.\"parentFolderId\" where shared.id='${folder_id}' and shared.name='${folder_name}' and shared.\"projectId\"='${expected_project_id}' and global_folder.id='${global_folder_id}' and global_folder.name='Global' and global_folder.\"parentFolderId\" is null and global_folder.\"projectId\"='${expected_project_id}';"; }
setup_id_count() { psql_scalar "select count(*) from workflow_entity where id in ('${workflow_id}','${companion_setup_id}');"; }
wf23_execution_count() { psql_scalar "select count(*) from execution_entity where \"workflowId\"='${workflow_id}';"; }
wf23_history_count() { psql_scalar "select count(*) from workflow_history where \"workflowId\"='${workflow_id}';"; }
baseline_digest() {
  psql_scalar "select line from (select 'W|'||w.id||'|'||w.name||'|'||w.active||'|'||coalesce(w.\"activeVersionId\",'')||'|'||coalesce(w.\"parentFolderId\",'')||'|'||w.nodes::text||'|'||w.connections::text||'|'||w.settings::text||'|'||coalesce(w.meta::text,'null')||'|'||coalesce(w.\"pinData\"::text,'null') as line from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id where s.\"projectId\"='${expected_project_id}' union all select 'S|'||s.\"workflowId\"||'|'||s.\"projectId\"||'|'||s.role from shared_workflow s where s.\"projectId\"='${expected_project_id}' union all select 'T|'||wt.\"workflowId\"||'|'||t.name from workflows_tags wt join shared_workflow s on s.\"workflowId\"=wt.\"workflowId\" join tag_entity t on t.id=wt.\"tagId\" where s.\"projectId\"='${expected_project_id}') q order by line;" | sha256sum | awk '{print $1}'
}

read_metadata() {
  local raw
  raw="$(timeout --foreground --signal=TERM --kill-after=30s 360s docker exec -i -e FINANCE_MICROSOFT_OAUTH_METADATA_ACK=READ_ONLY_REDACTED -e N8N_FINANCE_PROJECT_ID="${expected_project_id}" "${n8n_container}" node - list:workflow < "${runner_dir}/n8n-cli-microsoft-oauth-metadata-readback.cjs" 2>/dev/null | head -c 65537)" || return 1
  printf '%s' "${raw}" | python3 "${runner_dir}/parse_n8n_redacted_wrapper_output.py" oauth-metadata
}

data_table_digest() {
  local raw
  raw="$(timeout --foreground --signal=TERM --kill-after=30s 360s docker exec -i -e FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY -e N8N_FINANCE_PROJECT_ID="${expected_project_id}" "${n8n_container}" node - list:workflow < "${runner_dir}/n8n-cli-finance-data-table-digest.cjs" 2>/dev/null | head -c 65537)" || return 1
  printf '%s' "${raw}" | python3 "${runner_dir}/parse_n8n_redacted_wrapper_output.py" data-table
}

execute_probe() {
  local raw command_status=0 timeout_code
  raw="$(timeout --foreground --signal=TERM --kill-after=30s 360s docker exec -i -e FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK=EXECUTE_WF23_REDACTED_ONLY -e EXECUTIONS_DATA_SAVE_ON_SUCCESS=none -e EXECUTIONS_DATA_SAVE_ON_ERROR=none -e EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS=false "${n8n_container}" node - < "${runner_dir}/n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs" 2>/dev/null | head -c 65537)" || command_status=$?
  if [[ "${command_status}" == "0" ]]; then
    printf '%s' "${raw}" | python3 "${runner_dir}/parse_wf23_execution_output.py" success
    return
  fi
  timeout_code="$(printf '%s' "${raw}" | python3 "${runner_dir}/parse_wf23_execution_output.py" timeout)" || return 1
  printf '%s' "${timeout_code}"
  return 124
}

retain_execution_timeout_code() {
  case "$1" in
    WF23_TIMEOUT_CONFIG_LOAD|WF23_TIMEOUT_MODULE_LOAD|WF23_TIMEOUT_COMMAND_INIT|WF23_TIMEOUT_COMMAND_RUN|WF23_TIMEOUT_RAW_CAPTURE|WF23_TIMEOUT_FINALIZE) execution_failure_code="$1" ;;
    *) execution_failure_code="" ;;
  esac
}

[[ "$(project_state)" == "19|0|0" && "$(mapped_count)" == "19" && "$(tag_edge_count)" == "57" ]] || { echo "Expected 19-workflow deployed boundary" >&2; exit 1; }
[[ "$(setup_id_count)" == "0" && "$(wf23_execution_count)" == "0" && "$(wf23_history_count)" == "0" ]] || { echo "Transient setup state must start absent" >&2; exit 1; }
[[ "$(folder_hierarchy)" == "1" ]] || { echo "Canonical Global/Shared folder hierarchy required" >&2; exit 1; }
baseline_digest_before="$(baseline_digest)"; [[ "${baseline_digest_before}" =~ ^[0-9a-f]{64}$ ]] || { echo "Baseline digest unavailable" >&2; exit 1; }
data_table_digest_before="$(data_table_digest)" || { echo "Finance Data Table digest unavailable" >&2; exit 1; }
metadata_before="$(read_metadata)" || { echo "Microsoft OAuth metadata pre-read failed" >&2; exit 1; }
printf '%s' "${metadata_before}" | python3 "${runner_dir}/validate_microsoft_oauth_refresh_evidence.py" --require-expired-before || { echo "Both Microsoft access tokens must be expired before the proof" >&2; exit 1; }

outlook_credential_id="$(psql_scalar "select c.id from credentials_entity c join shared_credentials s on s.\"credentialsId\"=c.id where c.type='microsoftOutlookOAuth2Api' and s.\"projectId\"='${expected_project_id}' and s.role='credential:owner';")"
onedrive_credential_id="$(psql_scalar "select c.id from credentials_entity c join shared_credentials s on s.\"credentialsId\"=c.id where c.type='microsoftOneDriveOAuth2Api' and s.\"projectId\"='${expected_project_id}' and s.role='credential:owner';")"
[[ "${outlook_credential_id}" =~ ^[0-9A-Za-z_-]{8,64}$ && "${onedrive_credential_id}" =~ ^[0-9A-Za-z_-]{8,64}$ ]] || { echo "Exact owner credential bindings required" >&2; exit 1; }

if [[ "${preflight_mode}" == "true" ]]; then
  unset outlook_credential_id onedrive_credential_id metadata_before data_table_digest_before baseline_digest_before
  echo "Deployed WF23 preflight passed: source, boundary, Data Tables, health, and redacted credential metadata verified."
  exit 0
fi

run_id="microsoft-oauth-${expected_finance_commit:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
run_root="$(mktemp -d "/dev/shm/${run_id}.XXXXXX")"
bound_file="${run_root}/bound/23-microsoft-oauth-refresh-proof.json"
container_work_file="/tmp/${run_id}.json"
final_receipt="${receipt_root}/${run_id}.json"
failure_receipt="${receipt_root}/${run_id}-failure.json"
failure_stage="binding"; import_started=false; cleanup_verified=false; success=false
execution_failure_code=""
workflow_boundary_restored=false; execution_rows_zero_verified=false; data_table_digest_restored=false

remove_container_work_file() {
  docker exec "${n8n_container}" rm -f -- "${container_work_file}" >/dev/null 2>&1 || true
}

verify_clean_boundary() {
  local observed_data_table_digest
  [[ "$(project_state)" == "19|0|0" && "$(mapped_count)" == "19" && "$(tag_edge_count)" == "57" && "$(setup_id_count)" == "0" ]] || return 1
  [[ "$(wf23_execution_count)" == "0" ]] || return 1
  execution_rows_zero_verified=true
  [[ "$(wf23_history_count)" == "0" && "$(baseline_digest)" == "${baseline_digest_before}" ]] || return 1
  workflow_boundary_restored=true
  observed_data_table_digest="$(data_table_digest)" || return 1
  [[ "${observed_data_table_digest}" == "${data_table_digest_before}" ]] || return 1
  data_table_digest_restored=true
  cleanup_verified=true
}

remove_transient_wf23() {
  local cleanup_output
  [[ "$(wf23_execution_count)" == "0" ]] || return 1
  execution_rows_zero_verified=true
  cleanup_output="$(timeout --foreground --signal=TERM --kill-after=30s 360s docker exec -i -e FINANCE_MICROSOFT_OAUTH_PROOF_CLEANUP_ACK=REMOVE_TRANSIENT_WF23_ONLY -e N8N_FINANCE_PROJECT_ID="${expected_project_id}" "${n8n_container}" node - list:workflow < "${runner_dir}/n8n-cli-remove-transient-microsoft-oauth-refresh-proof.cjs" 2>/dev/null | head -c 4097)" || return 1
  grep -Eq '^transient WF23 cleanup verified:\{"status":"(VERIFIED|ALREADY_ABSENT)","workflows_removed":[01],"secret_values_recorded":false\}$' <<<"${cleanup_output}" || return 1
  import_started=false
  verify_clean_boundary
}

cleanup() {
  local status=$?; trap - EXIT; set +e
  remove_container_work_file
  if [[ "${success}" != "true" ]]; then
    if [[ "${import_started}" == "true" ]]; then remove_transient_wf23 || status=1; else verify_clean_boundary || status=1; fi
  fi
  if [[ "${success}" != "true" || "${status}" != "0" ]]; then
    python3 "${runner_dir}/build_microsoft_oauth_failure_receipt.py" "${failure_receipt}" "${run_id}" "${failure_stage}" "${cleanup_verified}" "${workflow_boundary_restored}" "${execution_rows_zero_verified}" "${data_table_digest_restored}" "${execution_failure_code}" || true
    echo "Transient WF23 proof failed; redacted failure receipt: ${failure_receipt}" >&2
  fi
  [[ "${run_root}" == /dev/shm/microsoft-oauth-* ]] && rm -rf -- "${run_root}"
  unset outlook_credential_id onedrive_credential_id metadata_before metadata_after_first metadata_after_second execution_first execution_second execution_failure_code refresh_after_first refresh_summary
  exit "${status}"
}
trap cleanup EXIT

export FINANCE_OUTLOOK_CREDENTIAL_ID="${outlook_credential_id}" FINANCE_ONEDRIVE_CREDENTIAL_ID="${onedrive_credential_id}"
python3 "${runner_dir}/bind-microsoft-oauth-refresh-proof.py" "${source_file}" "${bound_file}" --finance-commit "${expected_finance_commit}"
unset FINANCE_OUTLOOK_CREDENTIAL_ID FINANCE_ONEDRIVE_CREDENTIAL_ID

failure_stage="workflow_import"; import_started=true
docker exec -i "${n8n_container}" sh -c "cat > '${container_work_file}'" < "${bound_file}"
docker exec "${n8n_container}" n8n import:workflow --input="${container_work_file}" --projectId="${expected_project_id}" --activeState=false >/dev/null
remove_container_work_file
[[ "$(project_state)" == "20|0|0" && "$(setup_id_count)" == "1" ]] || { echo "WF23 inactive import boundary mismatch" >&2; exit 1; }

failure_stage="folder_placement"
[[ "$(psql_scalar "update workflow_entity w set \"parentFolderId\"='${folder_id}' where w.id='${workflow_id}' and w.active=false and w.\"activeVersionId\" is null and exists (select 1 from shared_workflow s where s.\"workflowId\"=w.id and s.\"projectId\"='${expected_project_id}' and s.role='workflow:owner');")" == "UPDATE 1" ]] || { echo "WF23 folder placement failed" >&2; exit 1; }
[[ "$(mapped_count)" == "20" && "$(tag_edge_count)" == "60" ]] || { echo "WF23 aggregate placement mismatch" >&2; exit 1; }
[[ "$(psql_scalar "select count(*) from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id where w.id='${workflow_id}' and w.name='${workflow_name}' and w.\"parentFolderId\"='${folder_id}' and w.active=false and w.\"activeVersionId\" is null and s.\"projectId\"='${expected_project_id}' and s.role='workflow:owner';")" == "1" ]] || { echo "WF23 exact inactive placement mismatch" >&2; exit 1; }
[[ "$(psql_scalar "select string_agg(t.name,',' order by t.name) from workflows_tags wt join tag_entity t on t.id=wt.\"tagId\" where wt.\"workflowId\"='${workflow_id}';")" == "finance,inactive,setup-required" ]] || { echo "WF23 exact tag set mismatch" >&2; exit 1; }
[[ "$(psql_scalar "select count(*) from workflow_entity where id='${workflow_id}' and nodes::text like '%BIND_%';")" == "0" && "$(wf23_execution_count)" == "0" ]] || { echo "WF23 binding/execution precheck failed" >&2; exit 1; }

failure_stage="first_execution"
execution_first="$(execute_probe)" || { retain_execution_timeout_code "${execution_first}"; unset execution_first; echo "WF23 first redacted execution failed" >&2; exit 1; }
[[ "$(wf23_execution_count)" == "0" ]] || { echo "WF23 first IRun was persisted" >&2; exit 1; }
metadata_after_first="$(read_metadata)" || { echo "Microsoft OAuth metadata first post-read failed" >&2; exit 1; }
refresh_after_first="$(printf '[%s,%s,%s]' "${metadata_before}" "${metadata_after_first}" "${metadata_after_first}" | python3 "${runner_dir}/validate_microsoft_oauth_refresh_evidence.py")" || { echo "First execution did not refresh both expired Microsoft tokens" >&2; exit 1; }

failure_stage="n8n_only_restart"
services=("${task_runners_service}" pdf-utility)
declare -A service_ids_before service_started_before
for service in "${services[@]}"; do
  service_ids_before["${service}"]="$(container_for_service "${service}")" || { echo "Required deployed service missing" >&2; exit 1; }
  service_started_before["${service}"]="$(docker inspect -f '{{.State.StartedAt}}' "${service_ids_before[${service}]}")"
done
n8n_started_before="$(docker inspect -f '{{.State.StartedAt}}' "${n8n_container}")"
docker restart "${n8n_container}" >/dev/null
healthy=false
for _ in $(seq 1 60); do
  if health_check "${n8n_container}"; then healthy=true; break; fi
  sleep 2
done
[[ "${healthy}" == "true" && "$(container_for_service "${n8n_service}")" == "${n8n_container}" && "$(docker inspect -f '{{.State.StartedAt}}' "${n8n_container}")" != "${n8n_started_before}" ]] || { echo "n8n-only restart did not complete" >&2; exit 1; }
for service in "${services[@]}"; do
  [[ "$(container_for_service "${service}")" == "${service_ids_before[${service}]}" && "$(docker inspect -f '{{.State.StartedAt}}' "${service_ids_before[${service}]}")" == "${service_started_before[${service}]}" ]] || { echo "Non-n8n service changed during restart" >&2; exit 1; }
done

failure_stage="second_execution"
execution_second="$(execute_probe)" || { retain_execution_timeout_code "${execution_second}"; unset execution_second; echo "WF23 second redacted execution failed" >&2; exit 1; }
[[ "$(wf23_execution_count)" == "0" ]] || { echo "WF23 second IRun was persisted" >&2; exit 1; }
metadata_after_second="$(read_metadata)" || { echo "Microsoft OAuth metadata second post-read failed" >&2; exit 1; }
refresh_summary="$(printf '[%s,%s,%s]' "${metadata_before}" "${metadata_after_first}" "${metadata_after_second}" | python3 "${runner_dir}/validate_microsoft_oauth_refresh_evidence.py")" || { echo "Post-restart Microsoft token expiry validation failed" >&2; exit 1; }
outlook_credential_id_after="$(psql_scalar "select c.id from credentials_entity c join shared_credentials s on s.\"credentialsId\"=c.id where c.type='microsoftOutlookOAuth2Api' and s.\"projectId\"='${expected_project_id}' and s.role='credential:owner';")"
onedrive_credential_id_after="$(psql_scalar "select c.id from credentials_entity c join shared_credentials s on s.\"credentialsId\"=c.id where c.type='microsoftOneDriveOAuth2Api' and s.\"projectId\"='${expected_project_id}' and s.role='credential:owner';")"
[[ "${outlook_credential_id_after}" == "${outlook_credential_id}" && "${onedrive_credential_id_after}" == "${onedrive_credential_id}" ]] || { echo "Microsoft credential owner binding changed during proof" >&2; exit 1; }
unset outlook_credential_id_after onedrive_credential_id_after outlook_credential_id onedrive_credential_id

failure_stage="transient_cleanup"
remove_transient_wf23 || { echo "WF23 transient cleanup failed" >&2; exit 1; }
[[ "${cleanup_verified}" == "true" && "${workflow_boundary_restored}" == "true" && "${execution_rows_zero_verified}" == "true" && "${data_table_digest_restored}" == "true" ]] || { echo "WF23 clean boundary postconditions incomplete" >&2; exit 1; }

failure_stage="final_receipt"
export WF23_METADATA_BEFORE="${metadata_before}" WF23_METADATA_AFTER_FIRST="${metadata_after_first}" WF23_METADATA_AFTER_SECOND="${metadata_after_second}"
export WF23_EXECUTION_FIRST="${execution_first}" WF23_EXECUTION_SECOND="${execution_second}"
export WF23_REFRESH_SUMMARY="${refresh_summary}"
python3 - "${final_receipt}" "${run_id}" "${expected_orchestrator_commit}" "${expected_finance_commit}" "${source_sha256}" <<'PY'
import datetime,json,os,pathlib,sys
target,run_id,orchestrator,finance,source_sha=sys.argv[1:]
snapshots=[json.loads(os.environ.pop(name)) for name in ("WF23_METADATA_BEFORE","WF23_METADATA_AFTER_FIRST","WF23_METADATA_AFTER_SECOND")]
executions=[json.loads(os.environ.pop(name)) for name in ("WF23_EXECUTION_FIRST","WF23_EXECUTION_SECOND")]
refresh=json.loads(os.environ.pop("WF23_REFRESH_SUMMARY"))
payload={"schema_version":1,"status":"VERIFIED","scope":"TRANSIENT_MICROSOFT_OAUTH_REFRESH_PROOF","run_id":run_id,"recorded_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"commits":{"finance":finance,"orchestrator":orchestrator},"workflow_source_sha256":source_sha,"executions":executions,"credential_metadata":{"before":snapshots[0],"after_first_execution":snapshots[1],"after_restart_second_execution":snapshots[2],"owner_bindings_stable":True,"credential_ids_recorded":False,"refresh":refresh},"restart":{"only_n8n_restarted":True,"n8n_healthy_after_restart":True,"other_service_containers_unchanged":True},"boundary":{"before":{"workflows":19,"active":0,"published":0,"folder_placements":19,"tag_edges":57},"during":{"workflows":20,"active":0,"published":0,"folder_placements":20,"tag_edges":60},"after":{"workflows":19,"active":0,"published":0,"folder_placements":19,"tag_edges":57}},"transient_workflow_removed":True,"baseline_digest_restored":True,"execution_history_absent":True,"raw_irun_persisted":False,"provider_response_logged":False,"finance_data_table_digest_restored":True,"finance_data_table_writes":False,"production_workflows_activated":False,"actual_writes":False,"cashback_writes":False,"secret_values_recorded":False,"token_fingerprints_recorded":False}
p=pathlib.Path(target);p.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8");p.chmod(0o600)
PY
unset WF23_METADATA_BEFORE WF23_METADATA_AFTER_FIRST WF23_METADATA_AFTER_SECOND WF23_EXECUTION_FIRST WF23_EXECUTION_SECOND WF23_REFRESH_SUMMARY
success=true
echo "Transient Microsoft OAuth refresh proof receipt: ${final_receipt}"
