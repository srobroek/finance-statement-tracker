from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from refactor_workflow_ui import format_code_nodes, layout


ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "integrations" / "n8n"
TABLES_PATH = N8N / "data-tables.json"
SEED_PATH = N8N / "generated" / "ai-policy-contracts.seed.json"
CONFIG_SEED_PATH = N8N / "generated" / "config-versions.seed.json"
MANIFEST_PATH = N8N / "generated" / "platform-bootstrap-manifest.json"
WORKFLOW_PATH = N8N / "workflows" / "19-platform-data-table-bootstrap.json"
BUNDLE_PATH = N8N / "generated" / "application-contract-bundle.json"
BUNDLE_SCHEMA_PATH = N8N / "generated" / "application-contract-bundle.schema.json"
MATRIX_PATH = N8N / "data-table-migration-matrix.json"

APPLICATION_CONFIG_PATHS = (
    "config/statement-sources.json",
    "config/transaction-email-sources.json",
    "config/ai-policies.json",
)
SOURCE_CONFIG_SPECS = {
    "config/statement-sources.json": {"collection": "sources", "identity_key": "card_code"},
    "config/transaction-email-sources.json": {"collection": "sources", "identity_key": "code"},
    "config/ai-policies.json": {"collection": "policies", "identity_key": "policy_id"},
}
SOURCE_CONTRACT_PATHS = (
    "config/statement-sources.json",
    "config/transaction-email-sources.json",
)
RESOLVER_WORKFLOWS = (
    {
        "workflow_code": "W02",
        "workflow_path": "integrations/n8n/workflows/02-rakbank-live-cashback.json",
        "source_path": "config/transaction-email-sources.json",
        "selection_key": "code",
        "row_key": "code",
    },
    {
        "workflow_code": "W04",
        "workflow_path": "integrations/n8n/workflows/04-ei-monthly-statement.json",
        "source_path": "config/statement-sources.json",
        "selection_key": "card_code",
        "row_key": "card_code",
    },
    {
        "workflow_code": "W05",
        "workflow_path": "integrations/n8n/workflows/05-wio-monthly-statement.json",
        "source_path": "config/statement-sources.json",
        "selection_key": "card_code",
        "row_key": "card_code",
    },
    {
        "workflow_code": "W09",
        "workflow_path": "integrations/n8n/workflows/09-ai-proposal.json",
        "source_path": "config/ai-policies.json",
        "selection_key": "policy_id",
        "row_key": "policy_id",
    },
)

WORKFLOW_ID = "10000000-0000-4000-8000-000000000019"
ERROR_WORKFLOW_ID = "10000000-0000-4000-8000-000000000016"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load_application_configs() -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Read each resolver input once and retain its Git-canonical fingerprint."""
    documents: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, str]] = []
    for source_path in APPLICATION_CONFIG_PATHS:
        path = ROOT / source_path
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError(f"application config must be an object: {source_path}")
        documents[source_path] = document
        sources.append({"path": source_path, "sha256": hashlib.sha256(raw).hexdigest()})
    return documents, sources


def _source_row(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = document.get("sources") if key == "sources" else document.get(key)
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"application config has no valid {key} rows")
    return rows


def _config_rows(documents: dict[str, dict[str, Any]], source_path: str) -> list[dict[str, Any]]:
    spec = SOURCE_CONFIG_SPECS[source_path]
    return _source_row(documents[source_path], spec["collection"])


def _identity_key(source_path: str) -> str:
    return SOURCE_CONFIG_SPECS[source_path]["identity_key"]


def _validate_unique_identities(
    rows: list[dict[str, Any]], source_path: str,
) -> None:
    identity_key = _identity_key(source_path)
    identities = [row.get(identity_key) for row in rows]
    if any(not isinstance(identity, str) or not identity for identity in identities):
        raise ValueError(f"application config has invalid {identity_key} identity: {source_path}")
    if len(set(identities)) != len(identities):
        raise ValueError(f"application config has duplicate {identity_key} identities: {source_path}")


def _config_version(document: dict[str, Any], source_path: str) -> str:
    version = document.get("schema_version")
    if not isinstance(version, (int, str)) or not str(version):
        raise ValueError(f"application config has no stable schema_version: {source_path}")
    return str(version)


def build_config_versions(
    documents: dict[str, dict[str, Any]], sources: list[dict[str, str]],
) -> list[dict[str, Any]]:
    by_path = {source["path"]: source["sha256"] for source in sources}
    return [
        {
            "config_name": Path(path).stem,
            "version": _config_version(documents[path], path),
            "source_path": path,
            "content_sha256": by_path[path],
            "state": "ACTIVE",
        }
        for path in APPLICATION_CONFIG_PATHS
    ]


def build_source_contracts(
    documents: dict[str, dict[str, Any]], sources: list[dict[str, str]],
) -> list[dict[str, Any]]:
    by_path = {source["path"]: source["sha256"] for source in sources}
    result: list[dict[str, Any]] = []
    for path in SOURCE_CONTRACT_PATHS:
        rows = _config_rows(documents, path)
        _validate_unique_identities(rows, path)
        for row in rows:
            source_code = row[_identity_key(path)]
            result.append({
                "source_code": source_code,
                "config_version": _config_version(documents[path], path),
                "source_path": path,
                "content_sha256": by_path[path],
                "contract": row,
            })
    if len({row["source_code"] for row in result}) != len(result):
        raise ValueError("application configs have duplicate semantic source_code identities")
    return result


def build_ai_policy_contracts(
    document: dict[str, Any], source: dict[str, str],
) -> list[dict[str, Any]]:
    policies = document.get("policies")
    if not isinstance(policies, list) or not policies or not all(isinstance(row, dict) for row in policies):
        raise ValueError("ai-policies.json has no valid policies")
    _validate_unique_identities(policies, "config/ai-policies.json")
    result = []
    for row in policies:
        policy_id = row.get("policy_id")
        version = row.get("version")
        if not isinstance(policy_id, str) or not policy_id or not isinstance(version, int):
            raise ValueError("AI policy is missing its stable identity")
        result.append({
            "policy_id": policy_id,
            "policy_version": version,
            "source_path": source["path"],
            "content_sha256": source["sha256"],
            "contract": row,
        })
    return result


def build_resolver_maps(
    documents: dict[str, dict[str, Any]], sources: list[dict[str, str]],
) -> list[dict[str, Any]]:
    source_by_path = {source["path"]: source for source in sources}
    rows_by_path = {
        source_path: _config_rows(documents, source_path)
        for source_path in APPLICATION_CONFIG_PATHS
    }
    for source_path, rows in rows_by_path.items():
        _validate_unique_identities(rows, source_path)
    result = []
    for resolver in RESOLVER_WORKFLOWS:
        source = source_by_path[resolver["source_path"]]
        entries = [
            {"key": row[resolver["row_key"]], "value": row}
            for row in rows_by_path[resolver["source_path"]]
        ]
        keys = [entry["key"] for entry in entries]
        if any(not isinstance(key, str) or not key for key in keys):
            raise ValueError(f"{resolver['workflow_code']} has an invalid resolver key")
        if len(set(keys)) != len(keys):
            raise ValueError(f"{resolver['workflow_code']} has duplicate resolver keys")
        result.append({
            "workflow_code": resolver["workflow_code"],
            "workflow_path": resolver["workflow_path"],
            "selection_key": resolver["selection_key"],
            "sources": [source],
            "entries": entries,
        })
    return result


def build_application_contract_schema(
    documents: dict[str, dict[str, Any]], sources: list[dict[str, str]],
) -> dict[str, Any]:
    source_paths = list(APPLICATION_CONFIG_PATHS)
    source_by_path = {source["path"]: source for source in sources}
    resolver_maps = build_resolver_maps(documents, sources)
    source_contracts = build_source_contracts(documents, sources)
    config_versions = build_config_versions(documents, sources)
    ai_policy_contracts = build_ai_policy_contracts(
        documents["config/ai-policies.json"], source_by_path["config/ai-policies.json"]
    )

    def exact_object_schema(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "required": sorted(value),
            "properties": {key: {"const": item} for key, item in value.items()},
            "additionalProperties": False,
        }

    def exact_tuple_schema(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "type": "array",
            "prefixItems": [exact_object_schema(row) for row in rows],
            "items": False,
            "minItems": len(rows),
            "maxItems": len(rows),
        }

    source_document_schemas = [
        exact_object_schema(source) for source in sources
    ]
    resolver_map_schemas = [exact_object_schema(resolver) for resolver in resolver_maps]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "finance-application-contract-bundle-v1",
        "title": "Finance application contract resolver bundle",
        "type": "object",
        "required": [
            "schema_version", "contract_status", "schema_path", "schema_sha256",
            "content_digest", "bundle_content_sha256", "source_documents",
            "resolver_order", "resolver_maps", "source_contracts",
            "config_versions", "ai_policy_contracts",
        ],
        "properties": {
            "schema_version": {"const": 1},
            "contract_status": {"const": "SPEC_ONLY"},
            "schema_path": {"const": "integrations/n8n/generated/application-contract-bundle.schema.json"},
            "schema_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "content_digest": {
                "type": "object",
                "required": ["algorithm", "canonicalization", "excluded_fields"],
                "properties": {
                    "algorithm": {"const": "SHA-256"},
                    "canonicalization": {"const": "sorted-object-keys;array-order-preserved;compact-utf8-json"},
                    "excluded_fields": {"const": ["bundle_content_sha256"]},
                },
                "additionalProperties": False,
            },
            "bundle_content_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "source_documents": {
                "type": "array",
                "prefixItems": source_document_schemas,
                "items": False,
                "minItems": len(source_paths),
                "maxItems": len(source_paths),
            },
            "resolver_order": {
                "const": [resolver["workflow_code"] for resolver in RESOLVER_WORKFLOWS]
            },
            "resolver_maps": {
                "type": "array",
                "prefixItems": resolver_map_schemas,
                "items": False,
                "minItems": len(resolver_maps),
                "maxItems": len(resolver_maps),
            },
            "source_contracts": exact_tuple_schema(source_contracts),
            "config_versions": exact_tuple_schema(config_versions),
            "ai_policy_contracts": exact_tuple_schema(ai_policy_contracts),
        },
        "additionalProperties": False,
    }


def build_application_contract_bundle(
    documents: dict[str, dict[str, Any]], sources: list[dict[str, str]], schema: dict[str, Any],
) -> dict[str, Any]:
    schema_text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
    schema_sha256 = hashlib.sha256(schema_text.encode("utf-8")).hexdigest()
    bundle: dict[str, Any] = {
        "schema_version": 1,
        "contract_status": "SPEC_ONLY",
        "schema_path": "integrations/n8n/generated/application-contract-bundle.schema.json",
        "schema_sha256": schema_sha256,
        "content_digest": {
            "algorithm": "SHA-256",
            "canonicalization": "sorted-object-keys;array-order-preserved;compact-utf8-json",
            "excluded_fields": ["bundle_content_sha256"],
        },
        "bundle_content_sha256": "",
        "source_documents": sources,
        "resolver_order": [resolver["workflow_code"] for resolver in RESOLVER_WORKFLOWS],
        "resolver_maps": build_resolver_maps(documents, sources),
        "source_contracts": build_source_contracts(documents, sources),
        "config_versions": build_config_versions(documents, sources),
        "ai_policy_contracts": build_ai_policy_contracts(
            documents["config/ai-policies.json"],
            next(source for source in sources if source["path"] == "config/ai-policies.json"),
        ),
    }
    digest_payload = dict(bundle)
    digest_payload.pop("bundle_content_sha256")
    bundle["bundle_content_sha256"] = hashlib.sha256(
        canonical_json(digest_payload).encode("utf-8")
    ).hexdigest()
    validate_application_contract_bundle(bundle, schema)
    return bundle


def validate_application_contract_bundle(
    bundle: dict[str, Any], schema: dict[str, Any],
) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(bundle),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors[:3]
        )
        raise ValueError(f"application contract bundle schema validation failed: {details}")


def readable_js_literal(
    value: object, level: int = 0, split_long_strings: bool = False,
) -> str:
    """Render generated configuration as reviewable JavaScript data.

    Fields stored canonically as JSON strings are expressed as JSON.stringify
    calls so their category/tag domains remain line-oriented in the Code editor.
    """
    indent = "  " * level
    child_indent = "  " * (level + 1)
    if isinstance(value, dict):
        lines = []
        for key in sorted(value):
            item = value[key]
            if key.endswith("_json") and isinstance(item, str):
                rendered = (
                    f"JSON.stringify({readable_js_literal(json.loads(item), level + 1, split_long_strings)})"
                )
            else:
                rendered = readable_js_literal(item, level + 1, split_long_strings)
            lines.append(f"{child_indent}{json.dumps(key)}: {rendered}")
        return "{\n" + ",\n".join(lines) + f"\n{indent}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = [
            f"{child_indent}{readable_js_literal(item, level + 1, split_long_strings)}"
            for item in value
        ]
        return "[\n" + ",\n".join(lines) + f"\n{indent}]"
    if split_long_strings and isinstance(value, str) and len(value) > 50:
        chunks = [value[index : index + 50] for index in range(0, len(value), 50)]
        string_indent = "  " * (level + 1)
        rendered_chunks = [
            f"{string_indent}{json.dumps(chunk, ensure_ascii=False)}"
            + (" +" if index < len(chunks) - 1 else "")
            for index, chunk in enumerate(chunks)
        ]
        return "(\n" + "\n".join(rendered_chunks) + f"\n{'  ' * level})"
    return json.dumps(value, ensure_ascii=False)


def ordered_tables(contract: dict) -> list[dict]:
    tables = contract["tables"]
    # Provision the error sink first so later bootstrap failures have somewhere
    # durable to land once workflow 16 has itself been imported and bound.
    return sorted(
        tables,
        key=lambda row: (row["name"] != "finance_execution_failures", row["name"]),
    )


def target_table_rows(matrix: dict) -> list[dict]:
    """Convert the generated migration matrix to native Data Table creates."""
    return [
        {
            "name": target,
            "columns": {
                field: definition["type"]
                for field, definition in matrix["target_schemas"][target]["columns"].items()
            },
        }
        for target in matrix["targets"]
    ]


def target_schema_contract(matrix: dict) -> tuple[list[str], dict[str, dict[str, Any]], str]:
    """Return the ordered target set, canonical n8n column arrays, and digest."""
    targets = list(matrix["targets"])
    schemas = {
        target: {
            "columns": [
                {"name": field, "type": definition["type"]}
                for field, definition in matrix["target_schemas"][target]["columns"].items()
            ],
            "logical_key": list(matrix["target_schemas"][target]["logical_key"]),
        }
        for target in targets
    }
    digest_payload = {
        "targets": targets,
        "target_schemas": matrix["target_schemas"],
    }
    digest = hashlib.sha256(canonical_json(digest_payload).encode("utf-8")).hexdigest()
    return targets, schemas, digest


def create_parameters(table: dict) -> dict:
    return {
        "resource": "table",
        "operation": "create",
        "tableName": table["name"],
        "columns": {
            "column": [
                {"name": name, "type": column_type}
                for name, column_type in table["columns"].items()
            ]
        },
        "options": {"createIfNotExists": True},
    }


def build_manifest(
    tables: dict,
    seed: dict,
    config_seed: dict,
    bundle: dict,
    bundle_file_sha256: str,
    bundle_schema_sha256: str,
    matrix: dict | None = None,
) -> dict:
    manifest = {
        "schema_version": 1,
        "contract_status": "SPEC_ONLY",
        "n8n_version": "2.36.2",
        "sources": {
            "data_tables": "integrations/n8n/data-tables.json",
            "data_tables_sha256": sha256(TABLES_PATH),
            "ai_policy_seed": "integrations/n8n/generated/ai-policy-contracts.seed.json",
            "ai_policy_seed_sha256": sha256(SEED_PATH),
            "config_version_seed": "integrations/n8n/generated/config-versions.seed.json",
            "config_version_seed_sha256": sha256(CONFIG_SEED_PATH),
            "application_contract_bundle": "integrations/n8n/generated/application-contract-bundle.json",
            "application_contract_bundle_sha256": bundle_file_sha256,
            "application_contract_bundle_schema": "integrations/n8n/generated/application-contract-bundle.schema.json",
            "application_contract_bundle_schema_sha256": bundle_schema_sha256,
        },
        "native_data_table_create_contract": {
            "node_type": "n8n-nodes-base.dataTable",
            "node_type_version": 1.1,
            "resource": "table",
            "operation": "create",
            "reuse_option": "createIfNotExists",
            "official_node_source": "https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/nodes/DataTable/actions/table/create.operation.ts",
            "official_docs": "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.datatable/tables/",
        },
        "table_create_operations": [
            {
                "table_name": table["name"],
                "parameters": create_parameters(table),
            }
            for table in ordered_tables(tables)
        ],
        "seed": {
            "table_name": "finance_ai_policy_contracts",
            "idempotency_key": ["policy_id", "policy_version"],
            "rows": seed["rows"],
            "exact_readback_fields": [
                "policy_id",
                "policy_version",
                "agent_profile",
                "agent_provider",
                "policy_sha256",
                "config_sha256",
                "output_schema_sha256",
                "allowed_fields_json",
                "allowed_values_json",
                "state",
            ],
        },
        "config_seed": {
            "table_name": "finance_config_versions",
            "idempotency_key": ["config_name", "version"],
            "rows": config_seed["rows"],
            "exact_readback_fields": [
                "config_name", "version", "source_path", "content_sha256",
                "git_commit", "state",
            ],
        },
        "application_contract": {
            "path": "integrations/n8n/generated/application-contract-bundle.json",
            "schema_path": "integrations/n8n/generated/application-contract-bundle.schema.json",
            "schema_version": bundle["schema_version"],
            "bundle_content_sha256": bundle["bundle_content_sha256"],
            "resolver_order": bundle["resolver_order"],
            "source_paths": [source["path"] for source in bundle["source_documents"]],
        },
        "execution_evidence": {
            "exact_image_import_tested": False,
            "disposable_create_reuse_tested": False,
            "seed_readback_tested": False,
            "production_validated": False,
        },
        "activation_blockers": [
            "EXACT_IMAGE_IMPORT_REQUIRED",
            "DISPOSABLE_BOOTSTRAP_RUNTIME_PROOF_REQUIRED",
        ],
        "warning": (
            "Generated provisioning shape only. It is not evidence that the workflow "
            "imports or executes in the pinned n8n image."
        ),
    }
    if matrix is not None:
        targets, _, schema_digest = target_schema_contract(matrix)
        manifest["sources"]["data_table_migration_matrix"] = "integrations/n8n/data-table-migration-matrix.json"
        manifest["sources"]["data_table_migration_matrix_sha256"] = sha256(MATRIX_PATH)
        manifest["table_create_operations"] = [
            {
                "table_name": table["name"],
                "parameters": create_parameters(table),
            }
            for table in target_table_rows(matrix)
        ]
        manifest.pop("seed")
        manifest.pop("config_seed")
        manifest["target_schema_contract"] = {
            "path": "integrations/n8n/data-table-migration-matrix.json",
            "digest": schema_digest,
            "target_tables": targets,
            "readback_operation": "list",
            "readback_required": True,
            "legacy_table_creation_forbidden": True,
            "row_seed_writes_forbidden": True,
        }
        manifest["execution_evidence"] = {
            "exact_image_import_tested": False,
            "disposable_create_reuse_tested": False,
            "table_list_readback_tested": False,
            "production_validated": False,
        }
    return manifest


def data_table_node(node_id: str, name: str, position: list[int], parameters: dict) -> dict:
    return {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 1.1,
        "position": position,
        "parameters": parameters,
    }


def target_bootstrap_document(
    nodes: list[dict],
    connections: dict[str, dict],
    bundle: dict,
    manifest: dict,
    targets: list[str],
    schema_digest: str,
) -> dict:
    """Build the manual-only W19 document after its four-table readback."""
    nodes.append({
        "id": "10000000-0000-4000-8000-000000000019-generated-note-1",
        "name": "Stage 1 · Manual Platform Bootstrap Only to Emit Redacted Bootstrap Receipt",
        "type": "n8n-nodes-base.stickyNote",
        "typeVersion": 1,
        "position": [-1160, -180],
        "parameters": {
            "content": (
                "## Stage 1 · Four-table schema bootstrap\\n"
                "**Input:** Manual Platform Bootstrap Only  ·  **Output:** canonical native table-list "
                "readback and redacted receipt\\nPartial, extra, mismatched, or ID-drifting schemas fail closed."
            ),
            "height": 110,
            "width": 2240,
            "color": 7,
        },
    })
    return {
        "id": WORKFLOW_ID,
        "name": "Finance · Platform Data Table Bootstrap",
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {
            "executionOrder": "v1",
            "timezone": "Asia/Dubai",
            "saveDataErrorExecution": "none",
            "saveDataSuccessExecution": "none",
            "errorWorkflow": ERROR_WORKFLOW_ID,
        },
        "pinData": {},
        "meta": {
            "financeWorkflowCode": "PLATFORM_DATA_TABLE_BOOTSTRAP",
            "migrationStatus": "SPEC_ONLY",
            "manualOnly": True,
            "platformBootstrapOnly": True,
            "financeLedgerMutationForbidden": True,
            "actualMutationForbidden": True,
            "sourceContract": "integrations/n8n/data-table-migration-matrix.json",
            "applicationContractBundle": "integrations/n8n/generated/application-contract-bundle.json",
            "applicationContractBundleSchema": "integrations/n8n/generated/application-contract-bundle.schema.json",
            "applicationContractBundleContentSha256": bundle["bundle_content_sha256"],
            "provisioningManifest": "integrations/n8n/generated/platform-bootstrap-manifest.json",
            "activationBlockers": manifest["activation_blockers"],
            "importTested": False,
            "fixtureExecuted": False,
            "credentialBindings": [],
            "setupRequired": True,
            "targetSchemaContract": "integrations/n8n/data-table-migration-matrix.json",
            "targetSchemaDigest": schema_digest,
            "targetTables": targets,
            "sourceTablesPreserved": True,
            "legacyTableCreationForbidden": True,
            "partialSchemaPolicy": "PARTIAL_RECONCILED_FAIL_CLOSED",
            "secondRunPolicy": "NOOP_ON_EXACT_SCHEMA",
            "rowMigrationForbidden": True,
            "seedWritesForbidden": True,
        },
    }


def build_workflow(
    tables: dict,
    seed: dict,
    config_seed: dict,
    manifest: dict,
    bundle: dict,
    matrix: dict | None = None,
) -> dict:
    table_rows = target_table_rows(matrix) if matrix is not None else ordered_tables(tables)
    seed_rows = seed["rows"]
    nodes: list[dict] = [
        {
            "id": "19001",
            "name": "Manual Platform Bootstrap Only",
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [-1800, 0],
            "parameters": {},
        }
    ]
    connections: dict[str, dict] = {}
    previous = nodes[0]["name"]

    embedded_bundle = readable_js_literal(bundle, split_long_strings=True)
    load_bundle_name = "Load and Validate Application Contract Bundle"
    nodes.append(
        {
            "id": "19000",
            "name": load_bundle_name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [-1600, 0],
            "parameters": {
                "jsCode": (
                    f"const bundle={embedded_bundle}; "
                    "if(bundle.schema_version!==1||bundle.contract_status!=='SPEC_ONLY') "
                    "throw new Error('APPLICATION_CONTRACT_BUNDLE_SCHEMA_MISMATCH'); "
                    "if(bundle.schema_path!=='integrations/n8n/generated/application-contract-bundle.schema.json') "
                    "throw new Error('APPLICATION_CONTRACT_BUNDLE_SCHEMA_PATH_MISMATCH'); "
                    "if(JSON.stringify(bundle.resolver_order)!==JSON.stringify(['W02','W04','W05','W09'])) "
                    "throw new Error('APPLICATION_CONTRACT_BUNDLE_RESOLVER_ORDER_MISMATCH'); "
                    "if(bundle.content_digest?.algorithm!=='SHA-256'||"
                    "JSON.stringify(bundle.content_digest?.excluded_fields)!==JSON.stringify(['bundle_content_sha256'])) "
                    "throw new Error('APPLICATION_CONTRACT_BUNDLE_DIGEST_CONTRACT_MISMATCH'); "
                    "const canonical=value=>Array.isArray(value)?value.map(canonical):"
                    "(value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value); "
                    "const payload={...bundle}; delete payload.bundle_content_sha256; "
                    "return [{json:{application_contract_bundle:bundle,"
                    "bundle_canonical:JSON.stringify(canonical(payload))}}];"
                )
            },
        }
    )
    connections[previous] = {
        "main": [[{"node": load_bundle_name, "type": "main", "index": 0}]]
    }
    previous = load_bundle_name

    hash_bundle_name = "SHA-256 Application Contract Bundle"
    nodes.append(
        {
            "id": "19000-hash",
            "name": hash_bundle_name,
            "type": "n8n-nodes-base.crypto",
            "typeVersion": 1,
            "position": [-1420, 0],
            "parameters": {
                "action": "hash",
                "type": "SHA256",
                "value": "={{ $json.bundle_canonical }}",
                "dataPropertyName": "bundle_content_sha256_observed",
            },
        }
    )
    connections[previous] = {
        "main": [[{"node": hash_bundle_name, "type": "main", "index": 0}]]
    }
    previous = hash_bundle_name

    verify_bundle_name = "Verify Application Contract Bundle Digest and Maps"
    nodes.append(
        {
            "id": "19000-verify",
            "name": verify_bundle_name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [-1240, 0],
            "parameters": {
                "jsCode": (
                    "const bundle=$json.application_contract_bundle; "
                    "if($json.bundle_content_sha256_observed!==bundle.bundle_content_sha256) "
                    "throw new Error('APPLICATION_CONTRACT_BUNDLE_DIGEST_MISMATCH'); "
                    "if(!Array.isArray(bundle.source_documents)||bundle.source_documents.length!==3) "
                    "throw new Error('APPLICATION_CONTRACT_BUNDLE_SOURCE_COUNT_MISMATCH'); "
                    "const paths=bundle.source_documents.map(source=>source.path); "
                    "if(new Set(paths).size!==paths.length||bundle.source_documents.some(source=>"
                    "!/^config\\/(statement-sources|transaction-email-sources|ai-policies)\\.json$/.test(source.path)||"
                    "!/^[a-f0-9]{64}$/.test(source.sha256))) "
                    "throw new Error('APPLICATION_CONTRACT_BUNDLE_SOURCE_HASH_INVALID'); "
                    "if(!Array.isArray(bundle.resolver_maps)||bundle.resolver_maps.length!==4||"
                    "bundle.resolver_maps.some((resolver,index)=>resolver.workflow_code!==bundle.resolver_order[index]||"
                    "!Array.isArray(resolver.entries)||!resolver.entries.length||!Array.isArray(resolver.sources)||"
                    "resolver.sources.length!==1||new Set(resolver.entries.map(entry=>entry.key)).size!==resolver.entries.length||"
                    "resolver.sources[0].sha256!==bundle.source_documents.find(source=>source.path===resolver.sources[0].path)?.sha256)) "
                    "throw new Error('APPLICATION_CONTRACT_BUNDLE_RESOLVER_MAP_INVALID'); "
                    "return [{json:{application_contract_bundle:bundle,"
                    "application_contract_bundle_verified:true,"
                    "bundle_content_sha256:bundle.bundle_content_sha256}}];"
                )
            },
        }
    )
    connections[previous] = {
        "main": [[{"node": verify_bundle_name, "type": "main", "index": 0}]]
    }
    previous = verify_bundle_name

    if matrix is not None:
        targets, target_schemas, target_schema_digest = target_schema_contract(matrix)
        target_guard_name = "Verify Four-Table Target Contract"
        nodes.append({
            "id": "19000-target-guard",
            "name": target_guard_name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [-680, 0],
            "parameters": {
                "jsCode": (
                    f"const contract={readable_js_literal(target_schemas)}; "
                    f"const expected={json.dumps(targets)}; "
                    "const names=Object.keys(contract); "
                    "if(names.length!==expected.length||expected.some(name=>!names.includes(name))) "
                    "throw new Error('TARGET_TABLE_SET_MISMATCH'); "
                    "for(const name of expected){const table=contract[name]; "
                    "if(!table.logical_key.length||!table.columns.length) "
                    "throw new Error(`TARGET_SCHEMA_EMPTY:${name}`); "
                    "if(table.columns.some(column=>!['string','number','boolean','date'].includes(column.type))) "
                    "throw new Error(`TARGET_SCHEMA_TYPE_UNSUPPORTED:${name}`);} "
                    f"return [{{json:{{status:'TARGET_SCHEMA_CONTRACT_VERIFIED',target_tables:expected,target_schemas:contract,target_schema_digest:'{target_schema_digest}',runtime_cutover:false,deletion_authorized:false}}}}];"
                )
            },
        })
        connections[previous] = {
            "main": [[{"node": target_guard_name, "type": "main", "index": 0}]]
        }
        previous = target_guard_name

    for index, table in enumerate(table_rows, start=1):
        name = f"Create or Reuse {table['name']}"
        node = data_table_node(
            f"19{index + 1:03d}",
            name,
            [-1600 + index * 180, 0],
            create_parameters(table),
        )
        nodes.append(node)
        connections[previous] = {
            "main": [[{"node": name, "type": "main", "index": 0}]]
        }
        previous = name

    if matrix is not None:
        list_name = "List Four Target Tables"
        nodes.append(data_table_node(
            "19006",
            list_name,
            [1380, 0],
            {"resource": "table", "operation": "list", "returnAll": True, "options": {}},
        ))
        connections[previous] = {
            "main": [[{"node": list_name, "type": "main", "index": 0}]]
        }
        previous = list_name

        readback_name = "Verify Four Target Table Readback"
        nodes.append({
            "id": "19007",
            "name": readback_name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1680, 0],
            "parameters": {
                "jsCode": r"""
const contract = $('Verify Four-Table Target Contract').first().json;
const expected = contract.target_tables;
const rows = $input.all().map(item => item.json || {});
if (!Array.isArray(expected) || expected.length !== 4) throw new Error('TARGET_TABLE_SET_MISMATCH');
const names = rows.map(row => String(row.name || ''));
const duplicateNames = names.filter((name, index) => name && names.indexOf(name) !== index);
if (duplicateNames.length) throw new Error('TARGET_TABLE_DUPLICATE:' + duplicateNames[0]);
const unexpectedTarget = rows.find(row => {
  const name = String(row.name || '');
  return /^finance_(ingestion_state|documents|actual_batches|ai_reviews)(?:[_-]|$)/.test(name) && !expected.includes(name);
});
if (unexpectedTarget) throw new Error('TARGET_TABLE_EXTRA:' + String(unexpectedTarget.name));
const observed = new Map(rows.filter(row => expected.includes(String(row.name || ''))).map(row => [String(row.name), row]));
if (observed.size !== expected.length) {
  const missing = expected.find(name => !observed.has(name));
  throw new Error('TARGET_TABLE_MISSING:' + missing);
}
for (const target of expected) {
  const row = observed.get(target);
  const schemaColumns = contract.target_schemas?.[target]?.columns;
  const expectedColumns = Array.isArray(schemaColumns) ? schemaColumns.map(column => ({ name: String(column.name || ''), type: String(column.type || '') })) : Object.entries(schemaColumns || {}).map(([name, spec]) => ({ name, type: spec.type }));
  const columns = Array.isArray(row.columns) ? row.columns.map(column => ({ name: String(column.name || ''), type: String(column.type || '') })) : [];
  if (columns.length !== expectedColumns.length) throw new Error('TARGET_SCHEMA_MISMATCH:' + target);
  for (let index = 0; index < columns.length; index += 1) {
    const wanted = expectedColumns[index];
    const actual = columns[index];
    if (actual.name !== wanted.name || actual.type !== wanted.type) throw new Error('TARGET_SCHEMA_MISMATCH:' + target + ':' + (actual.name || index));
  }
  const createRow = $('Create or Reuse ' + target).first().json || {};
  const createdId = String(createRow.id || createRow.dataTableId || createRow.tableId || '');
  const observedId = String(row.id || row.dataTableId || row.tableId || '');
  if (!createdId || !observedId) throw new Error('TARGET_TABLE_ID_MISSING:' + target);
  if (createdId !== observedId) throw new Error('TARGET_TABLE_ID_MISMATCH:' + target);
}
return [{ json: {
  status: 'TARGET_SCHEMA_READBACK_VERIFIED',
  target_tables: expected,
  target_schema_digest: contract.target_schema_digest,
  observed_table_count: observed.size,
  observed_schema_verified: true,
  observed_ids_verified: true,
  runtime_cutover: false,
  deletion_authorized: false,
  second_run_noop: true,
  old_tables_preserved: true,
  redacted: true,
  mode: '0600',
} }];
""".strip(),
            },
        })
        connections[previous] = {
            "main": [[{"node": readback_name, "type": "main", "index": 0}]]
        }
        previous = readback_name

        receipt_name = "Emit Redacted Bootstrap Receipt"
        nodes.append({
            "id": "19060",
            "name": receipt_name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1680, 0],
            "parameters": {
                "jsCode": (
                    f"const targetTables={json.dumps(targets)}; "
                    "return [{json:{schema_version:'data-table-migration-receipt-v1',"
                    "status:'SCHEMA_CONTRACT_READY',target_tables:targetTables,"
                    f"target_schema_digest:'{target_schema_digest}',runtime_cutover:false,"
                    "deletion_authorized:false,second_run_noop:true,old_tables_preserved:true,"
                    "redacted:true,mode:'0600'}}];"
                )
            },
        })
        connections[previous] = {
            "main": [[{"node": receipt_name, "type": "main", "index": 0}]]
        }
        return target_bootstrap_document(
            nodes,
            connections,
            bundle,
            manifest,
            targets,
            target_schema_digest,
        )

    embedded_seed = readable_js_literal(seed_rows)
    emit_name = "Emit Versioned AI Policy Seed"
    nodes.append(
        {
            "id": "19050",
            "name": emit_name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1200, 0],
            "parameters": {
                "jsCode": (
                    f"const rows={embedded_seed}; "
                    "const updated_at=new Date().toISOString(); "
                    "return rows.map(row=>({json:{...row,updated_at}}));"
                )
            },
        }
    )
    connections[previous] = {
        "main": [[{"node": emit_name, "type": "main", "index": 0}]]
    }

    upsert_name = "Upsert AI Policy Contracts"
    upsert_value = {
        field: f"={{{{ $json.{field} }}}}"
        for field in (
            "policy_id",
            "policy_version",
            "agent_profile",
            "agent_provider",
            "policy_sha256",
            "config_sha256",
            "output_schema_sha256",
            "allowed_fields_json",
            "allowed_values_json",
            "state",
            "updated_at",
        )
    }
    nodes.append(
        data_table_node(
            "19051",
            upsert_name,
            [1400, 0],
            {
                "resource": "row",
                "operation": "upsert",
                "dataTableId": {
                    "__rl": True,
                    "value": "finance_ai_policy_contracts",
                    "mode": "name",
                },
                "matchType": "allConditions",
                "filters": {
                    "conditions": [
                        {
                            "keyName": "policy_id",
                            "condition": "eq",
                            "keyValue": "={{ $json.policy_id }}",
                        },
                        {
                            "keyName": "policy_version",
                            "condition": "eq",
                            "keyValue": "={{ $json.policy_version }}",
                        },
                    ]
                },
                "columns": {
                    "mappingMode": "defineBelow",
                    "value": upsert_value,
                    "matchingColumns": [],
                    "schema": [],
                    "attemptToConvertTypes": False,
                    "convertFieldsToString": False,
                },
                "options": {"dryRun": False},
            },
        )
    )
    connections[emit_name] = {
        "main": [[{"node": upsert_name, "type": "main", "index": 0}]]
    }

    collapse_name = "Collapse Seed Writes to One Readback"
    nodes.append(
        {
            "id": "19052",
            "name": collapse_name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1600, 0],
            "parameters": {
                "jsCode": (
                    "const rows=$input.all(); "
                    "if(rows.length!=="
                    + str(len(seed_rows))
                    + ") throw new Error('AI_POLICY_SEED_UPSERT_COUNT_MISMATCH'); "
                    "return [{json:{seed_write_count:rows.length}}];"
                )
            },
        }
    )
    connections[upsert_name] = {
        "main": [[{"node": collapse_name, "type": "main", "index": 0}]]
    }

    read_name = "Read Back All ACTIVE AI Policy Contracts"
    read_node = data_table_node(
        "19053",
        read_name,
        [1800, 0],
        {
            "resource": "row",
            "operation": "get",
            "dataTableId": {
                "__rl": True,
                "value": "finance_ai_policy_contracts",
                "mode": "name",
            },
            "returnAll": True,
            "matchType": "allConditions",
            "filters": {
                "conditions": [
                    {"keyName": "state", "condition": "eq", "keyValue": "ACTIVE"}
                ]
            },
            "options": {},
        },
    )
    read_node["alwaysOutputData"] = True
    nodes.append(read_node)
    connections[collapse_name] = {
        "main": [[{"node": read_name, "type": "main", "index": 0}]]
    }

    compare_name = "Exact Compare AI Policy Seed Readback"
    fields = manifest["seed"]["exact_readback_fields"]
    nodes.append(
        {
            "id": "19054",
            "name": compare_name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [2000, 0],
            "parameters": {
                "jsCode": (
                    f"const expected={embedded_seed}; const fields={readable_js_literal(fields)}; "
                    "const observed=$input.all().map(item=>item.json).filter(row=>row&&row.state==='ACTIVE'); "
                    "if(observed.length!==expected.length) throw new Error('AI_POLICY_ACTIVE_COUNT_MISMATCH'); "
                    "const key=row=>JSON.stringify([row.policy_id,Number(row.policy_version)]); "
                    "const byKey=new Map(); for(const row of observed){const k=key(row); "
                    "if(byKey.has(k)) throw new Error('AI_POLICY_DUPLICATE_ACTIVE_VERSION'); byKey.set(k,row);} "
                    "for(const row of expected){const actual=byKey.get(key(row)); "
                    "if(!actual) throw new Error(`AI_POLICY_READBACK_MISSING:${row.policy_id}`); "
                    "for(const field of fields) if(String(actual[field])!==String(row[field])) "
                    "throw new Error(`AI_POLICY_READBACK_MISMATCH:${row.policy_id}:${field}`); "
                    "if(!actual.updated_at||Number.isNaN(Date.parse(actual.updated_at))) "
                    "throw new Error(`AI_POLICY_UPDATED_AT_INVALID:${row.policy_id}`);} "
                    f"return [{{json:{{status:'VERIFIED',tables_created_or_reused:{len(table_rows)},"
                    f"ai_policy_rows_verified:{len(seed_rows)},finance_ledger_writes:false,"
                    "actual_writes:false,contract_status:'SPEC_ONLY'}}];"
                )
            },
        }
    )
    connections[read_name] = {
        "main": [[{"node": compare_name, "type": "main", "index": 0}]]
    }

    embedded_config_seed = readable_js_literal(config_seed["rows"])
    emit_config_name = "Emit Versioned Config Fingerprints"
    nodes.append({
        "id": "19055", "name": emit_config_name, "type": "n8n-nodes-base.code",
        "typeVersion": 2, "position": [2200, 0],
        "parameters": {"jsCode": (
            f"const rows={embedded_config_seed}; const activated_at=new Date().toISOString(); "
            "return rows.map(row=>({json:{...row,activated_at}}));"
        )},
    })
    connections[compare_name] = {"main": [[{"node": emit_config_name, "type": "main", "index": 0}]]}

    config_upsert_name = "Upsert Config Version Fingerprints"
    config_fields = ("config_name", "version", "source_path", "content_sha256", "git_commit", "state", "readback_verified", "activated_at")
    nodes.append(data_table_node("19056", config_upsert_name, [2400, 0], {
        "resource": "row", "operation": "upsert",
        "dataTableId": {"__rl": True, "value": "finance_config_versions", "mode": "name"},
        "matchType": "allConditions",
        "filters": {"conditions": [
            {"keyName": "config_name", "condition": "eq", "keyValue": "={{ $json.config_name }}"},
            {"keyName": "version", "condition": "eq", "keyValue": "={{ $json.version }}"},
        ]},
        "columns": {"mappingMode": "defineBelow", "value": {field: f"={{{{ $json.{field} }}}}" for field in config_fields}, "matchingColumns": [], "schema": [], "attemptToConvertTypes": False, "convertFieldsToString": False},
        "options": {"dryRun": False},
    }))
    connections[emit_config_name] = {"main": [[{"node": config_upsert_name, "type": "main", "index": 0}]]}

    collapse_config_name = "Collapse Config Writes to One Readback"
    nodes.append({"id": "19057", "name": collapse_config_name, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [2600, 0], "parameters": {"jsCode": f"const rows=$input.all(); if(rows.length!=={len(config_seed['rows'])}) throw new Error('CONFIG_VERSION_UPSERT_COUNT_MISMATCH'); return [{{json:{{config_write_count:rows.length}}}}];"}})
    connections[config_upsert_name] = {"main": [[{"node": collapse_config_name, "type": "main", "index": 0}]]}

    config_read_name = "Read Back ACTIVE Config Fingerprints"
    config_read = data_table_node("19058", config_read_name, [2800, 0], {"resource": "row", "operation": "get", "dataTableId": {"__rl": True, "value": "finance_config_versions", "mode": "name"}, "returnAll": True, "matchType": "allConditions", "filters": {"conditions": [{"keyName": "state", "condition": "eq", "keyValue": "ACTIVE"}]}, "options": {}})
    config_read["alwaysOutputData"] = True
    nodes.append(config_read)
    connections[collapse_config_name] = {"main": [[{"node": config_read_name, "type": "main", "index": 0}]]}

    compare_config_name = "Exact Compare Config Fingerprint Readback"
    config_compare_fields = manifest["config_seed"]["exact_readback_fields"]
    nodes.append({"id": "19059", "name": compare_config_name, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [3000, 0], "parameters": {"jsCode": (
        f"const expected={embedded_config_seed},fields={readable_js_literal(config_compare_fields)}; "
        "const rows=$input.all().map(i=>i.json).filter(r=>r&&r.state==='ACTIVE'),byKey=new Map(rows.map(r=>[JSON.stringify([r.config_name,String(r.version)]),r])); "
        "for(const row of expected){const actual=byKey.get(JSON.stringify([row.config_name,String(row.version)])); if(!actual) throw new Error(`CONFIG_VERSION_READBACK_MISSING:${row.config_name}`); for(const field of fields){const value=field==='git_commit'&&row[field]==='RUNTIME_BIND_GIT_COMMIT'?actual[field]:row[field]; if(String(actual[field])!==String(value)) throw new Error(`CONFIG_VERSION_READBACK_MISMATCH:${row.config_name}:${field}`);}} "
        f"return [{{json:{{status:'VERIFIED',tables_created_or_reused:{len(table_rows)},ai_policy_rows_verified:{len(seed_rows)},config_rows_verified:{len(config_seed['rows'])},finance_ledger_writes:false,actual_writes:false,contract_status:'SPEC_ONLY'}}}}];"
    )}})
    connections[config_read_name] = {"main": [[{"node": compare_config_name, "type": "main", "index": 0}]]}

    return {
        "id": WORKFLOW_ID,
        "name": "Finance · Platform Data Table Bootstrap",
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {
            "executionOrder": "v1",
            "timezone": "Asia/Dubai",
            "saveDataErrorExecution": "none",
            "saveDataSuccessExecution": "none",
            "errorWorkflow": ERROR_WORKFLOW_ID,
        },
        "pinData": {},
        "meta": {
            "financeWorkflowCode": "PLATFORM_DATA_TABLE_BOOTSTRAP",
            "migrationStatus": "SPEC_ONLY",
            "manualOnly": True,
            "platformBootstrapOnly": True,
            "financeLedgerMutationForbidden": True,
            "actualMutationForbidden": True,
            "sourceContract": "integrations/n8n/data-tables.json",
            "seedContract": "integrations/n8n/generated/ai-policy-contracts.seed.json",
            "configSeedContract": "integrations/n8n/generated/config-versions.seed.json",
            "applicationContractBundle": "integrations/n8n/generated/application-contract-bundle.json",
            "applicationContractBundleSchema": "integrations/n8n/generated/application-contract-bundle.schema.json",
            "applicationContractBundleContentSha256": bundle["bundle_content_sha256"],
            "provisioningManifest": "integrations/n8n/generated/platform-bootstrap-manifest.json",
            "activationBlockers": manifest["activation_blockers"],
            "importTested": False,
            "fixtureExecuted": False,
            "credentialBindings": [],
            "setupRequired": True,
        },
    }


def render() -> tuple[str, str, str, str]:
    tables = load_json(TABLES_PATH)
    matrix = load_json(MATRIX_PATH)
    seed = load_json(SEED_PATH)
    config_seed = load_json(CONFIG_SEED_PATH)
    documents, sources = load_application_configs()
    bundle_schema = build_application_contract_schema(documents, sources)
    bundle = build_application_contract_bundle(documents, sources, bundle_schema)
    bundle_text = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"
    bundle_schema_text = json.dumps(bundle_schema, indent=2, ensure_ascii=False) + "\n"
    manifest = build_manifest(
        tables,
        seed,
        config_seed,
        bundle,
        hashlib.sha256(bundle_text.encode("utf-8")).hexdigest(),
        hashlib.sha256(bundle_schema_text.encode("utf-8")).hexdigest(),
        matrix,
    )
    workflow = build_workflow(tables, seed, config_seed, manifest, bundle, matrix)
    format_code_nodes([workflow])
    layout(workflow)
    return (
        bundle_text,
        bundle_schema_text,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        json.dumps(workflow, indent=2, ensure_ascii=False) + "\n",
    )


def generated_artifact_drift(
    expected: tuple[tuple[Path, str], ...], root: Path = ROOT,
) -> list[str]:
    return [
        path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        for path, expected_text in expected
        if not path.exists() or path.read_text(encoding="utf-8") != expected_text
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the SPEC_ONLY n8n platform Data Table bootstrap contract."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write generated artifacts")
    mode.add_argument(
        "--check",
        action="store_true",
        help="check generated artifacts and exit non-zero when stale",
    )
    args = parser.parse_args(argv)
    bundle_text, bundle_schema_text, manifest_text, workflow_text = render()
    if args.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        BUNDLE_PATH.write_text(bundle_text, encoding="utf-8", newline="\n")
        BUNDLE_SCHEMA_PATH.write_text(bundle_schema_text, encoding="utf-8", newline="\n")
        MANIFEST_PATH.write_text(manifest_text, encoding="utf-8")
        WORKFLOW_PATH.write_text(workflow_text, encoding="utf-8")
        return 0
    drift = generated_artifact_drift((
        (BUNDLE_PATH, bundle_text),
        (BUNDLE_SCHEMA_PATH, bundle_schema_text),
        (MANIFEST_PATH, manifest_text),
        (WORKFLOW_PATH, workflow_text),
    ))
    if drift:
        print("platform bootstrap artifacts are stale: " + ", ".join(drift))
        print("run: python integrations/n8n/generate_platform_bootstrap.py --write")
        return 1
    print("platform bootstrap artifacts are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
