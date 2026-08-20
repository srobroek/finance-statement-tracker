#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
package_dir="${repo_root}/packages/n8n-nodes-finance"
dockerfile="${package_dir}/Dockerfile.n8n"
base_file="${package_dir}/base-image.txt"
receipt="${TMPDIR:-/tmp}/finance-n8n-image-build-receipt.json"
tag="finance-n8n:spec-0.1.0"
dry_run=0

usage() {
  printf 'Usage: %s [--tag IMAGE] [--receipt PATH] [--dry-run]\n' "${0##*/}" >&2
}

while (($#)); do
  case "$1" in
    --tag)
      (($# >= 2)) || { usage; exit 2; }
      tag="$2"
      shift 2
      ;;
    --receipt)
      (($# >= 2)) || { usage; exit 2; }
      receipt="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

receipt="$(realpath -m "${receipt}")"
case "${receipt}" in
  "${repo_root}"|"${repo_root}"/*)
    echo "FINANCE_RUNTIME_RECEIPT_MUST_BE_EXTERNAL" >&2
    exit 1
    ;;
esac

base_image="$(tr -d '[:space:]' < "${base_file}")"
if [[ ! "${base_image}" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "FINANCE_BASE_IMAGE_MUST_BE_IMMUTABLE" >&2
  exit 1
fi

if [[ -n "$(git -C "${repo_root}" status --porcelain --untracked-files=all)" ]]; then
  echo "FINANCE_SOURCE_TREE_MUST_BE_CLEAN" >&2
  exit 1
fi

package_version="$(node -p "require(process.argv[1]).version" "${package_dir}/package.json")"
source_commit="$(git -C "${repo_root}" rev-parse HEAD 2>/dev/null || true)"
[[ "${source_commit}" =~ ^[0-9a-f]{40}$ ]] || source_commit=""
image_id=""

if ((dry_run == 0)); then
  docker build \
    --file "${dockerfile}" \
    --build-arg "N8N_BASE_IMAGE=${base_image}" \
    --build-arg "FINANCE_SOURCE_COMMIT=${source_commit:-UNVERIFIED}" \
    --tag "${tag}" \
    "${repo_root}"
  image_id="$(docker image inspect "${tag}" --format '{{.Id}}')"
  [[ "${image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "FINANCE_LOCAL_IMAGE_ID_INVALID" >&2
    exit 1
  }
fi

mkdir -p "$(dirname "${receipt}")"
FINANCE_RECEIPT="${receipt}" \
FINANCE_IMAGE_TAG="${tag}" \
FINANCE_IMAGE_ID="${image_id}" \
FINANCE_PACKAGE_VERSION="${package_version}" \
FINANCE_SOURCE_COMMIT="${source_commit}" \
FINANCE_BASE_IMAGE="${base_image}" \
node <<'NODE'
'use strict';
const fs = require('node:fs');

const digest = process.env.FINANCE_BASE_IMAGE.split('@')[1];
const sourceCommit = /^[0-9a-f]{40}$/.test(process.env.FINANCE_SOURCE_COMMIT)
  ? process.env.FINANCE_SOURCE_COMMIT
  : null;
const imageId = /^sha256:[0-9a-f]{64}$/.test(process.env.FINANCE_IMAGE_ID)
  ? process.env.FINANCE_IMAGE_ID
  : null;
const receipt = {
  schema_version: 1,
  status: 'SPEC_ONLY',
  package: `n8n-nodes-finance@${process.env.FINANCE_PACKAGE_VERSION}`,
  image: {
    requested_reference: process.env.FINANCE_IMAGE_TAG,
    image_digest: null,
    local_image_id: imageId,
  },
  base_image: {
    reference: process.env.FINANCE_BASE_IMAGE,
    digest,
  },
  source_commit: sourceCommit,
  sbom_sha256: null,
  scan_sha256: null,
  scan: {tool: null, result: 'NOT_RUN', high: null, critical: null},
  attestation: {
    type: null,
    predicate_type: null,
    subject_digest: null,
    source_commit: sourceCommit,
    sha256: null,
    status: 'NOT_AVAILABLE',
  },
  blockers: [
    'LIVE_REGISTRY_DIGEST_REQUIRED',
    'SBOM_SCAN_ATTESTATION_REQUIRED',
    'DISPOSABLE_IMAGE_IMPORT_REQUIRED',
  ],
};
fs.writeFileSync(process.env.FINANCE_RECEIPT, `${JSON.stringify(receipt, null, 2)}\n`, {
  encoding: 'utf8',
  mode: 0o644,
});
NODE

printf 'Wrote truthful SPEC_ONLY receipt: %s\n' "${receipt}"
