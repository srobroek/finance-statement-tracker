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
test "$FINANCE_N8N_RUNTIME_MODE" = "PRODUCTION_ONLY"

repo_dir="$(realpath -e -- "$FINANCE_REPOSITORY_DIR")"
script_repo="$(realpath -e -- "$runner_dir/../../../..")"
test "$repo_dir" = "$script_repo"
receipt_dir="$(realpath -e -- "$FINANCE_N8N_RECEIPT_DIR")"
test -d "$receipt_dir"

source_backup="$receipt_dir/finance-data-table-backup-v1.json"
migration_receipt="$receipt_dir/data-table-migration-receipt.json"
accepted_identity="$receipt_dir/finance-four-table-accepted-identity.json"
live_export="${FINANCE_N8N_LIVE_EXPORT:-$receipt_dir/finance-four-table-live-export.json}"
lock_receipt="$receipt_dir/finance-four-table-lock-receipt.json"
forward_runtime_receipt="$receipt_dir/finance-four-table-runtime-forward.json"
rollback_runtime_receipt="$receipt_dir/finance-four-table-runtime-rollback.json"
runtime_output="$receipt_dir/finance-four-table-runtime-${operation}.raw"
adapter="$repo_dir/integrations/n8n/setup-workflows/runner/n8n-cli-finance-data-table-digest.cjs"
runtime_script="$runner_dir/n8n-cli-four-table-cutover.cjs"
readback_parser="$runner_dir/parse_n8n_redacted_wrapper_output.py"
workflow_root="$repo_dir/integrations/n8n/workflows"

for path in "$source_backup" "$migration_receipt" "$accepted_identity" "$live_export" "$adapter" "$runtime_script" "$readback_parser"; do
  test -f "$path"
done
test "$(stat -c '%a' "$source_backup")" = 600
test "$(stat -c '%a' "$migration_receipt")" = 600
test "$(stat -c '%a' "$accepted_identity")" = 600
test "$(stat -c '%a' "$live_export")" = 600
test ! -L "$live_export"

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
identity_sha="$(python3 - "$accepted_identity" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["identity_sha256"])
PY
)"
test -n "$identity_sha"

ack="FOUR_TABLE_${operation^^}_REQUIRES_NAMED_OPERATOR_GATE"
runtime_action="FOUR_TABLE_${operation^^}_RUNTIME_EXECUTED"
python3 "$runner_dir/four_table_cutover.py" preflight \
  --source-backup "$source_backup" \
  --migration-receipt "$migration_receipt" \
  --migration-receipt-sha256 "$migration_sha" \
  --source-backup-sha256 "$source_backup_sha" \
  --repository-root "$repo_dir" \
  --accepted-identity "$accepted_identity" \
  --operator-ack "$ack" \
  --runtime-action "$runtime_action" \
  --workflow-root "$workflow_root" \
  --live-export "$live_export" \
  --operation-kind "${operation^^}" \
  --output "$lock_receipt" > "$receipt_dir/finance-four-table-production-precondition.json"
chmod 0600 "$lock_receipt" "$receipt_dir/finance-four-table-production-precondition.json"

export_b64="$(base64 -w0 -- "$live_export")"
lock_b64="$(base64 -w0 -- "$lock_receipt")"
runtime_env=(
  -e "N8N_FINANCE_PROJECT_ID=$N8N_FINANCE_PROJECT_ID"
  -e "FINANCE_FOUR_TABLE_OPERATION=${operation^^}"
  -e "FINANCE_FOUR_TABLE_ACK=$ack"
  -e "FINANCE_FOUR_TABLE_MIGRATION_SHA256=$migration_sha"
  -e "FINANCE_FOUR_TABLE_SOURCE_SHA256=$source_backup_sha"
  -e "FINANCE_FOUR_TABLE_IDENTITY_SHA256=$identity_sha"
  -e "FINANCE_FOUR_TABLE_EXPORT_B64=$export_b64"
  -e "FINANCE_FOUR_TABLE_LOCK_B64=$lock_b64"
)
if [[ "$operation" = rollback ]]; then
  test -f "$forward_runtime_receipt"
  forward_b64="$(base64 -w0 -- "$forward_runtime_receipt")"
  runtime_env+=( -e "FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64=$forward_b64" )
fi

docker exec -i "${runtime_env[@]}" "$FINANCE_N8N_CONTAINER" node - < "$runtime_script" > "$runtime_output"
grep '^finance four-table runtime verified:' "$runtime_output" \
  | tail -n 1 \
  | sed 's/^finance four-table runtime verified://' \
  > "$receipt_dir/finance-four-table-runtime-${operation}.json"
chmod 0600 "$receipt_dir/finance-four-table-runtime-${operation}.json"

if [[ "$operation" = forward ]]; then
  test -s "$forward_runtime_receipt"
  runtime_env+=( -e "FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64=$(base64 -w0 -- "$forward_runtime_receipt")" )
  docker exec -i "${runtime_env[@]}" "$FINANCE_N8N_CONTAINER" node - < "$runtime_script" > "$receipt_dir/finance-four-table-runtime-forward-replay.raw"
  grep '^finance four-table runtime verified:' "$receipt_dir/finance-four-table-runtime-forward-replay.raw" \
    | tail -n 1 \
    | grep -F '"replay_noop":true' >/dev/null
fi
