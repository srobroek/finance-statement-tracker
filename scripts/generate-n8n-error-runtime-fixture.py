#!/usr/bin/env python3
"""Derive isolated server fixtures that verify W16 durability and real errors."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime/n8n-disposable"
CREDENTIAL = {"id": "DISPOSABLE_FAILURE_HEADER", "name": "DISPOSABLE Failure Header"}
TOKEN = "disposable-error-test-no-production-authority"
ERROR_ID = "90000000-0000-4000-8000-000000001016"
THROW_ID = "90000000-0000-4000-8000-000000001017"


def webhook(node: dict, identity: str, route: str) -> None:
    node.update(type="n8n-nodes-base.webhook", typeVersion=2, webhookId=identity,
                parameters={"httpMethod": "POST", "path": route, "responseMode": "lastNode", "authentication": "headerAuth", "options": {}},
                credentials={"httpHeaderAuth": CREDENTIAL})


def write(name: str, workflow: dict) -> None:
    workflow["active"] = False
    workflow.setdefault("settings", {}).update(saveDataSuccessExecution="none", saveDataErrorExecution="none")
    workflow.setdefault("meta", {}).update(disposableOnly=True, productionImportForbidden=True)
    (OUT / name).write_text(json.dumps(workflow, indent=2) + "\n")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = ROOT / "integrations/n8n/workflows/16-operations-error-handler.json"
    error = json.loads(source.read_text())
    if error["active"] or error["id"] != "10000000-0000-4000-8000-000000000016":
        raise ValueError("Unexpected production W16 identity/state")
    if error["meta"].get("failureReceiptTable") != "finance_execution_failures":
        raise ValueError("W16 must use durable native failure receipts")
    error.update(id=ERROR_ID, name="DISPOSABLE ONLY · Real Error Trigger")
    error["meta"]["sourceContentSha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    write("16-real-error-handler.json", error)
    for source_name, output_name, route in [
        ("101-error-redaction.json", "16-error-writer-webhook.json", "disposable-error-write"),
        ("108-error-persistence-readback.json", "16-error-reader-webhook.json", "disposable-error-read"),
    ]:
        workflow = json.loads((ROOT / "integrations/n8n/disposable/generated" / source_name).read_text())
        trigger = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.manualTrigger")
        webhook(trigger, workflow["id"], route)
        write(output_name, workflow)
    trigger = {"id": "real-error-webhook", "name": "Trigger Real Error", "position": [0, 0]}
    webhook(trigger, THROW_ID, "disposable-error-throw")
    fail = {"id": "throw-sensitive-fixture", "name": "Throw Sensitive Fixture", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [300, 0], "parameters": {"jsCode": "throw new Error('password=DontLeak token=ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 card=4111111111111111');"}}
    write("16-error-throw-webhook.json", {"id": THROW_ID, "name": "DISPOSABLE ONLY · Throw Real Error", "nodes": [trigger, fail], "connections": {trigger["name"]: {"main": [[{"node": fail["name"], "type": "main", "index": 0}]]}}, "settings": {"errorWorkflow": ERROR_ID}})
    reader = json.loads((OUT / "16-error-reader-webhook.json").read_text())
    reader["id"] = "90000000-0000-4000-8000-000000001018"
    hook = next(node for node in reader["nodes"] if node["type"] == "n8n-nodes-base.webhook")
    webhook(hook, reader["id"], "disposable-real-error-read")
    table = next(node for node in reader["nodes"] if node["type"] == "n8n-nodes-base.dataTable")
    table["parameters"]["filters"]["conditions"] = [{"keyName": "workflow_id", "condition": "eq", "keyValue": THROW_ID}]
    terminal = next(node for node in reader["nodes"] if node["name"] == "Verify Durable Failure Receipt")
    terminal["parameters"]["jsCode"] = "const rows=$input.all().map(item=>item.json).filter(row=>row.execution_id); if(rows.length!==1 || rows[0].readback_verified!==true) throw new Error('REAL_FAILURE_RECEIPT_NOT_READY'); return [{json:{...rows[0],terminal_receipt_sink:'finance_execution_failures'}}];"
    write("16-real-error-reader-webhook.json", reader)
    (OUT / "16-error-credential.json").write_text(json.dumps([{"id": CREDENTIAL["id"], "name": CREDENTIAL["name"], "type": "httpHeaderAuth", "data": {"name": "x-disposable-token", "value": TOKEN}}]) + "\n")
    print("Generated isolated authenticated W16 server fixtures")


if __name__ == "__main__":
    main()
