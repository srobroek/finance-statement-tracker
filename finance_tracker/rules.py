from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from .models import Transaction, money


SCHEMA_VERSION = 1
STAGE_ORDER = {
    "TRANSACTION_NORMALIZATION": 10,
    "VENDOR_NORMALIZATION": 20,
    "CLASSIFICATION": 30,
    "TAGGING": 40,
    "EVIDENCE": 50,
    "CASHBACK": 60,
}


@dataclass(frozen=True, slots=True)
class RuleCondition:
    field: str
    operator: str
    value: Any = None
    second_value: Any = None
    group: int = 1
    negate: bool = False
    case_sensitive: bool = False


@dataclass(frozen=True, slots=True)
class RuleAction:
    action: str
    field: str | None = None
    value: Any = None
    sequence: int = 10


@dataclass(slots=True)
class StaticRule:
    rule_id: str
    name: str
    stage: str
    priority: int
    conditions: list[RuleCondition]
    actions: list[RuleAction]
    enabled: bool = True
    stop_on_match: bool = True
    schema_version: int = SCHEMA_VERSION

    def canonical(self) -> dict[str, Any]:
        groups: dict[int, list[dict[str, Any]]] = {}
        for condition in self.conditions:
            groups.setdefault(condition.group, []).append(asdict(condition))
        return {
            "schema_version": self.schema_version,
            "rule_id": self.rule_id,
            "name": self.name,
            "stage": self.stage,
            "priority": self.priority,
            "match": {"any": [{"all": values} for _, values in sorted(groups.items())]},
            "actions": [asdict(action) for action in sorted(self.actions, key=lambda item: item.sequence)],
            "stop_on_match": self.stop_on_match,
        }


@dataclass(frozen=True, slots=True)
class RuleTrace:
    transaction_id: str
    rule_id: str
    rule_name: str
    stage: str
    matched: bool
    actions_applied: tuple[str, ...] = ()


def _text(value: Any, case_sensitive: bool) -> str:
    result = "" if value is None else str(value)
    return result if case_sensitive else result.casefold()


def _values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def condition_matches(transaction: Transaction, condition: RuleCondition) -> bool:
    actual = transaction.value(condition.field)
    op = condition.operator.casefold()
    expected = condition.value
    actual_text = _text(actual, condition.case_sensitive)
    expected_text = _text(expected, condition.case_sensitive)

    if op == "equals":
        result = actual_text == expected_text
    elif op == "not_equals":
        result = actual_text != expected_text
    elif op == "contains":
        result = expected_text in actual_text
    elif op == "contains_any":
        result = any(_text(item, condition.case_sensitive) in actual_text for item in _values(expected))
    elif op == "not_contains":
        result = expected_text not in actual_text
    elif op == "starts_with":
        result = actual_text.startswith(expected_text)
    elif op == "ends_with":
        result = actual_text.endswith(expected_text)
    elif op == "regex":
        flags = 0 if condition.case_sensitive else re.IGNORECASE
        result = re.search(str(expected), "" if actual is None else str(actual), flags) is not None
    elif op == "in":
        result = actual_text in {_text(item, condition.case_sensitive) for item in _values(expected)}
    elif op in {"gt", "gte", "lt", "lte"}:
        left, right = money(actual), money(expected)
        result = {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[op]
    elif op == "between":
        left = money(actual)
        result = money(expected) <= left <= money(condition.second_value)
    elif op == "polarity":
        amount = money(actual)
        result = amount >= 0 if expected_text == "positive" else amount < 0
    elif op == "is_empty":
        result = actual is None or actual == "" or actual == [] or actual == set()
    elif op == "not_empty":
        result = not (actual is None or actual == "" or actual == [] or actual == set())
    elif op == "is_true":
        result = bool(actual)
    elif op == "is_false":
        result = not bool(actual)
    elif op == "has_tag":
        result = expected_text in {_text(item, condition.case_sensitive) for item in transaction.tags}
    else:
        raise ValueError(f"Unsupported rule operator: {condition.operator}")

    return not result if condition.negate else result


def rule_matches(transaction: Transaction, rule: StaticRule) -> bool:
    if not rule.conditions:
        raise ValueError(f"Rule {rule.rule_id} has no conditions")
    groups: dict[int, list[RuleCondition]] = {}
    for condition in rule.conditions:
        groups.setdefault(condition.group, []).append(condition)
    return any(all(condition_matches(transaction, item) for item in conditions) for conditions in groups.values())


def apply_action(transaction: Transaction, action: RuleAction) -> str:
    name = action.action.casefold()
    if name == "set":
        if not action.field:
            raise ValueError("set action requires a field")
        transaction.set_value(action.field, action.value)
        return f"set:{action.field}"
    if name == "add_tag":
        transaction.tags.add(str(action.value))
        return f"add_tag:{action.value}"
    if name == "remove_tag":
        transaction.tags.discard(str(action.value))
        return f"remove_tag:{action.value}"
    if name == "request_evidence":
        transaction.evidence_policy = str(action.value or "ON_DEMAND")
        transaction.evidence_status = "REQUESTED"
        return "request_evidence"
    if name == "require_review":
        transaction.review_required = True
        return "require_review"
    raise ValueError(f"Unsupported rule action: {action.action}")


class RuleEngine:
    def __init__(self, rules: Iterable[StaticRule]):
        self.rules = sorted(
            (rule for rule in rules if rule.enabled),
            key=lambda rule: (STAGE_ORDER.get(rule.stage, 999), rule.priority, rule.rule_id),
        )

    def apply(self, transaction: Transaction) -> list[RuleTrace]:
        traces: list[RuleTrace] = []
        stopped_stages: set[str] = set()
        for rule in self.rules:
            if rule.stage in stopped_stages:
                continue
            matched = rule_matches(transaction, rule)
            applied: list[str] = []
            if matched:
                for action in sorted(rule.actions, key=lambda item: item.sequence):
                    applied.append(apply_action(transaction, action))
                if rule.stop_on_match:
                    stopped_stages.add(rule.stage)
            traces.append(
                RuleTrace(
                    transaction_id=transaction.transaction_id,
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    stage=rule.stage,
                    matched=matched,
                    actions_applied=tuple(applied),
                )
            )
        return traces


def compile_rules(
    rule_rows: Iterable[dict[str, Any]],
    condition_rows: Iterable[dict[str, Any]],
    action_rows: Iterable[dict[str, Any]],
) -> list[StaticRule]:
    conditions_by_rule: dict[str, list[RuleCondition]] = {}
    for row in condition_rows:
        conditions_by_rule.setdefault(str(row["rule_id"]), []).append(
            RuleCondition(
                field=str(row["field"]),
                operator=str(row["operator"]),
                value=row.get("value"),
                second_value=row.get("second_value"),
                group=int(row.get("group", 1)),
                negate=bool(row.get("negate", False)),
                case_sensitive=bool(row.get("case_sensitive", False)),
            )
        )
    actions_by_rule: dict[str, list[RuleAction]] = {}
    for row in action_rows:
        actions_by_rule.setdefault(str(row["rule_id"]), []).append(
            RuleAction(
                action=str(row["action"]),
                field=row.get("field"),
                value=row.get("value"),
                sequence=int(row.get("sequence", 10)),
            )
        )
    compiled: list[StaticRule] = []
    for row in rule_rows:
        rule_id = str(row["rule_id"])
        compiled.append(
            StaticRule(
                rule_id=rule_id,
                name=str(row["name"]),
                stage=str(row["stage"]),
                priority=int(row.get("priority", 100)),
                enabled=bool(row.get("enabled", True)),
                stop_on_match=bool(row.get("stop_on_match", True)),
                schema_version=int(row.get("schema_version", SCHEMA_VERSION)),
                conditions=conditions_by_rule.get(rule_id, []),
                actions=actions_by_rule.get(rule_id, []),
            )
        )
    return compiled

