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
test "$FINANCE_N8N_RUNTIME_MODE" = "DISPOSABLE_ONLY"

repo_dir="$(realpath -e -- "$FINANCE_REPOSITORY_DIR")"
script_repo="$(realpath -e -- "$runner_dir/../../../..")"
test "$repo_dir" = "$script_repo"
receipt_dir="$(realpath -e -- "$FINANCE_N8N_RECEIPT_DIR")"
test -d "$receipt_dir"

source_backup="$receipt_dir/finance-data-table-backup-v1.json"
migration_receipt="$receipt_dir/data-table-migration-receipt.json"
accepted_identity="$receipt_dir/finance-four-table-accepted-identity.json"
cutover_receipt="$receipt_dir/finance-four-table-cutover-receipt.json"
forward_receipt="$receipt_dir/finance-four-table-forward-receipt.json"
pre_readback="$receipt_dir/finance-data-table-readback-${operation}-pre.raw"
post_readback="$receipt_dir/finance-data-table-readback-${operation}-post.raw"
second_post_readback="$receipt_dir/finance-data-table-readback-${operation}-second-post.raw"
runtime_proof="$receipt_dir/finance-data-table-rollback-runtime-proof.json"
adapter="$repo_dir/integrations/n8n/setup-workflows/runner/n8n-cli-finance-data-table-digest.cjs"
workflow_root="$repo_dir/integrations/n8n/workflows"
test -f "$source_backup"
test -f "$migration_receipt"
test -f "$accepted_identity"
test -f "$adapter"
test -d "$workflow_root"

migration_sha="$(sha256sum "$migration_receipt" | awk '{print $1}')"
test "$(stat -c '%a' "$migration_receipt")" = 600

run_readback() {
  local destination="$1"
  local phase="$2"
  docker exec -i \
    -e FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY \
    -e FINANCE_DATA_TABLE_READBACK_PHASE="$phase" \
    -e N8N_FINANCE_PROJECT_ID="$N8N_FINANCE_PROJECT_ID" \
    -e FINANCE_DATA_TABLE_MIGRATION_RECEIPT_SHA256="$migration_sha" \
    "$FINANCE_N8N_CONTAINER" node - list:workflow < "$adapter" > "$destination"
}

case "$operation" in
  forward)
    test "${FOUR_TABLE_FORWARD_ACK:-}" = "FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
    operator_ack="FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
    runtime_action="FOUR_TABLE_FORWARD_RUNTIME_EXECUTED"
    run_readback "$pre_readback" FORWARD_PRE
    docker exec "$FINANCE_N8N_CONTAINER" n8n execute --id 10000000-0000-4000-8000-000000000019
    run_readback "$post_readback" FORWARD_POST
    docker exec "$FINANCE_N8N_CONTAINER" n8n execute --id 10000000-0000-4000-8000-000000000019
    run_readback "$second_post_readback" FORWARD_POST
    ;;
  rollback)
    test "${FOUR_TABLE_ROLLBACK_ACK:-}" = "FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
    test -f "$forward_receipt"
    operator_ack="FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
    runtime_action="FOUR_TABLE_ROLLBACK_RUNTIME_EXECUTED"
    run_readback "$pre_readback" ROLLBACK
    python3 "$runner_dir/four_table_cutover.py" rollback-rehearsal \
      --source-backup "$source_backup" \
      --migration-receipt "$migration_receipt" \
      --migration-receipt-sha256 "$migration_sha" \
      --repository-root "$repo_dir" \
      --accepted-identity "$accepted_identity" \
      --operator-ack "$operator_ack" \
      --runtime-action "$runtime_action" \
      --workflow-root "$workflow_root" \
      --output "$runtime_proof"
    run_readback "$post_readback" ROLLBACK
    ;;
esac

args=(
  "$operation"
  --source-backup "$source_backup"
  --migration-receipt "$migration_receipt"
  --migration-receipt-sha256 "$migration_sha"
  --repository-root "$repo_dir"
  --accepted-identity "$accepted_identity"
  --operator-ack "$operator_ack"
  --runtime-action "$runtime_action"
  --workflow-root "$workflow_root"
  --pre-readback-raw "$pre_readback"
  --post-readback-raw "$post_readback"
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
