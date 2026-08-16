from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .browser_recipes import load_data, load_provider, validate_registry


def load_browser_sources(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Browser sources configuration schema_version must be 1")
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("Browser sources configuration requires accounts")
    names: set[str] = set()
    for row in accounts:
        if not isinstance(row, dict):
            raise ValueError("Browser source account entries must be objects")
        name = str(row.get("actual_account") or "").strip()
        if not name or name in names:
            raise ValueError(f"Browser source actual_account is missing or duplicated: {name}")
        names.add(name)
        last4 = str(row.get("account_last4") or "")
        if last4 and (not last4.isdigit() or len(last4) != 4):
            raise ValueError(f"Browser source account_last4 must contain exactly four digits: {name}")
    return payload


def account_source(config: Mapping[str, Any], actual_account: str) -> dict[str, Any]:
    matches = [
        dict(row)
        for row in config["accounts"]
        if str(row["actual_account"]).casefold() == actual_account.casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"Browser source account mapping not found: {actual_account}")
    return matches[0]


def capture_account(source: Mapping[str, Any]) -> dict[str, object]:
    result: dict[str, object] = {
        "label": str(source["label"]),
        "actual_account": str(source["actual_account"]),
        "currency": str(source.get("currency") or "AED"),
    }
    if source.get("card_code"):
        result["card_code"] = str(source["card_code"])
    if source.get("account_last4"):
        result["account_last4"] = str(source["account_last4"])
    return result


def validate_source_coverage(
    config: Mapping[str, Any],
    adapters_root: str | Path,
) -> dict[str, Any]:
    registry = validate_registry(adapters_root)
    violations = list(registry["violations"])
    coverage = []

    def check_source(source: Mapping[str, Any], source_name: str) -> dict[str, Any]:
        provider_id = source.get("provider_id")
        data_ids = list(source.get("data_ids") or [])
        status = str(source.get("status") or "READY")
        if provider_id:
            try:
                load_provider(str(provider_id), adapters_root)
                for data_id in data_ids:
                    load_data(str(provider_id), str(data_id), adapters_root)
            except ValueError as error:
                status = "INVALID"
                violations.append(f"{source_name}: {error}")
        elif status != "ADAPTER_REQUIRED":
            status = "INVALID"
            violations.append(f"{source_name}: provider_id is required")
        return {
            "source": source_name,
            "required": bool(source.get("required", False)),
            "provider_id": provider_id,
            "data_ids": data_ids,
            "preferred_acquisition": source.get("preferred_acquisition"),
            "status": status,
        }

    for source in config["accounts"]:
        coverage.append(check_source(source, str(source["actual_account"])))
    supplemental = []
    for source in config.get("supplemental_sources", []):
        source_name = str(source.get("source_id") or "").strip()
        if not source_name:
            violations.append("supplemental source_id is required")
            continue
        supplemental.append(check_source(source, source_name))
    return {
        "status": "ok" if not violations else "invalid",
        "coverage": coverage,
        "supplemental": supplemental,
        "registry": registry,
        "violations": violations,
    }
