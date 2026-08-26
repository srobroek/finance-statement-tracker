#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  printf 'usage: %s forward|rollback\n' "$0" >&2
  exit 2
}

operation="${1:-}"
case "$operation" in
  forward|rollback) ;;
  *) usage ;;
esac

runner_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
: "${FINANCE_REPOSITORY_DIR:?FINANCE_REPOSITORY_DIR is required}"
: "${FINANCE_N8N_RECEIPT_DIR:?FINANCE_N8N_RECEIPT_DIR is required}"
: "${FINANCE_N8N_CONTAINER:?FINANCE_N8N_CONTAINER is required}"
: "${N8N_FINANCE_PROJECT_ID:?N8N_FINANCE_PROJECT_ID is required}"
: "${FINANCE_N8N_RUNTIME_MODE:?FINANCE_N8N_RUNTIME_MODE is required}"
case "$FINANCE_N8N_RUNTIME_MODE" in
  DISPOSABLE_ONLY|PRODUCTION_ONLY) ;;
  *) echo "FINANCE_N8N_RUNTIME_MODE must be DISPOSABLE_ONLY or PRODUCTION_ONLY" >&2; exit 1 ;;
esac

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
runtime_output="$receipt_dir/finance-four-table-runtime-${operation}.raw"
pre_readback="$receipt_dir/finance-data-table-readback-${operation}-pre.raw"
post_readback="$receipt_dir/finance-data-table-readback-${operation}-post.raw"
second_post_readback="$receipt_dir/finance-data-table-readback-${operation}-second-post.raw"
runtime_proof="$receipt_dir/finance-data-table-rollback-runtime-proof.json"
runtime_state="$receipt_dir/finance-data-table-disposable-runtime-state.json"
adapter="$repo_dir/integrations/n8n/setup-workflows/runner/n8n-cli-finance-data-table-digest.cjs"
runtime_script="$runner_dir/n8n-cli-four-table-cutover.cjs"
readback_parser="$runner_dir/parse_n8n_redacted_wrapper_output.py"
workflow_root="$repo_dir/integrations/n8n/workflows"
for path in "$source_backup" "$migration_receipt" "$accepted_identity" "$live_export" "$runtime_script"; do
  test -f "$path"
done
if [[ "$FINANCE_N8N_RUNTIME_MODE" = DISPOSABLE_ONLY ]]; then
  test -f "$adapter"
  test -f "$readback_parser"
  test -d "$workflow_root"
fi
test "$(stat -c '%a' "$source_backup")" = 600
test "$(stat -c '%a' "$live_export")" = 600
if [[ -L "$live_export" ]]; then
  echo "Live workflow export must not be a symlink" >&2
  exit 1
fi
if [[ "$FINANCE_N8N_RUNTIME_MODE" = DISPOSABLE_ONLY ]]; then
  if [[ ! -e "$lock_path" ]]; then
    (umask 077; : > "$lock_path")
  fi
  test ! -L "$lock_path"
  chmod 0600 "$lock_path"
  exec 9<>"$lock_path"
  flock -n 9 || { echo "Exclusive four-table writer lock is busy" >&2; exit 1; }
fi

approved_digests="$(python3 - "$accepted_identity" <<'PY'
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
identity_sha="$(python3 - "$accepted_identity" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["identity_sha256"])
PY
)"
test -n "$identity_sha"

validate_inputs() {
  python3 "$runner_dir/four_table_cutover.py" validate-inputs \
    --source-backup "$source_backup" \
    --migration-receipt "$migration_receipt" \
    --migration-receipt-sha256 "$migration_sha" \
    --source-backup-sha256 "$source_backup_sha" \
    --repository-root "$repo_dir" \
    --accepted-identity "$accepted_identity" \
    --operator-ack "$1" \
    --runtime-action "$2" \
    --workflow-root "$workflow_root" \
    --live-export "$live_export" \
    --lock-receipt "$lock_receipt" \
    --operation-kind "${3^^}" > /dev/null
}

run_production_runtime() {
  preflight "$operator_ack" "$runtime_action" "$operation"
  chmod 0600 "$lock_receipt" "$receipt_dir/finance-four-table-precondition.json"

  local export_b64 lock_b64 runtime_json
  export_b64="$(base64 -w0 -- "$live_export")"
  lock_b64="$(base64 -w0 -- "$lock_receipt")"
  local -a runtime_env=(
    -e "N8N_FINANCE_PROJECT_ID=$N8N_FINANCE_PROJECT_ID"
    -e "FINANCE_FOUR_TABLE_OPERATION=${operation^^}"
    -e "FINANCE_FOUR_TABLE_ACK=$operator_ack"
    -e "FINANCE_FOUR_TABLE_MIGRATION_SHA256=$migration_sha"
    -e "FINANCE_FOUR_TABLE_SOURCE_SHA256=$source_backup_sha"
    -e "FINANCE_FOUR_TABLE_IDENTITY_SHA256=$identity_sha"
    -e "FINANCE_FOUR_TABLE_EXPORT_B64=$export_b64"
    -e "FINANCE_FOUR_TABLE_LOCK_B64=$lock_b64"
  )
  if [[ "$operation" = rollback ]]; then
    test -f "$forward_runtime_receipt"
    runtime_env+=( -e "FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64=$(base64 -w0 -- "$forward_runtime_receipt")" )
  fi

  docker exec -i "${runtime_env[@]}" "$FINANCE_N8N_CONTAINER" node - < "$runtime_script" > "$runtime_output"
  runtime_json="$receipt_dir/finance-four-table-runtime-${operation}.json"
  grep '^finance four-table runtime verified:' "$runtime_output" \
    | tail -n 1 \
    | sed 's/^finance four-table runtime verified://' \
    > "$runtime_json"
  chmod 0600 "$runtime_json"

  if [[ "$operation" = forward ]]; then
    grep -F '"replay_noop":true' "$runtime_json" >/dev/null
  fi
}

preflight() {
  python3 "$runner_dir/four_table_cutover.py" preflight \
    --source-backup "$source_backup" \
    --migration-receipt "$migration_receipt" \
    --migration-receipt-sha256 "$migration_sha" \
    --source-backup-sha256 "$source_backup_sha" \
    --repository-root "$repo_dir" \
    --accepted-identity "$accepted_identity" \
    --operator-ack "$1" \
    --runtime-action "$2" \
    --workflow-root "$workflow_root" \
    --live-export "$live_export" \
    --operation-kind "${3^^}" \
    --output "$lock_receipt" > "$receipt_dir/finance-four-table-precondition.json"
}

run_readback() {
  local destination="$1"
  local phase="$2"
  docker exec -i \
    -e FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY \
    -e FINANCE_DATA_TABLE_READBACK_PHASE="$phase" \
    -e N8N_FINANCE_PROJECT_ID="$N8N_FINANCE_PROJECT_ID" \
    -e FINANCE_DATA_TABLE_MIGRATION_RECEIPT_SHA256="$migration_sha" \
    "$FINANCE_N8N_CONTAINER" node - list:workflow < "$adapter" > "$destination"
  python3 "$readback_parser" data-table-receipt < "$destination" > /dev/null
}

if [[ "$FINANCE_N8N_RUNTIME_MODE" = PRODUCTION_ONLY ]]; then
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
  run_production_runtime
  exit 0
fi

case "$operation" in
  forward)
    test "${FOUR_TABLE_FORWARD_ACK:-}" = "FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
    operator_ack="FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
    runtime_action="FOUR_TABLE_FORWARD_RUNTIME_EXECUTED"
    preflight "$operator_ack" "$runtime_action" forward
    run_readback "$pre_readback" FORWARD_PRE
    validate_inputs "$operator_ack" "$runtime_action" forward
    docker exec "$FINANCE_N8N_CONTAINER" n8n execute --id 10000000-0000-4000-8000-000000000019
    run_readback "$post_readback" FORWARD_POST
    validate_inputs "$operator_ack" "$runtime_action" forward
    docker exec "$FINANCE_N8N_CONTAINER" n8n execute --id 10000000-0000-4000-8000-000000000019
    run_readback "$second_post_readback" FORWARD_POST
    ;;
  rollback)
    test "${FOUR_TABLE_ROLLBACK_ACK:-}" = "FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
    test -f "$forward_receipt"
    operator_ack="FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
    runtime_action="FOUR_TABLE_ROLLBACK_RUNTIME_EXECUTED"
    preflight "$operator_ack" "$runtime_action" rollback
    run_readback "$pre_readback" ROLLBACK_PRE
    python3 "$runner_dir/four_table_cutover.py" rollback-runtime \
      --source-backup "$source_backup" \
      --migration-receipt "$migration_receipt" \
      --migration-receipt-sha256 "$migration_sha" \
      --source-backup-sha256 "$source_backup_sha" \
      --repository-root "$repo_dir" \
      --accepted-identity "$accepted_identity" \
      --operator-ack "$operator_ack" \
      --runtime-action "$runtime_action" \
      --workflow-root "$workflow_root" \
      --live-export "$live_export" \
      --lock-receipt "$lock_receipt" \
      --runtime-state "$runtime_state" \
      --output "$runtime_proof"
    run_readback "$post_readback" ROLLBACK_POST
    ;;
esac

args=(
  "$operation"
  --source-backup "$source_backup"
  --migration-receipt "$migration_receipt"
  --migration-receipt-sha256 "$migration_sha"
  --source-backup-sha256 "$source_backup_sha"
  --repository-root "$repo_dir"
  --accepted-identity "$accepted_identity"
  --operator-ack "$operator_ack"
    --runtime-action "$runtime_action"
    --workflow-root "$workflow_root"
    --live-export "$live_export"
    --lock-receipt "$lock_receipt"
  --pre-readback-raw "$pre_readback"
  --post-readback-raw "$post_readback"
  --runtime-state "$runtime_state"
  --output "$cutover_receipt"
)
if [[ "$operation" = forward ]]; then
  args+=(--second-post-readback-raw "$second_post_readback")
fi
if [[ "$operation" = rollback ]]; then
  args+=(--forward-receipt "$forward_receipt" --runtime-proof "$runtime_proof")
fi

python3 "$runner_dir/four_table_cutover.py" "${args[@]}"
if [[ "$operation" = forward ]]; then
  cp -- "$cutover_receipt" "$forward_receipt"
  chmod 0600 "$forward_receipt"
fi
