"""Validate the redacted ProDex promotion and restart receipt contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "integrations/n8n/schemas/prodex-promotion-restart-receipt-v1.schema.json"


class ReceiptValidationError(ValueError):
    """Raised when a promotion receipt fails schema or identity checks."""


def _schema_errors(document: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    return [
        error.message
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=str,
        )
    ]


def _cross_field_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    source_commit = document["source"]["finance_commit"]
    identities = {
        name: document["image"][name]
        for name in ("candidate", "before", "after")
    }
    for name, identity in identities.items():
        reference_digest = identity["reference"].rsplit("@", 1)[-1]
        if identity["digest"] != reference_digest:
            errors.append(f"{name}: reference digest mismatch")
    if identities["candidate"] != identities["after"]:
        errors.append("candidate and after image identities differ")
    if identities["after"]["source_commit"] != source_commit:
        errors.append("after image source commit mismatch")
    protected = document["protected_state"]
    if protected["equal"] and protected["before_sha256"] != protected["after_sha256"]:
        errors.append("protected state fingerprint mismatch")
    return errors


def validate_receipt(
    document: dict[str, Any],
    *,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate receipt shape and the identities represented by its hashes."""

    active_schema = schema or json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_errors = _schema_errors(document, active_schema)
    if schema_errors:
        raise ReceiptValidationError("PRODEX_RECEIPT_SCHEMA_INVALID:" + ";".join(schema_errors))
    cross_field_errors = _cross_field_errors(document)
    if cross_field_errors:
        raise ReceiptValidationError(
            "PRODEX_RECEIPT_CROSS_FIELD_INVALID:" + ";".join(cross_field_errors)
        )
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.receipt.read_text(encoding="utf-8"))
        validate_receipt(document)
    except (OSError, json.JSONDecodeError, ReceiptValidationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("ProDex promotion receipt is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
