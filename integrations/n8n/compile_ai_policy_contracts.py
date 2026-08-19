"""Compile versioned AI policies into server-owned n8n policy-contract rows."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
POLICIES = ROOT / "config" / "ai-policies.json"
BOOTSTRAP = ROOT / "config" / "actual-bootstrap.json"
PROPERTIES = ROOT / "config" / "properties.json"
CASHBACK = ROOT / "config" / "cashback-programs.json"
OUTPUT_SCHEMA = ROOT / "integrations" / "n8n" / "contracts" / "ai-proposal-v1.schema.json"
AGENT_PROVIDERS = ROOT / "config" / "agent-providers.json"
OUTPUT = ROOT / "integrations" / "n8n" / "generated" / "ai-policy-contracts.seed.json"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def normalized_text_bytes(path: Path) -> bytes:
    """Return the Git-canonical LF bytes used by Linux runtime checkouts."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def compile_contracts() -> dict[str, Any]:
    policy_bytes = normalized_text_bytes(POLICIES)
    policy_doc = json.loads(policy_bytes)
    bootstrap = json.loads(BOOTSTRAP.read_text(encoding="utf-8"))
    properties = json.loads(PROPERTIES.read_text(encoding="utf-8"))
    cashback = json.loads(CASHBACK.read_text(encoding="utf-8"))
    output_schema_bytes = normalized_text_bytes(OUTPUT_SCHEMA)
    provider_doc = json.loads(AGENT_PROVIDERS.read_text(encoding="utf-8"))

    categories = sorted({name for group in bootstrap["category_groups"] for name in group["categories"]})
    property_codes = sorted({row["property_code"] for row in properties["properties"] if row.get("property_code")})
    rental_units = sorted({row["rental_unit"] for row in properties["properties"] if row.get("rental_unit")})
    bucket_codes = sorted({bucket["code"] for program in cashback["programs"] for bucket in program.get("buckets", [])})
    source_domains = {
        "actual.categories": categories,
        "properties.codes": property_codes,
        "properties.rental_units": rental_units,
        "cashback.buckets": bucket_codes,
    }
    # This must match the exact byte hash independently verified by the runner.
    # Resolved domains are additionally included in the canonical request hash.
    config_sha256 = hashlib.sha256(policy_bytes).hexdigest()
    output_schema_sha256 = hashlib.sha256(output_schema_bytes).hexdigest()
    provider_by_profile = {
        profile: row["agent_provider"]
        for profile, row in provider_doc["profiles"].items()
    }
    allowed_providers = set(provider_doc["providers"])
    if set(provider_by_profile.values()) - allowed_providers:
        raise ValueError("agent provider profile references an undefined provider")
    rows: list[dict[str, Any]] = []
    for policy in policy_doc["policies"]:
        if policy["agent_profile"] not in provider_by_profile:
            raise ValueError(f"no subscription agent provider owns {policy['agent_profile']}")
        fields = list(policy["target_fields"])
        domains: dict[str, list[Any]] = {
            field: list(values) for field, values in policy.get("allowed_values", {}).items()
        }
        for field, source in policy.get("allowed_value_sources", {}).items():
            if source not in source_domains:
                raise ValueError(f"unknown allowed value source {source}")
            domains[field] = source_domains[source]
        if "tags" in fields:
            domains["tags"] = list(policy.get("allowed_tags", []))
        if "subcategory" in fields and "subcategory" not in domains:
            domains["subcategory"] = categories
        constrained = {
            "category", "subcategory", "tags", "evidence_policy", "review_required",
            "is_subscription", "property_code", "rental_unit", "channel", "reward_bucket",
        }
        missing = sorted(field for field in fields if field in constrained and not domains.get(field))
        if missing:
            raise ValueError(f"policy {policy['policy_id']} has unresolved domains {missing}")
        rows.append({
            "policy_id": policy["policy_id"],
            "policy_version": policy["version"],
            "agent_profile": policy["agent_profile"],
            "agent_provider": provider_by_profile[policy["agent_profile"]],
            "policy_sha256": sha(policy),
            "config_sha256": config_sha256,
            "output_schema_sha256": output_schema_sha256,
            "allowed_fields_json": json.dumps(fields, separators=(",", ":")),
            "allowed_values_json": json.dumps(domains, sort_keys=True, separators=(",", ":")),
            "state": "ACTIVE",
        })
    return {
        "schema_version": 1,
        "contract_status": "SPEC_ONLY",
        "authoring_source": "config/ai-policies.json + config/agent-providers.json",
        "rows": rows,
        "warning": "Seed generation is not evidence that rows were imported into n8n Data Tables.",
    }


def render(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    expected = render(compile_contracts())
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
        return 0
    if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
        raise SystemExit(f"generated AI policy contract drift: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
