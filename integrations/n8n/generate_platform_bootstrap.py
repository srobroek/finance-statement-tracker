from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "integrations" / "n8n"
TABLES_PATH = N8N / "data-tables.json"
SEED_PATH = N8N / "generated" / "ai-policy-contracts.seed.json"
MANIFEST_PATH = N8N / "generated" / "platform-bootstrap-manifest.json"
WORKFLOW_PATH = N8N / "workflows" / "19-platform-data-table-bootstrap.json"

WORKFLOW_ID = "10000000-0000-4000-8000-000000000019"
ERROR_WORKFLOW_ID = "10000000-0000-4000-8000-000000000016"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def ordered_tables(contract: dict) -> list[dict]:
    tables = contract["tables"]
    # Provision the error sink first so later bootstrap failures have somewhere
    # durable to land once workflow 16 has itself been imported and bound.
    return sorted(
        tables,
        key=lambda row: (row["name"] != "finance_execution_failures", row["name"]),
    )


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


def build_manifest(tables: dict, seed: dict) -> dict:
    return {
        "schema_version": 1,
        "contract_status": "SPEC_ONLY",
        "n8n_version": "2.36.1",
        "sources": {
            "data_tables": "integrations/n8n/data-tables.json",
            "data_tables_sha256": sha256(TABLES_PATH),
            "ai_policy_seed": "integrations/n8n/generated/ai-policy-contracts.seed.json",
            "ai_policy_seed_sha256": sha256(SEED_PATH),
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
                "policy_sha256",
                "config_sha256",
                "output_schema_sha256",
                "allowed_fields_json",
                "allowed_values_json",
                "state",
            ],
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


def data_table_node(node_id: str, name: str, position: list[int], parameters: dict) -> dict:
    return {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 1.1,
        "position": position,
        "parameters": parameters,
    }


def build_workflow(tables: dict, seed: dict, manifest: dict) -> dict:
    table_rows = ordered_tables(tables)
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

    embedded_seed = canonical_json(seed_rows)
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
        field: f"={{ $json.{field} }}"
        for field in (
            "policy_id",
            "policy_version",
            "agent_profile",
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
                    f"const expected={embedded_seed}; const fields={canonical_json(fields)}; "
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

    return {
        "id": WORKFLOW_ID,
        "name": "Finance · Platform Data Table Bootstrap · SPEC ONLY",
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
            "provisioningManifest": "integrations/n8n/generated/platform-bootstrap-manifest.json",
            "activationBlockers": manifest["activation_blockers"],
            "importTested": False,
            "fixtureExecuted": False,
        },
    }


def render() -> tuple[str, str]:
    tables = load_json(TABLES_PATH)
    seed = load_json(SEED_PATH)
    manifest = build_manifest(tables, seed)
    workflow = build_workflow(tables, seed, manifest)
    return (
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        json.dumps(workflow, indent=2, ensure_ascii=False) + "\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the SPEC_ONLY n8n platform Data Table bootstrap contract."
    )
    parser.add_argument("--write", action="store_true", help="write generated artifacts")
    args = parser.parse_args()
    manifest_text, workflow_text = render()
    if args.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(manifest_text, encoding="utf-8")
        WORKFLOW_PATH.write_text(workflow_text, encoding="utf-8")
        return 0
    drift = []
    for path, expected in (
        (MANIFEST_PATH, manifest_text),
        (WORKFLOW_PATH, workflow_text),
    ):
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            drift.append(path.relative_to(ROOT).as_posix())
    if drift:
        print("platform bootstrap artifacts are stale: " + ", ".join(drift))
        print("run: python integrations/n8n/generate_platform_bootstrap.py --write")
        return 1
    print("platform bootstrap artifacts are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
