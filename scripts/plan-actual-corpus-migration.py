from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_tracker.corpus_migration import (
    build_guarded_migration_plan,
    regenerate_corpus,
    validate_guarded_migration_plan,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate and audit Actual corpus without writing Actual")
    parser.add_argument("manifest_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--migration-audit", type=Path)
    args = parser.parse_args()
    report, desired = regenerate_corpus(args.manifest_root, args.output_root)
    _write(args.report, report)
    if args.snapshot:
        if not args.plan or not args.migration_audit:
            parser.error("--snapshot requires --plan and --migration-audit")
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
        plan, audit = build_guarded_migration_plan(snapshot, desired)
        audit["preflight"] = validate_guarded_migration_plan(snapshot, plan)
        _write(args.plan, plan)
        _write(args.migration_audit, audit)
    print(json.dumps({
        "manifest_count": report["manifest_count"],
        "transaction_count": report["transaction_count"],
        "exception_count": report["exception_count"],
        "actual_write_performed": False,
    }, indent=2))


if __name__ == "__main__":
    main()
