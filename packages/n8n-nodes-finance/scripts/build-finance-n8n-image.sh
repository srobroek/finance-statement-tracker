#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
package_dir="${repo_root}/packages/n8n-nodes-finance"
dockerfile="${package_dir}/Dockerfile.n8n"
base_file="${package_dir}/base-image.txt"
base_provenance_file="${package_dir}/base-image-provenance.json"
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
[[ -f "${base_provenance_file}" ]] || {
  echo "FINANCE_BASE_IMAGE_PROVENANCE_MISSING" >&2
  exit 1
}
provenance_reference="$(node -p "require(process.argv[1]).reference" "${base_provenance_file}")"
provenance_digest="$(node -p "require(process.argv[1]).digest" "${base_provenance_file}")"
base_source_repository="$(node -p "require(process.argv[1]).source_repository" "${base_provenance_file}")"
base_source_commit="$(node -p "require(process.argv[1]).source_commit" "${base_provenance_file}")"
nodemailer_package="$(node -p "require(process.argv[1]).nodemailer_overlay.package" "${base_provenance_file}")"
nodemailer_tarball_sha256="$(node -p "require(process.argv[1]).nodemailer_overlay.tarball_sha256" "${base_provenance_file}")"
nodemailer_recipe_commit="$(node -p "require(process.argv[1]).nodemailer_overlay.source_commit" "${base_provenance_file}")"
nodemailer_dockerfile_blob="$(node -p "require(process.argv[1]).nodemailer_overlay.dockerfile_blob" "${base_provenance_file}")"
nodemailer_smoke_blob="$(node -p "require(process.argv[1]).nodemailer_overlay.smoke_blob" "${base_provenance_file}")"
[[ "${provenance_reference}" = "${base_image}" && "${provenance_digest}" = "${base_image##*@}" ]] || {
  echo "FINANCE_BASE_IMAGE_PROVENANCE_MISMATCH" >&2
  exit 1
}
[[ "${base_source_repository}" = "https://github.com/n8n-io/n8n" ]] || {
  echo "FINANCE_BASE_IMAGE_SOURCE_REPOSITORY_INVALID" >&2
  exit 1
}
[[ "${base_source_commit}" = "5542b8b6419cb6925cca8f11b270c9bfbe09d85e" ]] || {
  echo "FINANCE_BASE_IMAGE_SOURCE_COMMIT_INVALID" >&2
  exit 1
}
[[ "${nodemailer_package}" = "nodemailer@9.0.1" \
   && "${nodemailer_tarball_sha256}" = "ab8bdd84372cb54955930722db668f878865b86aa3520117ad92c4febe1af2a3" \
   && "${nodemailer_recipe_commit}" = "9bd6b55e88deade27591080e14f1a7c4bdc9808b" \
   && "${nodemailer_dockerfile_blob}" = "02cfd924119874c03aa1b94367bf8eefdf166d90" \
   && "${nodemailer_smoke_blob}" = "cdb2c9c08500e798ab7881818707fdf710709213" \
   && "$(git hash-object "${package_dir}/scripts/nodemailer-smoke.cjs")" = "${nodemailer_smoke_blob}" ]] || {
  echo "FINANCE_NODEMAILER_OVERLAY_PROVENANCE_INVALID" >&2
  exit 1
}

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
FINANCE_BASE_SOURCE_REPOSITORY="${base_source_repository}" \
FINANCE_BASE_SOURCE_COMMIT="${base_source_commit}" \
FINANCE_NODEMAILER_PACKAGE="${nodemailer_package}" \
FINANCE_NODEMAILER_TARBALL_SHA256="${nodemailer_tarball_sha256}" \
FINANCE_NODEMAILER_RECIPE_COMMIT="${nodemailer_recipe_commit}" \
FINANCE_BASE_PROVENANCE_PATH="packages/n8n-nodes-finance/base-image-provenance.json" \
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
    source_repository: process.env.FINANCE_BASE_SOURCE_REPOSITORY,
    source_commit: process.env.FINANCE_BASE_SOURCE_COMMIT,
    provenance_path: process.env.FINANCE_BASE_PROVENANCE_PATH,
    nodemailer_overlay: {
      package: process.env.FINANCE_NODEMAILER_PACKAGE,
      tarball_sha256: process.env.FINANCE_NODEMAILER_TARBALL_SHA256,
      recipe_commit: process.env.FINANCE_NODEMAILER_RECIPE_COMMIT,
    },
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
