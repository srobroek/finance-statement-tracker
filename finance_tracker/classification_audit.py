from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .models import Transaction


def _review_reasons(transaction: Transaction) -> set[str]:
    reasons = {
        str(value).strip().upper()
        for value in transaction.metadata.get("classification_review_reasons", [])
        if str(value).strip()
    }
    category = str(transaction.category or "").strip()
    if not category:
        reasons.add("UNCATEGORIZED")
    elif category.casefold() == "needs review":
        reasons.add("CATEGORY_NEEDS_REVIEW")
    if (
        transaction.metadata.get("category_recommendation")
        or transaction.metadata.get("category_recommendations")
    ) and not category:
        reasons.add("CATEGORY_RECOMMENDATION_PENDING")
    return reasons


def enforce_transaction_invariants(transaction: Transaction) -> tuple[str, ...]:
    """Normalize semantic tags and guarantee a complete review queue."""

    locked = set(transaction.metadata.get("locked_fields", []))
    normalized_tags = {
        str(tag).strip().casefold()
        for tag in transaction.tags
        if str(tag).strip()
    }
    if "review" in normalized_tags:
        normalized_tags.discard("review")
        normalized_tags.add("needs-review")
    if "tags" not in locked:
        transaction.tags = normalized_tags

    rental_units = sorted(
        tag for tag in normalized_tags if tag.startswith("rental:")
    )
    has_rental = "rental" in normalized_tags or bool(rental_units)
    if has_rental and "tags" not in locked:
        transaction.tags.add("rental")
        transaction.tags.discard("home")

    reasons = _review_reasons(transaction)
    if has_rental and len(rental_units) != 1:
        reasons.add("RENTAL_UNIT_TAG_COUNT")
    if transaction.review_required or "needs-review" in normalized_tags:
        reasons.add("REVIEW_REQUIRED")
    if transaction.metadata.get("property_review_reasons"):
        reasons.update(
            str(value).strip().upper()
            for value in transaction.metadata["property_review_reasons"]
            if str(value).strip()
        )

    if reasons:
        transaction.set_value("review_required", True)
        if "tags" not in locked:
            transaction.tags.add("needs-review")
    else:
        transaction.set_value("review_required", False)
        if "tags" not in locked:
            transaction.tags.discard("needs-review")
    transaction.metadata["classification_review_reasons"] = sorted(reasons)
    return tuple(sorted(reasons))


def build_classification_exception_report(
    transactions: Iterable[Transaction],
) -> dict[str, Any]:
    """Describe classification coverage without modifying source rows."""

    rows = list(transactions)
    exceptions: list[dict[str, Any]] = []
    unaccounted: list[str] = []
    reasons_by_code: Counter[str] = Counter()
    for transaction in rows:
        reasons = _review_reasons(transaction)
        reasons.update(
            str(value).strip().upper()
            for value in transaction.metadata.get("classification_review_reasons", [])
            if str(value).strip()
        )
        explicitly_queued = transaction.review_required or "needs-review" in {
            str(tag).casefold() for tag in transaction.tags
        }
        if reasons or explicitly_queued:
            if not explicitly_queued:
                unaccounted.append(transaction.transaction_id)
                reasons.add("UNQUEUED_EXCEPTION")
            for reason in reasons:
                reasons_by_code[reason] += 1
            exceptions.append(
                {
                    "transaction_id": transaction.transaction_id,
                    "merchant_raw": transaction.merchant_raw,
                    "category": transaction.category,
                    "reasons": sorted(reasons),
                    "queued": explicitly_queued,
                }
            )
    return {
        "schema_version": "classification-exception-report-v1",
        "transaction_count": len(rows),
        "resolved_count": len(rows) - len(exceptions),
        "exception_count": len(exceptions),
        "unaccounted_count": len(unaccounted),
        "unaccounted_transaction_ids": sorted(unaccounted),
        "exceptions_by_reason": dict(sorted(reasons_by_code.items())),
        "exceptions": exceptions,
    }
