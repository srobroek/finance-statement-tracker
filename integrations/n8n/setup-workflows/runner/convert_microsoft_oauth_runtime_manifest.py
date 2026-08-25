#!/usr/bin/env python3
"""Convert the checked-in Microsoft application contract to a runtime copy."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RUNNER = Path(__file__).resolve().parent
BINDER_PATH = RUNNER / "bind-microsoft-oauth-refresh-proof.py"
WORKFLOW_RELATIVE_PATH = Path("integrations/n8n/setup-workflows/23-microsoft-oauth-refresh-proof.json")
WORKFLOW_OUTPUT_NAME = "23-microsoft-oauth-refresh-proof.json"
RUNTIME_MANIFEST_SCHEMA = "runtime-bound-application-v1"
EXPECTED_SOURCE_KEYS = {
    "schema_version",
    "contract_status",
    "application",
    "finance_commit",
    "image_lock",
    "base_image",
    "extension_image",
    "workflow_manifest",
    "fixture_manifest",
    "inactive_corpus",
    "credentials",
    "mcp",
    "validators",
    "blockers",
}


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"SOURCE_MANIFEST_UNREADABLE:{type(error).__name__}") from error
    if not isinstance(value, dict):
        raise SystemExit("SOURCE_MANIFEST_OBJECT_REQUIRED")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corpus_sha256(root: Path) -> str:
    directory = root / "integrations" / "n8n" / "workflows"
    digest = hashlib.sha256()
    files = sorted(directory.glob("*.json"))
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def _regular_file(path: Path, error: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise SystemExit(error)
    return path


def _relative_artifact(root: Path, declaration: object, error: str) -> Path:
    if not isinstance(declaration, dict):
        raise SystemExit(error)
    relative = declaration.get("path")
    expected = declaration.get("sha256")
    if (
        not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not isinstance(expected, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        raise SystemExit(error)
    artifact = _regular_file(root / relative, error)
    if _sha256(artifact) != expected:
        raise SystemExit("SOURCE_MANIFEST_ARTIFACT_SHA256_MISMATCH")
    return artifact


def _load_binder():
    spec = importlib.util.spec_from_file_location("finance_microsoft_oauth_binder", BINDER_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit("MICROSOFT_OAUTH_BINDER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_source_manifest(source_manifest: Path, root: Path) -> tuple[dict[str, object], str, object]:
    _regular_file(source_manifest, "SOURCE_MANIFEST_REGULAR_FILE_REQUIRED")
    manifest = _load_json(source_manifest)
    canonical_path = root / "integrations" / "n8n" / "application-manifest.json"
    canonical_manifest = _load_json(_regular_file(canonical_path, "SOURCE_MANIFEST_CANONICAL_COPY_MISSING"))
    if manifest != canonical_manifest:
        raise SystemExit("SOURCE_MANIFEST_STALE")
    if set(manifest) != EXPECTED_SOURCE_KEYS:
        raise SystemExit("SPEC_ONLY_SOURCE_MANIFEST_SCHEMA_MISMATCH")
    if manifest.get("schema_version") != 1 or manifest.get("contract_status") != "SPEC_ONLY":
        raise SystemExit("SPEC_ONLY_SOURCE_MANIFEST_REQUIRED")
    if manifest.get("finance_commit") is not None:
        raise SystemExit("SPEC_ONLY_SOURCE_MANIFEST_COMMIT_FORBIDDEN")
    application = manifest.get("application")
    if application != {
        "name": "finance-statement-tracker",
        "repository": "srobroek/finance-statement-tracker",
    }:
        raise SystemExit("SOURCE_MANIFEST_APPLICATION_MISMATCH")
    extension = manifest.get("extension_image")
    if not isinstance(extension, dict) or extension.get("digest") is not None:
        raise SystemExit("SPEC_ONLY_EXTENSION_DIGEST_FORBIDDEN")

    base_image = manifest.get("base_image")
    if not isinstance(base_image, dict) or base_image.get("path") != "packages/n8n-nodes-finance/base-image.txt":
        raise SystemExit("SOURCE_MANIFEST_BASE_IMAGE_INVALID")
    base_path = _regular_file(root / base_image["path"], "SOURCE_MANIFEST_BASE_IMAGE_INVALID")
    base_reference = base_path.read_text(encoding="utf-8").strip()
    if base_image.get("reference") != base_reference or base_image.get("digest") != base_reference.rsplit("@", 1)[-1]:
        raise SystemExit("SOURCE_MANIFEST_BASE_IMAGE_STALE")
    _relative_artifact(root, extension.get("receipt"), "SOURCE_MANIFEST_EXTENSION_RECEIPT_INVALID")
    _relative_artifact(root, manifest.get("image_lock"), "SOURCE_MANIFEST_IMAGE_LOCK_INVALID")
    _relative_artifact(root, manifest.get("workflow_manifest"), "SOURCE_MANIFEST_WORKFLOW_MANIFEST_INVALID")
    _relative_artifact(root, manifest.get("fixture_manifest"), "SOURCE_MANIFEST_FIXTURE_MANIFEST_INVALID")
    mcp = manifest.get("mcp")
    if not isinstance(mcp, dict):
        raise SystemExit("SOURCE_MANIFEST_MCP_INVALID")
    _relative_artifact(root, mcp.get("contract"), "SOURCE_MANIFEST_MCP_CONTRACT_INVALID")
    corpus = manifest.get("inactive_corpus")
    if (
        not isinstance(corpus, dict)
        or corpus.get("path") != "integrations/n8n/workflows"
        or corpus.get("file_count") != 19
        or corpus.get("sha256") != _corpus_sha256(root)
        or corpus.get("required_state") != {"active": False, "published": False, "status": "SPEC_ONLY"}
        or corpus.get("setup_corpus_excluded") is not True
    ):
        raise SystemExit("SOURCE_MANIFEST_WORKFLOW_CORPUS_INVALID")

    credentials = manifest.get("credentials")
    if not isinstance(credentials, dict):
        raise SystemExit("SOURCE_MANIFEST_CREDENTIALS_INVALID")
    binding_contract = credentials.get("binding_contract")
    if (
        not isinstance(binding_contract, dict)
        or binding_contract.get("path") != "integrations/n8n/credential-bindings.json"
    ):
        raise SystemExit("SOURCE_MANIFEST_BINDING_CONTRACT_INVALID")
    contract_path = _regular_file(root / binding_contract["path"], "SOURCE_MANIFEST_BINDING_CONTRACT_INVALID")
    if _sha256(contract_path) != binding_contract.get("sha256"):
        raise SystemExit("SOURCE_MANIFEST_BINDING_CONTRACT_STALE")
    if credentials.get("values_included") is not False:
        raise SystemExit("SOURCE_MANIFEST_CREDENTIAL_VALUES_FORBIDDEN")
    placeholders = credentials.get("placeholders")
    if not isinstance(placeholders, list) or not placeholders:
        raise SystemExit("SOURCE_MANIFEST_CREDENTIAL_PLACEHOLDERS_INVALID")
    if any(
        not isinstance(row, dict)
        or set(row) != {"name", "binding", "type"}
        or not isinstance(row.get("name"), str)
        or not isinstance(row.get("binding"), str)
        or not isinstance(row.get("type"), str)
        for row in placeholders
    ):
        raise SystemExit("SOURCE_MANIFEST_CREDENTIAL_PLACEHOLDERS_INVALID")

    binder = _load_binder()
    workflow_source = _regular_file(
        root / WORKFLOW_RELATIVE_PATH,
        "MICROSOFT_OAUTH_WORKFLOW_SOURCE_MISSING",
    )
    if _raw_sha256(workflow_source) != binder.SOURCE_SHA256:
        raise SystemExit("MICROSOFT_OAUTH_WORKFLOW_SOURCE_STALE")
    return manifest, _raw_sha256(source_manifest), binder


def _validate_commit(finance_commit: str) -> None:
    if not isinstance(finance_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", finance_commit):
        raise SystemExit("EXACT_FINANCE_COMMIT_REQUIRED")


def _validate_ids(binder: object, credential_ids: Mapping[str, str]) -> None:
    validator = getattr(binder, "validate_credential_ids", None)
    if not callable(validator):
        raise SystemExit("MICROSOFT_OAUTH_CREDENTIAL_VALIDATOR_UNAVAILABLE")
    validator(credential_ids)


def convert_manifest(
    source_manifest: Path,
    destination: Path,
    finance_commit: str,
    credential_ids: Mapping[str, str],
    *,
    source_root: Path | None = None,
    expected_source_manifest_sha256: str | None,
) -> Path:
    """Create one redacted runtime manifest and one bound WF23 copy."""

    source_manifest = Path(source_manifest).resolve()
    destination = Path(destination).resolve()
    if source_root is not None:
        root = Path(source_root).resolve()
    else:
        candidate_root = source_manifest.parents[2]
        root = candidate_root if (candidate_root / "integrations" / "n8n" / "workflows").is_dir() else ROOT
    _validate_commit(finance_commit)
    if destination.exists() or destination.is_symlink():
        raise SystemExit("RUNTIME_MANIFEST_DESTINATION_MUST_NOT_EXIST")
    if destination == source_manifest:
        raise SystemExit("RUNTIME_MANIFEST_SOURCE_REPLACEMENT_FORBIDDEN")
    manifest, source_manifest_sha256, binder = _validate_source_manifest(source_manifest, root)
    if expected_source_manifest_sha256 is None:
        raise SystemExit("EXPECTED_SOURCE_MANIFEST_SHA256_REQUIRED")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_source_manifest_sha256):
        raise SystemExit("EXPECTED_SOURCE_MANIFEST_SHA256_INVALID")
    if source_manifest_sha256 != expected_source_manifest_sha256:
        raise SystemExit("SOURCE_MANIFEST_SHA256_MISMATCH")
    _validate_ids(binder, credential_ids)

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    artifact_dir = destination.parent / f"{destination.stem}.runtime"
    if artifact_dir.exists() or artifact_dir.is_symlink():
        raise SystemExit("RUNTIME_MANIFEST_ARTIFACT_DESTINATION_MUST_NOT_EXIST")
    workflow_source = root / WORKFLOW_RELATIVE_PATH
    workflow_destination = artifact_dir / WORKFLOW_OUTPUT_NAME
    try:
        binder.bind_workflow(workflow_source, workflow_destination, finance_commit, credential_ids)
        receipt_path = artifact_dir / "binding-receipt.json"
        providers = binder.PROVIDERS
        provider_bindings = [
            {
                "node_type": node_type,
                "credential_type": contract[0],
                "placeholder": contract[1],
                "name": contract[2],
            }
            for node_type, contract in providers.items()
        ]
        runtime_manifest = {
            "schema_version": RUNTIME_MANIFEST_SCHEMA,
            "status": "RUNTIME_BOUND",
            "application": manifest["application"],
            "source_manifest_sha256": source_manifest_sha256,
            "finance_commit": finance_commit,
            "workflow": {
                "id": binder.WORKFLOW_ID,
                "code": binder.WORKFLOW_CODE,
                "source_sha256": binder.SOURCE_SHA256,
                "bound_sha256": _raw_sha256(workflow_destination),
                "path": f"{artifact_dir.name}/{WORKFLOW_OUTPUT_NAME}",
                "active": False,
                "published": False,
            },
            "provider_bindings": provider_bindings,
            "binding_receipt": {
                "path": f"{artifact_dir.name}/binding-receipt.json",
                "sha256": _raw_sha256(receipt_path),
            },
            "redaction": {
                "credential_ids_recorded": False,
                "secret_values_recorded": False,
                "token_values_recorded": False,
            },
            "retained_state_replaced": False,
            "production_mutation": False,
        }
        destination.write_text(
            json.dumps(runtime_manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        destination.chmod(0o600)
    except BaseException:
        if artifact_dir.exists() and not artifact_dir.is_symlink():
            shutil.rmtree(artifact_dir)
        if destination.exists() and not destination.is_symlink():
            destination.unlink()
        raise
    return destination


def _credential_ids_from_environment(binder: object) -> dict[str, str]:
    return {
        node_type: binder.require_identifier(contract[3])
        for node_type, contract in binder.PROVIDERS.items()
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_manifest", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--finance-commit", required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-manifest-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    binder = _load_binder()
    credential_ids = _credential_ids_from_environment(binder)
    convert_manifest(
        args.source_manifest,
        args.destination,
        args.finance_commit,
        credential_ids,
        source_root=args.source_root,
        expected_source_manifest_sha256=args.source_manifest_sha256,
    )
    print("Created one redacted runtime-bound Microsoft OAuth manifest; identifiers were not printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
