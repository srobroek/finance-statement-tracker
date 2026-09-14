from __future__ import annotations

import re
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable
from pathlib import Path

from .models import Transaction
from .properties import locked_ownership_conflict


_NOISE = re.compile(r"\b(?:AED|UAE|DUBAI|ABU DHABI|POS|ONLINE|PURCHASE)\b|\d+")
_NON_ALNUM = re.compile(r"[^A-Z]+")
HISTORY_FIELDS = ("vendor", "category", "subcategory", "channel")


def merchant_fingerprint(value: str) -> str:
    normalized = _NOISE.sub(" ", value.upper())
    return " ".join(
        part for part in _NON_ALNUM.sub(" ", normalized).split() if len(part) > 1
    )


@dataclass(frozen=True, slots=True)
class HistoryDecision:
    fingerprint: str
    sample_count: int
    values: dict[str, object]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoryTrace:
    transaction_id: str
    fingerprint: str
    sample_count: int
    fields_applied: tuple[str, ...]


def build_history_index(
    transactions: Iterable[Transaction],
    *,
    minimum_samples: int = 2,
) -> dict[str, HistoryDecision]:
    grouped: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        if transaction.review_required:
            continue
        fingerprint = merchant_fingerprint(transaction.merchant_raw)
        if fingerprint:
            grouped[fingerprint].append(transaction)
    decisions: dict[str, HistoryDecision] = {}
    for fingerprint, rows in grouped.items():
        if len(rows) < minimum_samples:
            continue
        values: dict[str, object] = {}
        for field in HISTORY_FIELDS:
            observed = {
                row.value(field)
                for row in rows
                if row.value(field) not in (None, "", "UNKNOWN")
            }
            if len(observed) == 1:
                values[field] = observed.pop()
        common_tags = (
            set.intersection(*(set(row.tags) for row in rows)) if rows else set()
        )
        if values or common_tags:
            decisions[fingerprint] = HistoryDecision(
                fingerprint,
                len(rows),
                values,
                tuple(sorted(common_tags)),
            )
    return decisions


def _mark_ownership_conflict(transaction: Transaction) -> None:
    reasons = transaction.metadata.setdefault("property_review_reasons", [])
    if not isinstance(reasons, list):
        reasons = list(reasons) if isinstance(reasons, (tuple, set)) else [str(reasons)]
        transaction.metadata["property_review_reasons"] = reasons
    if "LOCKED_OWNERSHIP_CONFLICT" not in reasons:
        reasons.append("LOCKED_OWNERSHIP_CONFLICT")
    if "tags" not in set(transaction.metadata.get("locked_fields", [])):
        transaction.tags.discard("shared")
    transaction.review_required = True


def _history_tag_lock_reason(transaction: Transaction, tag: object) -> str | None:
    metadata = transaction.metadata
    locked = set(metadata.get("locked_fields", []))
    if "tags" in locked:
        return "tags"
    normalized = str(tag).strip().casefold()
    if normalized == "shared" and locked_ownership_conflict(transaction):
        return "ownership_conflict"
    if (
        normalized in {"subscription", "recurring"}
        and "is_subscription" in locked
        and not transaction.is_subscription
    ):
        return "is_subscription"
    if normalized != "shared":
        return None
    if "owner" in locked:
        owner = str(transaction.owner or "").strip().casefold()
        if owner in {"personal", "private"}:
            return "owner"
        if owner in {"joint", "shared"}:
            return None
    for field in ("property_ownership", "ownership"):
        if field in locked:
            ownership = str(metadata.get(field) or "").strip().casefold()
            if ownership in {"personal", "private"}:
                return field
            if ownership in {"joint", "shared"}:
                return None
    ownership = str(metadata.get("property_ownership") or "").strip().casefold()
    if ownership in {"personal", "private"}:
        return "property_ownership"
    return None


def apply_history_match(
    transaction: Transaction,
    history: dict[str, HistoryDecision],
) -> HistoryTrace | None:
    if locked_ownership_conflict(transaction):
        _mark_ownership_conflict(transaction)
    fingerprint = merchant_fingerprint(transaction.merchant_raw)
    decision = history.get(fingerprint)
    if decision is None:
        return None
    locked = set(transaction.metadata.get("locked_fields", []))
    applied: list[str] = []
    for field, value in decision.values.items():
        current = transaction.value(field)
        unresolved = current in (None, "", "UNKNOWN")
        if unresolved and field not in locked:
            transaction.set_value(field, value)
            applied.append(field)
    if decision.tags:
        allowed_tags = [
            tag
            for tag in decision.tags
            if _history_tag_lock_reason(transaction, tag) is None
        ]
        if allowed_tags:
            transaction.tags.update(allowed_tags)
            applied.append("tags")
    transaction.metadata["history_count"] = decision.sample_count
    trace = HistoryTrace(
        transaction.transaction_id,
        fingerprint,
        decision.sample_count,
        tuple(applied),
    )
    transaction.metadata.setdefault("history_trace", []).append(
        {
            "fingerprint": trace.fingerprint,
            "sample_count": trace.sample_count,
            "fields_applied": list(trace.fields_applied),
        }
    )
    return trace


def load_history_index(path: str | Path) -> dict[str, HistoryDecision]:
    source = json.loads(Path(path).read_text(encoding="utf-8"))
    if source.get("schema_version") != 1:
        raise ValueError("History index schema_version must be 1")
    result: dict[str, HistoryDecision] = {}
    for row in source.get("decisions", []):
        decision = HistoryDecision(
            fingerprint=str(row["fingerprint"]),
            sample_count=int(row["sample_count"]),
            values=dict(row.get("values", {})),
            tags=tuple(str(tag) for tag in row.get("tags", [])),
        )
        if decision.sample_count < 2:
            raise ValueError("History decisions require at least two samples")
        result[decision.fingerprint] = decision
    return result
