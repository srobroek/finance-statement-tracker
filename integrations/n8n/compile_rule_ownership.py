"""Compile canonical AutoCat rules into disjoint Actual and n8n ownership sets.

This deliberately uses a narrow Actual capability matrix. A rule is sent to
Actual only when every condition and action has a proven semantics-preserving
mapping. Everything else remains N8N_ONLY; no author duplicates derived flags.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config" / "static-rules.seed.json"
OUT_DIR = ROOT / "integrations" / "n8n" / "generated"
MANIFEST = OUT_DIR / "rule-ownership-manifest.json"
N8N_RULES = OUT_DIR / "n8n-runtime-rules.json"
ACTUAL_RULES = OUT_DIR / "actual-rules.json"

CAPABILITY_VERSION = "actual-rule-capability-v1"
ACTUAL_STAGES = {"CLASSIFICATION"}
ACTUAL_CONDITION_FIELDS = {"vendor"}
ACTUAL_OPERATORS = {"equals", "in", "contains", "regex"}
ACTUAL_ACTIONS = {("set_if_empty", "category"), ("set_if_empty", "subcategory")}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _actual_representable(rule: dict[str, Any]) -> tuple[bool, str]:
    if rule["stage"] not in ACTUAL_STAGES:
        return False, "STAGE_NOT_PROVEN_EQUIVALENT"
    conditions = [c for group in rule["match"]["any"] for c in group["all"]]
    if any(c["field"] not in ACTUAL_CONDITION_FIELDS for c in conditions):
        return False, "CONDITION_FIELD_NOT_PROVEN_EQUIVALENT"
    if any(c["operator"] not in ACTUAL_OPERATORS or c.get("negate", False) for c in conditions):
        return False, "CONDITION_OPERATOR_NOT_PROVEN_EQUIVALENT"
    if any((a["action"], a.get("field")) not in ACTUAL_ACTIONS for a in rule["actions"]):
        return False, "ACTION_NOT_PROVEN_EQUIVALENT"
    return True, "ACTUAL_CAPABILITY_MATRIX_MATCH"


def compile_outputs(rules: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual: list[dict[str, Any]] = []
    n8n: list[dict[str, Any]] = []
    ownership: list[dict[str, Any]] = []
    expected: set[tuple[str, str]] = set()

    for source_rule in rules:
        scopes = source_rule.get("rule_sets") or ["FULL_LEDGER"]
        representable, reason = _actual_representable(source_rule)
        for scope in scopes:
            expected.add((source_rule["rule_id"], scope))
            owner = "ACTUAL" if scope == "FULL_LEDGER" and representable else "N8N_ONLY"
            scoped = copy.deepcopy(source_rule)
            scoped["rule_sets"] = [scope]
            scoped["execution_owner"] = owner
            scoped["actual_representable"] = owner == "ACTUAL"
            scoped["ownership_reason"] = reason if scope == "FULL_LEDGER" else "NON_LEDGER_SCOPE"
            if owner == "ACTUAL":
                scoped["actual_semantics_version"] = CAPABILITY_VERSION
                actual.append(scoped)
            else:
                n8n.append(scoped)
            ownership.append(
                {
                    "rule_id": source_rule["rule_id"],
                    "rule_set": scope,
                    "execution_owner": owner,
                    "actual_representable": owner == "ACTUAL",
                    "reason": scoped["ownership_reason"],
                }
            )

    actual_keys = {(r["rule_id"], r["rule_sets"][0]) for r in actual}
    n8n_keys = {(r["rule_id"], r["rule_sets"][0]) for r in n8n}
    overlap = sorted(actual_keys & n8n_keys)
    unowned = sorted(expected - actual_keys - n8n_keys)
    unexpected = sorted((actual_keys | n8n_keys) - expected)
    if overlap or unowned or unexpected:
        raise ValueError(f"rule ownership invalid overlap={overlap} unowned={unowned} unexpected={unexpected}")

    source_sha = hashlib.sha256(_canonical_bytes(rules)).hexdigest()
    header = {
        "schema_version": 1,
        "contract_status": "SPEC_ONLY",
        "authoring_source": "config/static-rules.seed.json",
        "authoring_source_sha256": source_sha,
        "capability_version": CAPABILITY_VERSION,
    }
    manifest = {
        **header,
        "ownership": sorted(ownership, key=lambda r: (r["rule_id"], r["rule_set"])),
        "overlap": [],
        "unowned": [],
        "actual_rule_count": len(actual),
        "n8n_rule_count": len(n8n),
        "warning": "Generated ownership is not evidence that Actual rules were imported or parity-tested.",
    }
    actual_output = {**header, "execution_owner": "ACTUAL", "rules": actual}
    n8n_output = {**header, "execution_owner": "N8N_ONLY", "rules": n8n}
    return manifest, n8n_output, actual_output


def _render(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write generated outputs")
    args = parser.parse_args()
    rules = json.loads(SOURCE.read_text(encoding="utf-8"))
    outputs = dict(zip((MANIFEST, N8N_RULES, ACTUAL_RULES), compile_outputs(rules), strict=True))
    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for path, value in outputs.items():
            path.write_text(_render(value), encoding="utf-8", newline="\n")
        return 0
    drift = [str(path.relative_to(ROOT)) for path, value in outputs.items() if not path.exists() or path.read_text(encoding="utf-8") != _render(value)]
    if drift:
        raise SystemExit("generated rule ownership drift: " + ", ".join(drift))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
