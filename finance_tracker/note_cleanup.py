from __future__ import annotations

import argparse
import json
from pathlib import Path

from .actual_notes import build_actual_note_cleanup_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a guarded Actual note cleanup plan")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("audit", type=Path)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    plan, audit = build_actual_note_cleanup_plan(snapshot)
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.plan.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    args.audit.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
