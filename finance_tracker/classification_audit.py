from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .models import Transaction
from .actual_notes import parse_actual_notes
from .transaction_semantics import PENDING_CATEGORY_VALUES, UNKNOWN_PAYEE_VALUES


def _review_reasons(transaction: Transaction) -> set[str]:
    """Recompute review reasons from current fields, never stale raw metadata."""

    reasons: set[str] = set()
    category = str(transaction.category or "").strip()
    if category.casefold() in PENDING_CATEGORY_VALUES:
        reasons.update({"CATEGORY_UNRESOLVED", "UNCATEGORIZED"})
        if transaction.metadata.get(
            "category_recommendation"
        ) or transaction.metadata.get("category_recommendations"):
            reasons.add("CATEGORY_RECOMMENDATION_PENDING")
    payee = str(transaction.vendor or "").strip().casefold()
    merchant = str(transaction.merchant_raw or "").strip().casefold()
    if (payee and payee in UNKNOWN_PAYEE_VALUES) or (
        not payee
        and merchant in UNKNOWN_PAYEE_VALUES
        and transaction.source_type == "actual_snapshot"
    ):
        reasons.add("PAYEE_UNRESOLVED")
    if transaction.review_required or "needs-review" in {
        str(tag).casefold() for tag in transaction.tags
    }:
        reasons.add("REVIEW_REQUIRED")
    if transaction.metadata.get("property_review_reasons"):
        reasons.update(
            str(value).strip().upper()
            for value in transaction.metadata["property_review_reasons"]
            if str(value).strip()
        )
    if transaction.tags and "rental" in {
        str(tag).casefold() for tag in transaction.tags
    }:
        rental_units = [
            str(tag)
            for tag in transaction.tags
            if str(tag).casefold().startswith("rental:")
        ]
        if len(rental_units) != 1:
            reasons.add("RENTAL_UNIT_TAG_COUNT")
    return reasons


def enforce_transaction_invariants(transaction: Transaction) -> tuple[str, ...]:
    """Normalize derived classification state without overriding manual locks."""

    locked = set(transaction.metadata.get("locked_fields", []))
    queue_locked = bool(
        {
            "review_required",
            "classification_review_reasons",
            "category_resolution",
            "payee_resolution",
        }
        & locked
    )
    if "tags" not in locked:
        transaction.tags = {
            str(tag).strip().casefold() for tag in transaction.tags if str(tag).strip()
        }
        if "review" in transaction.tags:
            transaction.tags.discard("review")
            transaction.tags.add("needs-review")

    rental_units = sorted(tag for tag in transaction.tags if tag.startswith("rental:"))
    has_rental = "rental" in transaction.tags or bool(rental_units)
    if has_rental and "tags" not in locked:
        transaction.tags.add("rental")
        transaction.tags.discard("home")

    reasons = _review_reasons(transaction)
    if has_rental and len(rental_units) != 1:
        reasons.add("RENTAL_UNIT_TAG_COUNT")

    category = str(transaction.category or "").strip()
    if "category_resolution" not in locked:
        if category.casefold() in PENDING_CATEGORY_VALUES:
            transaction.metadata["category_resolution"] = "UNRESOLVED"
        elif category:
            transaction.metadata["category_resolution"] = "RESOLVED"
    payee = str(transaction.vendor or "").strip().casefold()
    merchant = str(transaction.merchant_raw or "").strip().casefold()
    if "payee_resolution" not in locked:
        if payee in UNKNOWN_PAYEE_VALUES and merchant in UNKNOWN_PAYEE_VALUES:
            transaction.metadata["payee_resolution"] = "UNRESOLVED"
        elif payee or merchant:
            transaction.metadata["payee_resolution"] = "RESOLVED"

    if not queue_locked:
        if reasons:
            transaction.review_required = True
            if "tags" not in locked:
                transaction.tags.add("needs-review")
        else:
            transaction.review_required = False
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


def build_actual_snapshot_classification_report(
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Audit raw Actual rows using canonical notes and current fields."""

    exceptions: list[dict[str, Any]] = []
    unaccounted: list[str] = []
    reasons_by_code: Counter[str] = Counter()
    raw_rows = list(rows)
    for row in raw_rows:
        parts = parse_actual_notes(str(row.get("notes") or ""), legacy=True)
        tags = {str(tag).casefold() for tag in parts.tags}
        if not any(
            key in row
            for key in ("category_name", "payee_name", "tags", "review_required")
        ):
            continue
        tags.update(str(tag).strip().casefold() for tag in row.get("tags") or ())
        category = str(row.get("category_name") or "").strip()
        payee = str(row.get("payee_name") or row.get("imported_payee") or "").strip()
        reasons: set[str] = set()
        if category.casefold() in PENDING_CATEGORY_VALUES:
            reasons.add("CATEGORY_UNRESOLVED")
        if payee.casefold() in UNKNOWN_PAYEE_VALUES:
            reasons.add("PAYEE_UNRESOLVED")
        explicitly_queued = bool(row.get("review_required")) or bool(
            {"review", "needs-review"} & tags
        )
        if explicitly_queued:
            reasons.add("REVIEW_REQUIRED")
        if not reasons:
            continue
        transaction_id = str(row.get("imported_id") or row.get("id") or "")
        if not explicitly_queued:
            unaccounted.append(transaction_id)
            reasons.add("UNQUEUED_EXCEPTION")
        for reason in reasons:
            reasons_by_code[reason] += 1
        exceptions.append(
            {
                "transaction_id": transaction_id,
                "merchant_raw": payee or row.get("imported_payee"),
                "category": category or None,
                "reasons": sorted(reasons),
                "queued": explicitly_queued,
            }
        )
    return {
        "schema_version": "classification-exception-report-v1",
        "transaction_count": len(raw_rows),
        "resolved_count": len(raw_rows) - len(exceptions),
        "exception_count": len(exceptions),
        "unaccounted_count": len(unaccounted),
        "unaccounted_transaction_ids": sorted(unaccounted),
        "exceptions_by_reason": dict(sorted(reasons_by_code.items())),
        "exceptions": exceptions,
    }
