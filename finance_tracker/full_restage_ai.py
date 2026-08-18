from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "full-restage-ai-decisions-v1"


def _matches(rule: dict[str, Any], transaction: dict[str, Any], policy_id: str) -> bool:
    if str(rule.get("policy_id") or "") != policy_id:
        return False
    pattern = str(rule.get("merchant_regex") or "")
    if pattern and not re.search(pattern, str(transaction.get("merchant_raw") or ""), re.I):
        return False
    category = rule.get("category")
    if category is not None and str(transaction.get("category") or "") != str(category):
        return False
    return True


def build_responses(
    run_root: str | Path,
    decisions_path: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    run_root = Path(run_root).resolve()
    output_root = Path(output_root).resolve()
    decisions = json.loads(Path(decisions_path).read_text(encoding="utf-8"))
    if decisions.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported full-restage AI decision schema")
    provider = str(decisions.get("provider") or "codex-interactive")
    model = str(decisions.get("model") or "gpt-5")
    defaults = decisions.get("default_proposals_by_policy") or {}
    rules = decisions.get("rules") or []
    exact = decisions.get("transactions") or {}
    if not isinstance(defaults, dict) or not isinstance(rules, list) or not isinstance(exact, dict):
        raise ValueError("Invalid full-restage AI decision structure")

    output_root.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    rule_hits: Counter[str] = Counter()
    seen: set[tuple[str, str, str]] = set()
    sources: list[dict[str, Any]] = []
    for result_path in sorted(run_root.glob("*.json")):
        if result_path.name == "summary.json":
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        handoff = result.get("ai_handoff") or {}
        transactions = handoff.get("transactions") or {}
        target = output_root / result_path.name
        existing_responses: list[dict[str, Any]] = []
        if target.exists():
            existing_responses = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(existing_responses, list):
                raise ValueError(f"Existing response file is not an array: {target}")
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for response in existing_responses:
            if not isinstance(response, dict):
                raise ValueError(f"Invalid existing response in {target}")
            response_key = (
                str(response.get("transaction_id") or ""),
                str(response.get("policy_id") or ""),
            )
            if not all(response_key) or response_key in merged:
                raise ValueError(f"Invalid or duplicate existing AI response: {response_key}")
            merged[response_key] = response
        new_response_count = 0
        for request in handoff.get("requests") or []:
            transaction_id = str(request.get("transaction_id") or "")
            transaction_ref = str(request.get("transaction_ref") or transaction_id)
            policy_id = str(request.get("policy_id") or "")
            key = (result_path.stem, transaction_id, policy_id)
            if not transaction_id or not policy_id or key in seen:
                raise ValueError(f"Invalid or duplicate AI request: {key}")
            seen.add(key)
            transaction = transactions.get(transaction_ref)
            if not isinstance(transaction, dict):
                raise ValueError(f"Missing transaction snapshot for {transaction_ref}")
            exact_policy = (exact.get(transaction_id) or {}).get(policy_id)
            proposals = exact_policy
            matched_rule = None
            if proposals is None:
                for index, rule in enumerate(rules):
                    if _matches(rule, transaction, policy_id):
                        proposals = rule.get("proposals")
                        matched_rule = str(rule.get("id") or index)
                        break
            if proposals is None:
                proposals = defaults.get(policy_id)
            if not isinstance(proposals, list):
                raise ValueError(
                    f"No proposal decision for {result_path.stem}/{transaction_id}/{policy_id}"
                )
            normalized: list[dict[str, Any]] = []
            for proposal in proposals:
                item = dict(proposal)
                item.setdefault("source_refs", [transaction_id])
                normalized.append(item)
            response_key = (transaction_id, policy_id)
            merged[response_key] = {
                "transaction_id": transaction_id,
                "policy_id": policy_id,
                "provider": provider,
                "model": model,
                "proposals": normalized,
            }
            new_response_count += 1
            counts[policy_id] += 1
            if matched_rule is not None:
                rule_hits[matched_rule] += 1
        responses = list(merged.values())
        target.write_text(json.dumps(responses, indent=2) + "\n", encoding="utf-8")
        sources.append(
            {
                "source": result_path.stem,
                "new_responses": new_response_count,
                "total_responses": len(responses),
            }
        )
    return {
        "schema_version": "full-restage-ai-response-build-v1",
        "run_root": str(run_root),
        "output_root": str(output_root),
        "response_count": sum(counts.values()),
        "new_response_count": sum(counts.values()),
        "total_response_count": sum(source["total_responses"] for source in sources),
        "by_policy": dict(sorted(counts.items())),
        "rule_hits": dict(sorted(rule_hits.items())),
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact per-source AI response arrays")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    report = build_responses(args.run_root, args.decisions, args.output_root)
    payload = json.dumps(report, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
