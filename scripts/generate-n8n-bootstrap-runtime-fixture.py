#!/usr/bin/env python3
"""Derive a disposable webhook fixture from the W19 bootstrap workflow."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "integrations" / "n8n" / "workflows" / "19-platform-data-table-bootstrap.json"
DEFAULT_OUTPUT = ROOT / "runtime" / "n8n-disposable" / "19-bootstrap-webhook.json"
FIXTURE_ID = "90000000-0000-4000-8000-000000001019"
TRIGGER_NAME = "Manual Platform Bootstrap Only"
WEBHOOK_PATH = "disposable-w19-bootstrap"


def derive(source: dict, source_sha256: str) -> dict:
    """Clone W19 and replace only its manual boundary with a test webhook."""
    if source.get("id") != "10000000-0000-4000-8000-000000000019":
        raise RuntimeError("unexpected W19 workflow identity")
    meta = source.get("meta") or {}
    if not meta.get("financeLedgerMutationForbidden") or not meta.get("actualMutationForbidden"):
        raise RuntimeError("W19 does not forbid finance-ledger and Actual mutations")
    if source.get("active"):
        raise RuntimeError("source W19 must remain inactive")

    fixture = copy.deepcopy(source)
    triggers = [node for node in fixture.get("nodes", []) if node.get("name") == TRIGGER_NAME]
    if len(triggers) != 1 or triggers[0].get("type") != "n8n-nodes-base.manualTrigger":
        raise RuntimeError("W19 manual trigger boundary changed")
    if any(node.get("credentials") for node in fixture.get("nodes", [])):
        raise RuntimeError("W19 runtime fixture may not contain credentials")
    allowed_types = {
        "n8n-nodes-base.code",
        "n8n-nodes-base.crypto",
        "n8n-nodes-base.dataTable",
        "n8n-nodes-base.manualTrigger",
    }
    unexpected = sorted(
        {node.get("type", "") for node in fixture.get("nodes", [])} - allowed_types
    )
    if unexpected:
        raise RuntimeError(f"W19 contains unexpected node types: {unexpected}")

    trigger = triggers[0]
    trigger.update(
        {
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "webhookId": FIXTURE_ID,
            "parameters": {
                "httpMethod": "POST",
                "path": WEBHOOK_PATH,
                "responseMode": "lastNode",
                "options": {},
            },
        }
    )
    fixture["id"] = FIXTURE_ID
    fixture["name"] = "DISPOSABLE ONLY · W19 Platform Bootstrap Runtime"
    fixture["active"] = False
    fixture.setdefault("settings", {}).pop("errorWorkflow", None)
    fixture["meta"] = {
        **meta,
        "disposableOnly": True,
        "productionImportForbidden": True,
        "derivedFrom": "19-platform-data-table-bootstrap.json",
        "sourceContentSha256": source_sha256,
        "externalBoundaryReplacement": "manual trigger replaced by isolated test webhook",
    }
    return fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source_bytes = args.source.read_bytes()
    source = json.loads(source_bytes)
    fixture = derive(source, hashlib.sha256(source_bytes).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
