from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = ROOT / "integrations" / "n8n" / "workflows"
OUTPUT_PATH = ROOT / "integrations" / "n8n" / "credential-bindings.json"


def _canonical_bytes(path: Path) -> bytes:
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
    if not all(isinstance(value, str) and value for value in (workflow_id, workflow_code)):
        raise ValueError(f"WORKFLOW_IDENTITY_INVALID:{path.name}")

    declared = workflow.get("meta", {}).get("credentialBindings", [])
    if not isinstance(declared, list):
        raise TypeError(f"CREDENTIAL_BINDINGS_INVALID:{path.name}")
    declared_placeholders = []
    for binding in declared:
        placeholder = binding.get("placeholder") if isinstance(binding, dict) else None
        if not isinstance(placeholder, str) or not placeholder.startswith("BIND_"):
            raise ValueError(f"CREDENTIAL_PLACEHOLDER_INVALID:{path.name}")
        declared_placeholders.append(placeholder)
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
                raise ValueError(f"CREDENTIAL_PLACEHOLDER_INVALID:{path.name}:{node.get('id')}")
            if placeholder not in declared_placeholders:
                raise ValueError(f"CREDENTIAL_NOT_DECLARED:{path.name}:{placeholder}")
            node_id = node.get("id")
            node_name = node.get("name")
            node_type = node.get("type")
            if not all(isinstance(item, str) and item for item in (node_id, node_name, node_type, credential_type)):
                raise ValueError(f"NODE_IDENTITY_INVALID:{path.name}:{node_id}")
            used_placeholders.add(placeholder)
            entries.append(
                {
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
            )
    if used_placeholders != set(declared_placeholders):
        missing = sorted(set(declared_placeholders) - used_placeholders)
        raise ValueError(f"CREDENTIAL_DECLARATION_UNUSED:{path.name}:{','.join(missing)}")
    return entries


def build_contract(workflow_root: Path = WORKFLOW_ROOT) -> dict[str, Any]:
    workflows = sorted(workflow_root.glob("*.json"))
    if not workflows:
        raise ValueError("WORKFLOW_CORPUS_EMPTY")
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in workflows:
        for entry in _binding_entries(path):
            key = (entry["placeholder"], entry["credential_type"], entry["node_type"])
            grouped[key].append({"workflow": entry["workflow"], "node": entry["node"]})

    bindings = []
    for (placeholder, credential_type, node_type), nodes in sorted(grouped.items()):
        bindings.append(
            {
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
        )
    return {
        "schema_version": 1,
        "source": {
            "path": "integrations/n8n/workflows",
            "file_count": len(workflows),
            "sha256": _corpus_sha256(workflows),
        },
        "bindings": bindings,
    }


def render(workflow_root: Path = WORKFLOW_ROOT) -> str:
    return json.dumps(build_contract(workflow_root), indent=2, ensure_ascii=False) + "\n"


def validate_current(document: dict[str, Any], workflow_root: Path = WORKFLOW_ROOT) -> None:
    expected = build_contract(workflow_root)
    if document != expected:
        raise ValueError("CREDENTIAL_BINDING_CONTRACT_DRIFT")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate finance n8n credential-binding identities.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    expected = render()
    if args.write:
        OUTPUT_PATH.write_text(expected, encoding="utf-8", newline="\n")
        return 0
    if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != expected:
        print(f"credential binding artifact is stale: {OUTPUT_PATH}")
        print("run: python integrations/n8n/generate_credential_bindings.py --write")
        return 1
    print("credential binding artifact is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
