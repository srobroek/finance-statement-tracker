from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
N8N = ROOT / "integrations" / "n8n"
PRODUCTION = N8N / "workflows"
HERE = N8N / "disposable"
OUTPUT = HERE / "generated"
MANIFEST = HERE / "fixture-manifest.json"

LEASE_ID = "10000000-0000-4000-8000-000000000018"
AI_ID = "10000000-0000-4000-8000-000000000009"
SWEEP_FIXTURE_ID = "90000000-0000-4000-8000-000000000012"
RECOVERY_FIXTURE_ID = "90000000-0000-4000-8000-000000000017"

INLINE_SOURCE_FILES = {
    "10000000-0000-4000-8000-000000000001": "01-outlook-finance-acquisition.json",
    AI_ID: "09-ai-proposal.json",
    LEASE_ID: "18-finance-writer-lease.json",
    "10000000-0000-4000-8000-000000000020": "20-actual-outbox-apply.json",
    "10000000-0000-4000-8000-000000000021": "21-subscription-agent-adapter.json",
}

ALLOWED_INLINE_EDGES = {
    "90000000-0000-4000-8000-000000000901": frozenset({SWEEP_FIXTURE_ID}),
    "90000000-0000-4000-8000-000000000902": frozenset({SWEEP_FIXTURE_ID}),
    "90000000-0000-4000-8000-000000000903": frozenset({SWEEP_FIXTURE_ID}),
    "90000000-0000-4000-8000-000000000904": frozenset({SWEEP_FIXTURE_ID}),
    "90000000-0000-4000-8000-000000000905": frozenset({LEASE_ID}),
    "90000000-0000-4000-8000-000000000906": frozenset({LEASE_ID}),
    "90000000-0000-4000-8000-000000000907": frozenset({LEASE_ID}),
    "90000000-0000-4000-8000-000000000908": frozenset({AI_ID}),
    "90000000-0000-4000-8000-000000000909": frozenset({AI_ID}),
    "90000000-0000-4000-8000-000000000910": frozenset({AI_ID}),
    "90000000-0000-4000-8000-000000000911": frozenset({AI_ID}),
    "90000000-0000-4000-8000-000000000912": frozenset({AI_ID}),
    "90000000-0000-4000-8000-000000000918": frozenset({RECOVERY_FIXTURE_ID}),
    "90000000-0000-4000-8000-000000000919": frozenset({RECOVERY_FIXTURE_ID}),
    "90000000-0000-4000-8000-000000000920": frozenset({RECOVERY_FIXTURE_ID}),
    SWEEP_FIXTURE_ID: frozenset({
        "10000000-0000-4000-8000-000000000001",
        "10000000-0000-4000-8000-000000000021",
    }),
    AI_ID: frozenset({"10000000-0000-4000-8000-000000000021"}),
    RECOVERY_FIXTURE_ID: frozenset({"10000000-0000-4000-8000-000000000020"}),
    "10000000-0000-4000-8000-000000000020": frozenset({LEASE_ID}),
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalized_text_bytes(path: Path) -> bytes:
    """Return the Git-canonical LF bytes used by Linux runtime checkouts."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def manual_node(node_id: str = "fixture-trigger") -> dict:
    return {
        "id": node_id,
        "name": "Run Disposable Fixture",
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-500, 0],
        "parameters": {},
    }


def code_node(node_id: str, name: str, js_code: str, position: list[int]) -> dict:
    return {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": position,
        "parameters": {"jsCode": js_code},
    }


def execute_node(node_id: str, name: str, workflow_id: str, position: list[int]) -> dict:
    return {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.executeWorkflow",
        "typeVersion": 1.2,
        "position": position,
        "parameters": {
            "workflowId": {"__rl": True, "value": workflow_id, "mode": "id"},
            "options": {"waitForSubWorkflow": True},
        },
    }


def fixture_settings() -> dict:
    return {
        "executionOrder": "v1",
        "timezone": "Asia/Dubai",
        "saveDataErrorExecution": "none",
        "saveDataSuccessExecution": "none",
    }


def wrapper(workflow_id: str, name: str, input_js: str, target_id: str) -> dict:
    trigger = manual_node()
    emit = code_node("fixture-input", "Emit Fixed Fixture Input", input_js, [-250, 0])
    call = execute_node("fixture-call", "Run Target Workflow", target_id, [0, 0])
    return {
        "id": workflow_id,
        "name": name,
        "active": False,
        "nodes": [trigger, emit, call],
        "connections": {
            trigger["name"]: {"main": [[{"node": emit["name"], "type": "main", "index": 0}]]},
            emit["name"]: {"main": [[{"node": call["name"], "type": "main", "index": 0}]]},
        },
        "settings": fixture_settings(),
        "pinData": {},
        "meta": {"disposableOnly": True, "productionImportForbidden": True},
    }


def build_sweep_core() -> dict:
    workflow = copy.deepcopy(read_json(PRODUCTION / "12-outlook-message-sweep.json"))
    workflow["id"] = SWEEP_FIXTURE_ID
    workflow["name"] = "DISPOSABLE ONLY · Derived Outlook Sweep Fixture Core"
    workflow["settings"] = fixture_settings()
    workflow["meta"] = {
        "disposableOnly": True,
        "productionImportForbidden": True,
        "derivedFrom": "12-outlook-message-sweep.json",
        "externalNodeReplacement": "Exhaust Outlook Pagination only",
    }
    for node in workflow["nodes"]:
        if node["name"] != "Exhaust Outlook Pagination":
            if node["name"] == "List Immutable Message Attachments":
                node.pop("credentials", None)
                node.pop("alwaysOutputData", None)
                node["type"] = "n8n-nodes-base.code"
                node["typeVersion"] = 2
                node["parameters"] = {
                    "jsCode": (
                        "return $input.all().flatMap(item => { "
                        "const attachments=Array.isArray(item.json.attachment_inventory) ? item.json.attachment_inventory : []; "
                        "return attachments.length ? attachments.map(attachment => ({json: attachment})) : [{json:{}}]; "
                        "});"
                    )
                }
            continue
        node.pop("credentials", None)
        node.pop("alwaysOutputData", None)
        node["type"] = "n8n-nodes-base.code"
        node["typeVersion"] = 2
        node["parameters"] = {
            "jsCode": (
                "const c=$('Freeze Trusted Cursor Window').first().json,kind=String(c.fixture_case||''); "
                "if(kind==='pagination-failure') throw new Error('FIXTURE_PAGE_2_FAILURE token=SHOULD_REDACT'); "
                "const mk=(id,offset,attachments=[])=>({json:{id,subject:c.subjects[0],receivedDateTime:new Date(new Date(c.window_start).getTime()+offset).toISOString(),from:{emailAddress:{address:c.senders[0]}},attachment_inventory:attachments}}); "
                "if(kind==='zero') return [{json:{}}]; "
                "if(kind==='one-no-attachments') return [mk('m001',1000)]; "
                "if(kind==='one-hundred-one') return Array.from({length:101},(_,i)=>mk(`m${String(i+1).padStart(3,'0')}`,(i+1)*1000)); "
                "if(kind==='late-out-of-order') return [mk('m3',3000),mk('m1',1000),mk('m2',2000)]; "
                "throw new Error('UNKNOWN_SWEEP_FIXTURE');"
            )
        }
    return workflow


def sweep_input(case: str) -> str:
    return (
        "const upper=new Date(Date.now()-5000),start=new Date(upper.getTime()-3600000); "
        f"return [{{json:{{fixture_case:'{case}',run_id:'fixture:{case}',source_code:'FIXTURE',"
        "folder_id:'fixture-folder',senders:['fixture@example.test'],subjects:['Fixture transaction'],"
        "window_start:start.toISOString(),run_upper_bound:upper.toISOString()}}];"
    )


def build_ai_wrapper(workflow_id: str, case: str) -> dict:
    base = {
        "policy_id": "classify-unresolved",
        "unresolved": [{
            "transaction_id": f"fixture:{case}",
            "allowed_fields": ["category"],
            "redacted_context": {"vendor": "Fixture Vendor"},
        }],
    }
    if case == "caller-model-rejected":
        base["model"] = "caller-selected-model"
    elif case == "locked-field-rejected":
        base["unresolved"][0]["allowed_fields"] = ["amount"]
    elif case == "missing-active-policy":
        base["policy_id"] = "fixture-policy-does-not-exist"
    else:
        raise ValueError(case)
    return wrapper(
        workflow_id,
        f"DISPOSABLE ONLY · AI {case}",
        f"return [{{json:{canonical(base)}}}];",
        AI_ID,
    )


def build_positive_ai_wrapper(workflow_id: str, profile: str) -> dict:
    if profile == "luna":
        request = {
            "policy_id": "classify-unresolved",
            "unresolved": [{
                "transaction_id": "fixture:positive:luna:carrefour",
                "allowed_fields": ["category", "tags"],
                "redacted_context": {
                    "merchant_description": "CARREFOUR MARKET UAE",
                    "normalized_vendor": "Carrefour",
                    "transaction_type": "PURCHASE",
                    "deterministic_result": "category unresolved",
                },
            }],
        }
        name = "DISPOSABLE ONLY · Positive Luna proposal"
    elif profile == "sol":
        request = {
            "policy_id": "recommend-category",
            "unresolved": [{
                "transaction_id": "fixture:positive:sol:category-recommendation",
                "allowed_fields": ["category_recommendation"],
                "redacted_context": {
                    "merchant_description": "SPECIALIST FIXTURE MERCHANT",
                    "normalized_vendor": "Specialist Fixture Merchant",
                    "transaction_type": "PURCHASE",
                    "deterministic_result": "no configured category fits",
                },
            }],
        }
        name = "DISPOSABLE ONLY · GATED Positive Sol proposal"
    else:
        raise ValueError(profile)
    workflow = wrapper(
        workflow_id,
        name,
        f"return [{{json:{canonical(request)}}}];",
        AI_ID,
    )
    workflow["meta"]["agentProfileExpected"] = (
        "LUNA_MAX" if profile == "luna" else "SOL_MEDIUM"
    )
    workflow["meta"]["financeWritesImpossible"] = True
    if profile == "sol":
        workflow["meta"]["executionGate"] = "DISPOSABLE_ALLOW_SOL_MEDIUM"
        workflow["meta"]["defaultExecutionForbidden"] = True
    return workflow


def build_lease_wrapper(workflow_id: str, owner: str) -> dict:
    request = {
        "operation": "ACQUIRE",
        "resource_key": "actual:fixture_concurrency",
        "lease_owner": owner,
        "ttl_seconds": 120,
    }
    return wrapper(
        workflow_id,
        f"DISPOSABLE ONLY · Lease acquire {owner}",
        f"return [{{json:{canonical(request)}}}];",
        LEASE_ID,
    )


def build_stale_lease_wrapper() -> dict:
    trigger = manual_node()
    acquire_input = code_node(
        "lease-stale-input",
        "Build Stale Fixture Acquire",
        "return [{json:{operation:'ACQUIRE',resource_key:'actual:fixture_stale',lease_owner:'n8n:fixture:stale',ttl_seconds:120}}];",
        [-250, 0],
    )
    acquire = execute_node("lease-stale-acquire", "Acquire Fixture Lease", LEASE_ID, [0, 0])
    corrupt = code_node(
        "lease-stale-corrupt",
        "Build Stale Fence Assertion",
        "return [{json:{operation:'ASSERT',resource_key:$json.resource_key,lease_id:$json.lease_id,fencing_token:Number($json.fencing_token)+1}}];",
        [250, 0],
    )
    assertion = execute_node("lease-stale-assert", "Assert Stale Fixture Fence", LEASE_ID, [500, 0])
    nodes = [trigger, acquire_input, acquire, corrupt, assertion]
    connections = {}
    for left, right in zip(nodes, nodes[1:]):
        connections[left["name"]] = {"main": [[{"node": right["name"], "type": "main", "index": 0}]]}
    return {
        "id": "90000000-0000-4000-8000-000000000907",
        "name": "DISPOSABLE ONLY · Stale writer fence rejected",
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": fixture_settings(),
        "pinData": {},
        "meta": {"disposableOnly": True, "productionImportForbidden": True},
    }


def build_error_redaction_fixture() -> dict:
    workflow = copy.deepcopy(read_json(PRODUCTION / "16-operations-error-handler.json"))
    workflow["id"] = "90000000-0000-4000-8000-000000000916"
    workflow["name"] = "DISPOSABLE ONLY · Derived Error Redaction Receipt"
    workflow["settings"] = fixture_settings()
    workflow["meta"] = {
        "disposableOnly": True,
        "productionImportForbidden": True,
        "derivedFrom": "16-operations-error-handler.json",
        "externalNodeReplacement": "Error Trigger only",
    }
    trigger = workflow["nodes"][0]
    old_name = trigger["name"]
    trigger.update(manual_node(trigger["id"]))
    emit = code_node(
        "fixture-error-envelope",
        "Emit Synthetic Sensitive Failure",
        "return [{json:{execution:{id:'fixture-error-redaction',error:{message:'password=DontLeak token=ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 card=4111111111111111'}},workflow:{id:'fixture-workflow',name:'Disposable fixture',financeWorkflowCode:'DISPOSABLE_ERROR'}}}];",
        [-600, 0],
    )
    workflow["nodes"].insert(1, emit)
    workflow["connections"].pop(old_name, None)
    workflow["connections"][trigger["name"]] = {
        "main": [[{"node": emit["name"], "type": "main", "index": 0}]]
    }
    workflow["connections"][emit["name"]] = {
        "main": [[{"node": "Redact and Classify Failure", "type": "main", "index": 0}]]
    }
    return workflow


def build_recovery_core() -> dict:
    workflow = copy.deepcopy(read_json(PRODUCTION / "17-actual-outbox-recovery.json"))
    workflow["id"] = RECOVERY_FIXTURE_ID
    workflow["name"] = "DISPOSABLE ONLY · Derived Outbox Recovery Core"
    workflow["settings"] = fixture_settings()
    workflow["meta"] = {
        "disposableOnly": True,
        "productionImportForbidden": True,
        "derivedFrom": "17-actual-outbox-recovery.json",
        "externalNodeReplacements": [
            "Schedule Trigger", "OneDrive artifact download", "Actual preflight/import/verify"
        ],
        "financeWritesImpossible": True,
    }
    replacements = {
        "Every 10 Minutes": (
            "n8n-nodes-base.executeWorkflowTrigger", 1.1, {"inputSource": "passthrough"}
        ),
        "Download Immutable Delta Artifact": (
            "n8n-nodes-base.code", 2, {"jsCode": "return $input.all();"}
        ),
        "SHA-256 Recovered Delta": (
            "n8n-nodes-base.code", 2,
            {"jsCode": "return $input.all().map(i=>({json:{...i.json,recovered_sha256:i.json.payload_sha256}}));"},
        ),
        "Extract Recovered Delta JSON": (
            "n8n-nodes-base.code", 2,
            {"jsCode": "return $input.all().map(i=>({json:{schema_version:i.json.artifact_schema_version,actual_file_id:i.json.actual_file_id,config_version:i.json.config_version,account_id:'fixture-account',period_start:'2026-08-01',period_end:'2026-08-31',transactions:[{imported_id:i.json.imported_id,date:'2026-08-15',amount:-100,imported_payee:'Fixture',cleared:true}],expected_statement_balance_minor:-100}}));"},
        ),
        "Recovery Actual Preflight": (
            "n8n-nodes-base.code", 2, {"jsCode": "return $input.all();"}
        ),
        "Recovery Import PREPARED": (
            "n8n-nodes-base.code", 2,
            {"jsCode": "return [{json:{actual_transaction_ids:['fixture-actual-transaction']}}];"},
        ),
        "Recovery Verify Actual": (
            "n8n-nodes-base.code", 2,
            {"jsCode": "return [{json:{invariants_passed:true,expected_payload_sha256:'fixture',observed_payload_sha256:'fixture'}}];"},
        ),
    }
    for node in workflow["nodes"]:
        replacement = replacements.get(node["name"])
        if replacement is None:
            continue
        node.pop("credentials", None)
        node.pop("alwaysOutputData", None)
        node["type"], node["typeVersion"], node["parameters"] = replacement
    return workflow


def outbox_upsert_node(state: str) -> dict:
    suffix = state.lower().replace("_", "-")
    value = {
        "outbox_id": f"fixture-recovery-{suffix}",
        "run_id": f"fixture-recovery-{suffix}",
        "imported_id": f"fixture:recovery:{suffix}",
        "actual_file_id": "fixture_actual",
        "payload_sha256": ("a" if state == "PREPARED" else "b" if state == "ACTUAL_OBSERVED" else "c") * 64,
        "artifact_item_id": f"fixture-artifact-{suffix}",
        "artifact_etag": "fixture-etag",
        "artifact_schema_version": "statement-delta-v1",
        "config_version": "fixture-v1",
        "parser_version": "fixture-v1",
        "state": state,
        "attempt_count": 0,
        "updated_at": "={{ $now.toISO() }}",
    }
    return {
        "id": f"seed-{suffix}",
        "name": f"Seed {state} Outbox Crash Boundary",
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 1.1,
        "position": [-200, 0],
        "parameters": {
            "resource": "row",
            "operation": "upsert",
            "dataTableId": {"__rl": True, "value": "finance_actual_outbox", "mode": "name"},
            "matchType": "allConditions",
            "filters": {"conditions": [{
                "keyName": "outbox_id", "condition": "eq", "keyValue": value["outbox_id"]
            }]},
            "columns": {
                "mappingMode": "defineBelow",
                "value": value,
                "matchingColumns": [],
                "schema": [],
                "attemptToConvertTypes": False,
                "convertFieldsToString": False,
            },
            "options": {"dryRun": False},
        },
    }


def build_recovery_wrapper(workflow_id: str, state: str) -> dict:
    trigger = manual_node()
    seed = outbox_upsert_node(state)
    call = execute_node("run-recovery", "Run Derived Recovery Core", RECOVERY_FIXTURE_ID, [100, 0])
    return {
        "id": workflow_id,
        "name": f"DISPOSABLE ONLY · Recover from {state}",
        "active": False,
        "nodes": [trigger, seed, call],
        "connections": {
            trigger["name"]: {"main": [[{"node": seed["name"], "type": "main", "index": 0}]]},
            seed["name"]: {"main": [[{"node": call["name"], "type": "main", "index": 0}]]},
        },
        "settings": fixture_settings(),
        "pinData": {},
        "meta": {"disposableOnly": True, "productionImportForbidden": True},
    }


def build_all() -> dict[str, dict]:
    workflows = {
        "90-derived-outlook-sweep-core.json": build_sweep_core(),
        "91-sweep-zero.json": wrapper("90000000-0000-4000-8000-000000000901", "DISPOSABLE ONLY · Sweep zero messages", sweep_input("zero"), SWEEP_FIXTURE_ID),
        "92-sweep-101.json": wrapper("90000000-0000-4000-8000-000000000902", "DISPOSABLE ONLY · Sweep 101 messages", sweep_input("one-hundred-one"), SWEEP_FIXTURE_ID),
        "93-sweep-late-order.json": wrapper("90000000-0000-4000-8000-000000000903", "DISPOSABLE ONLY · Sweep late out of order", sweep_input("late-out-of-order"), SWEEP_FIXTURE_ID),
        "94-sweep-pagination-failure.json": wrapper("90000000-0000-4000-8000-000000000904", "DISPOSABLE ONLY · Sweep pagination failure", sweep_input("pagination-failure"), SWEEP_FIXTURE_ID),
        "95-lease-acquire-a.json": build_lease_wrapper("90000000-0000-4000-8000-000000000905", "n8n:fixture:concurrent:a"),
        "96-lease-acquire-b.json": build_lease_wrapper("90000000-0000-4000-8000-000000000906", "n8n:fixture:concurrent:b"),
        "97-lease-stale-assert.json": build_stale_lease_wrapper(),
        "98-ai-caller-model-rejected.json": build_ai_wrapper("90000000-0000-4000-8000-000000000908", "caller-model-rejected"),
        "99-ai-locked-field-rejected.json": build_ai_wrapper("90000000-0000-4000-8000-000000000909", "locked-field-rejected"),
        "100-ai-missing-policy-rejected.json": build_ai_wrapper("90000000-0000-4000-8000-000000000910", "missing-active-policy"),
        "106-ai-positive-luna.json": build_positive_ai_wrapper("90000000-0000-4000-8000-000000000911", "luna"),
        "107-ai-positive-sol-gated.json": build_positive_ai_wrapper("90000000-0000-4000-8000-000000000912", "sol"),
        "101-error-redaction.json": build_error_redaction_fixture(),
        "102-derived-recovery-core.json": build_recovery_core(),
        "103-recover-prepared.json": build_recovery_wrapper("90000000-0000-4000-8000-000000000918", "PREPARED"),
        "104-recover-actual-observed.json": build_recovery_wrapper("90000000-0000-4000-8000-000000000919", "ACTUAL_OBSERVED"),
        "105-recover-verified.json": build_recovery_wrapper("90000000-0000-4000-8000-000000000920", "VERIFIED"),
    }
    catalog = {workflow["id"]: workflow for workflow in workflows.values()}
    for workflow_id, filename in INLINE_SOURCE_FILES.items():
        workflow = read_json(PRODUCTION / filename)
        if workflow.get("id") != workflow_id:
            raise ValueError(f"inline workflow ID mismatch for {filename}")
        catalog[workflow_id] = workflow
    inlined = {
        name: inline_execute_workflows(workflow, catalog)
        for name, workflow in workflows.items()
    }
    for workflow in inlined.values():
        validate_inline_workflow(workflow)
    return inlined


def database_target_id(node: dict) -> str:
    parameters = node.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("source", "database") != "database":
        raise ValueError(f"ExecuteWorkflow node {node.get('name')} is not a database-ID call")
    selector = parameters.get("workflowId")
    if not isinstance(selector, dict) or not isinstance(selector.get("value"), str):
        raise TypeError(f"ExecuteWorkflow node {node.get('name')} has an invalid workflow ID")
    target_id = selector["value"]
    if not target_id:
        raise ValueError(f"ExecuteWorkflow node {node.get('name')} has an empty workflow ID")
    return target_id


def inline_execute_workflows(
    workflow: dict,
    catalog: dict[str, dict],
    allowed_edges: dict[str, frozenset[str]] = ALLOWED_INLINE_EDGES,
    ancestors: tuple[str, ...] = (),
) -> dict:
    if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), str):
        raise TypeError("inline workflow must be an object with a string ID")
    workflow_id = workflow["id"]
    if workflow_id in ancestors:
        raise ValueError("inline workflow cycle: " + " -> ".join((*ancestors, workflow_id)))
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not isinstance(workflow.get("connections"), dict):
        raise TypeError(f"inline workflow {workflow_id} is malformed")

    result = copy.deepcopy(workflow)
    path = (*ancestors, workflow_id)
    for node in result["nodes"]:
        if not isinstance(node, dict):
            raise TypeError(f"inline workflow {workflow_id} has a malformed node")
        if node.get("type") != "n8n-nodes-base.executeWorkflow":
            continue
        target_id = database_target_id(node)
        if target_id not in allowed_edges.get(workflow_id, frozenset()):
            raise ValueError(f"inline edge {workflow_id} -> {target_id} is not allowlisted")
        target = catalog.get(target_id)
        if target is None:
            raise ValueError(f"inline target {target_id} is unknown")
        child = inline_execute_workflows(target, catalog, allowed_edges, path)
        options = node["parameters"].get("options", {})
        node["parameters"] = {
            "source": "parameter",
            "workflowJson": canonical(child),
            "options": options,
        }
    return result


def validate_inline_workflow(
    workflow: dict,
    allowed_edges: dict[str, frozenset[str]] = ALLOWED_INLINE_EDGES,
    ancestors: tuple[str, ...] = (),
) -> None:
    if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), str):
        raise TypeError("inline workflow must be an object with a string ID")
    workflow_id = workflow["id"]
    if workflow_id in ancestors:
        raise ValueError("inline workflow cycle: " + " -> ".join((*ancestors, workflow_id)))
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not isinstance(workflow.get("connections"), dict):
        raise TypeError(f"inline workflow {workflow_id} is malformed")

    path = (*ancestors, workflow_id)
    for node in nodes:
        if not isinstance(node, dict):
            raise TypeError(f"inline workflow {workflow_id} has a malformed node")
        if node.get("type") != "n8n-nodes-base.executeWorkflow":
            continue
        parameters = node.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("source") != "parameter":
            raise ValueError(f"residual database-ID ExecuteWorkflow in {workflow_id}")
        if "workflowId" in parameters:
            raise ValueError(f"residual workflow ID in {workflow_id}")
        workflow_json = parameters.get("workflowJson")
        if not isinstance(workflow_json, str):
            raise TypeError(f"inline workflow JSON in {workflow_id} is malformed")
        try:
            child = json.loads(workflow_json)
        except json.JSONDecodeError as error:
            raise ValueError(f"inline workflow JSON in {workflow_id} is malformed") from error
        if not isinstance(child, dict) or not isinstance(child.get("id"), str):
            raise TypeError(f"inline workflow JSON in {workflow_id} is malformed")
        target_id = child["id"]
        if target_id not in allowed_edges.get(workflow_id, frozenset()):
            raise ValueError(f"inline edge {workflow_id} -> {target_id} is not allowlisted")
        if workflow_json != canonical(child):
            raise ValueError(f"inline workflow JSON in {workflow_id} is not canonical")
        validate_inline_workflow(child, allowed_edges, path)


def build_manifest(workflows: dict[str, dict], rendered: dict[str, str]) -> dict:
    source_hashes = {
        name: hashlib.sha256(normalized_text_bytes(PRODUCTION / name)).hexdigest()
        for name in (
            "09-ai-proposal.json",
            "12-outlook-message-sweep.json",
            "16-operations-error-handler.json",
            "17-actual-outbox-recovery.json",
            "18-finance-writer-lease.json",
        )
    }
    scenario_contract = {
            "sweep_zero": {"workflow_id": "90000000-0000-4000-8000-000000000901", "expected_exit": 0, "expected": {"scanned_count": 0, "heartbeat": True}},
            "sweep_one_no_attachments": {"workflow_id": "90000000-0000-4000-8000-000000000012", "expected_exit": 0, "expected": {"scanned_count": 1, "matched_count": 1, "attachment_identity_keys": []}},
            "sweep_101": {"workflow_id": "90000000-0000-4000-8000-000000000902", "expected_exit": 0, "expected": {"scanned_count": 101, "matched_count": 101, "attachment_identity_keys": []}},
            "sweep_late_order": {"workflow_id": "90000000-0000-4000-8000-000000000903", "expected_exit": 0, "expected_ids": ["m1", "m2", "m3"]},
            "sweep_pagination_failure": {"workflow_id": "90000000-0000-4000-8000-000000000904", "expected_exit": "nonzero"},
            "lease_concurrency": {"workflow_ids": ["90000000-0000-4000-8000-000000000905", "90000000-0000-4000-8000-000000000906"], "run_concurrently": True, "expected_successes": 1},
            "lease_stale": {"workflow_id": "90000000-0000-4000-8000-000000000907", "expected_exit": "nonzero", "expected_error": "WRITER_LEASE_STALE"},
            "ai_negative": {"workflow_ids": ["90000000-0000-4000-8000-000000000908", "90000000-0000-4000-8000-000000000909", "90000000-0000-4000-8000-000000000910"], "expected_exit": "nonzero", "runner_calls": 0},
            "ai_positive_luna": {"workflow_id": "90000000-0000-4000-8000-000000000911", "expected_exit": 0, "policy_id": "classify-unresolved", "expected_model": "gpt-5.6-luna", "expected_reasoning_effort": "max", "expected_auth_mode": "CHATGPT_SUBSCRIPTION", "finance_writes": 0},
            "ai_positive_sol_gated": {"workflow_id": "90000000-0000-4000-8000-000000000912", "expected_exit": 0, "policy_id": "recommend-category", "expected_model": "gpt-5.6-sol", "expected_reasoning_effort": "medium", "expected_auth_mode": "CHATGPT_SUBSCRIPTION", "finance_writes": 0, "execution_gate": "DISPOSABLE_ALLOW_SOL_MEDIUM", "default_execution_forbidden": True},
            "error_redaction": {"workflow_id": "90000000-0000-4000-8000-000000000916", "expected_exit": 0, "receipt_table": "finance_execution_failures", "forbidden_readback": ["DontLeak", "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890", "4111111111111111"]},
            "outbox_recovery": {"workflow_ids": ["90000000-0000-4000-8000-000000000918", "90000000-0000-4000-8000-000000000919", "90000000-0000-4000-8000-000000000920"], "expected_exit": 0, "expected_state": "COMMITTED", "finance_writes": 0},
        }
    fixture_workflow_ids = {workflow["id"] for workflow in workflows.values()}
    scenario_workflow_ids: set[str] = set()
    for scenario in scenario_contract.values():
        workflow_id = scenario.get("workflow_id")
        if isinstance(workflow_id, str):
            scenario_workflow_ids.add(workflow_id)
        workflow_ids = scenario.get("workflow_ids")
        if isinstance(workflow_ids, list):
            scenario_workflow_ids.update(
                workflow_id for workflow_id in workflow_ids if isinstance(workflow_id, str)
            )
    missing_workflow_ids = sorted(scenario_workflow_ids - fixture_workflow_ids)
    if missing_workflow_ids:
        raise ValueError(
            "scenario workflow IDs are missing from fixture manifest: "
            + ", ".join(missing_workflow_ids)
        )
    return {
        "schema_version": 1,
        "contract_status": "DISPOSABLE_ONLY",
        "production_import_forbidden": True,
        "required_acknowledgement": "DISPOSABLE_ONLY",
        "source_workflow_sha256": source_hashes,
        "workflows": [
            {
                "file": name,
                "id": workflow["id"],
                "sha256": hashlib.sha256(rendered[name].encode()).hexdigest(),
            }
            for name, workflow in workflows.items()
        ],
        "scenario_contract": scenario_contract,
        "blocked_runtime_scenarios": {
            "bounded_mcp_network_negative": "Facade remains unpublished/inactive; an MCP transport test would require disposable publication and is outside the activation-disabled harness.",
            "real_actual_recovery_write": "Forbidden in disposable fixtures; custom-node unit tests cover mutation guards and exact readback while derived recovery uses no-op external replacements.",
        },
        "warning": "Derived fixtures are runtime evidence for deterministic orchestration branches only, not evidence that external providers or Actual were called.",
    }


def render() -> tuple[dict[str, str], str]:
    workflows = build_all()
    rendered = {
        name: json.dumps(workflow, indent=2, ensure_ascii=False) + "\n"
        for name, workflow in workflows.items()
    }
    manifest = build_manifest(workflows, rendered)
    return rendered, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    rendered, manifest_text = render()
    if args.write:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for stale in OUTPUT.glob("*.json"):
            if stale.name not in rendered:
                stale.unlink()
        for name, text in rendered.items():
            (OUTPUT / name).write_bytes(text.encode("utf-8"))
        MANIFEST.write_bytes(manifest_text.encode("utf-8"))
        return 0
    drift = []
    for name, text in rendered.items():
        path = OUTPUT / name
        if not path.exists() or path.read_bytes() != text.encode("utf-8"):
            drift.append(path.relative_to(ROOT).as_posix())
    if not MANIFEST.exists() or MANIFEST.read_bytes() != manifest_text.encode("utf-8"):
        drift.append(MANIFEST.relative_to(ROOT).as_posix())
    if drift:
        print("disposable fixture drift: " + ", ".join(drift))
        return 1
    print(f"disposable fixtures current: {len(rendered)} workflows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
