#!/usr/bin/env python3
"""Write a production-lock-compatible receipt from immutable CI evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re


ROLES = {"n8n", "task_runners", "pdf_utility"}
IMAGE_PREFIXES = {
    "n8n": "ghcr.io/srobroek/finance-n8n@",
    "task_runners": "ghcr.io/srobroek/finance-n8n-task-runners@",
    "pdf_utility": "ghcr.io/srobroek/finance-pdf-utility@",
}
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def fail(message: str) -> None:
    raise SystemExit(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def load_object(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"INVALID_JSON:{path}:{error}")
    require(isinstance(value, dict), f"JSON_OBJECT_REQUIRED:{path}")
    return value


def file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--semver", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--sbom", type=pathlib.Path, required=True)
    parser.add_argument("--scan", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--base-provenance", type=pathlib.Path)
    parser.add_argument("--n8n-source-commit")
    parser.add_argument("--launcher-source-commit")
    parser.add_argument("--closure-sha256")
    args = parser.parse_args()

    require(args.reference.startswith(IMAGE_PREFIXES[args.role]), "IMAGE_REFERENCE_PREFIX_INVALID")
    digest = args.reference.removeprefix(IMAGE_PREFIXES[args.role])
    require(DIGEST.fullmatch(digest) is not None, "IMAGE_DIGEST_INVALID")
    require(DIGEST.fullmatch(args.image_id) is not None, "IMAGE_ID_INVALID")
    require(VERSION.fullmatch(args.semver) is not None, "IMAGE_VERSION_INVALID")
    require(COMMIT.fullmatch(args.source_commit) is not None, "SOURCE_COMMIT_INVALID")
    require(args.sbom.is_file() and not args.sbom.is_symlink(), "SBOM_MISSING")
    require(args.scan.is_file() and not args.scan.is_symlink(), "SCAN_MISSING")

    sbom = load_object(args.sbom)
    require(sbom.get("spdxVersion") == "SPDX-2.3", "SBOM_SCHEMA_INVALID")
    require(isinstance(sbom.get("packages"), list) and sbom["packages"], "SBOM_PACKAGES_MISSING")

    scan = load_object(args.scan)
    require(scan.get("Metadata", {}).get("ImageID") == args.image_id, "SCAN_IMAGE_ID_MISMATCH")
    require(args.reference in (scan.get("Metadata", {}).get("RepoDigests") or []), "SCAN_REPO_DIGEST_MISMATCH")
    results = scan.get("Results")
    require(isinstance(results, list) and results, "SCAN_RESULTS_MISSING")
    findings = [
        finding
        for result in results
        for finding in (result.get("Vulnerabilities") or [])
        if finding.get("Severity") in {"HIGH", "CRITICAL"}
    ]
    require(not findings, "SCAN_HIGH_OR_CRITICAL_FINDINGS")

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    require(repository == "srobroek/finance-statement-tracker", "CI_REPOSITORY_INVALID")
    require(run_id.isdigit() and int(run_id) > 0, "CI_RUN_ID_INVALID")
    repository_url = f"https://github.com/{repository}"

    receipt = {
        "schema_version": 1,
        "status": "VERIFIED_CI",
        "image": {
            "role": args.role,
            "reference": args.reference,
            "digest": digest,
            "image_id": args.image_id,
            "semver": args.semver,
            "source_commit": args.source_commit,
        },
        "sbom_sha256": file_sha256(args.sbom),
        "scan": {
            "tool": "Trivy via aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25",
            "result": "PASS",
            "high": 0,
            "critical": 0,
            "artifact_sha256": file_sha256(args.scan),
        },
        "ci": {
            "repository": repository_url,
            "run_url": f"{repository_url}/actions/runs/{run_id}",
            "run_id": int(run_id),
        },
    }

    if args.role == "n8n":
        require(args.base_provenance is not None, "BASE_PROVENANCE_REQUIRED")
        provenance = load_object(args.base_provenance)
        require(provenance.get("version") == args.semver, "BASE_VERSION_MISMATCH")
        require(provenance.get("release_channel") == "stable", "BASE_NOT_STABLE")
        workflow = provenance.get("bundled_n8n_workflow", "")
        require(VERSION.fullmatch(workflow) is not None, "BUNDLED_WORKFLOW_VERSION_INVALID")
        receipt["n8n"] = {
            "base_image_reference": provenance.get("reference"),
            "base_provenance_sha256": file_sha256(args.base_provenance),
            "bundled_n8n_workflow": workflow,
            "custom_node_package": "n8n-nodes-finance@0.1.0",
        }
    elif args.role == "task_runners":
        require(COMMIT.fullmatch(args.n8n_source_commit or "") is not None, "N8N_SOURCE_COMMIT_INVALID")
        require(COMMIT.fullmatch(args.launcher_source_commit or "") is not None, "LAUNCHER_SOURCE_COMMIT_INVALID")
        require(SHA256.fullmatch(args.closure_sha256 or "") is not None, "CLOSURE_SHA256_INVALID")
        receipt["task_runners"] = {
            "n8n_source_commit": args.n8n_source_commit,
            "launcher_source_commit": args.launcher_source_commit,
            "closure_sha256": args.closure_sha256,
        }
    else:
        require(args.base_provenance is None, "BASE_PROVENANCE_ROLE_INVALID")

    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    args.output.chmod(0o600)


if __name__ == "__main__":
    main()
