#!/usr/bin/env bash
set -euo pipefail
umask 0077

# Backups contain private financial data.  Keep every directory and persisted
# artifact private even when the operator invokes this helper outside systemd
# or the configured backup root already exists with a broader mode.
PRIVATE_DIR_MODE=700
PRIVATE_FILE_MODE=600
PRIVATE_OWNER_UID="${EUID}"

ACTUAL_STACK_DIR="${FINANCE_ACTUAL_STACK_DIR:-/opt/stacks/finance-actual-poc}"
CASHBACK_STACK_DIR="${FINANCE_CASHBACK_STACK_DIR:-/opt/stacks/finance-cashback}"
BACKUP_ROOT="${FINANCE_BACKUP_ROOT:-/opt/backups/finance-actual-poc}"
RETENTION_DAYS="${FINANCE_BACKUP_RETENTION_DAYS:-30}"

ACTUAL_DATA_DIR="${FINANCE_ACTUAL_DATA_DIR:-${ACTUAL_STACK_DIR}/data}"
CASHBACK_DATA_DIR="${FINANCE_CASHBACK_DATA_DIR:-${ACTUAL_STACK_DIR}/cashback-data}"
VERIFY_SCRIPT="${FINANCE_BACKUP_VERIFY_SCRIPT:-${ACTUAL_STACK_DIR}/verify-backup.py}"
SANITIZE_SCRIPT="${FINANCE_CASHBACK_BACKUP_SANITIZE_SCRIPT:-${ACTUAL_STACK_DIR}/sanitize-cashback-backup.py}"

fail() {
  printf '{"level":"error","event":"backup_refused","reason":"%s"}\n' "$1" >&2
  exit 1
}

ensure_private_dir() {
  local path="$1"
  local label="$2"
  local owner
  local mode

  [[ ! -L "${path}" ]] || fail "${label}_symlink"
  if [[ ! -e "${path}" ]]; then
    mkdir -p -- "${path}"
  fi
  [[ ! -L "${path}" && -d "${path}" ]] || fail "${label}_not_directory"
  owner="$(stat -c '%u' -- "${path}")" || fail "${label}_stat_failed"
  [[ "${owner}" == "${PRIVATE_OWNER_UID}" ]] || fail "${label}_owner"
  chmod "${PRIVATE_DIR_MODE}" -- "${path}" || fail "${label}_chmod_failed"
  mode="$(stat -c '%a' -- "${path}")" || fail "${label}_stat_failed"
  [[ "${mode}" == "${PRIVATE_DIR_MODE}" ]] || fail "${label}_mode"
}

ensure_private_file() {
  local path="$1"
  local label="$2"
  local owner
  local mode

  [[ ! -L "${path}" ]] || fail "${label}_symlink"
  [[ -f "${path}" ]] || fail "${label}_not_regular"
  owner="$(stat -c '%u' -- "${path}")" || fail "${label}_stat_failed"
  [[ "${owner}" == "${PRIVATE_OWNER_UID}" ]] || fail "${label}_owner"
  chmod "${PRIVATE_FILE_MODE}" -- "${path}" || fail "${label}_chmod_failed"
  mode="$(stat -c '%a' -- "${path}")" || fail "${label}_stat_failed"
  [[ "${mode}" == "${PRIVATE_FILE_MODE}" ]] || fail "${label}_mode"
}

resolved() {
  readlink -m -- "$1"
}

if [[ "${EUID}" -ne 0 ]]; then fail "root_required"; fi
[[ "$(resolved "${ACTUAL_STACK_DIR}")" == "/opt/stacks/finance-actual-poc" ]] || fail "unexpected_actual_stack_path"
[[ "$(resolved "${CASHBACK_STACK_DIR}")" == "/opt/stacks/finance-cashback" ]] || fail "unexpected_cashback_stack_path"
backup_root_resolved="$(resolved "${BACKUP_ROOT}")"
[[ "${backup_root_resolved}" == "/opt/backups/finance-actual-poc" || "${backup_root_resolved}" == /opt/backups/finance-actual-poc/* ]] || fail "unexpected_backup_root"
[[ "$(resolved "${ACTUAL_DATA_DIR}")" == "${ACTUAL_STACK_DIR}/data" ]] || fail "unexpected_actual_data_path"
[[ "$(resolved "${CASHBACK_DATA_DIR}")" == "${ACTUAL_STACK_DIR}/cashback-data" ]] || fail "unexpected_cashback_data_path"
[[ "$(resolved "${VERIFY_SCRIPT}")" == "${ACTUAL_STACK_DIR}/verify-backup.py" ]] || fail "unexpected_verify_script_path"
[[ "$(resolved "${SANITIZE_SCRIPT}")" == "${ACTUAL_STACK_DIR}/sanitize-cashback-backup.py" ]] || fail "unexpected_sanitize_script_path"
[[ -f "${VERIFY_SCRIPT}" ]] || fail "missing_verify_script"
[[ -f "${SANITIZE_SCRIPT}" ]] || fail "missing_sanitize_script"
for required_dir in "${ACTUAL_DATA_DIR}" "${CASHBACK_DATA_DIR}"; do
  [[ ! -L "${required_dir}" && -d "${required_dir}" ]] || fail "unsafe_data_directory"
done

ensure_private_dir "${BACKUP_ROOT}" "backup_root"
lock_file="${BACKUP_ROOT}/.backup.lock"
[[ ! -L "${lock_file}" ]] || fail "backup_lock_symlink"
if [[ ! -e "${lock_file}" ]]; then
  : >"${lock_file}"
fi
ensure_private_file "${lock_file}" "backup_lock"
exec 9>"${lock_file}"
ensure_private_file "${lock_file}" "backup_lock"
flock -n 9 || {
  echo '{"level":"warning","event":"backup_skipped","reason":"already_running"}'
  exit 0
}

declare -A paused_services=()
probe_failure_reason="probe_unhealthy"

container_state() {
  local name="$1"
  local status
  local paused

  if ! status="$(docker inspect -f '{{.State.Status}}' "${name}" 2>/dev/null 9>&-)"; then
    printf 'inspect_error\n'
    return 0
  fi
  case "${status}" in
    paused)
      printf 'paused\n'
      ;;
    running)
      if ! paused="$(docker inspect -f '{{.State.Paused}}' "${name}" 2>/dev/null 9>&-)"; then
        printf 'inspect_error\n'
      elif [[ "${paused}" == "true" ]]; then
        printf 'paused\n'
      elif [[ "${paused}" == "false" ]]; then
        printf 'running\n'
      else
        printf 'unexpected_state\n'
      fi
      ;;
    created|restarting|removing|exited|dead)
      printf 'not_running\n'
      ;;
    *)
      printf 'unexpected_state\n'
      ;;
  esac
}

retain_unknown_ownership() {
  local name="$1"
  local reason="$2"
  paused_services["${name}"]=unknown
  printf '{"level":"error","event":"backup_resume_state_unknown","service":"%s","reason":"%s"}\n' "${name}" "${reason}" >&2
}

resume_services() {
  local failed=0
  local name
  local ownership
  local state
  for name in finance-actual-poc finance-cashback-control finance-actual-proxy; do
    [[ -n "${paused_services[${name}]+set}" ]] || continue
    ownership="${paused_services[${name}]}"
    state="$(container_state "${name}")"
    case "${state}" in
      paused)
        if docker unpause "${name}" >/dev/null 9>&-; then
          unset "paused_services[${name}]"
        else
          retain_unknown_ownership "${name}" "unpause_failed"
          failed=1
        fi
        ;;
      running)
        if [[ "${ownership}" == "paused" ]]; then
          unset "paused_services[${name}]"
        else
          retain_unknown_ownership "${name}" "pause_state_unconfirmed"
          failed=1
        fi
        ;;
      not_running)
        retain_unknown_ownership "${name}" "container_not_running"
        failed=1
        ;;
      inspect_error|unexpected_state)
        retain_unknown_ownership "${name}" "${state}"
        failed=1
        ;;
      *)
        retain_unknown_ownership "${name}" "unexpected_state"
        failed=1
        ;;
    esac
  done
  return "${failed}"
}

pause_service() {
  local name="$1"
  # Record ownership before pause so a partial pause failure remains recoverable.
  paused_services["${name}"]=pending
  if docker pause "${name}" >/dev/null 9>&-; then
    paused_services["${name}"]=paused
  else
    retain_unknown_ownership "${name}" "pause_failed"
    return 1
  fi
}

actual_state="$(container_state finance-actual-poc)"
proxy_state="$(container_state finance-actual-proxy)"
cashback_state="$(container_state finance-cashback-control)"
declare -A initial_states=(
  [finance-actual-poc]="${actual_state}"
  [finance-actual-proxy]="${proxy_state}"
  [finance-cashback-control]="${cashback_state}"
)
for name in finance-actual-poc finance-actual-proxy finance-cashback-control; do
  state="${initial_states[${name}]}"
  if [[ "${state}" == "inspect_error" || "${state}" == "unexpected_state" ]]; then
    printf '{"level":"error","event":"backup_container_state_unknown","service":"%s","reason":"%s"}\n' "${name}" "${state}" >&2
    fail "container_state_unknown"
  fi
done

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
working="${BACKUP_ROOT}/.${stamp}.incomplete"
destination="${BACKUP_ROOT}/${stamp}"
payload="${working}/payload"
[[ ! -e "${working}" && ! -e "${destination}" ]] || fail "backup_destination_exists"
mkdir -p \
  "${payload}/actual-data" "${payload}/cashback-data" "${payload}/configuration"
ensure_private_dir "${working}" "backup_working"
ensure_private_dir "${payload}" "backup_payload"
ensure_private_dir "${payload}/actual-data" "backup_actual_data"
ensure_private_dir "${payload}/cashback-data" "backup_cashback_data"
ensure_private_dir "${payload}/configuration" "backup_configuration"

probe_cashback_health() {
  local output
  local lower_output
  if output="$(docker exec finance-cashback-control python apps/cashback-control/probe_health.py 2>&1 9>&-)"; then
    probe_failure_reason=""
    return 0
  fi

  lower_output="${output,,}"
  if [[ ( "${lower_output}" == *"probe_health.py"* && ( "${lower_output}" == *"no such file"* || "${lower_output}" == *"can't open file"* || "${lower_output}" == *"not found"* ) ) || "${lower_output}" == *"missing probe"* || "${lower_output}" == *"missing_probe"* ]]; then
    probe_failure_reason="missing_probe"
  elif [[ "${lower_output}" == *"error response from daemon"* || "${lower_output}" == *"cannot connect to the docker daemon"* || "${lower_output}" == *"oci runtime exec failed"* || "${lower_output}" == *"runtime exec failed"* || ( "${lower_output}" == *"docker exec"* && "${lower_output}" == *"failed"* ) || "${lower_output}" == *"no such container"* || "${lower_output}" == *"no such object"* || "${lower_output}" == *"not running"* || "${lower_output}" == *"failed to exec"* ]]; then
    probe_failure_reason="runtime_exec_failed"
  else
    probe_failure_reason="probe_unhealthy"
  fi
  return 1
}

wait_for_url() {
  local label="$1"
  local url="$2"
  probe_failure_reason="probe_unhealthy"
  for _ in $(seq 1 90); do
    if [[ "${label}" == "cashback" ]]; then
      if probe_cashback_health; then
        return 0
      fi
      [[ "${probe_failure_reason}" == "missing_probe" ]] && break
    elif curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    else
      probe_failure_reason="probe_unhealthy"
    fi
    sleep 1
  done
  printf '{"level":"error","event":"backup_resume_unhealthy","service":"%s","reason":"%s"}\n' "${label}" "${probe_failure_reason}" >&2
  return 1
}

emergency_resume() {
  if ((${#paused_services[@]} > 0)); then
    resume_services || true
  fi
}
trap emergency_resume EXIT

if [[ "${proxy_state}" == "running" ]]; then
  pause_service finance-actual-proxy
fi
if [[ "${actual_state}" == "running" ]]; then
  pause_service finance-actual-poc
fi
if [[ "${cashback_state}" == "running" ]]; then
  pause_service finance-cashback-control
fi
sync

cp -a "${ACTUAL_DATA_DIR}/." "${payload}/actual-data/"
# Disposable pre-deploy snapshots are rollback material, not restore input.
# Exclude them before they enter the archive so an old copy cannot bypass the
# sanitizer or leak push credentials through a historical filename.
tar -C "${CASHBACK_DATA_DIR}" \
  --exclude='pre-deploy-*.sqlite3*' \
  -cf - . | tar -C "${payload}/cashback-data" -xf -
while IFS= read -r -d '' database; do
  python3 "${SANITIZE_SCRIPT}" "${database}"
done < <(find "${payload}" -type f \( -name '*.sqlite' -o -name '*.sqlite3' \) -print0)

install -m "${PRIVATE_FILE_MODE}" "${ACTUAL_STACK_DIR}/compose.yaml" "${payload}/configuration/actual-compose.yaml"
install -m "${PRIVATE_FILE_MODE}" "${CASHBACK_STACK_DIR}/compose.yaml" "${payload}/configuration/cashback-compose.yaml"
if [[ -d "${ACTUAL_STACK_DIR}/nginx" ]]; then
  cp -a "${ACTUAL_STACK_DIR}/nginx" "${payload}/configuration/actual-nginx"
fi
for config_name in cashback-profile.json actual-bootstrap.json static-rules.json transaction-email-sources.json; do
  if [[ -f "${CASHBACK_STACK_DIR}/${config_name}" ]]; then
    install -m "${PRIVATE_FILE_MODE}" "${CASHBACK_STACK_DIR}/${config_name}" "${payload}/configuration/${config_name}"
  fi
done

tar -C "${payload}" -czf "${working}/finance-data.tar.gz" .
ensure_private_file "${working}/finance-data.tar.gz" "backup_archive"
rm -rf -- "${payload}"
(
  cd "${working}"
  sha256sum finance-data.tar.gz > SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)
ensure_private_file "${working}/SHA256SUMS" "backup_checksums"
cat > "${working}/manifest.json" <<EOF
{"schema_version":4,"created_at":"${stamp}","includes":["actual-data","cashback-data","configuration"],"secrets_included":false,"excluded_data":["cashback-data/cashback-events.sqlite3:push_deliveries","cashback-data/cashback-events.sqlite3:push_state","cashback-data/cashback-events.sqlite3:push_subscriptions"],"excluded_paths":["cashback-data/pre-deploy-*.sqlite3*"],"containers":{"actual":"finance-actual-poc","proxy":"finance-actual-proxy","cashback":"finance-cashback-control"}}
EOF
ensure_private_file "${working}/manifest.json" "backup_manifest"

resume_services
if [[ "${actual_state}" == "running" && "${proxy_state}" == "running" ]]; then
  wait_for_url "actual" "http://127.0.0.1:5006/"
fi
if [[ "${cashback_state}" == "running" ]]; then
  wait_for_url "cashback" "http://127.0.0.1:5010/api/health"
fi
trap - EXIT
mv -- "${working}" "${destination}"
ensure_private_dir "${destination}" "backup_destination"
ensure_private_file "${destination}/finance-data.tar.gz" "backup_archive"
ensure_private_file "${destination}/SHA256SUMS" "backup_checksums"
ensure_private_file "${destination}/manifest.json" "backup_manifest"
python3 "${VERIFY_SCRIPT}" \
  --backup-root "${BACKUP_ROOT}" \
  --backup-path "${destination}" \
  --write-receipt
if [[ -e "${destination}/verification.json" || -L "${destination}/verification.json" ]]; then
  ensure_private_file "${destination}/verification.json" "backup_verification"
fi

if [[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS > 0 )); then
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' -mtime "+${RETENTION_DAYS}" -exec rm -rf -- {} +
fi

echo "{\"level\":\"info\",\"event\":\"backup_complete\",\"path\":\"${destination}\"}"
