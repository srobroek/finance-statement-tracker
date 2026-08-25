#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s forward|rollback\n' "$0" >&2
  exit 2
}

operation="${1:-}"
case "$operation" in
  forward|rollback) ;;
  *) usage ;;
esac

runner_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

: "${FINANCE_DATA_TABLE_SOURCE_BACKUP:?FINANCE_DATA_TABLE_SOURCE_BACKUP is required}"
: "${FINANCE_DATA_TABLE_MIGRATION_RECEIPT:?FINANCE_DATA_TABLE_MIGRATION_RECEIPT is required}"
: "${FINANCE_DATA_TABLE_MIGRATION_RECEIPT_SHA256:?FINANCE_DATA_TABLE_MIGRATION_RECEIPT_SHA256 is required}"
: "${FINANCE_DATA_TABLE_SOURCE_HEAD:?FINANCE_DATA_TABLE_SOURCE_HEAD is required}"
: "${FINANCE_DATA_TABLE_GENERATOR_HEAD:?FINANCE_DATA_TABLE_GENERATOR_HEAD is required}"
: "${FINANCE_DATA_TABLE_CUTOVER_RECEIPT:?FINANCE_DATA_TABLE_CUTOVER_RECEIPT is required}"
: "${FINANCE_DATA_TABLE_WORKFLOW_ROOT:?FINANCE_DATA_TABLE_WORKFLOW_ROOT is required}"

case "$operation" in
  forward)
    test "${FOUR_TABLE_FORWARD_ACK:-}" = "FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
    operator_ack="FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE"
    ;;
  rollback)
    test "${FOUR_TABLE_ROLLBACK_ACK:-}" = "FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
    : "${FINANCE_DATA_TABLE_FORWARD_RECEIPT:?FINANCE_DATA_TABLE_FORWARD_RECEIPT is required for rollback}"
    operator_ack="FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE"
    ;;
esac

args=(
  "$operation"
  --source-backup "$FINANCE_DATA_TABLE_SOURCE_BACKUP"
  --migration-receipt "$FINANCE_DATA_TABLE_MIGRATION_RECEIPT"
  --migration-receipt-sha256 "$FINANCE_DATA_TABLE_MIGRATION_RECEIPT_SHA256"
  --source-head "$FINANCE_DATA_TABLE_SOURCE_HEAD"
  --generator-head "$FINANCE_DATA_TABLE_GENERATOR_HEAD"
  --operator-ack "$operator_ack"
  --workflow-root "$FINANCE_DATA_TABLE_WORKFLOW_ROOT"
  --output "$FINANCE_DATA_TABLE_CUTOVER_RECEIPT"
)
if [[ -n "${FINANCE_DATA_TABLE_READBACK_RAW:-}" ]]; then
  args+=(--readback-raw "$FINANCE_DATA_TABLE_READBACK_RAW")
fi
if [[ "$operation" = rollback ]]; then
  args+=(--forward-receipt "$FINANCE_DATA_TABLE_FORWARD_RECEIPT")
fi

exec python3 "$runner_dir/four_table_cutover.py" "${args[@]}"
