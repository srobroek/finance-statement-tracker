#!/usr/bin/env bash
set -euo pipefail

ACTUAL_STACK_DIR="${FINANCE_ACTUAL_STACK_DIR:-/opt/stacks/finance-actual-poc}"
CASHBACK_STACK_DIR="${FINANCE_CASHBACK_STACK_DIR:-/opt/stacks/finance-cashback}"
BACKUP_ROOT="${FINANCE_BACKUP_ROOT:-/opt/backups/finance-actual-poc}"
MAX_BACKUP_AGE_HOURS="${FINANCE_MAX_BACKUP_AGE_HOURS:-48}"

log() {
  local level="$1"
  local event="$2"
  local service="${3:-}"
  printf '{"level":"%s","event":"%s","service":"%s","timestamp":"%s"}\n' \
    "${level}" "${event}" "${service}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

fail() {
  log error monitor_refused "$1" >&2
  exit 1
}

resolved() {
  readlink -m -- "$1"
}

if [[ "${EUID}" -ne 0 ]]; then fail root_required; fi
[[ "$(resolved "${ACTUAL_STACK_DIR}")" == "/opt/stacks/finance-actual-poc" ]] || fail unexpected_actual_stack_path
[[ "$(resolved "${CASHBACK_STACK_DIR}")" == "/opt/stacks/finance-cashback" ]] || fail unexpected_cashback_stack_path
[[ "$(resolved "${BACKUP_ROOT}")" == "/opt/backups/finance-actual-poc" ]] || fail unexpected_backup_root
[[ "${MAX_BACKUP_AGE_HOURS}" =~ ^[0-9]+$ ]] || fail invalid_backup_age

mkdir -p "${BACKUP_ROOT}"
exec 9>"${BACKUP_ROOT}/.backup.lock"
if ! flock -n 9; then
  log info monitor_skipped backup_in_progress
  exit 0
fi

probe() {
  local url="$1"
  local required="${2:-}"
  local container="${3:-}"
  local response
  if [[ -n "${container}" ]]; then
    response="$(docker exec "${container}" python apps/cashback-control/probe_health.py 2>/dev/null 9>&-)" || return 1
  else
    response="$(curl --connect-timeout 3 --max-time 10 -fsS "${url}" 2>/dev/null)" || return 1
  fi
  [[ -z "${required}" || "${response}" == *"${required}"* ]]
}

probe_twice() {
  local url="$1"
  local required="${2:-}"
  local container="${3:-}"
  probe "${url}" "${required}" "${container}" && return 0
  sleep 3
  probe "${url}" "${required}" "${container}"
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null 9>&- || true)" == "running" ]]
}

recover_container() {
  local name="$1"
  local stack_dir="$2"
  local project="$3"
  local service="$4"
  if docker inspect "${name}" >/dev/null 2>&1 9>&-; then
    docker restart "${name}" >/dev/null 9>&-
  else
    docker compose -p "${project}" -f "${stack_dir}/compose.yaml" up -d --pull never "${service}" >/dev/null 9>&-
  fi
  log warning service_recovered "${name}"
}

ensure_service() {
  local name="$1"
  local stack_dir="$2"
  local project="$3"
  local service="$4"
  local url="$5"
  local required="${6:-}"
  local container="${7:-}"

  if container_running "${name}" && probe_twice "${url}" "${required}" "${container}"; then
    log info service_healthy "${name}"
    return 0
  fi

  log warning service_unhealthy "${name}" >&2
  recover_container "${name}" "${stack_dir}" "${project}" "${service}"
  for _ in $(seq 1 60); do
    if container_running "${name}" && probe "${url}" "${required}" "${container}"; then
      log info service_verified "${name}"
      return 0
    fi
    sleep 2
  done
  log error service_recovery_failed "${name}" >&2
  return 1
}

failed=0

# The public Actual port is owned by the proxy. Recovering the upstream first
# prevents a cached 502 from being mistaken for a healthy application.
if ! container_running finance-actual; then
  recover_container finance-actual "${ACTUAL_STACK_DIR}" finance-actual-poc actual || failed=1
fi
if ! ensure_service finance-actual-proxy "${ACTUAL_STACK_DIR}" finance-actual-poc actual-proxy \
  http://127.0.0.1:5006/; then
  # A running but unhealthy upstream can leave the proxy alive and returning
  # errors. Restart only these two containers, in dependency order.
  recover_container finance-actual "${ACTUAL_STACK_DIR}" finance-actual-poc actual || true
  recover_container finance-actual-proxy "${ACTUAL_STACK_DIR}" finance-actual-poc actual-proxy || true
  probe_twice http://127.0.0.1:5006/ || failed=1
fi

ensure_service finance-cashback-control "${CASHBACK_STACK_DIR}" finance-cashback cashback-control \
  http://127.0.0.1:5010/api/health '"status":"ok"' finance-cashback-control || failed=1
latest_backup="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 || true)"
if [[ -z "${latest_backup}" ]]; then
  log error backup_missing finance-backup >&2
  failed=1
else
  latest_epoch="${latest_backup%% *}"
  latest_epoch="${latest_epoch%.*}"
  age_seconds="$(( $(date +%s) - latest_epoch ))"
  if (( age_seconds > MAX_BACKUP_AGE_HOURS * 3600 )); then
    log error backup_stale finance-backup >&2
    failed=1
  else
    log info backup_fresh finance-backup
    latest_path="${latest_backup#* }"
    verification_receipt="${latest_path}/verification.json"
    if [[ ! -s "${verification_receipt}" ]] || ! python3 -c \
      'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(0 if data.get("status") == "ok" and data.get("backup") == sys.argv[2] else 1)' \
      "${verification_receipt}" "$(basename "${latest_path}")"; then
      log error backup_unverified finance-backup >&2
      failed=1
    else
      log info backup_verified finance-backup
    fi
  fi
fi

if (( failed )); then
  log error monitor_failed finance-runtime >&2
  exit 1
fi
log info monitor_complete finance-runtime
