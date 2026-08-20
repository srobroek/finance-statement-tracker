"""Fail-closed verifier for the machine-readable project acceptance ledger."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "config" / "project-acceptance.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
VERIFIED_STATES = {"VERIFIED", "PRODUCTION"}


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(payload: dict, requirement_id: str | None = None) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 2:
        errors.append("ledger schema_version must be 2")
    rows = payload.get("requirements")
    if not isinstance(rows, list) or not rows:
        return errors + ["requirements must be a non-empty array"]

    identities = [row.get("id") for row in rows]
    if len(identities) != len(set(identities)):
        errors.append("requirement identifiers must be unique")
    known = set(identities)
    selected = [row for row in rows if requirement_id is None or row.get("id") == requirement_id]
    if requirement_id is not None and not selected:
        errors.append(f"unknown requirement: {requirement_id}")

    now = datetime.now(timezone.utc)
    for row in selected:
        identity = row.get("id", "<missing>")
        if not row.get("invariant"):
            errors.append(f"{identity}: invariant is missing")
        verifier = row.get("verifier", {})
        if not verifier.get("command") or not verifier.get("expected"):
            errors.append(f"{identity}: verifier command/expected is missing")
        unknown_dependencies = set(row.get("dependencies", [])) - known
        if unknown_dependencies:
            errors.append(f"{identity}: unknown dependencies {sorted(unknown_dependencies)}")

        evidence = row.get("evidence", [])
        if row.get("status") in VERIFIED_STATES and not evidence:
            errors.append(f"{identity}: verified state has no evidence")
        if row.get("status") not in VERIFIED_STATES | {"SUPERSEDED"} and not row.get("blockers"):
            errors.append(f"{identity}: unverified state has no blockers")

        for item in evidence:
            if not SHA256.fullmatch(item.get("sha256", "")):
                errors.append(f"{identity}: invalid evidence sha256")
            if not GIT_SHA.fullmatch(item.get("git_commit", "")):
                errors.append(f"{identity}: invalid evidence git commit")
            if not item.get("non_empty"):
                errors.append(f"{identity}: evidence is not marked non-empty")
            expires_at = item.get("expires_at")
            if expires_at and _date(expires_at) <= now:
                errors.append(f"{identity}: evidence expired at {expires_at}")

        if requirement_id is not None and row.get("status") not in VERIFIED_STATES:
            errors.append(f"{identity}: status is {row.get('status')}, not verified")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require", dest="requirement_id")
    args = parser.parse_args()
    payload = json.loads(LEDGER.read_text(encoding="utf-8"))
    errors = validate(payload, args.requirement_id)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"acceptance ledger valid: {len(payload['requirements'])} requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
