"""Generate deterministic, review-only FAB/Sarwa/ADCB account proposals."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_tracker.account_completeness import load_account_completeness_manifest  # noqa: E402
from finance_tracker.account_proposals import (  # noqa: E402
    build_adcb_closed_zero_assertion,
    build_fab_inventory_proposal,
    build_sarwa_account_proposal,
    build_sarwa_position_sidecar,
)
from finance_tracker.wealth import parse_registered_wealth_capture  # noqa: E402


FAB_INVENTORY = ROOT / "config" / "evidence" / "browser-captures" / "fab-non-credit-inventory-2026-08-19.json"
SARWA_CAPTURE = ROOT / "runtime" / "browser-captures" / "sarwa-holdings-2026-08-18.json"
COMPLETENESS = ROOT / "config" / "account-completeness.json"
WEALTH_CONFIG = ROOT / "config" / "wealth-sources.json"
PROPOSAL = ROOT / "config" / "proposals" / "actual-accounts-fab-sarwa.json"
SIDECAR = ROOT / "config" / "proposals" / "sarwa-position-sidecar.json"
ADCB_STATUS = ROOT / "config" / "evidence" / "adcb-closed-card-status-2026-08-19.json"


def _parse_timestamp(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build(evaluated_at: datetime) -> tuple[dict, dict]:
    manifest = load_account_completeness_manifest(COMPLETENESS)
    adcb_account = next(
        row for row in manifest.accounts
        if row.provider_account_id == "adcb:credit:8833-6838"
    )
    fab_inventory = json.loads(FAB_INVENTORY.read_text(encoding="utf-8"))
    adcb_status = json.loads(ADCB_STATUS.read_text(encoding="utf-8"))
    wealth = parse_registered_wealth_capture(
        "sarwa",
        "holdings",
        SARWA_CAPTURE,
        WEALTH_CONFIG,
        adapters_root=ROOT / "browser_adapters",
    )
    fab = build_fab_inventory_proposal(
        fab_inventory,
        manifest,
        evaluated_at=evaluated_at,
    )
    sarwa = build_sarwa_account_proposal(wealth, None, evaluated_at=evaluated_at)
    issuer_statement = adcb_status["issuer_statement"]
    adcb = build_adcb_closed_zero_assertion(
        adcb_account,
        issuer_closing_balance_minor=int(issuer_statement["closing_balance_minor"]),
        issuer_evidence_id=str(issuer_statement["evidence_id"]),
    )
    blockers = list(dict.fromkeys(fab["blockers"] + sarwa["blockers"] + adcb["blockers"]))
    proposal = {
        "schema_version": 2,
        "mode": "PROPOSAL_ONLY",
        "actual_writes_allowed": False,
        "status": "BLOCKED" if blockers else "READY_FOR_REVIEW",
        "evaluated_at": evaluated_at.isoformat(),
        "generated_from": [
            "config/account-completeness.json",
            "config/evidence/browser-captures/fab-non-credit-inventory-2026-08-19.json",
            "config/evidence/adcb-closed-card-status-2026-08-19.json",
            "runtime/browser-captures/sarwa-holdings-2026-08-18.json",
        ],
        "blockers": blockers,
        "fab": fab,
        "sarwa": sarwa,
        "adcb": adcb,
    }
    return proposal, build_sarwa_position_sidecar(wealth, evaluated_at=evaluated_at)


def _render(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    proposal, sidecar = build(_parse_timestamp(args.evaluated_at))
    expected = {PROPOSAL: _render(proposal), SIDECAR: _render(sidecar)}
    if args.check:
        stale = []
        for path, content in expected.items():
            if not path.exists():
                stale.append(str(path.relative_to(ROOT)))
                continue
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                stale.append(str(path.relative_to(ROOT)))
                continue
            if current != json.loads(content):
                stale.append(str(path.relative_to(ROOT)))
        if stale:
            print(json.dumps({"status": "STALE", "files": stale}, indent=2))
            return 1
        print(json.dumps({"status": "CURRENT", "files": [str(p.relative_to(ROOT)) for p in expected]}, indent=2))
        return 0
    for path, content in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
