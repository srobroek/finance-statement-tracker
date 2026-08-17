#!/usr/bin/env bash
set -euo pipefail

STACK_DIR="${FINANCE_STACK_DIR:-/opt/stacks/finance-actual-poc}"
BACKUP_ROOT="${FINANCE_BACKUP_ROOT:-/opt/backups/finance-actual-poc}"
RETENTION_DAYS="${FINANCE_BACKUP_RETENTION_DAYS:-30}"

if [[ "${EUID}" -ne 0 ]]; then
  echo '{"level":"error","event":"backup_refused","reason":"root_required"}' >&2
  exit 1
fi
if [[ "$(readlink -f "${STACK_DIR}")" != "/opt/stacks/finance-actual-poc" ]]; then
  echo '{"level":"error","event":"backup_refused","reason":"unexpected_stack_path"}' >&2
  exit 1
fi

mkdir -p "${BACKUP_ROOT}"
exec 9>"${BACKUP_ROOT}/.backup.lock"
flock -n 9 || {
  echo '{"level":"warning","event":"backup_skipped","reason":"already_running"}'
  exit 0
}

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="${BACKUP_ROOT}/${stamp}"
mkdir -p "${destination}"

actual_running="$(docker inspect -f '{{.State.Running}}' finance-actual-poc 2>/dev/null || true)"
proxy_running="$(docker inspect -f '{{.State.Running}}' finance-actual-proxy 2>/dev/null || true)"
cashback_running="$(docker inspect -f '{{.State.Running}}' finance-cashback-control 2>/dev/null || true)"

restart_services() {
  if [[ "${actual_running}" == "true" ]]; then docker start finance-actual-poc >/dev/null; fi
  if [[ "${cashback_running}" == "true" ]]; then docker start finance-cashback-control >/dev/null; fi
  # Podman may assign Actual a new address after a stop/start. Restart Nginx
  # last so its startup DNS lookup never retains the pre-backup address.
  if [[ "${proxy_running}" == "true" ]]; then docker start finance-actual-proxy >/dev/null; fi
}
trap restart_services EXIT

if [[ "${proxy_running}" == "true" ]]; then docker stop finance-actual-proxy >/dev/null; fi
if [[ "${actual_running}" == "true" ]]; then docker stop finance-actual-poc >/dev/null; fi
if [[ "${cashback_running}" == "true" ]]; then docker stop finance-cashback-control >/dev/null; fi

tar -C "${STACK_DIR}" -czf "${destination}/finance-data.tar.gz" data cashback-data
sha256sum "${destination}/finance-data.tar.gz" > "${destination}/SHA256SUMS"
cat > "${destination}/manifest.json" <<EOF
{"schema_version":1,"created_at":"${stamp}","actual_image":"actualbudget/actual-server:26.8.1@sha256:6478d9ddfc0924479c09e6699c205e354c6f2216dfe7de3c0fb7b590d6edcdc5","includes":["data","cashback-data"],"secrets_included":false}
EOF

restart_services
trap - EXIT

if [[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS > 0 )); then
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf -- {} +
fi

echo "{\"level\":\"info\",\"event\":\"backup_complete\",\"path\":\"${destination}\"}"
