#!/usr/bin/env bash
set -euo pipefail

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
  [[ -d "${required_dir}" ]] || fail "missing_data_directory"
done

mkdir -p "${BACKUP_ROOT}"
exec 9>"${BACKUP_ROOT}/.backup.lock"
flock -n 9 || {
  echo '{"level":"warning","event":"backup_skipped","reason":"already_running"}'
  exit 0
}

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
working="${BACKUP_ROOT}/.${stamp}.incomplete"
destination="${BACKUP_ROOT}/${stamp}"
payload="${working}/payload"
[[ ! -e "${working}" && ! -e "${destination}" ]] || fail "backup_destination_exists"
mkdir -p "${payload}/actual-data" "${payload}/cashback-data" "${payload}/configuration"

actual_running="$(docker inspect -f '{{.State.Status}}' finance-actual-poc 2>/dev/null || true)"
proxy_running="$(docker inspect -f '{{.State.Status}}' finance-actual-proxy 2>/dev/null || true)"
cashback_running="$(docker inspect -f '{{.State.Status}}' finance-cashback-control 2>/dev/null || true)"

resume_services() {
  local failed=0
  if [[ "${actual_running}" == "running" ]]; then docker unpause finance-actual-poc >/dev/null || failed=1; fi
  if [[ "${cashback_running}" == "running" ]]; then docker unpause finance-cashback-control >/dev/null || failed=1; fi
  if [[ "${proxy_running}" == "running" ]]; then docker unpause finance-actual-proxy >/dev/null || failed=1; fi
  return "${failed}"
}

wait_for_url() {
  local label="$1"
  local url="$2"
  for _ in $(seq 1 90); do
    if [[ "${label}" == "cashback" ]]; then
      if docker exec finance-cashback-control python apps/cashback-control/probe_health.py >/dev/null 2>&1; then
        return 0
      fi
    elif curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  printf '{"level":"error","event":"backup_resume_unhealthy","service":"%s"}\n' "${label}" >&2
  return 1
}

emergency_resume() {
  resume_services || true
}
trap emergency_resume EXIT

if [[ "${proxy_running}" == "running" ]]; then docker pause finance-actual-proxy >/dev/null; fi
if [[ "${actual_running}" == "running" ]]; then docker pause finance-actual-poc >/dev/null; fi
if [[ "${cashback_running}" == "running" ]]; then docker pause finance-cashback-control >/dev/null; fi
sync

cp -a "${ACTUAL_DATA_DIR}/." "${payload}/actual-data/"
# Disposable pre-deploy snapshots are rollback material, not restore input.
# Exclude them before they enter the archive so an old copy cannot bypass the
# sanitizer or leak push credentials through a historical filename.
tar -C "${CASHBACK_DATA_DIR}" \
  --exclude='pre-deploy-*.sqlite3' \
  -cf - . | tar -C "${payload}/cashback-data" -xf -
while IFS= read -r -d '' database; do
  python3 "${SANITIZE_SCRIPT}" "${database}"
done < <(find "${payload}" -type f \( -name '*.sqlite' -o -name '*.sqlite3' \) -print0)

install -m 0644 "${ACTUAL_STACK_DIR}/compose.yaml" "${payload}/configuration/actual-compose.yaml"
install -m 0644 "${CASHBACK_STACK_DIR}/compose.yaml" "${payload}/configuration/cashback-compose.yaml"
if [[ -d "${ACTUAL_STACK_DIR}/nginx" ]]; then
  cp -a "${ACTUAL_STACK_DIR}/nginx" "${payload}/configuration/actual-nginx"
fi
for config_name in cashback-profile.json actual-bootstrap.json static-rules.json transaction-email-sources.json; do
  if [[ -f "${CASHBACK_STACK_DIR}/${config_name}" ]]; then
    install -m 0644 "${CASHBACK_STACK_DIR}/${config_name}" "${payload}/configuration/${config_name}"
  fi
done

tar -C "${payload}" -czf "${working}/finance-data.tar.gz" .
rm -rf -- "${payload}"
(
  cd "${working}"
  sha256sum finance-data.tar.gz > SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)
cat > "${working}/manifest.json" <<EOF
{"schema_version":4,"created_at":"${stamp}","includes":["actual-data","cashback-data","configuration"],"secrets_included":false,"excluded_data":["cashback-data/cashback-events.sqlite3:push_deliveries","cashback-data/cashback-events.sqlite3:push_state","cashback-data/cashback-events.sqlite3:push_subscriptions"],"excluded_paths":["cashback-data/pre-deploy-*.sqlite3"],"containers":{"actual":"finance-actual-poc","proxy":"finance-actual-proxy","cashback":"finance-cashback-control"}}
EOF

resume_services
if [[ "${actual_running}" == "running" && "${proxy_running}" == "running" ]]; then
  wait_for_url "actual" "http://127.0.0.1:5006/"
fi
if [[ "${cashback_running}" == "running" ]]; then
  wait_for_url "cashback" "http://127.0.0.1:5010/api/health"
fi
trap - EXIT
mv -- "${working}" "${destination}"
python3 "${VERIFY_SCRIPT}" \
  --backup-root "${BACKUP_ROOT}" \
  --backup-path "${destination}" \
  --write-receipt

if [[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS > 0 )); then
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' -mtime "+${RETENTION_DAYS}" -exec rm -rf -- {} +
fi

echo "{\"level\":\"info\",\"event\":\"backup_complete\",\"path\":\"${destination}\"}"
