#!/usr/bin/env python3
"""Create one runtime-only WF23 copy with existing Microsoft OAuth bindings."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys

WORKFLOW_ID = "10000000-0000-4000-8000-000000000023"
WORKFLOW_CODE = "MICROSOFT_OAUTH_REFRESH_PROOF"
SOURCE_COMMIT = "b3bce6e197c6603d3e8708156bed987f26ac8513"
SOURCE_SHA256 = "879d637a5ad71e5a35ec8a90001d33c00067e05115a3bcdd28a80a9191c7224e"
PROVIDERS = {
    "n8n-nodes-base.microsoftOutlook": (
        "microsoftOutlookOAuth2Api",
        "BIND_OUTLOOK",
        "Finance Outlook",
        "FINANCE_OUTLOOK_CREDENTIAL_ID",
    ),
    "n8n-nodes-base.microsoftOneDrive": (
        "microsoftOneDriveOAuth2Api",
        "BIND_ONEDRIVE",
        "Finance OneDrive",
        "FINANCE_ONEDRIVE_CREDENTIAL_ID",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("destination", type=pathlib.Path)
    parser.add_argument("--finance-commit", required=True)
    return parser.parse_args()


def require_identifier(environment_name: str) -> str:
    value = os.environ.pop(environment_name, "")
    if not re.fullmatch(r"[0-9A-Za-z_-]{8,64}", value):
        raise SystemExit(f"EXACT_{environment_name}_REQUIRED")
    return value


def main() -> int:
    args = parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.finance_commit):
        raise SystemExit("EXACT_FINANCE_COMMIT_REQUIRED")
    credential_ids = {
        node_type: require_identifier(contract[3])
        for node_type, contract in PROVIDERS.items()
    }
    if not args.source.is_file() or args.source.is_symlink():
        raise SystemExit("REGULAR_SETUP_WORKFLOW_SOURCE_REQUIRED")
    raw = args.source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise SystemExit("SETUP_WORKFLOW_SOURCE_SHA256_MISMATCH")
    if args.destination.exists():
        raise SystemExit("SETUP_WORKFLOW_DESTINATION_MUST_NOT_EXIST")

    workflow = json.loads(raw)
    meta = workflow.get("meta", {})
    if (
        workflow.get("id") != WORKFLOW_ID
        or workflow.get("active") is not False
        or workflow.get("activeVersionId") is not None
        or meta.get("financeWorkflowCode") != WORKFLOW_CODE
        or meta.get("migrationStatus") != "READY_FOR_REVIEWED_MANUAL_IMPORT"
        or meta.get("manualOnly") is not True
        or meta.get("setupOnly") is not True
        or meta.get("activationForbidden") is not True
        or meta.get("scheduleForbidden") is not True
        or meta.get("providerMutationScope") != "NONE"
    ):
        raise SystemExit("SETUP_WORKFLOW_CONTRACT_MISMATCH")
    settings = workflow.get("settings", {})
    if (
        "errorWorkflow" in settings
        or settings.get("saveDataErrorExecution") != "none"
        or settings.get("saveDataSuccessExecution") != "none"
    ):
        raise SystemExit("SETUP_WORKFLOW_EXECUTION_PERSISTENCE_FORBIDDEN")

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise SystemExit("SETUP_WORKFLOW_NODES_INVALID")
    if any(node.get("type") == "n8n-nodes-base.scheduleTrigger" for node in nodes):
        raise SystemExit("SETUP_WORKFLOW_SCHEDULE_FORBIDDEN")
    forbidden_types = {"n8n-nodes-base.httpRequest", "n8n-nodes-base.executeCommand", "n8n-nodes-base.ssh"}
    if any(node.get("type") in forbidden_types or str(node.get("type", "")).startswith("n8n-nodes-finance.") for node in nodes):
        raise SystemExit("SETUP_WORKFLOW_FORBIDDEN_NODE_TYPE")

    for node_type, (credential_type, placeholder, name, _) in PROVIDERS.items():
        provider_nodes = [node for node in nodes if node.get("type") == node_type]
        if len(provider_nodes) != 1:
            raise SystemExit("SETUP_WORKFLOW_PROVIDER_NODE_COUNT_MISMATCH")
        node = provider_nodes[0]
        if node.get("credentials", {}).get(credential_type) != {"id": placeholder, "name": name}:
            raise SystemExit("UNEXPECTED_PROVIDER_BINDING")
        node["credentials"][credential_type] = {"id": credential_ids[node_type], "name": name}

    outlook = next(node for node in nodes if node.get("type") == "n8n-nodes-base.microsoftOutlook")
    if (
        outlook.get("parameters", {}).get("operation") != "getAll"
        or outlook["parameters"].get("returnAll") is not False
        or outlook["parameters"].get("output") != "fields"
        or outlook["parameters"].get("fields") != ["id"]
        or outlook["parameters"].get("options") != {"downloadAttachments": False}
    ):
        raise SystemExit("OUTLOOK_BOUNDED_ID_ONLY_CONTRACT_MISMATCH")
    drive = next(node for node in nodes if node.get("type") == "n8n-nodes-base.microsoftOneDrive")
    if drive.get("parameters", {}).get("operation") != "getChildren":
        raise SystemExit("ONEDRIVE_ROOT_READ_CONTRACT_MISMATCH")

    bindings = meta.get("credentialBindings")
    expected_placeholders = {"BIND_OUTLOOK", "BIND_ONEDRIVE"}
    if not isinstance(bindings, list) or {row.get("placeholder") for row in bindings} != expected_placeholders:
        raise SystemExit("SETUP_WORKFLOW_META_BINDING_MISMATCH")
    for binding in bindings:
        binding.update(configured=True, action_required=False)
        binding.pop("credential_id", None)

    args.destination.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    args.destination.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.destination.chmod(0o600)
    receipt = {
        "schema_version": 1,
        "status": "VERIFIED",
        "scope": "WF23_RUNTIME_BINDING",
        "workflow_id": WORKFLOW_ID,
        "finance_commit": args.finance_commit,
        "source_commit": SOURCE_COMMIT,
        "source_sha256": SOURCE_SHA256,
        "provider_node_count": 2,
        "credential_ids_recorded": False,
        "secret_values_recorded": False,
        "portable_source_modified": False,
    }
    receipt_path = args.destination.parent / "binding-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)
    print("Created one inactive WF23 runtime copy with two provider bindings; identifiers were not printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
