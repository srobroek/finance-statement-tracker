#!/usr/bin/env bash
set -euo pipefail
umask 077

# The drill consumes the finance backup contract and owns only disposable
# sidecars. It never reads the retained env file or a live Compose project.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_SCRIPT="${SCRIPT_DIR}/verify-backup.py"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SOURCE_SCRIPT_SHA256="$(sha256sum "${BASH_SOURCE[0]}" | awk '{print $1}')"
SOURCE_COMMIT="$(git -C "${SOURCE_ROOT}" rev-parse HEAD 2>/dev/null || true)"
REPEAT_COUNT="${FINANCE_CASHBACK_RESTORE_RUNS:-2}"
BACKUP_ROOT="${FINANCE_CASHBACK_BACKUP_ROOT:-/opt/backups/finance-actual}"
BACKUP_PATH="${FINANCE_CASHBACK_BACKUP_PATH:-}"
RECEIPT_PATH="${FINANCE_CASHBACK_RESTORE_RECEIPT:-}"
IMAGE="${FINANCE_CASHBACK_RESTORE_IMAGE:-ghcr.io/srobroek/finance-statement-tracker-cashback-control:main}"
RUNTIME="${FINANCE_CONTAINER_RUNTIME:-}"
HEALTH_ATTEMPTS="${FINANCE_CASHBACK_RESTORE_HEALTH_ATTEMPTS:-60}"
TEMP_ROOT="${FINANCE_CASHBACK_RESTORE_TEMP_ROOT:-/tmp}"

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
finished_at=""
temp_dir=""
verify_file=""
backup_root_dir=""
runtime_name=""
runtime_available=false
runtime_verified=false
image_digest=""
backup_name=""
archive_sha256=""
archive_bytes=""
backup_verified=false
failure_code=""
failure_stage=""
failure_detail=""
status="failed"
cleanup_verified=true
current_container=""
current_data_dir=""
last_sidecar_id=""
declare -a run_records=()

usage() {
  cat <<'EOF'
Usage: restore-cashback-disposable.sh [options]

Verify the authoritative finance backup, restore only the Cashback SQLite
database into disposable isolated sidecars, restart and health-check each
sidecar, compare logical SQLite state, and remove each exact disposable ID.

Options:
  --backup-root PATH   Backup root (default: FINANCE_CASHBACK_BACKUP_ROOT)
  --backup-path PATH   Exact timestamped backup directory
  --receipt PATH       Redacted mode-0600 JSON receipt
  --image IMAGE        Local Cashback image (pulling is disabled)
  --runtime COMMAND    docker or podman command
  --repeat COUNT       Number of independent runs (default: 2)
  --help               Show this message
EOF
}

record_failure() {
  if [[ -z "${failure_code}" ]]; then
    failure_code="$1"
    failure_stage="$2"
  fi
}

json_value() {
  local field="$1"
  python3 - "${verify_file}" "${field}" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
value = payload.get(sys.argv[2])
if value is None:
    raise SystemExit(f"missing verification field: {sys.argv[2]}")
print(value)
PY
}

logical_state() {
  python3 - "$1" <<'PY'
import base64
import hashlib
import json
import sqlite3
import sys

path = sys.argv[1]

def encoded(value):
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bytes):
        return {"type": "blob", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, bool):
        return {"type": "integer", "value": int(value)}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        return {"type": "real", "value": repr(value)}
    return {"type": "text", "value": str(value)}

def identifier(value):
    return '"' + value.replace('"', '""') + '"'

uri = f"file:{path}?mode=ro"
with sqlite3.connect(uri, uri=True) as connection:
    connection.row_factory = sqlite3.Row
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("sqlite integrity check failed")
    schema = [
        {
            "type": row["type"],
            "name": row["name"],
            "table": row["tbl_name"],
            "sql": row["sql"],
        }
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name, tbl_name"
        )
    ]
    tables = {}
    row_count = 0
    table_names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in table_names:
        columns = [
            {
                "cid": row[0],
                "name": row[1],
                "type": row[2],
                "notnull": row[3],
                "default": row[4],
                "pk": row[5],
            }
            for row in connection.execute(f"PRAGMA table_info({identifier(table)})")
        ]
        raw_rows = list(connection.execute(f"SELECT * FROM {identifier(table)}"))
        if table in {"push_subscriptions", "push_deliveries"} and raw_rows:
            raise SystemExit(f"{table} must be empty in a restore proof")
        if table == "push_state":
            if any(str(row[0]) != "routing-map" for row in raw_rows):
                raise SystemExit("push_state contains an unallowed key")
            raw_rows = []
        rows = [[encoded(value) for value in row] for row in raw_rows]
        rows.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))
        row_count += len(rows)
        tables[table] = {"columns": columns, "rows": rows}

logical = {"schema": schema, "tables": tables}
canonical = json.dumps(logical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
print(json.dumps({
    "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    "table_count": len(table_names),
    "row_count": row_count,
}, sort_keys=True))
PY
}

extract_cashback_database() {
  local archive="$1"
  local destination="$2"
  python3 - "${archive}" "${destination}" <<'PY'
import shutil
import sys
import tarfile
from pathlib import PurePosixPath

archive, destination = sys.argv[1:]
target = "cashback-data/cashback-events.sqlite3"
found = False
with tarfile.open(archive, "r:gz") as bundle:
    for member in bundle:
        if member.name != target:
            continue
        if found or not member.isfile() or PurePosixPath(member.name).is_absolute():
            raise SystemExit("cashback database archive member is unsafe or duplicated")
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit("cashback database archive member cannot be read")
        with source, open(destination, "xb") as output:
            shutil.copyfileobj(source, output)
        found = True
if not found:
    raise SystemExit("cashback database is missing from the verified archive")
PY
}

write_receipt() {
  python3 - "${RECEIPT_PATH}" "${status}" "${started_at}" "${finished_at}" \
    "${backup_name}" "${archive_sha256}" "${archive_bytes}" "${backup_verified}" \
    "${runtime_name}" "${IMAGE}" "${image_digest}" "${runtime_available}" "${runtime_verified}" \
    "${SOURCE_COMMIT}" "${SOURCE_SCRIPT_SHA256}" \
    "${REPEAT_COUNT}" "${cleanup_verified}" "${failure_code}" "${failure_stage}" \
    "${failure_detail}" \
    "${run_records[@]}" <<'PY'
import json
import os
import pathlib
import sys

(
    receipt_path,
    status,
    started_at,
    completed_at,
    backup_name,
    archive_sha256,
    archive_bytes,
    backup_verified,
    runtime_name,
    image,
    image_digest,
    runtime_available,
    runtime_verified,
    source_commit,
    source_script_sha256,
    requested_runs,
    cleanup_verified,
    failure_code,
    failure_stage,
    failure_detail,
    *record_paths,
) = sys.argv[1:]

def optional(value):
    return value if value else None

def integer(value):
    return int(value) if value else None

records = [
    json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    for path in record_paths
]
receipt = {
    "schema_version": 1,
    "status": status,
    "mode": "disposable",
    "redacted": True,
    "started_at": started_at,
    "completed_at": completed_at,
    "backup": {
        "name": optional(backup_name),
        "archive_sha256": optional(archive_sha256),
        "archive_bytes": integer(archive_bytes),
        "verified": backup_verified == "true",
    },
    "runtime": {
        "engine": optional(runtime_name),
        "image": image,
        "image_digest": optional(image_digest),
        "available": runtime_available == "true",
        "verified": runtime_verified == "true",
    },
    "source_provenance": {
        "commit": optional(source_commit),
        "script_sha256": source_script_sha256,
    },
    "requested_runs": int(requested_runs),
    "runs": records,
    "cleanup_verified": cleanup_verified == "true",
    "production_mutated": False,
    "retained_mutated": False,
    "secret_values_recorded": False,
    "error": None if not failure_code else {
        "code": failure_code,
        "stage": failure_stage,
        **({"detail": failure_detail} if failure_detail else {}),
    },
}

target = pathlib.Path(receipt_path)
target.parent.mkdir(parents=True, exist_ok=True)
temporary = pathlib.Path(str(target) + ".tmp")
temporary.write_text(
    json.dumps(receipt, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
os.chmod(temporary, 0o600)
temporary.replace(target)
os.chmod(target, 0o600)
PY
}

cleanup_current() {
  local cleanup_ok=true
  local inspect_status
  local inspect_error
  local post_inspect_error
  if [[ -n "${current_container}" && "${runtime_available}" == true ]]; then
    inspect_error="${temp_dir}/inspect-${current_container}.stderr"
    post_inspect_error="${temp_dir}/post-inspect-${current_container}.stderr"
    if "${RUNTIME}" inspect "${current_container}" >/dev/null 2>"${inspect_error}"; then
      if ! "${RUNTIME}" rm -f "${current_container}" >/dev/null 2>&1; then
        cleanup_ok=false
      fi
      if "${RUNTIME}" inspect "${current_container}" >/dev/null 2>"${post_inspect_error}"; then
        cleanup_ok=false
      elif ! inspect_status="$(inspect_result "${current_container}" "${post_inspect_error}")" || [[ "${inspect_status}" != absent ]]; then
        cleanup_ok=false
      fi
    elif ! inspect_status="$(inspect_result "${current_container}" "${inspect_error}")" || [[ "${inspect_status}" != absent ]]; then
      cleanup_ok=false
    fi
  fi
  if [[ -n "${current_data_dir}" && -e "${current_data_dir}" ]]; then
    if ! rm -rf -- "${current_data_dir}"; then
      cleanup_ok=false
    fi
    if [[ -e "${current_data_dir}" ]]; then
      cleanup_ok=false
    fi
  fi
  current_container=""
  current_data_dir=""
  if [[ "${cleanup_ok}" != true ]]; then
    cleanup_verified=false
    record_failure "cleanup_not_verified" "cleanup"
    return 1
  fi
  return 0
}

inspect_result() {
  local container="$1"
  local error_file="$2"
  python3 - "${container}" "${error_file}" <<'PY'
import pathlib
import sys

container, error_file = sys.argv[1:]
message = pathlib.Path(error_file).read_text(encoding="utf-8", errors="replace").casefold()
not_found_markers = (
    "no such container",
    "no container with name or id",
    "container does not exist",
    "container not found",
)
if any(marker in message for marker in not_found_markers):
    print("absent")
    raise SystemExit(0)
print("error", file=sys.stderr)
raise SystemExit(1)
PY
}

classify_runtime_failure() {
  local error_file="$1"
  local classification
  classification="$(python3 - "${error_file}" <<'PY'
import pathlib
import sys

message = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").casefold()
if "/run/user/1000/libpod" in message:
    print("podman-libpod-read-only")
elif "emulate docker cli using podman" in message:
    print("podman")
else:
    print("generic")
PY
)"
  case "${classification}" in
    podman-libpod-read-only)
      runtime_name="podman"
      failure_detail="podman preflight failed at /run/user/1000/libpod: read-only file system"
      ;;
    podman)
      runtime_name="podman"
      failure_detail="podman preflight failed"
      ;;
    *)
      failure_detail="container runtime preflight failed"
      ;;
  esac
}

write_record() {
  local run_index="$1"
  local run_status="$2"
  local pre_hash="${3:-}"
  local post_hash="${4:-}"
  local exact_match="${5:-false}"
  local health_authorized="${6:-false}"
  local restart_verified="${7:-false}"
  local cleanup_result="${8:-false}"
  local record_file="${temp_dir}/run-${run_index}.json"
  python3 - "${record_file}" "${run_index}" "${last_sidecar_id}" \
    "${pre_hash}" "${post_hash}" "${exact_match}" "${health_authorized}" \
    "${restart_verified}" "${cleanup_result}" "${run_status}" <<'PY'
import json
import pathlib
import sys

(path, index, sidecar, pre_hash, post_hash, exact, health, restart, cleanup, status) = sys.argv[1:]
payload = {
    "run_index": int(index),
    "sidecar_id": sidecar or f"uncreated-run-{index}",
    "pre_state_sha256": pre_hash or None,
    "post_state_sha256": post_hash or None,
    "exact_state_match": exact == "true",
    "health_authorized": health == "true",
    "restart_verified": restart == "true",
    "cleanup_verified": cleanup == "true",
    "status": status,
}
pathlib.Path(path).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
PY
  run_records+=("${record_file}")
}

wait_for_health() {
  local container="$1"
  local error_log="${temp_dir}/health-${container}.log"
  local attempt
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    if "${RUNTIME}" exec "${container}" python apps/cashback-control/probe_health.py \
      >/dev/null 2>"${error_log}"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

run_once() {
  local run_index="$1"
  local sidecar_id
  sidecar_id="finance-cashback-restore-${run_index}-$(date -u +%Y%m%dT%H%M%SZ)-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
  local data_dir="${temp_dir}/data-${run_index}"
  local database="${data_dir}/cashback-events.sqlite3"
  local token
  local pre_state
  local post_state
  local pre_hash=""
  local post_hash=""
  local health_authorized=false
  local restart_verified=false
  local exact_match=false
  local cleanup_result=false

  current_container="${sidecar_id}"
  current_data_dir="${data_dir}"
  last_sidecar_id="${sidecar_id}"
  mkdir -p "${data_dir}"
  if ! extract_cashback_database "${backup_root_dir}/finance-data.tar.gz" "${database}"; then
    record_failure "cashback_extract_failed" "extract"
    cleanup_current || true
    write_record "${run_index}" "failed"
    return 1
  fi

  if ! pre_state="$(logical_state "${database}")"; then
    record_failure "pre_state_read_failed" "pre_state"
    cleanup_current || true
    write_record "${run_index}" "failed"
    return 1
  fi
  pre_hash="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["sha256"])' "${pre_state}")"
  token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

  if ! "${RUNTIME}" run -d --pull=never --network none --name "${sidecar_id}" \
    --user "$(id -u):$(id -g)" \
    -e CASHBACK_INGEST_TOKEN="${token}" \
    -e CASHBACK_REFRESH_SECONDS=0 \
    -e CASHBACK_DB_PATH=/var/lib/cashback-control/cashback-events.sqlite3 \
    -e CASHBACK_DASHBOARD_PATH=/var/lib/cashback-control/cashback-dashboard.json \
    -v "${data_dir}:/var/lib/cashback-control" \
    "${IMAGE}" >"${temp_dir}/run-${run_index}.id" 2>"${temp_dir}/run-${run_index}.stderr"; then
    record_failure "sidecar_start_failed" "start"
    cleanup_current || true
    write_record "${run_index}" "failed" "${pre_hash}"
    return 1
  fi

  if ! wait_for_health "${sidecar_id}"; then
    record_failure "authorized_health_failed" "health"
    cleanup_current || true
    write_record "${run_index}" "failed" "${pre_hash}" "" "false" "false" "false" "false"
    return 1
  fi
  health_authorized=true

  if ! "${RUNTIME}" restart "${sidecar_id}" >/dev/null 2>"${temp_dir}/restart-${run_index}.stderr"; then
    record_failure "sidecar_restart_failed" "restart"
  elif ! wait_for_health "${sidecar_id}"; then
    record_failure "authorized_health_after_restart_failed" "restart_health"
  else
    restart_verified=true
  fi

  if [[ "${restart_verified}" == true ]]; then
    if ! post_state="$(logical_state "${database}")"; then
      record_failure "post_state_read_failed" "post_state"
    else
      post_hash="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["sha256"])' "${post_state}")"
      if [[ "${pre_hash}" == "${post_hash}" ]]; then
        exact_match=true
      else
        record_failure "logical_state_mismatch" "compare"
      fi
    fi
  fi

  if ! cleanup_current; then
    cleanup_result=false
  else
    cleanup_result=true
  fi
  if [[ -z "${failure_code}" && "${cleanup_result}" == true && "${exact_match}" == true && "${restart_verified}" == true ]]; then
    write_record "${run_index}" "passed" "${pre_hash}" "${post_hash}" "${exact_match}" "${health_authorized}" "${restart_verified}" "${cleanup_result}"
    return 0
  fi
  write_record "${run_index}" "failed" "${pre_hash}" "${post_hash}" "${exact_match}" "${health_authorized}" "${restart_verified}" "${cleanup_result}"
  return 1
}

on_exit() {
  local exit_code=$?
  if [[ -n "${current_container}" || -n "${current_data_dir}" ]]; then
    cleanup_current || true
  fi
  if (( exit_code == 0 )); then
    status="passed"
  elif [[ "${status}" != blocked ]]; then
    status="failed"
  fi
  finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ -n "${RECEIPT_PATH}" && -n "${temp_dir}" ]]; then
    if ! write_receipt; then
      printf '%s\n' '{"level":"error","event":"restore_receipt_write_failed"}' >&2
      exit_code=1
    fi
  fi
  if [[ -n "${temp_dir}" && -d "${temp_dir}" ]]; then
    rm -rf -- "${temp_dir}" || true
  fi
  exit "${exit_code}"
}
trap on_exit EXIT

while (($#)); do
  case "$1" in
    --backup-root)
      [[ $# -ge 2 ]] || { echo "--backup-root requires a value" >&2; exit 64; }
      BACKUP_ROOT="$2"
      shift 2
      ;;
    --backup-path)
      [[ $# -ge 2 ]] || { echo "--backup-path requires a value" >&2; exit 64; }
      BACKUP_PATH="$2"
      shift 2
      ;;
    --receipt)
      [[ $# -ge 2 ]] || { echo "--receipt requires a value" >&2; exit 64; }
      RECEIPT_PATH="$2"
      shift 2
      ;;
    --image)
      [[ $# -ge 2 ]] || { echo "--image requires a value" >&2; exit 64; }
      IMAGE="$2"
      shift 2
      ;;
    --runtime)
      [[ $# -ge 2 ]] || { echo "--runtime requires a value" >&2; exit 64; }
      RUNTIME="$2"
      shift 2
      ;;
    --repeat)
      [[ $# -ge 2 ]] || { echo "--repeat requires a value" >&2; exit 64; }
      REPEAT_COUNT="$2"
      shift 2
      ;;
    --help)
      usage
      trap - EXIT
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

if [[ -z "${RECEIPT_PATH}" ]]; then
  RECEIPT_PATH="/tmp/finance-cashback-restore-$(date -u +%Y%m%dT%H%M%SZ).json"
fi
if ! [[ "${REPEAT_COUNT}" =~ ^([2-9]|[1-9][0-9]+)$ ]]; then
  record_failure "invalid_repeat_count" "arguments"
  exit 64
fi
if ! [[ "${HEALTH_ATTEMPTS}" =~ ^[1-9][0-9]*$ ]]; then
  record_failure "invalid_health_attempts" "arguments"
  exit 64
fi
if [[ ! -f "${VERIFY_SCRIPT}" ]]; then
  record_failure "missing_backup_verifier" "preflight"
  exit 1
fi

temp_dir="$(mktemp -d "${TEMP_ROOT%/}/finance-cashback-restore.XXXXXXXX")"
verify_file="${temp_dir}/backup-verification.json"
verify_args=(--backup-root "${BACKUP_ROOT}" --work-root "${temp_dir}")
if [[ -n "${BACKUP_PATH}" ]]; then
  verify_args+=(--backup-path "${BACKUP_PATH}")
fi
if ! python3 "${VERIFY_SCRIPT}" "${verify_args[@]}" >"${verify_file}" 2>"${temp_dir}/backup-verification.stderr"; then
  record_failure "backup_verification_failed" "backup"
  exit 1
fi
backup_verified=true
backup_name="$(json_value backup)"
archive_sha256="$(json_value archive_sha256)"
archive_bytes="$(json_value archive_bytes)"
backup_root_dir="${BACKUP_ROOT%/}/${backup_name}"
if ! (cd "${backup_root_dir}" && sha256sum -c SHA256SUMS >/dev/null 2>"${temp_dir}/checksum.stderr"); then
  record_failure "backup_checksum_recheck_failed" "checksum"
  exit 1
fi

if [[ -z "${RUNTIME}" ]]; then
  if command -v docker >/dev/null 2>&1; then
    RUNTIME="$(command -v docker)"
  elif command -v podman >/dev/null 2>&1; then
    RUNTIME="$(command -v podman)"
  fi
fi
if [[ -z "${RUNTIME}" ]]; then
  status="blocked"
  failure_detail="no docker or podman command found"
  record_failure "container_runtime_unavailable" "runtime"
  exit 2
fi
runtime_name="$(basename "${RUNTIME}")"
if ! "${RUNTIME}" version >/dev/null 2>"${temp_dir}/runtime-version.stderr"; then
  classify_runtime_failure "${temp_dir}/runtime-version.stderr"
  status="blocked"
  record_failure "container_runtime_unavailable" "runtime"
  exit 2
fi
image_digest_raw=""
if ! image_digest_raw="$("${RUNTIME}" image inspect --format '{{.Id}}' "${IMAGE}" 2>"${temp_dir}/image-inspect.stderr")"; then
  status="blocked"
  failure_detail="cashback image digest unavailable"
  record_failure "image_digest_unavailable" "runtime"
  exit 2
fi
image_digest_raw="${image_digest_raw//$'\n'/}"
if [[ "${image_digest_raw}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  image_digest="${image_digest_raw}"
elif [[ "${image_digest_raw}" =~ ^[0-9a-f]{64}$ ]]; then
  image_digest="sha256:${image_digest_raw}"
else
  status="blocked"
  failure_detail="cashback image digest malformed"
  record_failure "image_digest_malformed" "runtime"
  exit 2
fi
runtime_available=true
runtime_verified=true

for run_index in $(seq 1 "${REPEAT_COUNT}"); do
  if run_once "${run_index}"; then
    :
  else
    break
  fi
done

if [[ -n "${failure_code}" || "${#run_records[@]}" -ne "${REPEAT_COUNT}" ]]; then
  exit 1
fi
exit 0
