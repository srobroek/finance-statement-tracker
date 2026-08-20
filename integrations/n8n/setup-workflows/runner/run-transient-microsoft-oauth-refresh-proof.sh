#!/usr/bin/env bash
set -euo pipefail

[[ "${FINANCE_MICROSOFT_OAUTH_PROOF_ACK:-}" == "RUN_TRANSIENT_WF23_ONLY" ]] || { echo "Set FINANCE_MICROSOFT_OAUTH_PROOF_ACK=RUN_TRANSIENT_WF23_ONLY" >&2; exit 1; }
[[ "$(id -u)" != "0" ]] || { echo "Run as the rootless stack owner, not root" >&2; exit 1; }

readonly source_commit="f2f8d772bb3f397278d4aa5ded8c741a71d73466"
readonly source_sha256="2e26bd188468cf007562d3f4f47670aeb3661fbd7a8e86053a62da2cc845d940"
readonly prior_promoted_commit="00491aae2ab43c486f3a9b4a62ce3ba5e63032f6"
readonly expected_project="finance-n8n-disposable-20260819155134"
readonly expected_project_id="gT5rxq26L0PoNUWX"
readonly workflow_id="10000000-0000-4000-8000-000000000023"
readonly companion_setup_id="10000000-0000-4000-8000-000000000022"
readonly workflow_name="Finance · Microsoft OAuth Refresh Proof · Manual Read Only"
readonly folder_id="f1000000-0000-4000-8000-000000000090"
readonly folder_name="90 Platform & Admin"
# n8n 2.36.2 Execute.init() starts a task broker. The retained service already
# owns 5679, so the transient internal runner uses one reviewed loopback port.
readonly internal_runner_broker_port="15679"
readonly finance_repo="${FINANCE_REPOSITORY_DIR:-/opt/stacks/finance-statement-tracker}"
readonly stack_dir="${FINANCE_N8N_STACK_DIR:-/opt/stacks/finance-n8n}"
readonly runner_dir="${finance_repo}/integrations/n8n/setup-workflows/runner"
readonly retained_root="/opt/disposable/finance-n8n/20260819155134"
readonly env_file="${retained_root}/disposable.env"
readonly receipt_root="${FINANCE_N8N_RECEIPT_DIR:-${retained_root}/receipts}"
readonly source_file="${finance_repo}/integrations/n8n/setup-workflows/23-microsoft-oauth-refresh-proof.json"
readonly preflight_mode="${FINANCE_MICROSOFT_OAUTH_PROOF_PREFLIGHT:-false}"
readonly expected_finance_commit="${FINANCE_REPOSITORY_COMMIT:-}"
readonly expected_orchestrator_commit="${ORCHESTRATOR_REPOSITORY_COMMIT:-}"

[[ "${expected_finance_commit}" =~ ^[0-9a-f]{40}$ ]] || { echo "Exact finance commit required" >&2; exit 1; }
[[ "${expected_orchestrator_commit}" =~ ^[0-9a-f]{40}$ ]] || { echo "Exact orchestrator commit required" >&2; exit 1; }
[[ "${N8N_FINANCE_PROJECT_ID:-${expected_project_id}}" == "${expected_project_id}" ]] || { echo "Exact Finance project required" >&2; exit 1; }
[[ "${preflight_mode}" == "true" || "${preflight_mode}" == "false" ]] || { echo "FINANCE_MICROSOFT_OAUTH_PROOF_PREFLIGHT must be true or false" >&2; exit 1; }
[[ -d "${finance_repo}/.git" && -d "${stack_dir}/.git" ]] || { echo "Expected host repositories are missing" >&2; exit 1; }
[[ -z "$(git -C "${finance_repo}" status --porcelain)" ]] || { echo "Finance repository must be completely clean" >&2; exit 1; }
[[ -z "$(git -C "${stack_dir}" status --porcelain --untracked-files=no)" ]] || { echo "Orchestrator repository has tracked changes" >&2; exit 1; }
[[ "$(git -C "${finance_repo}" rev-parse HEAD)" == "${expected_finance_commit}" ]] || { echo "Finance commit mismatch" >&2; exit 1; }
[[ "$(git -C "${stack_dir}" rev-parse HEAD)" == "${expected_orchestrator_commit}" ]] || { echo "Orchestrator commit mismatch" >&2; exit 1; }
git -C "${finance_repo}" merge-base --is-ancestor "${source_commit}" "${expected_finance_commit}" || { echo "Finance commit does not descend from reviewed WF23 source" >&2; exit 1; }
git -C "${finance_repo}" merge-base --is-ancestor "${prior_promoted_commit}" "${expected_finance_commit}" || { echo "Finance commit does not descend from promoted corpus" >&2; exit 1; }
[[ -f "${env_file}" && ! -L "${env_file}" && "$(stat -c '%a' "${env_file}")" == "600" ]] || { echo "Retained mode-600 environment required" >&2; exit 1; }
[[ -f "${source_file}" && ! -L "${source_file}" ]] || { echo "Regular WF23 source required" >&2; exit 1; }
[[ "$(sha256sum "${source_file}" | awk '{print $1}')" == "${source_sha256}" ]] || { echo "WF23 source SHA-256 mismatch" >&2; exit 1; }
for helper in bind-microsoft-oauth-refresh-proof.py validate_microsoft_oauth_refresh_evidence.py build_microsoft_oauth_failure_receipt.py parse_n8n_redacted_wrapper_output.py parse_wf23_execution_output.py n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs n8n-cli-wf23-direct-transport-probe.cjs n8n-cli-microsoft-oauth-metadata-readback.cjs n8n-cli-finance-data-table-digest.cjs n8n-cli-remove-transient-microsoft-oauth-refresh-proof.cjs; do
  [[ -f "${runner_dir}/${helper}" && ! -L "${runner_dir}/${helper}" ]] || { echo "Reviewed WF23 runner helper missing" >&2; exit 1; }
done

cd "${stack_dir}"
export COMPOSE_PROJECT_NAME="${expected_project}"
export COMPOSE_FILE="compose.yaml:compose.disposable.yaml"
export COMPOSE_ENV_FILES="${env_file}"
export FINANCE_N8N_DATA_ROOT="${retained_root}"
export FINANCE_RUNTIME_NETWORK_NAME="${expected_project}-runtime"
export FINANCE_N8N_DEPLOYMENT_ENV_FILE="${env_file}"
export FINANCE_N8N_IMAGE_LOCK="${FINANCE_N8N_IMAGE_LOCK:-/opt/disposable/finance-n8n/local-da6b0c1-20260819T183158Z/runtime-lock.json}"
unset N8N_ENCRYPTION_KEY
scripts/verify-image-lock.sh "${FINANCE_N8N_IMAGE_LOCK}" "${env_file}"
install -d -m 0700 "${receipt_root}"

n8n_container="$(docker compose ps -q n8n)"
postgres_container="$(docker compose ps -q postgres)"
[[ -n "${n8n_container}" && "${n8n_container}" != *$'\n'* && -n "${postgres_container}" && "${postgres_container}" != *$'\n'* ]] || { echo "Exact retained containers required" >&2; exit 1; }
for container in "${n8n_container}" "${postgres_container}"; do
  [[ "$(docker inspect -f '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container}")" == "true|healthy" ]] || { echo "Retained service health failed" >&2; exit 1; }
done

direct_transport_probe() {
  local raw expected
  expected='WF23 direct transport probe verified:{"schema_version":1,"status":"VERIFIED","scope":"DIRECT_EXECUTE_INSTANCE_TRANSPORT","execute_instance_resolved":true,"instance_log_override_invoked":true,"workflow_loaded":false,"workflow_executed":false,"provider_calls":false,"database_initialized":false,"raw_irun_persisted":false,"provider_response_logged":false,"secret_values_recorded":false}'
  raw="$(timeout --foreground --signal=TERM --kill-after=30s 120s docker exec -i -e FINANCE_WF23_TRANSPORT_PROBE_ACK=READ_ONLY_DIRECT_EXECUTE_INSTANCE "${n8n_container}" node - < "${runner_dir}/n8n-cli-wf23-direct-transport-probe.cjs" 2>/dev/null | head -c 4097)" || return 1
  [[ "${raw}" == "${expected}" ]]
}

direct_transport_probe || { echo "WF23 direct execution transport probe failed before metadata/provider access" >&2; exit 1; }

internal_runner_port_preflight() {
  timeout --foreground --signal=TERM --kill-after=5s 15s docker exec -i \
    -e WF23_INTERNAL_BROKER_PORT="${internal_runner_broker_port}" \
    "${n8n_container}" node -e \
    'const net=require("node:net");const port=Number(process.env.WF23_INTERNAL_BROKER_PORT);if(!Number.isInteger(port)||port!==15679)process.exit(1);const server=net.createServer();server.once("error",()=>process.exit(1));server.listen(port,"127.0.0.1",()=>server.close((error)=>process.exit(error?1:0)));' \
    >/dev/null 2>&1
}

internal_runner_port_preflight || { echo "WF23 dedicated internal task-runner broker port unavailable" >&2; exit 1; }

psql_scalar() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -At -U "${N8N_POSTGRES_USER:-n8n}" -d "${N8N_POSTGRES_DATABASE:-n8n}" -c "$1"; }
project_state() { psql_scalar "select count(*)||'|'||count(*) filter (where w.active)||'|'||count(*) filter (where w.\"activeVersionId\" is not null) from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id where s.\"projectId\"='${expected_project_id}';"; }
mapped_count() { psql_scalar "select count(*) from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id join folder f on f.id=w.\"parentFolderId\" where s.\"projectId\"='${expected_project_id}' and f.\"projectId\"='${expected_project_id}';"; }
tag_edge_count() { psql_scalar "select count(*) from workflows_tags wt join shared_workflow s on s.\"workflowId\"=wt.\"workflowId\" join tag_entity t on t.id=wt.\"tagId\" where s.\"projectId\"='${expected_project_id}' and t.name in ('finance','setup-required','inactive');"; }
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
  internal_runner_port_preflight || return 1
  raw="$(timeout --foreground --signal=TERM --kill-after=30s 360s docker exec -i -e FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK=EXECUTE_WF23_REDACTED_ONLY -e EXECUTIONS_DATA_SAVE_ON_SUCCESS=none -e EXECUTIONS_DATA_SAVE_ON_ERROR=none -e EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS=false -e N8N_RUNNERS_MODE=internal -e N8N_RUNNERS_BROKER_PORT="${internal_runner_broker_port}" -e N8N_RUNNERS_BROKER_LISTEN_ADDRESS=127.0.0.1 "${n8n_container}" node - < "${runner_dir}/n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs" 2>/dev/null | head -c 65537)" || command_status=$?
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

[[ "$(project_state)" == "21|0|0" && "$(mapped_count)" == "21" && "$(tag_edge_count)" == "63" ]] || { echo "Expected retained 21/0/0, 21-folder, 63-tag boundary" >&2; exit 1; }
[[ "$(setup_id_count)" == "0" && "$(wf23_execution_count)" == "0" && "$(wf23_history_count)" == "0" ]] || { echo "Transient setup state must start absent" >&2; exit 1; }
[[ "$(psql_scalar "select count(*) from folder where id='${folder_id}' and name='${folder_name}' and \"projectId\"='${expected_project_id}';")" == "1" ]] || { echo "Exact Platform & Admin folder required" >&2; exit 1; }
baseline_digest_before="$(baseline_digest)"; [[ "${baseline_digest_before}" =~ ^[0-9a-f]{64}$ ]] || { echo "Baseline digest unavailable" >&2; exit 1; }
data_table_digest_before="$(data_table_digest)" || { echo "Finance Data Table digest unavailable" >&2; exit 1; }
metadata_before="$(read_metadata)" || { echo "Microsoft OAuth metadata pre-read failed" >&2; exit 1; }
printf '%s' "${metadata_before}" | python3 "${runner_dir}/validate_microsoft_oauth_refresh_evidence.py" --require-expired-before || { echo "Both Microsoft access tokens must be expired before the proof" >&2; exit 1; }

outlook_credential_id="$(psql_scalar "select c.id from credentials_entity c join shared_credentials s on s.\"credentialsId\"=c.id where c.type='microsoftOutlookOAuth2Api' and s.\"projectId\"='${expected_project_id}' and s.role='credential:owner';")"
onedrive_credential_id="$(psql_scalar "select c.id from credentials_entity c join shared_credentials s on s.\"credentialsId\"=c.id where c.type='microsoftOneDriveOAuth2Api' and s.\"projectId\"='${expected_project_id}' and s.role='credential:owner';")"
[[ "${outlook_credential_id}" =~ ^[0-9A-Za-z_-]{8,64}$ && "${onedrive_credential_id}" =~ ^[0-9A-Za-z_-]{8,64}$ ]] || { echo "Exact owner credential bindings required" >&2; exit 1; }

if [[ "${preflight_mode}" == "true" ]]; then
  unset outlook_credential_id onedrive_credential_id metadata_before data_table_digest_before baseline_digest_before
  echo "Transient WF23 preflight passed: source, baseline, Data Tables, health, and redacted credential metadata verified."
  exit 0
fi

run_id="microsoft-oauth-${expected_finance_commit:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
run_root="$(mktemp -d "/dev/shm/${run_id}.XXXXXX")"
bound_file="${run_root}/bound/23-microsoft-oauth-refresh-proof.json"
final_receipt="${receipt_root}/${run_id}.json"
failure_receipt="${receipt_root}/${run_id}-failure.json"
failure_stage="binding"; import_started=false; cleanup_verified=false; success=false
execution_failure_code=""
workflow_boundary_restored=false; execution_rows_zero_verified=false; data_table_digest_restored=false

verify_clean_boundary() {
  local observed_data_table_digest
  [[ "$(project_state)" == "21|0|0" && "$(mapped_count)" == "21" && "$(tag_edge_count)" == "63" && "$(setup_id_count)" == "0" ]] || return 1
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
  cleanup_output="$(timeout --foreground --signal=TERM --kill-after=30s 360s docker compose run --rm --no-deps --entrypoint node -e FINANCE_MICROSOFT_OAUTH_PROOF_CLEANUP_ACK=REMOVE_TRANSIENT_WF23_ONLY -e N8N_FINANCE_PROJECT_ID="${expected_project_id}" -v "${runner_dir}/n8n-cli-remove-transient-microsoft-oauth-refresh-proof.cjs:/runtime/remove-wf23.cjs:ro" n8n /runtime/remove-wf23.cjs list:workflow 2>/dev/null | head -c 4097)" || return 1
  grep -Eq '^transient WF23 cleanup verified:\{"status":"(VERIFIED|ALREADY_ABSENT)","workflows_removed":[01],"secret_values_recorded":false\}$' <<<"${cleanup_output}" || return 1
  import_started=false
  verify_clean_boundary
}

cleanup() {
  local status=$?; trap - EXIT; set +e
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
docker compose run --rm --no-deps -v "${bound_file}:/runtime/wf23.json:ro" n8n import:workflow --input=/runtime/wf23.json --projectId="${expected_project_id}" --activeState=false >/dev/null
[[ "$(project_state)" == "22|0|0" && "$(setup_id_count)" == "1" ]] || { echo "WF23 inactive import boundary mismatch" >&2; exit 1; }

failure_stage="folder_placement"
[[ "$(psql_scalar "update workflow_entity w set \"parentFolderId\"='${folder_id}' where w.id='${workflow_id}' and w.active=false and w.\"activeVersionId\" is null and exists (select 1 from shared_workflow s where s.\"workflowId\"=w.id and s.\"projectId\"='${expected_project_id}' and s.role='workflow:owner');")" == "UPDATE 1" ]] || { echo "WF23 folder placement failed" >&2; exit 1; }
[[ "$(mapped_count)" == "22" && "$(tag_edge_count)" == "66" ]] || { echo "WF23 aggregate placement mismatch" >&2; exit 1; }
[[ "$(psql_scalar "select count(*) from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id where w.id='${workflow_id}' and w.name='${workflow_name}' and w.\"parentFolderId\"='${folder_id}' and w.active=false and w.\"activeVersionId\" is null and s.\"projectId\"='${expected_project_id}' and s.role='workflow:owner';")" == "1" ]] || { echo "WF23 exact inactive placement mismatch" >&2; exit 1; }
[[ "$(psql_scalar "select string_agg(t.name,',' order by t.name) from workflows_tags wt join tag_entity t on t.id=wt.\"tagId\" where wt.\"workflowId\"='${workflow_id}';")" == "finance,inactive,setup-required" ]] || { echo "WF23 exact tag set mismatch" >&2; exit 1; }
[[ "$(psql_scalar "select count(*) from workflow_entity where id='${workflow_id}' and nodes::text like '%BIND_%';")" == "0" && "$(wf23_execution_count)" == "0" ]] || { echo "WF23 binding/execution precheck failed" >&2; exit 1; }

failure_stage="first_execution"
execution_first="$(execute_probe)" || { retain_execution_timeout_code "${execution_first}"; unset execution_first; echo "WF23 first redacted execution failed" >&2; exit 1; }
[[ "$(wf23_execution_count)" == "0" ]] || { echo "WF23 first IRun was persisted" >&2; exit 1; }
metadata_after_first="$(read_metadata)" || { echo "Microsoft OAuth metadata first post-read failed" >&2; exit 1; }
refresh_after_first="$(printf '[%s,%s,%s]' "${metadata_before}" "${metadata_after_first}" "${metadata_after_first}" | python3 "${runner_dir}/validate_microsoft_oauth_refresh_evidence.py")" || { echo "First execution did not refresh both expired Microsoft tokens" >&2; exit 1; }

failure_stage="n8n_only_restart"
services=(postgres task-runners codex-agent-runner pdf-utility)
declare -A service_ids_before service_started_before
for service in "${services[@]}"; do
  service_ids_before["${service}"]="$(docker compose ps -q "${service}")"
  [[ -n "${service_ids_before[${service}]}" ]] || { echo "Required retained service missing" >&2; exit 1; }
  service_started_before["${service}"]="$(docker inspect -f '{{.State.StartedAt}}' "${service_ids_before[${service}]}")"
done
n8n_started_before="$(docker inspect -f '{{.State.StartedAt}}' "${n8n_container}")"
docker compose restart n8n >/dev/null
healthy=false
for _ in $(seq 1 60); do
  if [[ "$(docker inspect -f '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${n8n_container}" 2>/dev/null || true)" == "true|healthy" ]]; then healthy=true; break; fi
  sleep 2
done
[[ "${healthy}" == "true" && "$(docker compose ps -q n8n)" == "${n8n_container}" && "$(docker inspect -f '{{.State.StartedAt}}' "${n8n_container}")" != "${n8n_started_before}" ]] || { echo "n8n-only restart did not complete" >&2; exit 1; }
for service in "${services[@]}"; do
  [[ "$(docker compose ps -q "${service}")" == "${service_ids_before[${service}]}" && "$(docker inspect -f '{{.State.StartedAt}}' "${service_ids_before[${service}]}")" == "${service_started_before[${service}]}" ]] || { echo "Non-n8n service changed during restart" >&2; exit 1; }
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
payload={"schema_version":1,"status":"VERIFIED","scope":"TRANSIENT_MICROSOFT_OAUTH_REFRESH_PROOF","run_id":run_id,"recorded_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"commits":{"finance":finance,"orchestrator":orchestrator},"workflow_source_sha256":source_sha,"executions":executions,"credential_metadata":{"before":snapshots[0],"after_first_execution":snapshots[1],"after_restart_second_execution":snapshots[2],"owner_bindings_stable":True,"credential_ids_recorded":False,"refresh":refresh},"restart":{"only_n8n_restarted":True,"n8n_healthy_after_restart":True,"other_service_containers_unchanged":True},"boundary":{"before":{"workflows":21,"active":0,"published":0,"folder_placements":21,"tag_edges":63},"during":{"workflows":22,"active":0,"published":0,"folder_placements":22,"tag_edges":66},"after":{"workflows":21,"active":0,"published":0,"folder_placements":21,"tag_edges":63}},"transient_workflow_removed":True,"baseline_digest_restored":True,"execution_history_absent":True,"raw_irun_persisted":False,"provider_response_logged":False,"finance_data_table_digest_restored":True,"finance_data_table_writes":False,"production_workflows_activated":False,"actual_writes":False,"cashback_writes":False,"secret_values_recorded":False,"token_fingerprints_recorded":False}
p=pathlib.Path(target);p.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8");p.chmod(0o600)
PY
unset WF23_METADATA_BEFORE WF23_METADATA_AFTER_FIRST WF23_METADATA_AFTER_SECOND WF23_EXECUTION_FIRST WF23_EXECUTION_SECOND WF23_REFRESH_SUMMARY
success=true
echo "Transient Microsoft OAuth refresh proof receipt: ${final_receipt}"
