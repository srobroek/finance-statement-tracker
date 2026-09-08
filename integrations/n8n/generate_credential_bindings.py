from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = ROOT / "integrations" / "n8n" / "workflows"
OUTPUT_PATH = ROOT / "integrations" / "n8n" / "credential-bindings.json"
APPLICATION_MANIFEST_PATH = ROOT / "integrations" / "n8n" / "application-manifest.json"
APPLICATION_SCHEMA_PATH = (
    ROOT / "integrations" / "n8n" / "application-manifest.schema.json"
)
SOURCE_BINDINGS_MANIFEST_PATH = ROOT / "integrations" / "n8n" / "source-contract-bindings.json"


def _canonical_bytes(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"REGULAR_SOURCE_FILE_REQUIRED:{path.name}")
    return path.read_bytes().replace(b"\r\n", b"\n")


def _corpus_sha256(workflows: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in workflows:
        try:
            relative = path.relative_to(ROOT)
        except ValueError:
            relative = Path("integrations/n8n/workflows") / path.name
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def _binding_entries(path: Path) -> list[dict[str, Any]]:
    workflow = json.loads(_canonical_bytes(path))
    workflow_id = workflow.get("id")
    workflow_code = workflow.get("meta", {}).get("financeWorkflowCode")
    if not all(
        isinstance(value, str) and value for value in (workflow_id, workflow_code)
    ):
        raise ValueError(f"WORKFLOW_IDENTITY_INVALID:{path.name}")
    declared = workflow.get("meta", {}).get("credentialBindings", [])
    if not isinstance(declared, list):
        raise TypeError(f"CREDENTIAL_BINDINGS_INVALID:{path.name}")

    declared_placeholders = []
    declared_names: dict[str, str] = {}
    for binding in declared:
        placeholder = binding.get("placeholder") if isinstance(binding, dict) else None
        if not isinstance(placeholder, str) or not placeholder.startswith("BIND_"):
            raise ValueError(f"CREDENTIAL_PLACEHOLDER_INVALID:{path.name}")
        declared_placeholders.append(placeholder)
        credential_name = binding.get("credential_name") if isinstance(binding, dict) else None
        if credential_name is not None:
            if not isinstance(credential_name, str) or not credential_name:
                raise ValueError(f"CREDENTIAL_NAME_INVALID:{path.name}:{placeholder}")
            declared_names[placeholder] = credential_name
    if len(set(declared_placeholders)) != len(declared_placeholders):
        raise ValueError(f"CREDENTIAL_PLACEHOLDER_DUPLICATE:{path.name}")

    entries: list[dict[str, Any]] = []
    used_placeholders: set[str] = set()
    for node in workflow.get("nodes", []):
        credentials = node.get("credentials", {})
        if credentials is None:
            credentials = {}
        if not isinstance(credentials, dict):
            raise TypeError(f"NODE_CREDENTIALS_INVALID:{path.name}:{node.get('id')}")
        for credential_type, value in credentials.items():
            placeholder = value.get("id") if isinstance(value, dict) else None
            if not isinstance(placeholder, str) or not placeholder.startswith("BIND_"):
                raise ValueError(
                    f"CREDENTIAL_PLACEHOLDER_INVALID:{path.name}:{node.get('id')}"
                )
            if placeholder not in declared_placeholders:
                raise ValueError(f"CREDENTIAL_NOT_DECLARED:{path.name}:{placeholder}")
            node_id = node.get("id")
            node_name = node.get("name")
            node_type = node.get("type")
            if not all(
                isinstance(item, str) and item
                for item in (node_id, node_name, node_type, credential_type)
            ):
                raise ValueError(f"NODE_IDENTITY_INVALID:{path.name}:{node_id}")
            used_placeholders.add(placeholder)
            entry = {
                "placeholder": placeholder,
                "credential_type": credential_type,
                "node_type": node_type,
                "workflow": {
                    "code": workflow_code,
                    "file": path.name,
                    "id": workflow_id,
                },
                "node": {"id": node_id, "name": node_name},
            }
            if placeholder in declared_names:
                entry["credential_name"] = declared_names[placeholder]
            entries.append(entry)
    if used_placeholders != set(declared_placeholders):
        missing = sorted(set(declared_placeholders) - used_placeholders)
        raise ValueError(
            f"CREDENTIAL_DECLARATION_UNUSED:{path.name}:{','.join(missing)}"
        )
    return entries


def build_contract(workflow_root: Path = WORKFLOW_ROOT) -> dict[str, Any]:
    workflows = sorted(workflow_root.glob("*.json"))
    if not workflows:
        raise ValueError("WORKFLOW_CORPUS_EMPTY")
    grouped: dict[tuple[str, str, str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for path in workflows:
        for entry in _binding_entries(path):
            key = (
                entry["placeholder"],
                entry["credential_type"],
                entry["node_type"],
                entry.get("credential_name"),
            )
            grouped[key].append(
                {"workflow": entry["workflow"], "node": entry["node"]}
            )

    bindings = []
    for (placeholder, credential_type, node_type, credential_name), nodes in sorted(
        grouped.items()
    ):
        binding = {
            "placeholder": placeholder,
            "credential_type": credential_type,
            "node_type": node_type,
            "nodes": sorted(
                nodes,
                key=lambda item: (
                    item["workflow"]["code"],
                    item["workflow"]["file"],
                    item["workflow"]["id"],
                    item["node"]["id"],
                ),
            ),
        }
        if credential_name is not None:
            binding["credential_name"] = credential_name
        bindings.append(binding)
    return {
        "schema_version": 1,
        "workflow_code_metadata_key": "financeWorkflowCode",
        "source": {
            "path": "integrations/n8n/workflows",
            "file_count": len(workflows),
            "sha256": _corpus_sha256(workflows),
        },
        "bindings": bindings,
    }


def render(workflow_root: Path = WORKFLOW_ROOT) -> str:
    return (
        json.dumps(build_contract(workflow_root), indent=2, ensure_ascii=False) + "\n"
    )


def validate_current(
    document: dict[str, Any], workflow_root: Path = WORKFLOW_ROOT
) -> None:
    expected = build_contract(workflow_root)
    if document != expected:
        raise ValueError("CREDENTIAL_BINDING_CONTRACT_DRIFT")


def _validate_fixture_manifest(path: Path, workflow_files: set[str]) -> None:
    fixture = json.loads(_canonical_bytes(path))
    if (
        not isinstance(fixture, dict)
        or fixture.get("contract_status") != "DISPOSABLE_ONLY"
        or fixture.get("production_import_forbidden") is not True
        or fixture.get("required_acknowledgement") != "DISPOSABLE_ONLY"
    ):
        raise ValueError("DISPOSABLE_FIXTURE_CONTRACT_INVALID")
    source_hashes = fixture.get("source_workflow_sha256")
    if (
        not isinstance(source_hashes, dict)
        or not source_hashes
        or not set(source_hashes) <= workflow_files
    ):
        raise ValueError("DISPOSABLE_FIXTURE_SOURCE_CONTRACT_INVALID")
    for filename, expected in source_hashes.items():
        if (
            hashlib.sha256(_canonical_bytes(WORKFLOW_ROOT / filename)).hexdigest()
            != expected
        ):
            raise ValueError(f"DISPOSABLE_FIXTURE_SOURCE_STALE:{filename}")
    workflows = fixture.get("workflows")
    if not isinstance(workflows, list) or len(workflows) != 18:
        raise ValueError("DISPOSABLE_FIXTURE_WORKFLOW_CONTRACT_INVALID")
    filenames: set[str] = set()
    for workflow in workflows:
        filename = workflow.get("file") if isinstance(workflow, dict) else None
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".json")
            or filename in filenames
        ):
            raise ValueError("DISPOSABLE_FIXTURE_WORKFLOW_CONTRACT_INVALID")
        filenames.add(filename)
        source = path.parent / "generated" / filename
        if hashlib.sha256(_canonical_bytes(source)).hexdigest() != workflow.get(
            "sha256"
        ):
            raise ValueError(f"DISPOSABLE_FIXTURE_WORKFLOW_STALE:{filename}")


def _render_application_manifest(contract_text: str) -> str:
    manifest = json.loads(_canonical_bytes(APPLICATION_MANIFEST_PATH))
    schema = json.loads(_canonical_bytes(APPLICATION_SCHEMA_PATH))
    validator = Draft202012Validator(schema)
    if not validator.is_valid(manifest):
        raise ValueError("APPLICATION_MANIFEST_SCHEMA_INVALID")
    if manifest["contract_status"] != "SPEC_ONLY":
        raise ValueError("SPEC_ONLY_APPLICATION_MANIFEST_REQUIRED")
    contract = json.loads(contract_text)
    credentials = manifest["credentials"]
    placeholders = credentials["placeholders"]
    declared_types = {row["binding"]: row["type"] for row in placeholders}
    contract_types = {
        row["placeholder"]: row["credential_type"] for row in contract["bindings"]
    }
    if len(declared_types) != len(placeholders) or len(contract_types) != len(
        contract["bindings"]
    ):
        raise ValueError("CREDENTIAL_BINDINGS_DUPLICATED")
    if declared_types != contract_types:
        raise ValueError("CREDENTIAL_BINDING_IDENTITIES_OUT_OF_SYNC")
    if credentials["forbidden_fields"] != [
        "id",
        "value",
        "token",
        "secret",
        "password",
        "client_secret",
    ]:
        raise ValueError("CREDENTIAL_FORBIDDEN_FIELDS_INVALID")
    workflow_files = {path.name for path in WORKFLOW_ROOT.glob("*.json")}
    registry = json.loads(
        _canonical_bytes(ROOT / manifest["workflow_manifest"]["path"])
    )
    registry_files = [row["file"] for row in registry["workflows"]]
    if (
        len(registry_files) != len(workflow_files)
        or set(registry_files) != workflow_files
    ):
        raise ValueError("WORKFLOW_REGISTRY_CORPUS_MISMATCH")
    _validate_fixture_manifest(
        ROOT / manifest["fixture_manifest"]["path"], workflow_files
    )
    image_lock = manifest["image_lock"]
    image_lock["sha256"] = hashlib.sha256(
        _canonical_bytes(ROOT / image_lock["path"])
    ).hexdigest()
    source_bindings = manifest["source_contract_bindings"]
    if source_bindings["path"] != "integrations/n8n/source-contract-bindings.json":
        raise ValueError("SOURCE_BINDING_MANIFEST_PATH_INVALID")
    source_bindings["sha256"] = hashlib.sha256(
        _canonical_bytes(SOURCE_BINDINGS_MANIFEST_PATH)
    ).hexdigest()
    for key in ("workflow_manifest", "fixture_manifest"):
        declaration = manifest[key]
        declaration["sha256"] = hashlib.sha256(
            _canonical_bytes(ROOT / declaration["path"])
        ).hexdigest()
    manifest["inactive_corpus"].update(contract["source"])
    credentials["binding_contract"]["sha256"] = hashlib.sha256(
        contract_text.encode("utf-8")
    ).hexdigest()
    if not validator.is_valid(manifest):
        raise ValueError("APPLICATION_MANIFEST_SCHEMA_INVALID")
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def render_outputs() -> dict[Path, str]:
    contract_text = render()
    return {
        OUTPUT_PATH: contract_text,
        APPLICATION_MANIFEST_PATH: _render_application_manifest(contract_text),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate finance credential and application source metadata."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    outputs = render_outputs()
    if args.write:
        for path, expected in outputs.items():
            path.write_text(expected, encoding="utf-8", newline="\n")
        return 0
    stale = [
        path.relative_to(ROOT).as_posix()
        for path, expected in outputs.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != expected
    ]
    if stale:
        print("credential and application metadata are stale: " + ", ".join(stale))
        print("run: python integrations/n8n/generate_credential_bindings.py --write")
        return 1
    print("credential and application metadata are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
