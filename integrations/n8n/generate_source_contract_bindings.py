from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "integrations" / "n8n"
BUNDLE_PATH = N8N / "generated" / "application-contract-bundle.json"
SCHEMA_PATH = N8N / "source-contract-bindings.schema.json"
OUTPUT_PATH = N8N / "source-contract-bindings.json"
SOURCE_FIELDS = (
    "source_code",
    "config_version",
    "folder_id",
    "senders_json",
    "subjects_json",
    "onedrive_parent_id",
    "manifest_onedrive_parent_id",
    "overlap_seconds",
    "cycle_day",
    "deadline_days",
    "actual_file_id",
    "account_id",
    "card_code",
    "cashback_close_required",
    "enabled",
    "content_sha256",
    "updated_at",
)
SLOT_KEY = "financeSourceBindingSlots"
SLOT_PARAMETER = "parameters.jsonOutput"


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(canonical(value), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def as_json_array(value: Any) -> str:
    if not isinstance(value, list):
        return "[]"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_binding(row: dict[str, Any] | None, source_code: str) -> dict[str, Any]:
    if row is None:
        return {
            "source_code": source_code,
            **{field: None for field in SOURCE_FIELDS if field != "source_code"},
        }
    contract = row.get("contract")
    if not isinstance(contract, dict):
        raise ValueError("SOURCE_CONTRACT_ROW_INVALID")
    senders = contract.get("senders", contract.get("email_senders"))
    subjects = contract.get("subjects", contract.get("email_subjects"))
    status = contract.get("status", contract.get("adapter_status"))
    binding = {
        "source_code": source_code,
        "config_version": row.get("config_version"),
        "folder_id": contract.get("folder_id", contract.get("mail_folder")),
        "senders_json": as_json_array(senders),
        "subjects_json": as_json_array(subjects),
        "onedrive_parent_id": contract.get("onedrive_parent_id"),
        "manifest_onedrive_parent_id": contract.get("manifest_onedrive_parent_id"),
        "overlap_seconds": contract.get("overlap_seconds"),
        "cycle_day": contract.get("cycle_day"),
        "deadline_days": contract.get("deadline_days"),
        "actual_file_id": contract.get("actual_file_id"),
        "account_id": contract.get("account_id"),
        "card_code": contract.get("card_code"),
        "cashback_close_required": contract.get("cashback_close_required"),
        "enabled": status == "ACTIVE" or contract.get("enabled") is True,
        "content_sha256": row.get("content_sha256"),
        "updated_at": contract.get("updated_at"),
    }
    if binding["config_version"] is None or binding["content_sha256"] is None:
        raise ValueError(f"SOURCE_CONTRACT_IDENTITY_MISSING:{source_code}")
    return binding


def build_manifest() -> dict[str, Any]:
    bundle = read_json(BUNDLE_PATH)
    workflow_specs: list[dict[str, Any]] = []
    slots: dict[str, dict[str, Any]] = {}
    for path in sorted(N8N.joinpath("workflows").glob("*.json")):
        workflow = read_json(path)
        metadata = workflow.get("meta")
        declared = metadata.get(SLOT_KEY) if isinstance(metadata, dict) else None
        if declared is None:
            continue
        if not isinstance(declared, dict) or not declared:
            raise ValueError(f"SOURCE_BINDING_SLOT_METADATA_INVALID:{path.name}")
        for source_code, slot in declared.items():
            if not isinstance(slot, dict) or set(slot) != {
                "node", "parameter", "allowedFields", "requiredFields",
            }:
                raise ValueError(f"SOURCE_BINDING_SLOT_INVALID:{source_code}")
            if not isinstance(slot["node"], str) or not slot["node"] or slot["parameter"] != SLOT_PARAMETER:
                raise ValueError(f"SOURCE_BINDING_SLOT_MISMATCH:{source_code}")
            required = slot["requiredFields"]
            allowed = slot["allowedFields"]
            if (
                not isinstance(allowed, list) or not allowed
                or set(allowed) - set(SOURCE_FIELDS)
                or not isinstance(required, list) or not required
                or set(required) - set(allowed)
                or "source_code" not in required
            ):
                raise ValueError(f"SOURCE_BINDING_FIELDS_INVALID:{source_code}")
            if source_code in slots:
                raise ValueError(f"SOURCE_BINDING_SOURCE_DUPLICATED:{source_code}")
            slots[source_code] = slot
        workflow_raw = path.read_bytes().replace(b"\r\n", b"\n")
        workflow_specs.append({
            "path": f"integrations/n8n/workflows/{path.name}",
            "workflow_id": workflow.get("id"),
            "workflow_code": metadata.get("financeWorkflowCode"),
            "source_sha256": sha256_bytes(workflow_raw),
            "slot_metadata_key": SLOT_KEY,
        })
    if not workflow_specs:
        raise ValueError("SOURCE_BINDING_SLOT_METADATA_REQUIRED")
    rows = bundle.get("source_contracts")
    if not isinstance(rows, list):
        raise ValueError("SOURCE_CONTRACTS_REQUIRED")
    by_code = {row.get("source_code"): row for row in rows if isinstance(row, dict)}
    bindings = {source_code: build_binding(by_code.get(source_code), source_code) for source_code in sorted(slots)}
    prerequisites = []
    for source_code, binding in bindings.items():
        required_fields = slots[source_code]["requiredFields"]
        missing = [
            field for field in required_fields
            if binding.get(field) in (None, "", "[]")
        ]
        if missing:
            prerequisites.append({
                "source_code": source_code,
                "fields": sorted(set(missing)),
                "reason": "Owner-supplied source configuration is required before import; no provider identifiers are fabricated.",
            })
    manifest = {
        "schema_version": 1,
        "contract_status": "READY" if not prerequisites else "ACTIVATION_REQUIRED",
        "source": {
            "path": "integrations/n8n/generated/application-contract-bundle.json",
            "sha256": sha256_bytes(BUNDLE_PATH.read_bytes()),
            "table": "finance_source_contracts",
            "allowed_fields": list(SOURCE_FIELDS),
        },
        "workflows": workflow_specs,
        "bindings": bindings,
        "activation_prerequisites": prerequisites,
    }
    manifest["manifest_sha256"] = sha256_value(manifest)
    validate_manifest_shape(manifest)
    return manifest


def validate_manifest_shape(manifest: dict[str, Any]) -> None:
    if set(manifest) != {
        "schema_version", "contract_status", "manifest_sha256", "source",
        "workflows", "bindings", "activation_prerequisites",
    } or manifest["schema_version"] != 1:
        raise ValueError("SOURCE_BINDING_SCHEMA_INVALID")
    if manifest["contract_status"] not in {"ACTIVATION_REQUIRED", "READY"}:
        raise ValueError("SOURCE_BINDING_STATUS_INVALID")
    if not isinstance(manifest["source"], dict) or manifest["source"].get("table") != "finance_source_contracts":
        raise ValueError("SOURCE_BINDING_SOURCE_INVALID")
    if manifest["source"].get("path") != "integrations/n8n/generated/application-contract-bundle.json":
        raise ValueError("SOURCE_BINDING_SOURCE_PATH_INVALID")
    if set(manifest["source"].get("allowed_fields", [])) != set(SOURCE_FIELDS):
        raise ValueError("SOURCE_BINDING_FIELDS_INVALID")
    if not isinstance(manifest["workflows"], list) or not manifest["workflows"]:
        raise ValueError("SOURCE_BINDING_WORKFLOWS_INVALID")
    declared = set()
    for workflow in manifest["workflows"]:
        if not isinstance(workflow, dict) or set(workflow) != {
            "path", "workflow_id", "workflow_code", "source_sha256", "slot_metadata_key",
        }:
            raise ValueError("SOURCE_BINDING_WORKFLOW_INVALID")
        if workflow["slot_metadata_key"] != SLOT_KEY:
            raise ValueError("SOURCE_BINDING_WORKFLOW_SLOT_KEY_INVALID")
    if not isinstance(manifest["bindings"], dict) or not manifest["bindings"]:
        raise ValueError("SOURCE_BINDING_BINDINGS_INVALID")
    for source_code, binding in manifest["bindings"].items():
        if not isinstance(binding, dict) or set(binding) != set(SOURCE_FIELDS) or binding["source_code"] != source_code:
            raise ValueError(f"SOURCE_BINDING_ROW_INVALID:{source_code}")
        declared.add(source_code)
    if not isinstance(manifest["activation_prerequisites"], list):
        raise ValueError("SOURCE_BINDING_PREREQUISITES_INVALID")
    for prerequisite in manifest["activation_prerequisites"]:
        if not isinstance(prerequisite, dict) or set(prerequisite) != {"source_code", "fields", "reason"}:
            raise ValueError("SOURCE_BINDING_PREREQUISITE_INVALID")
        if prerequisite["source_code"] not in declared or not prerequisite["fields"]:
            raise ValueError("SOURCE_BINDING_PREREQUISITE_SOURCE_INVALID")
def render() -> str:
    return json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n"


def validate_current() -> None:
    expected = render()
    if not OUTPUT_PATH.is_file() or OUTPUT_PATH.read_text(encoding="utf-8") != expected:
        raise ValueError("SOURCE_BINDING_MANIFEST_DRIFT")
    document = read_json(OUTPUT_PATH)
    declared = document.pop("manifest_sha256", None)
    if declared != sha256_value(document):
        raise ValueError("SOURCE_BINDING_MANIFEST_HASH_MISMATCH")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    if args.check:
        validate_current()
        return 0
    args.output.write_text(render(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
