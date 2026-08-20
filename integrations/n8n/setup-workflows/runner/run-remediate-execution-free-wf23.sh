#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly remediation_mode="${FINANCE_WF23_EXECUTION_FREE_REMEDIATION_MODE:-}"
case "${remediation_mode}" in
  REHEARSAL)
    [[ "${FINANCE_WF23_EXECUTION_FREE_REMEDIATION_ACK:-}" == "REHEARSE_EXECUTION_FREE_WF23_TRANSACTION_AND_ROLL_BACK" ]] || {
      echo "Set the exact WF23 rollback-rehearsal acknowledgement" >&2
      exit 1
    }
    readonly commit_authorized="off"
    ;;
  COMMIT)
    [[ "${FINANCE_WF23_EXECUTION_FREE_REMEDIATION_ACK:-}" == "REMOVE_EXACT_EXECUTION_FREE_WF23" ]] || {
      echo "Set the exact WF23 commit acknowledgement" >&2
      exit 1
    }
    readonly commit_authorized="on"
    ;;
  *)
    echo "Set FINANCE_WF23_EXECUTION_FREE_REMEDIATION_MODE to REHEARSAL or COMMIT" >&2
    exit 1
    ;;
esac
[[ "$(id -u)" != "0" ]] || { echo "Run as the rootless stack owner, not root" >&2; exit 1; }

readonly incident_finance_commit="8149f42f2694c200ca9fae37875c4dba4e727978"
readonly source_commit="f2f8d772bb3f397278d4aa5ded8c741a71d73466"
readonly source_sha256="2e26bd188468cf007562d3f4f47670aeb3661fbd7a8e86053a62da2cc845d940"
readonly required_orchestrator_commit="2c3286ae3c63a80b86ade945f19d419bf562874b"
readonly expected_project="finance-n8n-disposable-20260819155134"
readonly expected_project_id="gT5rxq26L0PoNUWX"
readonly workflow_id="10000000-0000-4000-8000-000000000023"
readonly companion_setup_id="10000000-0000-4000-8000-000000000022"
readonly workflow_name="Finance · Microsoft OAuth Refresh Proof · Manual Read Only"
readonly incident_execution_id="15"
readonly folder_id="f1000000-0000-4000-8000-000000000090"
readonly finance_repo="${FINANCE_REPOSITORY_DIR:-/opt/stacks/finance-statement-tracker}"
readonly stack_dir="${FINANCE_N8N_STACK_DIR:-/opt/stacks/finance-n8n}"
readonly runner_dir="${finance_repo}/integrations/n8n/setup-workflows/runner"
readonly retained_root="/opt/disposable/finance-n8n/20260819155134"
readonly env_file="${retained_root}/disposable.env"
readonly receipt_root="${FINANCE_N8N_RECEIPT_DIR:-${retained_root}/receipts}"
readonly source_file="${finance_repo}/integrations/n8n/setup-workflows/23-microsoft-oauth-refresh-proof.json"
readonly sql_file="${runner_dir}/remediate-execution-free-wf23.sql"
readonly expected_finance_commit="${FINANCE_REPOSITORY_COMMIT:-}"
readonly expected_orchestrator_commit="${ORCHESTRATOR_REPOSITORY_COMMIT:-}"

[[ "${expected_finance_commit}" =~ ^[0-9a-f]{40}$ ]] || { echo "Exact finance commit required" >&2; exit 1; }
[[ "${expected_orchestrator_commit}" == "${required_orchestrator_commit}" ]] || { echo "Reviewed orchestrator commit required" >&2; exit 1; }
[[ "${N8N_FINANCE_PROJECT_ID:-${expected_project_id}}" == "${expected_project_id}" ]] || { echo "Exact Finance project required" >&2; exit 1; }
[[ -d "${finance_repo}/.git" && -d "${stack_dir}/.git" ]] || { echo "Expected host repositories are missing" >&2; exit 1; }
[[ -z "$(git -C "${finance_repo}" status --porcelain)" ]] || { echo "Finance repository must be completely clean" >&2; exit 1; }
[[ -z "$(git -C "${stack_dir}" status --porcelain --untracked-files=no)" ]] || { echo "Orchestrator repository has tracked changes" >&2; exit 1; }
[[ "$(git -C "${finance_repo}" rev-parse HEAD)" == "${expected_finance_commit}" ]] || { echo "Finance commit mismatch" >&2; exit 1; }
[[ "$(git -C "${stack_dir}" rev-parse HEAD)" == "${expected_orchestrator_commit}" ]] || { echo "Orchestrator commit mismatch" >&2; exit 1; }
git -C "${finance_repo}" merge-base --is-ancestor "${incident_finance_commit}" "${expected_finance_commit}" || { echo "Finance commit does not contain the incident state" >&2; exit 1; }
git -C "${finance_repo}" merge-base --is-ancestor "${source_commit}" "${expected_finance_commit}" || { echo "Finance commit does not contain the reviewed WF23 source" >&2; exit 1; }
[[ -f "${env_file}" && ! -L "${env_file}" && "$(stat -c '%a' "${env_file}")" == "600" ]] || { echo "Retained mode-600 environment required" >&2; exit 1; }
[[ -f "${source_file}" && ! -L "${source_file}" && "$(sha256sum "${source_file}" | awk '{print $1}')" == "${source_sha256}" ]] || { echo "WF23 source SHA-256 mismatch" >&2; exit 1; }
for helper in canonicalize-wf23-source.py remediate-execution-free-wf23.sql validate-wf23-execution-free-rehearsal-receipt.py n8n-cli-finance-data-table-digest.cjs parse_n8n_redacted_wrapper_output.py; do
  [[ -f "${runner_dir}/${helper}" && ! -L "${runner_dir}/${helper}" ]] || { echo "Reviewed remediation helper missing" >&2; exit 1; }
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
services_healthy() {
  local container
  for container in "${n8n_container}" "${postgres_container}"; do
    [[ "$(docker inspect -f '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container}")" == "true|healthy" ]] || return 1
  done
}
wait_for_n8n_health() {
  local attempt
  for attempt in $(seq 1 60); do
    if [[ "$(docker inspect -f '{{.State.Running}}|{{.State.Paused}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${n8n_container}")" == "true|false|healthy" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}
n8n_stopped="false"
restore_n8n_if_stopped() {
  if [[ "${n8n_stopped:-false}" == "true" ]]; then
    timeout --foreground --signal=TERM --kill-after=15s 60s docker compose start n8n >/dev/null 2>&1 || true
  fi
}
trap restore_n8n_if_stopped EXIT
services_healthy || { echo "Retained service health failed" >&2; exit 1; }
[[ -z "$(docker ps -aq --filter "name=${expected_project}-n8n-run-")" ]] || { echo "Transient n8n-run container still exists" >&2; exit 1; }


psql_scalar() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -At -U "${N8N_POSTGRES_USER:-n8n}" -d "${N8N_POSTGRES_DATABASE:-n8n}" -c "$1"; }
project_state() { psql_scalar "select count(*)||'|'||count(*) filter (where w.active)||'|'||count(*) filter (where w.\"activeVersionId\" is not null) from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id where s.\"projectId\"='${expected_project_id}';"; }
mapped_count() { psql_scalar "select count(*) from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id join folder f on f.id=w.\"parentFolderId\" where s.\"projectId\"='${expected_project_id}' and f.\"projectId\"='${expected_project_id}';"; }
tag_edge_count() { psql_scalar "select count(*) from workflows_tags wt join shared_workflow s on s.\"workflowId\"=wt.\"workflowId\" where s.\"projectId\"='${expected_project_id}';"; }
bad_project_tag_set_count() { psql_scalar "select count(*) from (select s.\"workflowId\",count(wt.\"tagId\") as edge_count,coalesce(string_agg(t.name,',' order by t.name),'') as tag_names from shared_workflow s left join workflows_tags wt on wt.\"workflowId\"=s.\"workflowId\" left join tag_entity t on t.id=wt.\"tagId\" where s.\"projectId\"='${expected_project_id}' group by s.\"workflowId\") tagged where edge_count<>3 or tag_names<>'finance,inactive,setup-required';"; }
setup_id_count() { psql_scalar "select count(*) from workflow_entity where id in ('${workflow_id}','${companion_setup_id}');"; }
wf23_workflow_count() { psql_scalar "select count(*) from workflow_entity where id='${workflow_id}';"; }
wf23_execution_count() { psql_scalar "select count(*) from execution_entity where \"workflowId\"='${workflow_id}';"; }
wf23_execution_data_count() { psql_scalar "select count(*) from execution_data where \"executionId\"=${incident_execution_id};"; }
wf23_history_count() { psql_scalar "select count(*) from workflow_history where \"workflowId\"='${workflow_id}';"; }
wf23_version_id() { psql_scalar "select \"versionId\" from workflow_entity where id='${workflow_id}';"; }
workflow_corpus_digest() {
  psql_scalar "select line from (select 'W|'||w.id||'|'||w.name||'|'||w.active||'|'||coalesce(w.\"activeVersionId\",'')||'|'||coalesce(w.\"parentFolderId\",'')||'|'||w.\"versionId\"||'|'||w.\"isArchived\"||'|'||w.\"triggerCount\"||'|'||coalesce(w.\"sourceWorkflowId\",'')||'|'||w.nodes::text||'|'||w.connections::text||'|'||w.settings::text||'|'||coalesce(w.meta::text,'null')||'|'||coalesce(w.\"pinData\"::text,'null')||'|'||coalesce(w.\"staticData\"::text,'null')||'|'||coalesce(w.\"nodeGroups\"::text,'null') as line from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id where s.\"projectId\"='${expected_project_id}' union all select 'S|'||s.\"workflowId\"||'|'||s.\"projectId\"||'|'||s.role from shared_workflow s where s.\"projectId\"='${expected_project_id}' union all select 'T|'||wt.\"workflowId\"||'|'||wt.\"tagId\"||'|'||t.name from workflows_tags wt join shared_workflow s on s.\"workflowId\"=wt.\"workflowId\" join tag_entity t on t.id=wt.\"tagId\" where s.\"projectId\"='${expected_project_id}' union all select 'H|'||h.\"workflowId\"||'|'||h.\"versionId\"||'|'||coalesce(h.name,'')||'|'||coalesce(h.description,'')||'|'||coalesce(h.authors,'')||'|'||coalesce(h.autosaved::text,'')||'|'||h.nodes::text||'|'||h.connections::text||'|'||coalesce(h.\"nodeGroups\"::text,'null') from workflow_history h join shared_workflow s on s.\"workflowId\"=h.\"workflowId\" where s.\"projectId\"='${expected_project_id}') q order by line;" | sha256sum | awk '{print $1}'
}
corpus_digest_without_wf23() {
  psql_scalar "select line from (select 'W|'||w.id||'|'||w.name||'|'||w.active||'|'||coalesce(w.\"activeVersionId\",'')||'|'||coalesce(w.\"parentFolderId\",'')||'|'||w.\"versionId\"||'|'||w.\"isArchived\"||'|'||w.\"triggerCount\"||'|'||coalesce(w.\"sourceWorkflowId\",'')||'|'||w.nodes::text||'|'||w.connections::text||'|'||w.settings::text||'|'||coalesce(w.meta::text,'null')||'|'||coalesce(w.\"pinData\"::text,'null')||'|'||coalesce(w.\"staticData\"::text,'null')||'|'||coalesce(w.\"nodeGroups\"::text,'null') as line from workflow_entity w join shared_workflow s on s.\"workflowId\"=w.id where s.\"projectId\"='${expected_project_id}' and w.id<>'${workflow_id}' union all select 'S|'||s.\"workflowId\"||'|'||s.\"projectId\"||'|'||s.role from shared_workflow s where s.\"projectId\"='${expected_project_id}' and s.\"workflowId\"<>'${workflow_id}' union all select 'T|'||wt.\"workflowId\"||'|'||wt.\"tagId\"||'|'||t.name from workflows_tags wt join shared_workflow s on s.\"workflowId\"=wt.\"workflowId\" join tag_entity t on t.id=wt.\"tagId\" where s.\"projectId\"='${expected_project_id}' and wt.\"workflowId\"<>'${workflow_id}' union all select 'H|'||h.\"workflowId\"||'|'||h.\"versionId\"||'|'||coalesce(h.name,'')||'|'||coalesce(h.description,'')||'|'||coalesce(h.authors,'')||'|'||coalesce(h.autosaved::text,'')||'|'||h.nodes::text||'|'||h.connections::text||'|'||coalesce(h.\"nodeGroups\"::text,'null') from workflow_history h join shared_workflow s on s.\"workflowId\"=h.\"workflowId\" where s.\"projectId\"='${expected_project_id}' and h.\"workflowId\"<>'${workflow_id}') q order by line;" | sha256sum | awk '{print $1}'
}
credential_digest() {
  psql_scalar "select (select count(*)||'|'||md5(coalesce(string_agg(id||'|'||name||'|'||type||'|'||md5(data),E'\\n' order by id),'')) from credentials_entity)||'|'||(select count(*)||'|'||md5(coalesce(string_agg(\"credentialsId\"||'|'||\"projectId\"||'|'||role,E'\\n' order by \"credentialsId\",\"projectId\"),'')) from shared_credentials);"
}
data_table_digest() {
  local raw
  raw="$(timeout --foreground --signal=TERM --kill-after=30s 360s docker exec -i -e FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY -e N8N_FINANCE_PROJECT_ID="${expected_project_id}" "${n8n_container}" node - list:workflow < "${runner_dir}/n8n-cli-finance-data-table-digest.cjs" 2>/dev/null | head -c 65537)" || return 1
  printf '%s' "${raw}" | python3 "${runner_dir}/parse_n8n_redacted_wrapper_output.py" data-table
}

[[ "$(project_state)" == "22|0|0" && "$(mapped_count)" == "22" && "$(tag_edge_count)" == "66" && "$(bad_project_tag_set_count)" == "0" ]] || { echo "Expected exact execution-free 22/0/0, 22-folder, 66-tag boundary" >&2; exit 1; }
[[ "$(setup_id_count)" == "1" && "$(wf23_workflow_count)" == "1" && "$(wf23_execution_count)" == "0" && "$(wf23_execution_data_count)" == "0" && "$(wf23_history_count)" == "1" ]] || { echo "Expected exactly one execution-free WF23 workflow and exact history row" >&2; exit 1; }
workflow_digest_before="$(workflow_corpus_digest)"; [[ "${workflow_digest_before}" =~ ^[0-9a-f]{64}$ ]] || { echo "Workflow corpus digest unavailable" >&2; exit 1; }
corpus_digest_before="$(corpus_digest_without_wf23)"; [[ "${corpus_digest_before}" =~ ^[0-9a-f]{64}$ ]] || { echo "Retained corpus digest unavailable" >&2; exit 1; }
credential_digest_before="$(credential_digest)"; [[ "${credential_digest_before}" =~ ^[0-9]+\|[0-9a-f]{32}\|[0-9]+\|[0-9a-f]{32}$ ]] || { echo "Credential digest unavailable" >&2; exit 1; }
credential_digest_sha256_before="$(printf '%s' "${credential_digest_before}" | sha256sum | awk '{print $1}')"
data_table_digest_before="$(data_table_digest)"; [[ "${data_table_digest_before}" =~ ^[0-9a-f]{64}$ ]] || { echo "Finance Data Table digest unavailable" >&2; exit 1; }
sql_sha256="$(sha256sum "${sql_file}" | awk '{print $1}')"; [[ "${sql_sha256}" =~ ^[0-9a-f]{64}$ ]] || { echo "Remediation SQL digest unavailable" >&2; exit 1; }
expected_workflow_b64="$(python3 "${runner_dir}/canonicalize-wf23-source.py" "${source_file}" workflow)"
expected_version_id="$(wf23_version_id)"
[[ "${expected_version_id}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || { echo "Exact WF23 version ID unavailable" >&2; exit 1; }
expected_history_b64="$(python3 "${runner_dir}/canonicalize-wf23-source.py" "${source_file}" history "${expected_version_id}")"
[[ "${expected_workflow_b64}" =~ ^[A-Za-z0-9+/]+={0,2}$ && "${expected_history_b64}" =~ ^[A-Za-z0-9+/]+={0,2}$ ]] || { echo "Canonical WF23 projections unavailable" >&2; exit 1; }

if [[ "${remediation_mode}" == "COMMIT" ]]; then
  rehearsal_receipt="${FINANCE_WF23_EXECUTION_FREE_REHEARSAL_RECEIPT:-}"
  receipt_root_real="$(realpath -e "${receipt_root}")"
  [[ -n "${rehearsal_receipt}" && -f "${rehearsal_receipt}" && ! -L "${rehearsal_receipt}" && "$(stat -c '%a' "${rehearsal_receipt}")" == "600" ]] || { echo "Mode-600 WF23 rehearsal receipt required" >&2; exit 1; }
  [[ "$(dirname "$(realpath -e "${rehearsal_receipt}")")" == "${receipt_root_real}" ]] || { echo "WF23 rehearsal receipt must be in the retained receipt directory" >&2; exit 1; }
  receipt_validation="$(python3 "${runner_dir}/validate-wf23-execution-free-rehearsal-receipt.py" \
    "${rehearsal_receipt}" "${expected_finance_commit}" "${expected_orchestrator_commit}" \
    "${source_sha256}" "${sql_sha256}" "${workflow_digest_before}" \
    "${credential_digest_sha256_before}" "${data_table_digest_before}" 2>/dev/null)"
  [[ "${receipt_validation}" == "WF23_EXECUTION_FREE_REHEARSAL_RECEIPT_VERIFIED" ]] || { echo "Verified recent WF23 rehearsal receipt required" >&2; exit 1; }
fi

# Cleanly stop the sole retained n8n writer before either transaction. Unlike
# pausing, stopping closes any in-flight database connection instead of
# freezing it while it holds a lock. Every stop/start and the SQL itself is
# independently bounded; the EXIT trap attempts recovery on ordinary errors.
timeout --foreground --signal=TERM --kill-after=15s 60s docker compose stop -t 30 n8n >/dev/null
n8n_stopped="true"
[[ "$(docker inspect -f '{{.State.Running}}|{{.State.Paused}}' "${n8n_container}")" == "false|false" ]] || { echo "n8n quiescent window not established" >&2; exit 1; }
[[ "$(docker inspect -f '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${postgres_container}")" == "true|healthy" ]] || { echo "PostgreSQL health failed during quiescent window" >&2; exit 1; }
[[ -z "$(docker ps -aq --filter "name=${expected_project}-n8n-run-")" ]] || { echo "Transient n8n-run container exists during quiescent window" >&2; exit 1; }
[[ "$(project_state)" == "22|0|0" && "$(mapped_count)" == "22" && "$(tag_edge_count)" == "66" && "$(bad_project_tag_set_count)" == "0" ]] || { echo "Execution-free boundary changed before locked transaction" >&2; exit 1; }
[[ "$(setup_id_count)" == "1" && "$(wf23_workflow_count)" == "1" && "$(wf23_execution_count)" == "0" && "$(wf23_execution_data_count)" == "0" && "$(wf23_history_count)" == "1" ]] || { echo "Execution-free WF23 rows changed before locked transaction" >&2; exit 1; }
[[ "$(workflow_corpus_digest)" == "${workflow_digest_before}" && "$(corpus_digest_without_wf23)" == "${corpus_digest_before}" && "$(credential_digest)" == "${credential_digest_before}" ]] || { echo "Live pre-state digest changed before locked transaction" >&2; exit 1; }

# The SQL performs every detailed read-only proof before its first DELETE. Its
# transaction-local backup tables and post-delete assertions share one
# serializable transaction; any mismatch rolls the entire operation back.
timeout --foreground --signal=TERM --kill-after=30s 180s docker compose exec -T postgres psql -X -q -v ON_ERROR_STOP=1 \
  -v workflow_id="${workflow_id}" \
  -v project_id="${expected_project_id}" \
  -v folder_id="${folder_id}" \
  -v workflow_name="${workflow_name}" \
  -v incident_execution_id="${incident_execution_id}" \
  -v expected_workflow_b64="${expected_workflow_b64}" \
  -v expected_history_b64="${expected_history_b64}" \
  -v commit_authorized="${commit_authorized}" \
  -U "${N8N_POSTGRES_USER:-n8n}" -d "${N8N_POSTGRES_DATABASE:-n8n}" < "${sql_file}" >/dev/null
timeout --foreground --signal=TERM --kill-after=15s 60s docker compose start n8n >/dev/null
wait_for_n8n_health || { echo "n8n did not become healthy after quiescent transaction" >&2; exit 1; }
n8n_stopped="false"

if [[ "${remediation_mode}" == "REHEARSAL" ]]; then
  [[ "$(project_state)" == "22|0|0" && "$(mapped_count)" == "22" && "$(tag_edge_count)" == "66" && "$(bad_project_tag_set_count)" == "0" ]] || { echo "Rollback rehearsal did not restore the execution-free workflow boundary" >&2; exit 1; }
  [[ "$(setup_id_count)" == "1" && "$(wf23_workflow_count)" == "1" && "$(wf23_execution_count)" == "0" && "$(wf23_execution_data_count)" == "0" && "$(wf23_history_count)" == "1" ]] || { echo "Rollback rehearsal did not restore the execution-free WF23 rows" >&2; exit 1; }
  [[ "$(workflow_corpus_digest)" == "${workflow_digest_before}" ]] || { echo "Rollback rehearsal changed the workflow corpus" >&2; exit 1; }
  [[ "$(corpus_digest_without_wf23)" == "${corpus_digest_before}" ]] || { echo "Rollback rehearsal changed the retained workflow corpus" >&2; exit 1; }
  [[ "$(credential_digest)" == "${credential_digest_before}" ]] || { echo "Rollback rehearsal changed the credential corpus" >&2; exit 1; }
  data_table_digest_after="$(data_table_digest)"; [[ "${data_table_digest_after}" == "${data_table_digest_before}" ]] || { echo "Rollback rehearsal changed Finance Data Tables" >&2; exit 1; }
  services_healthy || { echo "Retained service health failed after rollback rehearsal" >&2; exit 1; }
  [[ -z "$(docker ps -aq --filter "name=${expected_project}-n8n-run-")" ]] || { echo "Transient n8n-run container exists after rollback rehearsal" >&2; exit 1; }

  receipt="${receipt_root}/wf23-execution-free-postgresql-rollback-rehearsal-${expected_finance_commit:0:12}-$(date -u +%Y%m%dT%H%M%SZ).json"
  python3 - "${receipt}" "${expected_finance_commit}" "${expected_orchestrator_commit}" "${source_sha256}" "${sql_sha256}" "${workflow_digest_before}" "${credential_digest_sha256_before}" "${data_table_digest_before}" <<'PY'
import datetime
import json
import pathlib
import sys

target, finance_commit, orchestrator_commit, source_sha256, sql_sha256, workflow_digest, credential_digest, data_table_digest = sys.argv[1:]
payload = {
    "schema_version": 1,
    "status": "VERIFIED",
    "scope": "WF23_EXECUTION_FREE_POSTGRESQL_ROLLBACK_REHEARSAL",
    "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "commits": {"finance": finance_commit, "orchestrator": orchestrator_commit},
    "workflow_source_sha256": source_sha256,
    "sql_sha256": sql_sha256,
    "live_pre_state": {
        "project_id": "gT5rxq26L0PoNUWX",
        "workflow_id": "10000000-0000-4000-8000-000000000023",
        "incident_execution_id": 15,
        "project_state": "22|0|0",
        "folder_placements": 22,
        "tag_edges": 66,
        "bad_tag_sets": 0,
        "setup_ids": 1,
        "wf23_workflows": 1,
        "wf23_executions": 0,
        "wf23_execution_data_rows": 0,
        "wf23_histories": 1,
        "workflow_corpus_sha256": workflow_digest,
        "credential_corpus_sha256": credential_digest,
        "finance_data_table_sha256": data_table_digest,
        "execution_free_signature": "EXECUTION_FREE_INACTIVE_WORKFLOW_WITH_EXACT_HISTORY",
    },
    "transaction_outcome": "ROLLED_BACK",
    "production_sql_body_completed": True,
    "post_state_unchanged": True,
    "services_healthy": True,
    "provider_calls": False,
    "secret_values_recorded": False,
}
path = pathlib.Path(target)
with path.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
path.chmod(0o600)
PY
  unset workflow_digest_before corpus_digest_before credential_digest_before credential_digest_sha256_before data_table_digest_before data_table_digest_after sql_sha256 expected_workflow_b64 expected_history_b64 expected_version_id
  echo "Exact execution-free WF23 PostgreSQL rollback rehearsal verified; redacted receipt: ${receipt}"
  exit 0
fi

[[ "$(project_state)" == "21|0|0" && "$(mapped_count)" == "21" && "$(tag_edge_count)" == "63" && "$(bad_project_tag_set_count)" == "0" ]] || { echo "Restored workflow boundary mismatch" >&2; exit 1; }
[[ "$(setup_id_count)" == "0" && "$(wf23_workflow_count)" == "0" && "$(wf23_execution_count)" == "0" && "$(wf23_execution_data_count)" == "0" && "$(wf23_history_count)" == "0" ]] || { echo "WF23 cleanup readback mismatch" >&2; exit 1; }
[[ "$(corpus_digest_without_wf23)" == "${corpus_digest_before}" ]] || { echo "Retained workflow corpus changed" >&2; exit 1; }
[[ "$(credential_digest)" == "${credential_digest_before}" ]] || { echo "Credential corpus changed" >&2; exit 1; }
data_table_digest_after="$(data_table_digest)"; [[ "${data_table_digest_after}" == "${data_table_digest_before}" ]] || { echo "Finance Data Table digest changed" >&2; exit 1; }
services_healthy || { echo "Retained service health failed after remediation" >&2; exit 1; }
[[ -z "$(docker ps -aq --filter "name=${expected_project}-n8n-run-")" ]] || { echo "Transient n8n-run container exists after remediation" >&2; exit 1; }

receipt="${receipt_root}/wf23-execution-free-remediation-${expected_finance_commit:0:12}-$(date -u +%Y%m%dT%H%M%SZ).json"
python3 - "${receipt}" "${expected_finance_commit}" "${expected_orchestrator_commit}" "${source_sha256}" <<'PY'
import datetime
import json
import pathlib
import sys

target, finance_commit, orchestrator_commit, source_sha256 = sys.argv[1:]
payload = {
    "schema_version": 1,
    "status": "VERIFIED",
    "scope": "WF23_EXACT_EXECUTION_FREE_STATE_REMEDIATION",
    "observed_execution_state": "EXECUTION_FREE_INACTIVE_WORKFLOW_WITH_EXACT_HISTORY",
    "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "commits": {"finance": finance_commit, "orchestrator": orchestrator_commit},
    "workflow_source_sha256": source_sha256,
    "workflow_removed": True,
    "execution_rows_removed": 0,
    "history_rows_removed": 1,
    "quiescent_n8n_window_established": True,
    "boundary_restored": True,
    "retained_workflow_digest_restored": True,
    "finance_data_table_digest_restored": True,
    "credential_corpus_unchanged": True,
    "credential_values_recorded": False,
    "provider_calls": False,
    "production_workflows_activated": False,
    "actual_writes": False,
    "cashback_writes": False,
    "secret_values_recorded": False,
}
path = pathlib.Path(target)
with path.open("x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
path.chmod(0o600)
PY
unset workflow_digest_before corpus_digest_before credential_digest_before credential_digest_sha256_before data_table_digest_before data_table_digest_after sql_sha256 expected_workflow_b64 expected_history_b64 expected_version_id rehearsal_receipt receipt_root_real receipt_validation
echo "Exact execution-free WF23 remediation verified; redacted receipt: ${receipt}"
