#!/usr/bin/env bash
set -euo pipefail

readonly service_dir="/tmp/sjors-podman-codex"
readonly socket_path="${service_dir}/podman.sock"
readonly log_path="${service_dir}/service.log"
readonly pid_path="${service_dir}/service.pid"

install -d -m 700 "${service_dir}"

if [[ -S "${socket_path}" ]] && curl --silent --fail --unix-socket "${socket_path}" http://d/_ping >/dev/null 2>&1; then
  printf 'PODMAN_PROOF_SERVICE_READY=%s\n' "${socket_path}"
  exit 0
fi

if [[ -f "${pid_path}" ]]; then
  old_pid="$(tr -cd '0-9' < "${pid_path}")"
  if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
    printf 'Existing Podman proof service process %s is not ready; see %s\n' "${old_pid}" "${log_path}" >&2
    exit 1
  fi
fi

rm -f -- "${socket_path}" "${pid_path}"
nohup podman system service --time=0 "unix://${socket_path}" >"${log_path}" 2>&1 &
service_pid=$!
printf '%s\n' "${service_pid}" > "${pid_path}"
chmod 600 "${pid_path}" "${log_path}"

for _ in $(seq 1 50); do
  if [[ -S "${socket_path}" ]] && curl --silent --fail --unix-socket "${socket_path}" http://d/_ping >/dev/null 2>&1; then
    chmod 600 "${socket_path}"
    printf 'PODMAN_PROOF_SERVICE_READY=%s\n' "${socket_path}"
    exit 0
  fi
  if ! kill -0 "${service_pid}" 2>/dev/null; then
    printf 'Podman proof service exited; see %s\n' "${log_path}" >&2
    exit 1
  fi
  sleep 0.1
done

printf 'Podman proof service did not become ready; see %s\n' "${log_path}" >&2
exit 1
