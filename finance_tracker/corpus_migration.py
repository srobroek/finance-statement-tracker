"""Deterministic, read-only corpus semantics and note-v2 migration planning."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .actual_notes import format_actual_notes, parse_actual_notes, validate_actual_notes


SEMANTIC_TAGS = frozenset(
    {"refund", "reversal", "reward", "amazon-credit", "transfer", "card-payment", "review"}
)
TRANSFER_PATTERNS = (
    "TRANSFER PAYMENT RECEIVED",
    "PAYMENT RECEIVED THANK YOU",
    "CREDIT CARD PAYMENT",
    "CARD PAYMENT",
)
REWARD_PATTERNS = ("CASHBACK", "CASH BACK", "REWARD", "RDMPTION")
REVERSAL_PATTERNS = ("REVERSAL", "REVERSED")
FEE_PATTERNS = ("FEE", "VAT ON")
INCOME_CATEGORIES = frozenset(
    {"salary", "rental income", "other income", "interest & dividends"}
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _text(record: dict[str, Any]) -> str:
    values = (
        record.get("imported_payee"),
        record.get("payee_name"),
        record.get("description"),
        record.get("memo"),
    )
    return " ".join(" ".join(str(value or "").upper().split()) for value in values)


@dataclass(frozen=True, slots=True)
class TopicDecision:
    topic: str
    source_direction: str
    reason: str
    category_name: str | None
    tags: tuple[str, ...]
    reward_medium: str | None = None
    cash_equivalent: bool | None = None


def classify_actual_record(record: dict[str, Any], account: str) -> TopicDecision:
    """Classify economic topic without changing Actual's signed amount."""
    amount = record.get("amount")
    if not isinstance(amount, int):
        raise ValueError("Actual record amount must be an integer")
    direction = "CREDIT" if amount > 0 else "DEBIT"
    text = _text(record)
    category = str(record.get("category_name") or "").strip()
    explicit_topic = str(record.get("transaction_topic") or "").strip().upper()
    explicit_reward = explicit_topic in {"REWARD", "REWARD_CREDIT", "AMAZON_CREDIT"}
    explicit_transfer = explicit_topic in {"PAYMENT", "TRANSFER"}

    if explicit_transfer or category.casefold() == "card payments" or any(
        token in text for token in TRANSFER_PATTERNS
    ):
        return TopicDecision(
            "TRANSFER", direction, "EXPLICIT_TRANSFER_EVIDENCE", "Card Payments",
            ("card-payment", "transfer"),
        )
    if direction == "CREDIT" and any(token in text for token in REVERSAL_PATTERNS):
        return TopicDecision(
            "REVERSAL", direction, "EXPLICIT_REVERSAL_EVIDENCE",
            "Refunds & Reimbursements", ("refund", "reversal"),
        )
    if direction == "CREDIT" and (
        explicit_reward or any(token in text for token in REWARD_PATTERNS)
    ):
        # "Amazon credit" alone is not reward evidence: it can describe an
        # ordinary merchant credit/refund.  It becomes the non-cash reward
        # medium only after an explicit reward topic or reward phrase has
        # established that this row is actually an issuer reward.
        amazon_credit = "AMAZON CREDIT" in text or explicit_topic == "AMAZON_CREDIT"
        return TopicDecision(
            "REWARD_CREDIT", direction, "EXPLICIT_REWARD_EVIDENCE",
            "Cashback & Rewards",
            ("amazon-credit", "reward") if amazon_credit else ("reward",),
            reward_medium="AMAZON_CREDIT" if amazon_credit else "ISSUER_REWARD",
            cash_equivalent=False if amazon_credit else True,
        )
    if direction == "CREDIT" and (
        category.casefold() in INCOME_CATEGORIES or "SALARY" in text
    ):
        return TopicDecision(
            "INCOME", direction, "EXPLICIT_INCOME_EVIDENCE", category or "Other Income", ()
        )
    if direction == "CREDIT":
        # A positive Amazon merchant row on the EI statement is a merchant
        # refund. EI cashback is delivered as an Amazon credit and therefore
        # requires explicit Amazon-credit evidence rather than an amount guess.
        is_credit_card = "CREDIT CARD" in account.upper()
        has_expense_context = bool(category and category.casefold() != "needs review")
        if is_credit_card or has_expense_context:
            return TopicDecision(
                "REFUND", direction, "POSITIVE_MERCHANT_CREDIT_DEFAULT",
                "Refunds & Reimbursements", ("refund",),
            )
        return TopicDecision(
            "UNRESOLVED_CREDIT", direction, "POSITIVE_DEPOSIT_CREDIT_REQUIRES_EVIDENCE",
            category or "Needs Review", ("review",),
        )
    if explicit_topic == "INTEREST" or "INTEREST" in text:
        return TopicDecision("INTEREST", direction, "EXPLICIT_INTEREST", category or None, ())
    if explicit_topic == "FEE" or any(token in text for token in FEE_PATTERNS):
        return TopicDecision("FEE", direction, "EXPLICIT_FEE", category or None, ())
    return TopicDecision("PURCHASE", direction, "DEBIT_DEFAULT_PURCHASE", category or None, ())


def _notes_with_topic(notes: str, decision: TopicDecision) -> str:
    parts = parse_actual_notes(notes, legacy=True)
    tags = (set(parts.tags) - SEMANTIC_TAGS) | set(decision.tags)
    return format_actual_notes(
        tags=tags,
        documents=parts.documents,
        reviews=parts.reviews,
        memos=parts.memos,
    )


def regenerate_manifest(manifest: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a deterministic note-v2/topic regeneration of one manifest."""
    output = copy.deepcopy(manifest)
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for envelope in output.get("envelopes") or []:
        account = str(envelope.get("account") or "")
        for record in envelope.get("records") or []:
            imported_id = str(record.get("imported_id") or "").strip()
            if not imported_id:
                raise ValueError("Every corpus row must have a stable imported_id")
            if imported_id in seen:
                raise ValueError(f"Duplicate imported_id within manifest: {imported_id}")
            seen.add(imported_id)
            original_amount = record.get("amount")
            original_notes = str(record.get("notes") or "")
            original_category = str(record.get("category_name") or "")
            decision = classify_actual_record(record, account)
            desired_notes = _notes_with_topic(original_notes, decision)
            validate_actual_notes(desired_notes)
            record["notes"] = desired_notes
            if decision.category_name:
                record["category_name"] = decision.category_name
            if record.get("amount") != original_amount:
                raise AssertionError("Corpus migration must never mutate amount or sign")
            decisions.append({
                "imported_id": imported_id,
                "account": account,
                "date": str(record.get("date") or ""),
                "amount": original_amount,
                "source_direction": decision.source_direction,
                "topic": decision.topic,
                "reason": decision.reason,
                "reward_medium": decision.reward_medium,
                "cash_equivalent": decision.cash_equivalent,
                "original_category_name": original_category,
                "desired_category_name": record.get("category_name"),
                "original_notes": original_notes,
                "desired_notes": desired_notes,
            })
    return output, decisions


def regenerate_corpus(
    manifest_root: Path, output_root: Path
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Regenerate every JSON manifest and emit a corpus-wide exception report."""
    paths = sorted(path for path in manifest_root.rglob("*.json") if path.is_file())
    if not paths:
        raise ValueError("Manifest corpus is empty")
    rows: list[dict[str, Any]] = []
    desired_by_id: dict[str, dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(manifest_root).as_posix()
        source_bytes = path.read_bytes()
        source = json.loads(source_bytes.decode("utf-8-sig"))
        regenerated, decisions = regenerate_manifest(source)
        encoded = json.dumps(regenerated, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        target = output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        files.append({
            "path": relative,
            "source_sha256": sha256(source_bytes).hexdigest(),
            "regenerated_sha256": sha256(encoded).hexdigest(),
            "transaction_count": len(decisions),
        })
        for row in decisions:
            imported_id = row["imported_id"]
            prior = desired_by_id.get(imported_id)
            if prior is not None:
                qualifier = (
                    "Divergent duplicate"
                    if _canonical_json(prior) != _canonical_json(row)
                    else "Duplicate"
                )
                raise ValueError(f"{qualifier} imported_id across corpus: {imported_id}")
            desired_by_id[imported_id] = row
            rows.append({"manifest": relative, **row})

    topic_counts: dict[str, int] = {}
    for row in rows:
        topic_counts[row["topic"]] = topic_counts.get(row["topic"], 0) + 1
    exceptions = [
        row for row in rows
        if row["topic"] != "PURCHASE"
        or row["original_notes"] != row["desired_notes"]
        or row["original_category_name"] != row["desired_category_name"]
    ]
    report = {
        "schema_version": "actual-corpus-semantics-audit-v1",
        "manifest_count": len(files),
        "transaction_count": len(rows),
        "unique_imported_id_count": len(desired_by_id),
        "amount_and_sign_checked_count": len(rows),
        "amount_mutation_count": 0,
        "note_contract_checked_count": len(rows),
        "note_contract_violation_count": 0,
        "topic_counts": dict(sorted(topic_counts.items())),
        "exception_count": len(exceptions),
        "files": files,
        "exceptions": exceptions,
    }
    return report, desired_by_id


def build_guarded_migration_plan(
    snapshot: dict[str, Any], desired_by_id: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an exact-state, dry-run-only delta keyed by stable imported IDs."""
    changes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in snapshot.get("transactions") or []:
        if not isinstance(row, dict) or row.get("tombstone"):
            continue
        imported_id = str(row.get("imported_id") or "").strip()
        desired = desired_by_id.get(imported_id)
        if not desired:
            continue
        if imported_id in seen:
            raise ValueError(f"Duplicate imported_id in Actual snapshot: {imported_id}")
        seen.add(imported_id)
        if row.get("transfer_id") or row.get("is_parent") or row.get("is_child"):
            conflicts.append({"imported_id": imported_id, "reason": "STRUCTURAL_ACTUAL_STATE"})
            continue
        amount = row.get("amount")
        if amount != desired["amount"]:
            conflicts.append({
                "imported_id": imported_id,
                "reason": "AMOUNT_OR_SIGN_DRIFT",
                "expected_manifest_amount": desired["amount"],
                "actual_amount": amount,
            })
            continue
        current_category = str(row.get("category_name") or "")
        imported_category = str(desired["original_category_name"] or "")
        desired_category = str(desired["desired_category_name"] or "")
        if current_category not in {imported_category, desired_category}:
            conflicts.append({
                "imported_id": imported_id,
                "reason": "MANUAL_CATEGORY_PRESERVED",
                "actual_category_name": current_category,
                "imported_category_name": imported_category,
                "proposed_category_name": desired_category,
            })
            desired_category = current_category

        # Start from the current Actual note, not the old manifest note. This
        # preserves user memos, review text, document links, and non-semantic
        # tags while replacing only the controlled semantic tags.
        desired_notes = _notes_with_topic(str(row.get("notes") or ""), TopicDecision(
            topic=desired["topic"],
            source_direction=desired["source_direction"],
            reason=desired["reason"],
            category_name=desired_category or None,
            tags=tuple(
                tag
                for tag in parse_actual_notes(
                    desired["desired_notes"], legacy=False
                ).tags
                if tag in SEMANTIC_TAGS
            ),
            reward_medium=desired.get("reward_medium"),
            cash_equivalent=desired.get("cash_equivalent"),
        ))
        current_notes = str(row.get("notes") or "")
        if current_notes == desired_notes and current_category == desired_category:
            continue
        current_state = {
            "imported_id": imported_id,
            "account": str(row.get("account_name") or ""),
            "date": str(row.get("date") or ""),
            "amount": amount,
            "notes": current_notes,
            "category_name": current_category,
        }
        changes.append({
            "imported_id": imported_id,
            "account": current_state["account"],
            "date": current_state["date"],
            "expected_current_amount": amount,
            "expected_current_notes": current_notes,
            "expected_current_category_name": current_category,
            "desired_notes": desired_notes,
            "desired_category_name": desired_category,
            "topic": desired["topic"],
            "source_direction": desired["source_direction"],
            "guard_sha256": sha256(_canonical_json(current_state)).hexdigest(),
        })

    plan = {
        "schema_version": "actual-corpus-migration-v1",
        "mode": "DRY_RUN_ONLY",
        "expected_server_version": str(snapshot.get("server", {}).get("version") or "26.8.1"),
        "source_snapshot_generated_at": snapshot.get("generated_at"),
        "amount_mutation_count": 0,
        "changes": sorted(changes, key=lambda item: item["imported_id"]),
        "conflicts": sorted(conflicts, key=lambda item: (item["imported_id"], item["reason"])),
    }
    audit = {
        "schema_version": "actual-corpus-migration-audit-v1",
        "snapshot_transaction_count": len(snapshot.get("transactions") or []),
        "corpus_identity_count": len(desired_by_id),
        "matched_identity_count": len(seen),
        "change_count": len(changes),
        "conflict_count": len(conflicts),
        "missing_from_snapshot": sorted(set(desired_by_id) - seen),
        "amount_mutation_count": 0,
    }
    return plan, audit


def validate_guarded_migration_plan(
    snapshot: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Preflight a dry-run plan against the exact snapshot that it guards."""
    if plan.get("mode") != "DRY_RUN_ONLY":
        raise ValueError("Corpus migration plan must remain DRY_RUN_ONLY")
    if plan.get("amount_mutation_count") != 0:
        raise ValueError("Corpus migration cannot contain amount mutations")
    rows: dict[str, dict[str, Any]] = {}
    for row in snapshot.get("transactions") or []:
        imported_id = str(row.get("imported_id") or "").strip()
        if not imported_id or row.get("tombstone"):
            continue
        if imported_id in rows:
            raise ValueError(f"Duplicate imported_id in Actual snapshot: {imported_id}")
        rows[imported_id] = row
    checked = 0
    for change in plan.get("changes") or []:
        imported_id = str(change.get("imported_id") or "")
        row = rows.get(imported_id)
        if row is None:
            raise ValueError(f"Guarded transaction is absent: {imported_id}")
        current_state = {
            "imported_id": imported_id,
            "account": str(row.get("account_name") or ""),
            "date": str(row.get("date") or ""),
            "amount": row.get("amount"),
            "notes": str(row.get("notes") or ""),
            "category_name": str(row.get("category_name") or ""),
        }
        actual_guard = sha256(_canonical_json(current_state)).hexdigest()
        if actual_guard != change.get("guard_sha256"):
            raise ValueError(f"Exact-state guard drift for {imported_id}")
        if row.get("amount") != change.get("expected_current_amount"):
            raise ValueError(f"Amount/sign drift for {imported_id}")
        if change.get("desired_amount") is not None:
            raise ValueError("Downstream migration may not propose desired_amount")
        checked += 1
    return {
        "schema_version": "actual-corpus-migration-preflight-v1",
        "status": "PASS",
        "checked_change_count": checked,
        "amount_mutation_count": 0,
        "actual_write_performed": False,
    }
