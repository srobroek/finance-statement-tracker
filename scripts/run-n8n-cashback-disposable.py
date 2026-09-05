#!/usr/bin/env python3
"""Run source-derived W02 against fresh n8n and Cashback, including restart replay.

No host ports, shared production networks, provider credentials or production
volumes are used. Mail enumeration alone is replaced by the checked RAK fixture.
The real service classifies, stores, commits its cursor and calculates buckets.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ID = "90000000-0000-4000-8000-000000002002"
TOKEN = "disposable-no-production-authority"


def derive_workflow(root: Path, day: datetime) -> dict:
    source = root / "integrations/n8n/workflows/02-rakbank-live-cashback.json"
    raw = source.read_bytes()
    workflow = json.loads(raw)
    if workflow.get("active") or workflow["id"] != "10000000-0000-4000-8000-000000000002":
        raise ValueError("Unexpected production W02 identity/state")
    message = json.loads((root / "tests/fixtures/rakbank-card-transaction.json").read_text())
    message["id"] = "disposable-w02-rak-amazon-1"
    message["receivedDateTime"] = day.isoformat().replace("+00:00", "Z")
    message["bodyPreview"] = message["bodyPreview"].replace("on 17/08.", f"on {day:%d/%m}.")
    original_types = {}
    for node in workflow["nodes"]:
        name = node["name"]
        original_types[name] = node["type"]
        if node["type"] == "n8n-nodes-base.scheduleTrigger":
            node.update(type="n8n-nodes-base.webhook", typeVersion=2, webhookId=WORKFLOW_ID,
                        parameters={"httpMethod": "POST", "path": "disposable-w02-live-cashback",
                                    "responseMode": "lastNode", "options": {}})
        elif name in {"Load Trusted Mail Contract", "Assemble Trusted Sweep Contract"}:
            node.update(type="n8n-nodes-base.code", typeVersion=2,
                        parameters={"jsCode": "return $input.all();"})
            node.pop("credentials", None)
        elif name == "Sweep Exact Outlook Messages":
            node.update(type="n8n-nodes-base.code", typeVersion=2, parameters={"jsCode":
                "const window = $('Freeze Cursor Minus Overlap Window').first().json;\n"
                f"const messages = {json.dumps([message])};\n"
                "return [{json:{...window,messages,pagination_exhausted:true,scanned_count:1,matched_count:1}}];"})
            node.pop("credentials", None)
        elif node["type"] == "n8n-nodes-base.httpRequest":
            url = node["parameters"]["url"]
            if not url.startswith("http://cashback:5010/api/"):
                raise ValueError("Unexpected W02 remote boundary")
            node["parameters"]["url"] = url.replace("http://cashback:5010", "http://127.0.0.1:5010")
            node["credentials"] = {"httpHeaderAuth": {"id": "DISPOSABLE_CASHBACK_ONLY", "name": "DISPOSABLE Cashback"}}
    required = {"Load Trusted Mail Contract", "Assemble Trusted Sweep Contract", "Sweep Exact Outlook Messages"}
    if not required <= original_types.keys():
        raise ValueError("W02 mailbox boundary changed")
    workflow.update(id=WORKFLOW_ID, name="DISPOSABLE ONLY · Real W02 Cashback Replay", active=False)
    workflow.setdefault("settings", {}).pop("errorWorkflow", None)
    workflow["meta"] = {"disposableOnly": True, "productionImportForbidden": True,
                        "derivedFrom": source.name, "sourceContentSha256": hashlib.sha256(raw).hexdigest(),
                        "unprovenBoundaries": ["Real Outlook acquisition", "OneDrive archive barrier", "Monthly statement ingestion"],
                        "replacedBoundaries": ["Schedule to isolated webhook", "Mailbox contract and enumeration to checked fixture"]}
    return workflow


READBACK = r'''
import json, os, sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from finance_tracker.cashback_events import CashbackEventStore, build_live_dashboard
p = os.environ['CASHBACK_DB_PATH']
c = sqlite3.connect(p); c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute('SELECT source_event_id,card_code,amount_aed_minor,purchase_type,merchant,bucket_code,status,decision_trace_json FROM cashback_events')]
assert len(rows) == 1, 'duplicate or missing event'
r = rows[0]
assert r['source_event_id'] == 'disposable-w02-rak-amazon-1:0'
assert r['amount_aed_minor'] == 4149 and r['card_code'] == 'RAK_WORLD'
assert r['merchant'] == 'Amazon' and r['purchase_type'] == 'AMAZON'
assert r['bucket_code'] == 'RAK_STANDARD' and r['status'] == 'ACTIVE'
assert json.loads(r['decision_trace_json']), 'classification trace absent'
d = build_live_dashboard(CashbackEventStore(Path(p)), date.fromisoformat(os.environ['DISPOSABLE_AS_OF']))
card = next(x for x in d['cards'] if x['card'] == 'RAK_WORLD')
assert Decimal(str(card['total_spend_aed'])) == Decimal('41.49'), 'bucket spend did not match event'
bucket = next(x for x in card['buckets'] if x['code'] == 'RAK_STANDARD')
assert Decimal(str(bucket['spend_aed'])) == Decimal('41.49'), 'classified bucket did not receive the event'
assert Decimal(str(bucket['headroom_aed'])) == Decimal(str(bucket['spend_cap_aed'])) - Decimal('41.49')
assert all(Decimal(str(x['spend_aed'])) == 0 for x in card['buckets'] if x['code'] != 'RAK_STANDARD'), 'event leaked into another bucket'
print(json.dumps({'events':1,'card':'RAK_WORLD','purchase_type':'AMAZON','bucket':'RAK_STANDARD','spend_aed':str(card['total_spend_aed']),'classification_trace_present':True,'buckets':card['buckets']}))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("docker", "podman"), default="docker")
    parser.add_argument("--n8n-image", required=True)
    parser.add_argument("--cashback-image", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    for image in (args.n8n_image, args.cashback_image):
        if not re.fullmatch(r"[a-z0-9./_-]+@sha256:[a-f0-9]{64}", image):
            parser.error("Images must be immutable digest references")
    day = datetime.now(timezone.utc)
    workflow = derive_workflow(ROOT, day)
    if args.validate_only:
        print("Source-derived W02 fixture validated; no runtime proof claimed")
        return 0
    if os.environ.get("DISPOSABLE_ONLY_ACK") != "DISPOSABLE_ONLY":
        parser.error("Set DISPOSABLE_ONLY_ACK=DISPOSABLE_ONLY")
    prefix = "finance-disposable-w02-" + uuid.uuid4().hex[:12]
    companion, runner = prefix + "-cashback", prefix + "-n8n"
    state, data = prefix + "-n8n-state", prefix + "-cashback-data"

    def run(*command: str, stdin: str | None = None, timeout: int = 180) -> str:
        result = subprocess.run(command, input=stdin, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
        if result.returncode:
            # These isolated containers have only a test token, but avoid dumping
            # execution payloads or environment values as a general contract.
            raise RuntimeError(f"Disposable command failed ({result.returncode}): {command[:3]}\n{result.stderr[-1500:]}")
        return result.stdout

    def engine(*command: str, **kwargs) -> str:
        return run(args.engine, *command, **kwargs)

    receipts = []
    try:
        for volume in (state, data):
            engine("volume", "create", "--label", "finance.disposable=true", volume)
        engine("run", "-d", "--name", companion, "--network", "none",
               "--label", "finance.disposable=true", "--volume", f"{data}:/var/lib/cashback-control",
               "--env", "CASHBACK_HOST=127.0.0.1", "--env", "CASHBACK_PUBLIC_URL=http://127.0.0.1:5010",
               "--env", "CASHBACK_REFRESH_SECONDS=0", "--env", f"CASHBACK_INGEST_TOKEN={TOKEN}",
               "--env", f"DISPOSABLE_AS_OF={day.date().isoformat()}", args.cashback_image)
        common = ["--name", runner, "--network", "container:" + companion,
                  "--label", "finance.disposable=true", "--volume", f"{state}:/home/node/.n8n",
                  "--env", "N8N_ENCRYPTION_KEY=disposable-runtime-only-key-000001",
                  "--env", "N8N_DIAGNOSTICS_ENABLED=false", "--env", "N8N_VERSION_NOTIFICATIONS_ENABLED=false",
                  "--env", "N8N_RUNNERS_ENABLED=false"]
        engine("run", "-d", *common, "--entrypoint", "sleep", args.n8n_image, "infinity")
        with tempfile.TemporaryDirectory(prefix=prefix) as temp:
            directory = Path(temp)
            for name, value in (("workflow.json", workflow), ("credential.json", [{
                "id": "DISPOSABLE_CASHBACK_ONLY", "name": "DISPOSABLE Cashback", "type": "httpHeaderAuth",
                "data": {"name": "Authorization", "value": "Bearer " + TOKEN}}])):
                path = directory / name
                path.write_text(json.dumps(value)); path.chmod(0o644)
                engine("cp", str(path), f"{runner}:/tmp/{name}")
            engine("exec", runner, "n8n", "import:credentials", "--input", "/tmp/credential.json")
            engine("exec", runner, "n8n", "import:workflow", "--input", "/tmp/workflow.json")
            engine("exec", runner, "n8n", "publish:workflow", "--id", WORKFLOW_ID)
        engine("rm", "-f", runner)
        engine("run", "-d", *common, args.n8n_image, "start")
        execute = r'''
const pause = ms => new Promise(r => setTimeout(r, ms));
let ready = false;
for(let i=0;i<180;i++) {
  try {const r=await fetch('http://127.0.0.1:5678/healthz/readiness'); if(r.ok&&(await r.json()).status==='ok'){ready=true;break;}}catch{}
  await pause(500);
}
if(!ready) throw Error('n8n readiness timeout');
ready = false;
for(let i=0;i<120;i++) {
  try {const r=await fetch('http://127.0.0.1:5010/api/health',{headers:{Authorization:'Bearer disposable-no-production-authority'}}); if(r.ok){ready=true;break;}}catch{}
  await pause(500);
}
if(!ready) throw Error('Cashback readiness timeout');
let response;
for(let i=0;i<120;i++) {
 response=await fetch('http://127.0.0.1:5678/webhook/disposable-w02-live-cashback',{method:'POST',headers:{'content-type':'application/json'},body:'{}'});
 if(response.status!==404) break;
 await pause(500);
}
if(!response.ok) throw Error('W02 failed HTTP '+response.status+' '+await response.text());
let r=await response.json(); if(Array.isArray(r)) r=r[0];
if(r.status!=='SUCCESS_VERIFIED'||r.scanned_count!==1||r.accepted_count!==1) throw Error('W02 terminal receipt failed');
console.log(JSON.stringify(r));
'''
        for attempt in range(2):
            terminal = json.loads(engine("exec", "-i", runner, "node", "--input-type=module", stdin=execute))
            observed = json.loads(engine("exec", "-i", companion, "python", "-", stdin=READBACK))
            receipts.append({"terminal": terminal, "readback": observed})
        if receipts[0]["readback"] != receipts[1]["readback"]:
            raise RuntimeError("Replay changed stored classification or bucket state")
        # Restart both runtimes before the third replay: in-memory dedup is insufficient.
        engine("restart", companion)
        engine("restart", runner)
        terminal = json.loads(engine("exec", "-i", runner, "node", "--input-type=module", stdin=execute))
        observed = json.loads(engine("exec", "-i", companion, "python", "-", stdin=READBACK))
        if observed != receipts[0]["readback"]:
            raise RuntimeError("Restart changed stored classification or bucket state")
        receipts.append({"terminal": terminal, "readback": observed})
        result = {"schema_version": "n8n-cashback-runtime-proof-v1", "status": "PASS",
                  "source_sha256": workflow["meta"]["sourceContentSha256"],
                  "n8n_image": args.n8n_image, "cashback_image": args.cashback_image,
                  "replays": receipts, "production_data_touched": False,
                  "unproven": workflow["meta"]["unprovenBoundaries"]}
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(result, indent=2) + "\n")
        print("PASS: real W02 HTTP ingestion, classification, stored buckets, cursor, double replay and restart")
        return 0
    finally:
        for container in (runner, companion):
            subprocess.run([args.engine, "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for volume in (state, data):
            subprocess.run([args.engine, "volume", "rm", volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
