"""Verify a read-only wealth/account evidence bundle and emit a JSON report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_tracker.wealth_acceptance import (  # noqa: E402
    REQUIREMENTS,
    load_wealth_acceptance_bundle,
    validate_wealth_acceptance_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed, read-only wealth/account acceptance verifier"
    )
    parser.add_argument("--bundle", required=True, help="Evidence-bundle JSON path")
    parser.add_argument("--require", choices=REQUIREMENTS)
    args = parser.parse_args()
    bundle_path = Path(args.bundle)
    if not bundle_path.is_absolute():
        bundle_path = ROOT / bundle_path
    try:
        payload = load_wealth_acceptance_bundle(bundle_path)
        report = validate_wealth_acceptance_bundle(
            payload,
            base_dir=bundle_path.parent,
            requirement_id=args.require,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "schema_version": 1,
            "mode": "READ_ONLY_ACCEPTANCE",
            "status": "BLOCKED",
            "production_write_allowed": False,
            "issues": [{"code": "EVIDENCE_BUNDLE_UNAVAILABLE", "message": str(exc)}],
        }, indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
