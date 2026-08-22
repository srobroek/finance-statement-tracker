"""Generate deterministic EI/Wio offline receipts for the pre-provider gate."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .real_mail_e2e import canonical_json, run_synthetic_e2e
except ImportError:  # pragma: no cover - exercised by direct script invocation
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from real_mail_e2e import canonical_json, run_synthetic_e2e


def generate_bundle(*, count: int = 1) -> dict:
    if count < 0 or count > 101:
        raise ValueError("count must be between 0 and 101")
    receipts = [
        run_synthetic_e2e(source_code=source_code, count=count)
        for source_code in ("EI_AMAZON", "WIO_CREDIT")
    ]
    return {
        "schema_version": "real-mail-e2e-fixture-bundle-v1",
        "contract_status": "SYNTHETIC_OFFLINE",
        "provider_proof": False,
        "receipts": receipts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = generate_bundle(count=args.count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(bundle) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
