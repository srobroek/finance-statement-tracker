#!/usr/bin/env bash
set -euo pipefail

readonly source_codex_home="${CODEX_PROBE_SOURCE_HOME:-${HOME}/.codex}"
readonly source_marketplace="${CODEX_PROBE_MARKETPLACE:-${source_codex_home}/.tmp/plugins}"
readonly image="${CODEX_PROBE_IMAGE:-docker.io/library/node:24-bookworm}"
readonly codex_version="${CODEX_PROBE_VERSION:-0.149.0}"
readonly default_socket="/tmp/sjors-podman-codex/podman.sock"
readonly podman_socket="${PODMAN_PROOF_SOCKET:-$([[ -S "${default_socket}" ]] && printf '%s' "${default_socket}")}"
script_dir="$(dirname "$0")"
readonly script_dir
entrypoint="$(realpath "${script_dir}/../probe/container-entrypoint.sh")"
readonly entrypoint

work="$(mktemp -d /tmp/ccp.XXXXXX)"
runtime="$(mktemp -d /tmp/ccr.XXXXXX)"
cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n "${container_id:-}" && -n "${podman_socket}" ]]; then
    curl --silent --unix-socket "${podman_socket}" \
      --request DELETE "http://d/v1.41/containers/${container_id}?force=true" >/dev/null || true
  fi
  chmod -R u+rwX "${work}" "${runtime}" 2>/dev/null || true
  rm -rf -- "${work}" "${runtime}"
  exit "${status}"
}
trap cleanup EXIT
chmod 700 "${work}" "${runtime}"
mkdir -m 700 "${work}/codex" "${work}/codex/.tmp" "${runtime}/xdg" "${runtime}/runroot" "${runtime}/tmp"

install -m 600 "${source_codex_home}/auth.json" "${work}/codex/auth.json"
install -m 600 "$(dirname "$0")/../probe/minimal-config.toml" "${work}/codex/config.toml"
install -m 600 "$(dirname "$0")/../probe/empty-registry-auth.json" "${work}/containers-auth.json"
install -m 600 "${source_codex_home}/.tmp/plugins.sha" "${work}/codex/.tmp/plugins.sha"
cp -a "${source_marketplace}" "${work}/codex/.tmp/plugins"
chmod -R go-rwx "${work}/codex"

prompt='Use only the installed OpenAI Outlook Email and SharePoint connectors. Call get_profile once on each connector as a read-only authentication check. Do not print source data or identifiers. Reply exactly CONTAINER_CONNECTOR_OK=true only if both calls completed; otherwise reply exactly CONTAINER_CONNECTOR_OK=false.'

printf '%s\n' 'PROBE_STAGE=container_start'
if [[ -n "${podman_socket}" ]]; then
  curl --silent --fail --unix-socket "${podman_socket}" http://d/_ping >/dev/null
  create_payload="$(jq -cn \
    --arg image "${image}" \
    --arg codex_home "${work}/codex:/home/node/.codex:rw" \
    --arg entrypoint_mount "${entrypoint}:/probe/container-entrypoint.sh:ro" \
    --arg version "${codex_version}" \
    --arg prompt "${prompt}" \
    '{Image:$image,Cmd:["/probe/container-entrypoint.sh"],Env:["HOME=/home/node","CODEX_HOME=/home/node/.codex",("CODEX_PROBE_VERSION="+$version),("CODEX_PROBE_PROMPT="+$prompt)],HostConfig:{Binds:[$codex_home,$entrypoint_mount]}}')"
  create_result="$(curl --silent --fail-with-body --unix-socket "${podman_socket}" \
    --header 'Content-Type: application/json' \
    --data "${create_payload}" \
    http://d/v1.41/containers/create)"
  container_id="$(jq -er '.Id // error(.message // "container create returned no ID")' <<<"${create_result}")"
  curl --silent --fail --unix-socket "${podman_socket}" \
    --request POST "http://d/v1.41/containers/${container_id}/start" >/dev/null
  wait_result="$(curl --silent --fail --unix-socket "${podman_socket}" \
    --request POST "http://d/v1.41/containers/${container_id}/wait")"
  curl --silent --fail --unix-socket "${podman_socket}" \
    "http://d/v1.41/containers/${container_id}/logs?stdout=true&stderr=true" \
    | sed 's/^[^[:print:]]*//'
  if [[ "$(jq -er '.StatusCode' <<<"${wait_result}")" -ne 0 ]]; then
    printf '%s\n' 'PROBE_FAILURE=container_exit' >&2
    exit 35
  fi
else
  REGISTRY_AUTH_FILE="${work}/containers-auth.json" XDG_RUNTIME_DIR="${runtime}/xdg" podman \
    --runroot "${runtime}/runroot" \
    --tmpdir "${runtime}/tmp" \
    --transient-store \
    run --rm --network=slirp4netns \
    --mount "type=bind,src=${work}/codex,dst=/home/node/.codex,rw" \
    --mount "type=bind,src=${entrypoint},dst=/probe/container-entrypoint.sh,ro" \
    --env HOME=/home/node \
    --env CODEX_HOME=/home/node/.codex \
    --env "CODEX_PROBE_VERSION=${codex_version}" \
    --env "CODEX_PROBE_PROMPT=${prompt}" \
    "${image}" \
    /probe/container-entrypoint.sh
fi
