"""Generate the Actual account, completeness, and worker taxonomy projections."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "config" / "actual-account-taxonomy.json"
SCHEMA = ROOT / "config" / "actual-account-taxonomy-schema-v1.json"
BOOTSTRAP = ROOT / "config" / "actual-bootstrap.json"
COMPLETENESS = ROOT / "config" / "account-completeness.json"
WORKER_TAXONOMY = ROOT / "config" / "actual-worker-taxonomy.json"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object: {path}")
    return value


def _render(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _validate_manifest(manifest: dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(_read(SCHEMA)).iter_errors(manifest), key=str)
    if errors:
        raise ValueError("Invalid canonical taxonomy: " + "; ".join(error.message for error in errors[:3]))

    provider_ids = [row["provider_id"] for row in manifest["providers"]]
    if len(provider_ids) != len(set(provider_ids)):
        raise ValueError("Canonical taxonomy has duplicate provider inventories")
    known_providers = set(provider_ids)
    account_ids = [row["provider_account_id"] for row in manifest["accounts"]]
    if len(account_ids) != len(set(account_ids)):
        raise ValueError("Canonical taxonomy has duplicate account identities")
    account_names = [row["display_name"].casefold() for row in manifest["accounts"]]
    if len(account_names) != len(set(account_names)):
        raise ValueError("Canonical taxonomy has duplicate display names")
    if set(row["provider_id"] for row in manifest["accounts"]) - known_providers:
        raise ValueError("Every account provider requires a provider inventory")

    for row in manifest["accounts"]:
        actual = row["actual"]
        if actual["bootstrap"] and row["lifecycle_status"] == "CLOSED":
            raise ValueError(f"Closed account cannot be a bootstrap target: {row['provider_account_id']}")
        if actual["bootstrap"] and row["actual_account_name"] != actual["name"]:
            raise ValueError(f"Bootstrap name must match actual account name: {row['provider_account_id']}")
        if row["balance_reconciliation_required"] and row["expected_balance_minor"] is None:
            raise ValueError(f"Reconciled account has no expected balance: {row['provider_account_id']}")
        if row["expected_balance_minor"] is not None and row["balance_evidence_status"] != "EVIDENCED":
            raise ValueError(f"Expected balance is not evidence-backed: {row['provider_account_id']}")
        if row["account_type"] == "mortgage" and row["balance_sign"] != "LIABILITY_NEGATIVE":
            raise ValueError(f"Mortgage must use liability-negative balances: {row['provider_account_id']}")
        if row["lifecycle_status"] == "PLANNED" and (row["active"] or row["include_in_active_routing"]):
            raise ValueError(f"Planned account cannot be active or routed: {row['provider_account_id']}")
        initial = actual.get("initial_balance_minor")
        if initial is not None and row["balance_evidence_status"] == "EVIDENCED":
            raise ValueError(f"Bootstrap initial balance cannot masquerade as live evidence: {row['provider_account_id']}")

    for key in ("tags", "payees"):
        rows = manifest[key]
        identity_key = "tag" if key == "tags" else "name"
        identities = [row[identity_key].casefold() for row in rows]
        if len(identities) != len(set(identities)):
            raise ValueError(f"Canonical taxonomy has duplicate {key}")
        if any(not row["bootstrap"] and not row["worker"] for row in rows):
            raise ValueError(f"Every {key[:-1]} must have an explicit owner")


def _bootstrap_account(row: dict[str, Any]) -> dict[str, Any]:
    actual = row["actual"]
    result: dict[str, Any] = {
        "name": actual["name"],
        "type": actual["type"],
        "offbudget": actual["offbudget"],
        "provider_account_id": row["provider_account_id"],
    }
    for key in ("aliases", "card_code", "card_last4", "loan_code", "activation_note"):
        if key in actual:
            result[key] = copy.deepcopy(actual[key])
    if actual.get("enabled") is False:
        result["enabled"] = False
    if "initial_balance_minor" in actual:
        result["initial_balance"] = actual["initial_balance_minor"]
    return result


def _bootstrap_static_digest(current: dict[str, Any]) -> str:
    static = {
        key: value
        for key, value in current.items()
        if key not in {"accounts", "retired_accounts", "tags", "payees"}
    }
    payload = json.dumps(static, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_bootstrap(manifest: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    actual_digest = _bootstrap_static_digest(current)
    if actual_digest != manifest["bootstrap_static_sha256"]:
        raise ValueError(
            "actual-bootstrap static contract drifted: "
            f"expected {manifest['bootstrap_static_sha256']}, got {actual_digest}"
        )
    result = copy.deepcopy(current)
    result["accounts"] = [
        _bootstrap_account(row)
        for row in manifest["accounts"]
        if row["actual"]["bootstrap"]
    ]
    result["retired_accounts"] = list(manifest["retired_accounts"])
    result["tags"] = [
        {"tag": row["tag"], "description": row["description"]}
        for row in manifest["tags"]
        if row["bootstrap"]
    ]
    result["payees"] = [
        {"name": row["name"]}
        for row in manifest["payees"]
        if row["bootstrap"]
    ]
    return result


def _replace_top_level_member(text: str, key: str, value: object) -> str:
    match = re.search(rf'^  "{re.escape(key)}":', text, flags=re.MULTILINE)
    if match is None:
        raise ValueError(f"actual-bootstrap is missing generated member: {key}")
    value_start = match.end()
    while value_start < len(text) and text[value_start] in " \t":
        value_start += 1
    depth = 0
    end = value_start
    in_string = False
    escaped = False
    started = False
    while end < len(text):
        character = text[end]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            started = True
            depth += 1
        elif character in "]}":
            depth -= 1
            if started and depth == 0:
                end += 1
                break
        elif not started and character == ",":
            break
        end += 1
    if started and depth != 0:
        raise ValueError(f"actual-bootstrap member is not valid JSON: {key}")
    comma = "," if end < len(text) and text[end] == "," else ""
    rendered = json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\n  ")
    return text[:match.end()] + " " + rendered + comma + text[end + len(comma):]


def _render_bootstrap(manifest: dict[str, Any], current: dict[str, Any]) -> str:
    result = _build_bootstrap(manifest, current)
    text = BOOTSTRAP.read_text(encoding="utf-8")
    for key in ("accounts", "retired_accounts", "tags", "payees"):
        text = _replace_top_level_member(text, key, result[key])
    return text.rstrip("\n") + "\n"


def _build_completeness(manifest: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "provider_id", "provider_account_id", "display_name", "actual_account_name",
        "account_type", "currency", "last4", "owner", "lifecycle_status", "active",
        "retain_history", "include_in_active_routing", "include_in_actual", "actual_offbudget",
        "include_in_net_worth", "balance_sign", "balance_evidence_status",
        "expected_balance_minor", "balance_reconciliation_required", "balance_source", "balance_as_of",
    )
    return {
        "schema_version": 1,
        "scope": list(manifest["scope"]),
        "providers": copy.deepcopy(manifest["providers"]),
        "accounts": [{field: copy.deepcopy(row[field]) for field in fields} for row in manifest["accounts"]],
    }


def _build_worker_taxonomy(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "config/actual-account-taxonomy.json",
        "tags": [copy.deepcopy(row) for row in manifest["tags"] if row["worker"]],
        "payees": [copy.deepcopy(row) for row in manifest["payees"] if row["worker"]],
    }


def _expected(manifest: dict[str, Any]) -> dict[Path, str]:
    return {
        BOOTSTRAP: _render_bootstrap(manifest, _read(BOOTSTRAP)),
        COMPLETENESS: _render(_build_completeness(manifest)),
        WORKER_TAXONOMY: _render(_build_worker_taxonomy(manifest)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write generated projections")
    mode.add_argument("--check", action="store_true", help="fail when projections are stale")
    args = parser.parse_args(argv)
    manifest = _read(CANONICAL)
    _validate_manifest(manifest)
    expected = _expected(manifest)
    drift = [
        path.relative_to(ROOT).as_posix()
        for path, content in expected.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if args.write:
        for path, content in expected.items():
            path.write_text(content, encoding="utf-8", newline="\n")
        return 0
    if drift:
        print("actual taxonomy projections are stale: " + ", ".join(drift))
        print("run: python scripts/generate-actual-account-taxonomy.py --write")
        return 1
    print("actual taxonomy projections are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
