#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  printf 'usage: %s forward|rollback\n' "$0" >&2
  exit 2
}

operation="${1:-}"
case "$operation" in
forward | rollback) ;;
*) usage ;;
esac

runner_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
: "${FINANCE_REPOSITORY_DIR:?FINANCE_REPOSITORY_DIR is required}"
: "${FINANCE_N8N_RECEIPT_DIR:?FINANCE_N8N_RECEIPT_DIR is required}"
: "${FINANCE_N8N_CONTAINER:?FINANCE_N8N_CONTAINER is required}"
: "${N8N_FINANCE_PROJECT_ID:?N8N_FINANCE_PROJECT_ID is required}"
: "${FINANCE_N8N_RUNTIME_MODE:?FINANCE_N8N_RUNTIME_MODE is required}"
: "${FINANCE_FOUR_TABLE_OPERATION_NONCE:?FINANCE_FOUR_TABLE_OPERATION_NONCE is required}"
: "${FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST:?FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST is required}"
: "${FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST:?FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST is required}"
: "${FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST:?FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST is required}"
test "$FINANCE_FOUR_TABLE_OPERATION_NONCE" = "r6-20260826-orc-partial-cutover-recovery-plan"
test "$FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST" = "74b77a7f4c1c870815bbde8cf4563b20984d76785d076a050fcef8880a7a4b69"
test "$FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST" = "9b49963355aa4d025e414eb1fd02abcb2891b340afa96f8d2ed4f00102301154"
test "$FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST" = "b8c25ec57b00e1bd8b511a33fa576d390d3a46c7aa58708237268cb51c29d00a"
case "$FINANCE_N8N_RUNTIME_MODE" in
DISPOSABLE_ONLY | PRODUCTION_ONLY) ;;
*)
  echo "FINANCE_N8N_RUNTIME_MODE must be DISPOSABLE_ONLY or PRODUCTION_ONLY" >&2
  exit 1
  ;;
esac
if [[ ! "$N8N_FINANCE_PROJECT_ID" =~ ^[A-Za-z0-9_-]{8,64}$ ]]; then
  echo "N8N_FINANCE_PROJECT_ID must be 8-64 safe identity characters" >&2
  exit 1
fi
if [[ ! "$FINANCE_N8N_CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "FINANCE_N8N_CONTAINER must be a simple container identity" >&2
  exit 1
fi
lock_timeout_ms="${FINANCE_FOUR_TABLE_LOCK_TIMEOUT_MS:-5000}"
statement_timeout_ms="${FINANCE_FOUR_TABLE_STATEMENT_TIMEOUT_MS:-30000}"
test "$lock_timeout_ms" = 5000
test "$statement_timeout_ms" = 30000

repo_dir="$(realpath -e -- "$FINANCE_REPOSITORY_DIR")"
script_repo="$(realpath -e -- "$runner_dir/../../../..")"
test "$repo_dir" = "$script_repo"
receipt_dir="$(realpath -e -- "$FINANCE_N8N_RECEIPT_DIR")"
test -d "$receipt_dir"

source_backup="$receipt_dir/finance-data-table-backup-v1.json"
migration_receipt="$receipt_dir/data-table-migration-receipt.json"
accepted_identity="$receipt_dir/finance-four-table-accepted-identity.json"
live_export="${FINANCE_N8N_LIVE_EXPORT:-$receipt_dir/finance-four-table-live-export.json}"
lock_path="$receipt_dir/finance-four-table-cutover.lock"
lock_receipt="$receipt_dir/finance-four-table-lock-receipt.json"
cutover_receipt="$receipt_dir/finance-four-table-cutover-receipt.json"
forward_receipt="$receipt_dir/finance-four-table-forward-receipt.json"
forward_runtime_receipt="$receipt_dir/finance-four-table-runtime-forward.json"
rollback_runtime_receipt="$receipt_dir/finance-four-table-runtime-rollback.json"
runtime_stdout="$receipt_dir/finance-four-table-runtime-${operation}.stdout.raw"
runtime_stderr="$receipt_dir/finance-four-table-runtime-${operation}.stderr.raw"
recovery_stdout="$receipt_dir/finance-four-table-runtime-${operation}-recovery.stdout.raw"
recovery_stderr="$receipt_dir/finance-four-table-runtime-${operation}-recovery.stderr.raw"
pre_readback="$receipt_dir/finance-data-table-readback-${operation}-pre.raw"
post_readback="$receipt_dir/finance-data-table-readback-${operation}-post.raw"
second_post_readback="$receipt_dir/finance-data-table-readback-${operation}-second-post.raw"
runtime_proof="$receipt_dir/finance-data-table-rollback-runtime-proof.json"
runtime_state="$receipt_dir/finance-data-table-disposable-runtime-state.json"
adapter="$repo_dir/integrations/n8n/setup-workflows/runner/n8n-cli-finance-data-table-digest.cjs"
runtime_script="$runner_dir/n8n-cli-four-table-cutover.cjs"
credential_bindings="$repo_dir/integrations/n8n/credential-bindings.json"
readback_parser="$runner_dir/parse_n8n_redacted_wrapper_output.py"
workflow_root="$repo_dir/integrations/n8n/workflows"
canonical_source="$receipt_dir/finance-four-table-canonical-source.json"
precondition_receipt="$receipt_dir/finance-four-table-precondition.json"
rollback_receipt_args=()
if [[ "$operation" = rollback ]]; then
  rollback_receipt_args+=(
    --forward-runtime-receipt "$forward_runtime_receipt"
    --forward-receipt "$forward_receipt"
  )
fi
protected_inputs=(
  "$source_backup"
  "$migration_receipt"
  "$accepted_identity"
  "$live_export"
  "$runtime_script"
  "$credential_bindings"
)
if [[ "$operation" = rollback ]]; then
  protected_inputs+=("$forward_receipt" "$forward_runtime_receipt")
fi

prepare_output() {
  local destination="$1"
  local protected temporary
  for protected in "${protected_inputs[@]}"; do
    test "$(realpath -m -- "$destination")" != "$(realpath -e -- "$protected")"
  done
  temporary="$(mktemp --tmpdir="$receipt_dir" .four-table-output.XXXXXX)"
  chmod 0600 "$temporary"
  mv -T -- "$temporary" "$destination"
  test -f "$destination"
  test ! -L "$destination"
  test "$(stat -c '%a' "$destination")" = 600
  test "$(stat -c '%h' "$destination")" = 1
  test "$(stat -c '%u' "$destination")" = "$(id -u)"
}
resolver_args=()
if [[ -n "${FINANCE_FOUR_TABLE_ALIAS_BUNDLE:-}${FINANCE_FOUR_TABLE_ALIAS_BUNDLE_SHA256:-}" ]]; then
  resolver_args+=(
    --alias-bundle "${FINANCE_FOUR_TABLE_ALIAS_BUNDLE:?ALIAS_BUNDLE_PINNED_INPUT_REQUIRED}"
    --alias-bundle-sha256 "${FINANCE_FOUR_TABLE_ALIAS_BUNDLE_SHA256:?ALIAS_BUNDLE_PINNED_INPUT_REQUIRED}"
  )
fi
if [[ -n "${FINANCE_FOUR_TABLE_VERIFICATION_ARTIFACTS:-}${FINANCE_FOUR_TABLE_VERIFICATION_ARTIFACTS_SHA256:-}" ]]; then
  resolver_args+=(
    --verification-artifacts "${FINANCE_FOUR_TABLE_VERIFICATION_ARTIFACTS:?VERIFICATION_ARTIFACTS_PINNED_INPUT_REQUIRED}"
    --verification-artifacts-sha256 "${FINANCE_FOUR_TABLE_VERIFICATION_ARTIFACTS_SHA256:?VERIFICATION_ARTIFACTS_PINNED_INPUT_REQUIRED}"
  )
fi
for path in "$source_backup" "$migration_receipt" "$accepted_identity" "$live_export" "$runtime_script" "$credential_bindings"; do
  test -f "$path"
  test ! -L "$path"
done
if [[ "$operation" = rollback ]]; then
  for path in "$forward_receipt" "$forward_runtime_receipt"; do
    test -f "$path"
    test ! -L "$path"
    test "$(stat -c '%a' "$path")" = 600
  done
fi
if [[ "$FINANCE_N8N_RUNTIME_MODE" = DISPOSABLE_ONLY ]]; then
  test -f "$adapter"
  test -f "$readback_parser"
  test -d "$workflow_root"
fi
test "$(stat -c '%a' "$source_backup")" = 600
test "$(stat -c '%a' "$live_export")" = 600
test ! -L "$live_export"
test "$(stat -c '%a' "$receipt_dir")" = 700
if [[ "$FINANCE_N8N_RUNTIME_MODE" = DISPOSABLE_ONLY ]]; then
  if [[ ! -e "$lock_path" ]]; then
    (
      umask 077
      : >"$lock_path"
    )
  fi
  test ! -L "$lock_path"
  chmod 0600 "$lock_path"
  exec 9<>"$lock_path"
  flock -n 9 || {
    echo "Exclusive four-table writer lock is busy" >&2
    exit 1
  }
fi

approved_digests="$(
  python3 - "$accepted_identity" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    identity = json.load(handle)
print(identity["migration_receipt_sha256"])
print(identity["source_backup_sha256"])
PY
)"
migration_sha="${approved_digests%%$'\n'*}"
source_backup_sha="${approved_digests#*$'\n'}"
test -n "$migration_sha"
test -n "$source_backup_sha"
test "$(stat -c '%a' "$migration_receipt")" = 600
identity_sha="$(
  python3 - "$accepted_identity" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["identity_sha256"])
PY
)"
test -n "$identity_sha"
source_head="$(git -C "$repo_dir" rev-parse HEAD)"
generator_head="$(python3 "$repo_dir/integrations/n8n/generate_data_table_migration.py" --schema-digest | tail -n 1)"
test -n "$source_head"
test -n "$generator_head"

validate_inputs() {
  local validation_json
  validation_json="$(python3 "$runner_dir/four_table_cutover.py" validate-inputs \
    "${resolver_args[@]}" "${rollback_receipt_args[@]}" \
    --source-backup "$source_backup" \
    --migration-receipt "$migration_receipt" \
    --migration-receipt-sha256 "$migration_sha" \
    --source-backup-sha256 "$source_backup_sha" \
    --operation-nonce "$FINANCE_FOUR_TABLE_OPERATION_NONCE" \
    --protected-quiescence-receipt-digest "$FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST" \
    --required-live-export-digest "$FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST" \
    --contract-bijection-digest "$FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST" \
    --repository-root "$repo_dir" \
    --project-id "$N8N_FINANCE_PROJECT_ID" \
    --accepted-identity "$accepted_identity" \
    --operator-ack "$1" \
    --runtime-action "$2" \
    --workflow-root "$workflow_root" \
    --live-export "$live_export" \
    --canonical-source-input "$canonical_source" \
    --lock-receipt "$lock_receipt" \
    --operation-kind "${3^^}")"
  readarray -t approved_runtime_digests < <(
    python3 -c 'import json,sys; value=json.load(sys.stdin); print(value["canonical_source_file_sha256"] or ""); print(value["credential_bindings_sha256"])' <<<"$validation_json"
  )
  approved_canonical_source_sha="${approved_runtime_digests[0]}"
  approved_credential_bindings_sha="${approved_runtime_digests[1]}"
}
recover_runtime_receipt() {
  local recovery_input="$1"
  shift
  local -a recovery_env=("$@")
  recovery_env+=(
    -e "FINANCE_FOUR_TABLE_RECOVER_JOURNAL=1"
    -e "FINANCE_FOUR_TABLE_RECOVERY_REASON=${operation^^}_RUNTIME_FAILURE"
  )
  local recovery_status
  prepare_output "$recovery_stdout"
  prepare_output "$recovery_stderr"
  if docker exec -i "${recovery_env[@]}" "$FINANCE_N8N_CONTAINER" node -e "$(<"$runtime_script")" \
    <"$recovery_input" >"$recovery_stdout" 2>"$recovery_stderr"; then
    recovery_status=0
  else
    recovery_status=$?
  fi
  chmod 0600 "$recovery_stdout" "$recovery_stderr"
  if ((recovery_status != 0)); then
    return "$recovery_status"
  fi
  local recovered_json="$receipt_dir/finance-four-table-runtime-${operation}-recovered.json"
  prepare_output "$recovered_json"
  grep '^finance four-table runtime verified:' "$recovery_stdout" |
    tail -n 1 |
    sed 's/^finance four-table runtime verified://' \
      >"$recovered_json"
  test -s "$recovered_json"
  chmod 0600 "$recovered_json"
  cp -- "$recovery_stdout" "$runtime_stdout"
  if [[ "$operation" = forward ]]; then
    prepare_output "$forward_runtime_receipt"
    cp -- "$recovered_json" "$forward_runtime_receipt"
    chmod 0600 "$forward_runtime_receipt"
  fi
}

run_runtime() {
  chmod 0600 "$lock_receipt" "$receipt_dir/finance-four-table-precondition.json"
  validate_inputs "$operator_ack" "$runtime_action" "$operation"
  local runtime_input="$canonical_source"
  if [[ "$operation" = rollback ]]; then
    local receipt_schema
    receipt_schema="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["forward_runtime_receipt_schema"])' "$receipt_dir/finance-four-table-precondition.json")"
    case "$receipt_schema" in
    finance-four-table-runtime-plan-v1) runtime_input="$forward_runtime_receipt" ;;
    finance-four-table-runtime-plan-v2 | finance-four-table-runtime-plan-v3) ;;
    *)
      echo "FORWARD_RUNTIME_RECEIPT_SCHEMA_INVALID" >&2
      return 1
      ;;
    esac
  fi

  local export_b64 lock_b64 credential_bindings_b64 canonical_source_sha runtime_json
  export_b64="$(base64 -w0 -- "$live_export")"
  lock_b64="$(base64 -w0 -- "$lock_receipt")"
  canonical_source_sha="$approved_canonical_source_sha"
  credential_bindings_b64="$(base64 -w0 -- "$credential_bindings")"
  local -a runtime_env=(
    -e "N8N_FINANCE_PROJECT_ID=$N8N_FINANCE_PROJECT_ID"
    -e "FINANCE_FOUR_TABLE_OPERATION=${operation^^}"
    -e "FINANCE_FOUR_TABLE_ACK=$operator_ack"
    -e "FINANCE_FOUR_TABLE_MIGRATION_SHA256=$migration_sha"
    -e "FINANCE_FOUR_TABLE_SOURCE_SHA256=$source_backup_sha"
    -e "FINANCE_FOUR_TABLE_IDENTITY_SHA256=$identity_sha"
    -e "FINANCE_FOUR_TABLE_REPOSITORY_ROOT=$repo_dir"
    -e "FINANCE_FOUR_TABLE_SOURCE_HEAD=$source_head"
    -e "FINANCE_FOUR_TABLE_GENERATOR_HEAD=$generator_head"
    -e "FINANCE_FOUR_TABLE_OPERATION_NONCE=$FINANCE_FOUR_TABLE_OPERATION_NONCE"
    -e "FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST=$FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST"
    -e "FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST=$FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST"
    -e "FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST=$FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST"
    -e "FINANCE_FOUR_TABLE_LOCK_TIMEOUT_MS=$lock_timeout_ms"
    -e "FINANCE_FOUR_TABLE_STATEMENT_TIMEOUT_MS=$statement_timeout_ms"
    -e "FINANCE_FOUR_TABLE_EXPORT_B64=$export_b64"
    -e "FINANCE_FOUR_TABLE_LOCK_B64=$lock_b64"
    -e "FINANCE_FOUR_TABLE_CREDENTIAL_BINDINGS_SHA256=$approved_credential_bindings_sha"
    -e "FINANCE_FOUR_TABLE_CREDENTIAL_BINDINGS_B64=$credential_bindings_b64"
  )
  if [[ -n "$canonical_source_sha" ]]; then
    runtime_env+=(
      -e "FINANCE_FOUR_TABLE_CANONICAL_SOURCE_FILE_SHA256=$canonical_source_sha"
    )
  fi
  if [[ "$operation" = rollback ]]; then
    test -f "$forward_runtime_receipt"
    test ! -L "$forward_runtime_receipt"
    test "$(stat -c '%a' "$forward_runtime_receipt")" = 600
    runtime_env+=(-e "FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64=$(base64 -w0 -- "$forward_runtime_receipt")")
  fi

  prepare_output "$runtime_stdout"
  prepare_output "$runtime_stderr"
  if docker exec -i "${runtime_env[@]}" "$FINANCE_N8N_CONTAINER" node -e "$(<"$runtime_script")" \
    <"$runtime_input" >"$runtime_stdout" 2>"$runtime_stderr"; then
    runtime_status=0
  else
    runtime_status=$?
    chmod 0600 "$runtime_stdout" "$runtime_stderr"
    if ! recover_runtime_receipt "$runtime_input" "${runtime_env[@]}"; then
      echo "Unable to recover the committed ${operation} runtime journal" >&2
      return "$runtime_status"
    fi
  fi
  chmod 0600 "$runtime_stdout" "$runtime_stderr"
  runtime_json="$receipt_dir/finance-four-table-runtime-${operation}.json"
  prepare_output "$runtime_json"
  grep '^finance four-table runtime verified:' "$runtime_stdout" |
    tail -n 1 |
    sed 's/^finance four-table runtime verified://' \
      >"$runtime_json"
  test -s "$runtime_json"
  chmod 0600 "$runtime_json"

  grep -F '"durable_journal":true' "$runtime_json" >/dev/null
  grep -F '"commit_protocol":"postgresql_synchronous_wal"' "$runtime_json" >/dev/null
}

preflight() {
  local -a canonical_args=(--canonical-source-output "$canonical_source")
  prepare_output "$precondition_receipt"
  python3 "$runner_dir/four_table_cutover.py" preflight \
    "${resolver_args[@]}" "${canonical_args[@]}" "${rollback_receipt_args[@]}" \
    --source-backup "$source_backup" \
    --migration-receipt "$migration_receipt" \
    --migration-receipt-sha256 "$migration_sha" \
    --source-backup-sha256 "$source_backup_sha" \
    --operation-nonce "$FINANCE_FOUR_TABLE_OPERATION_NONCE" \
    --protected-quiescence-receipt-digest "$FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST" \
    --required-live-export-digest "$FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST" \
    --contract-bijection-digest "$FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST" \
    --repository-root "$repo_dir" \
    --project-id "$N8N_FINANCE_PROJECT_ID" \
    --accepted-identity "$accepted_identity" \
    --operator-ack "$1" \
    --runtime-action "$2" \
    --workflow-root "$workflow_root" \
    --live-export "$live_export" \
    --operation-kind "${3^^}" \
    --output "$lock_receipt" >"$precondition_receipt"
  chmod 0600 "$lock_receipt" "$precondition_receipt"
}
run_rollback_restore() {
  python3 "$runner_dir/four_table_cutover.py" rollback-runtime \
    "${resolver_args[@]}" \
    --source-backup "$source_backup" \
    --migration-receipt "$migration_receipt" \
    --migration-receipt-sha256 "$migration_sha" \
    --source-backup-sha256 "$source_backup_sha" \
    --operation-nonce "$FINANCE_FOUR_TABLE_OPERATION_NONCE" \
    --protected-quiescence-receipt-digest "$FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST" \
    --required-live-export-digest "$FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST" \
    --contract-bijection-digest "$FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST" \
    --repository-root "$repo_dir" \
    --project-id "$N8N_FINANCE_PROJECT_ID" \
    --accepted-identity "$accepted_identity" \
    --operator-ack "$operator_ack" \
    --runtime-action "$runtime_action" \
    --workflow-root "$workflow_root" \
    --live-export "$live_export" \
    --lock-receipt "$lock_receipt" \
    --forward-runtime-receipt "$forward_runtime_receipt" \
    --forward-receipt "$forward_receipt" \
    --rollback-runtime-receipt "$rollback_runtime_receipt" \
    --runtime-state "$runtime_state" \
    --output "$runtime_proof" >/dev/null
  chmod 0600 "$runtime_state" "$runtime_proof"
}

run_readback() {
  local destination="$1"
  local phase="$2"
  prepare_output "$destination"
  docker exec -i \
    -e FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY \
    -e FINANCE_DATA_TABLE_READBACK_PHASE="$phase" \
    -e N8N_FINANCE_PROJECT_ID="$N8N_FINANCE_PROJECT_ID" \
    -e FINANCE_DATA_TABLE_MIGRATION_RECEIPT_SHA256="$migration_sha" \
    "$FINANCE_N8N_CONTAINER" node - list:workflow <"$adapter" >"$destination"
  python3 "$readback_parser" data-table-receipt <"$destination" >/dev/null
  chmod 0600 "$destination"
}

case "$operation" in
forward)
  test "${FOUR_TABLE_FORWARD_ACK:-}" = "FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
  operator_ack="FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
  runtime_action="FOUR_TABLE_FORWARD_RUNTIME_EXECUTED"
  ;;
rollback)
  test "${FOUR_TABLE_ROLLBACK_ACK:-}" = "FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
  operator_ack="FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
  runtime_action="FOUR_TABLE_ROLLBACK_RUNTIME_EXECUTED"
  ;;
esac

preflight "$operator_ack" "$runtime_action" "$operation"
if [[ "$FINANCE_N8N_RUNTIME_MODE" = PRODUCTION_ONLY ]]; then
  run_runtime
  exit 0
fi

case "$operation" in
forward)
  run_readback "$pre_readback" FORWARD_PRE
  validate_inputs "$operator_ack" "$runtime_action" forward
  run_runtime
  run_readback "$post_readback" FORWARD_POST
  validate_inputs "$operator_ack" "$runtime_action" forward
  run_readback "$second_post_readback" FORWARD_POST
  ;;
rollback)
  test -f "$forward_receipt"
  test ! -L "$forward_receipt"
  test "$(stat -c '%a' "$forward_receipt")" = 600
  run_readback "$pre_readback" ROLLBACK_PRE
  validate_inputs "$operator_ack" "$runtime_action" rollback
  run_runtime
  run_rollback_restore
  run_readback "$post_readback" ROLLBACK_POST
  ;;
esac
args=(
  "$operation"
  "${resolver_args[@]}"
  --source-backup "$source_backup"
  --migration-receipt "$migration_receipt"
  --migration-receipt-sha256 "$migration_sha"
  --source-backup-sha256 "$source_backup_sha"
  --operation-nonce "$FINANCE_FOUR_TABLE_OPERATION_NONCE"
  --protected-quiescence-receipt-digest "$FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST"
  --required-live-export-digest "$FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST"
  --contract-bijection-digest "$FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST"
  --repository-root "$repo_dir"
  --project-id "$N8N_FINANCE_PROJECT_ID"
  --accepted-identity "$accepted_identity"
  --operator-ack "$operator_ack"
  --runtime-action "$runtime_action"
  --workflow-root "$workflow_root"
  --live-export "$live_export"
  --lock-receipt "$lock_receipt"
  --pre-readback-raw "$pre_readback"
  --post-readback-raw "$post_readback"
  --runtime-state "$runtime_state"
  --forward-runtime-receipt "$forward_runtime_receipt"
  --output "$cutover_receipt"
)
if [[ "$operation" = forward ]]; then
  args+=(--second-post-readback-raw "$second_post_readback")
fi
if [[ "$operation" = rollback ]]; then
  args+=(
    --forward-receipt "$forward_receipt"
    --runtime-proof "$runtime_proof"
    --rollback-runtime-receipt "$rollback_runtime_receipt"
  )
fi

python3 "$runner_dir/four_table_cutover.py" "${args[@]}"
if [[ "$operation" = forward ]]; then
  forward_receipt_temp="$(mktemp --tmpdir="$receipt_dir" .finance-four-table-forward.XXXXXX)"
  trap 'rm -f -- "$forward_receipt_temp"' EXIT
  cp -- "$cutover_receipt" "$forward_receipt_temp"
  chmod 0600 "$forward_receipt_temp"
  mv -T -- "$forward_receipt_temp" "$forward_receipt"
  trap - EXIT
  test -f "$forward_receipt"
  test ! -L "$forward_receipt"
  test "$(stat -c '%a' "$forward_receipt")" = 600
  test "$(stat -c '%h' "$forward_receipt")" = 1
  test "$(stat -c '%u' "$forward_receipt")" = "$(id -u)"
  for protected in "$source_backup" "$migration_receipt" "$accepted_identity" "$live_export" "$forward_runtime_receipt"; do
    test "$(realpath -e -- "$forward_receipt")" != "$(realpath -e -- "$protected")"
  done
fi
