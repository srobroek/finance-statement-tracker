from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from .actual_pipeline import (
    account_owner_map,
    load_actual_config,
    load_compiled_rules,
)
from .ai_rules import AIEnrichmentEngine, AITrace, load_ai_policies, load_ai_provider
from .history import HistoryDecision, HistoryTrace, apply_history_match, load_history_index
from .models import Transaction, money
from .platforms import ActualBudgetAdapter
from .rules import RuleEngine, StaticRule


CAPTURE_KINDS = {
    "ACCOUNT_SNAPSHOT",
    "STATEMENT_PDF",
    "STATEMENT_ROWS",
    "TRANSACTION_ROWS",
}
CAPTURE_METHODS = {
    "ACCOUNT_OVERVIEW",
    "OFFICIAL_EXPORT",
    "STATEMENT_DOWNLOAD",
    "VISIBLE_ROWS",
}
REFUND_MATCH_WINDOW_DAYS = 60
SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "cookies",
    "cvv",
    "full_card_number",
    "mfa_code",
    "otp",
    "passcode",
    "password",
    "pin",
    "recovery_code",
    "refresh_token",
    "secret",
    "session",
    "session_token",
}


def _reject_sensitive_values(value: Any, path: str = "capture") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in SENSITIVE_KEYS:
                raise ValueError(f"Browser capture contains forbidden sensitive field: {path}.{key}")
            _reject_sensitive_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_values(child, f"{path}[{index}]")


def _required_text(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"{context}.{key} is required")
    return value


def _date(value: Any, context: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} must be an ISO date") from exc


def _datetime(value: Any, context: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{context} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be an ISO datetime") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _optional_money(value: Any) -> Decimal | None:
    return None if value in (None, "") else money(value)


def _safe_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source.url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source.url must not contain credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _visible_rows_approval(
    capture: Mapping[str, Any],
    *,
    capture_id: str,
    capture_method: str,
) -> dict[str, str] | None:
    """Validate an explicit owner approval for one immutable visible-row capture.

    Approval clears only the review reason created by the acquisition method.
    Source warnings, unclassified credits, missing currency evidence, and account
    mapping failures remain independent review gates.
    """
    raw = capture.get("approval")
    if raw in (None, {}):
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("capture.approval must be an object")
    if capture_method != "VISIBLE_ROWS":
        raise ValueError("capture.approval is valid only for VISIBLE_ROWS captures")
    status = _required_text(raw, "status", "capture.approval").upper()
    scope = _required_text(raw, "scope", "capture.approval").upper()
    approved_capture_id = _required_text(
        raw,
        "capture_id",
        "capture.approval",
    )
    approved_by = _required_text(raw, "approved_by", "capture.approval").upper()
    if status != "OWNER_APPROVED":
        raise ValueError("capture.approval.status must be OWNER_APPROVED")
    if scope != "ALL_VISIBLE_ROWS":
        raise ValueError("capture.approval.scope must be ALL_VISIBLE_ROWS")
    if approved_capture_id != capture_id:
        raise ValueError("capture.approval.capture_id must match capture.capture_id")
    if approved_by != "OWNER":
        raise ValueError("capture.approval.approved_by must be OWNER")
    approved_at = _datetime(raw.get("approved_at"), "capture.approval.approved_at")
    return {
        "status": status,
        "scope": scope,
        "capture_id": approved_capture_id,
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat(),
    }


def _slug(value: str) -> str:
    return "-".join(part for part in "".join(
        character.casefold() if character.isalnum() else " " for character in value
    ).split()) or "portal"


def _stable_transaction_id(
    provider: str,
    account_identity: str,
    row: Mapping[str, Any],
    transaction_date: date,
    amount_aed: Decimal,
    direction: str,
    occurrence: int,
) -> str:
    source_id = str(row.get("source_id") or row.get("reference") or "").strip()
    if source_id:
        material = f"source|{provider}|{account_identity}|{source_id}"
    else:
        description = " ".join(_required_text(row, "description", "row").upper().split())
        material = "|".join(
            (
                "identity",
                provider,
                account_identity,
                transaction_date.isoformat(),
                description,
                str(amount_aed),
                direction,
                str(occurrence),
            )
        )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"browser:{_slug(provider)}:{digest}"


@dataclass(frozen=True, slots=True)
class BrowserIngestionRun:
    schema_version: int
    capture_id: str
    source: dict[str, object]
    artifact: dict[str, object]
    account_snapshot: dict[str, object]
    statement_check: dict[str, object]
    staging_status: str
    review_count: int
    import_blockers: tuple[str, ...]
    transactions: tuple[dict[str, object], ...]
    rule_trace: tuple[dict[str, object], ...]
    history_trace: tuple[dict[str, object], ...]
    ai_trace: tuple[dict[str, object], ...]
    envelopes: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _resolve_account(
    capture_account: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    accounts = [dict(row) for row in config["accounts"]]
    requested_name = str(capture_account.get("actual_account") or "").strip().casefold()
    requested_code = str(capture_account.get("card_code") or "").strip().upper()
    requested_last4 = str(capture_account.get("account_last4") or "").strip()
    matches: list[dict[str, Any]] = []
    if requested_name:
        matches = [row for row in accounts if str(row["name"]).casefold() == requested_name]
    elif requested_code:
        matches = [
            row
            for row in accounts
            if str(row.get("card_code") or row["name"]).upper() == requested_code
        ]
    elif requested_last4:
        matches = [
            row
            for row in accounts
            if requested_last4 in {str(value) for value in row.get("card_last4", [])}
        ]
    if len(matches) == 1:
        return matches[0], ()
    if len(matches) > 1:
        return None, ("Account identifier matched more than one configured Actual account",)
    return None, ("Browser account is not mapped to a configured Actual account",)


def _statement_check(
    capture: Mapping[str, Any],
    transactions: Iterable[Transaction],
    account_type: str,
) -> dict[str, object]:
    raw = capture.get("statement")
    if not isinstance(raw, Mapping):
        return {
            "available": False,
            "balance_tied": False,
            "reason": "No statement balance metadata was captured",
        }
    opening = _optional_money(raw.get("opening_balance_aed"))
    closing = _optional_money(raw.get("closing_balance_aed"))
    convention = str(raw.get("balance_convention") or (
        "LIABILITY" if account_type.casefold() == "credit" else "ASSET"
    )).upper()
    if convention not in {"ASSET", "LIABILITY"}:
        raise ValueError("statement.balance_convention must be ASSET or LIABILITY")
    if opening is None or closing is None:
        return {
            "available": False,
            "balance_tied": False,
            "reason": "Opening and closing balances are both required",
            "balance_convention": convention,
        }
    debits = sum(
        (row.amount_aed for row in transactions if row.metadata["browser_direction"] == "DEBIT"),
        Decimal("0"),
    )
    credits = sum(
        (row.amount_aed for row in transactions if row.metadata["browser_direction"] == "CREDIT"),
        Decimal("0"),
    )
    calculated = (
        opening + debits - credits
        if convention == "LIABILITY"
        else opening - debits + credits
    )
    difference = calculated - closing
    return {
        "available": True,
        "balance_convention": convention,
        "opening_balance_aed": str(opening),
        "debit_total_aed": str(debits),
        "credit_total_aed": str(credits),
        "calculated_closing_balance_aed": str(calculated),
        "closing_balance_aed": str(closing),
        "balance_difference_aed": str(difference),
        "balance_tied": abs(difference) <= Decimal("0.01"),
        "statement_reference": str(raw.get("statement_reference") or "").strip() or None,
        "period_start": str(raw.get("period_start") or "").strip() or None,
        "period_end": str(raw.get("period_end") or "").strip() or None,
        "payment_due_date": str(raw.get("payment_due_date") or "").strip() or None,
    }


def _match_exact_refunds(transactions: Iterable[Transaction]) -> int:
    """Resolve a generic credit only when one exact prior purchase explains it."""
    rows = list(transactions)
    matched_purchase_ids: set[str] = set()
    resolved = 0
    for credit in rows:
        reasons = list(credit.metadata.get("browser_review_reasons", []))
        if (
            "UNCLASSIFIED_CREDIT" not in reasons
            or credit.transaction_type != "CREDIT"
            or credit.metadata.get("browser_direction") != "CREDIT"
        ):
            continue
        normalized_merchant = " ".join(credit.merchant_raw.upper().split())
        candidates: list[tuple[Transaction, int]] = []
        for purchase in rows:
            age_days = (credit.transaction_at.date() - purchase.transaction_at.date()).days
            same_amount = (
                purchase.amount_aed == credit.amount_aed
                if credit.currency == "AED"
                else (
                    purchase.amount_original is not None
                    and credit.amount_original is not None
                    and purchase.amount_original == credit.amount_original
                )
            )
            if (
                purchase.transaction_id not in matched_purchase_ids
                and purchase.metadata.get("browser_direction") == "DEBIT"
                and purchase.transaction_type == "PURCHASE"
                and purchase.account == credit.account
                and purchase.account_last4 == credit.account_last4
                and purchase.currency == credit.currency
                and same_amount
                and " ".join(purchase.merchant_raw.upper().split()) == normalized_merchant
                and 0 <= age_days <= REFUND_MATCH_WINDOW_DAYS
            ):
                candidates.append((purchase, age_days))
        if len(candidates) != 1:
            continue
        purchase, age_days = candidates[0]
        matched_purchase_ids.add(purchase.transaction_id)
        credit.transaction_type = "REFUND"
        credit.is_refund = True
        credit.vendor = credit.vendor or purchase.vendor
        credit.category = credit.category or purchase.category
        credit.subcategory = credit.subcategory or purchase.subcategory
        credit.tags.update({"refund", *purchase.tags})
        reasons.remove("UNCLASSIFIED_CREDIT")
        credit.metadata["browser_review_reasons"] = reasons
        credit.metadata.setdefault("browser_review_resolutions", []).append(
            "EXACT_UNIQUE_REFUND_PAIR"
        )
        credit.metadata["browser_refund_match"] = {
            "purchase_transaction_id": purchase.transaction_id,
            "age_days": age_days,
            "window_days": REFUND_MATCH_WINDOW_DAYS,
        }
        credit.review_required = bool(reasons)
        resolved += 1
    return resolved


def build_browser_ingestion_run(
    capture: Mapping[str, Any],
    config: Mapping[str, Any],
    rules: Iterable[StaticRule] = (),
    *,
    history_index: dict[str, HistoryDecision] | None = None,
    ai_engine: AIEnrichmentEngine | None = None,
    ai_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> BrowserIngestionRun:
    _reject_sensitive_values(capture)
    if capture.get("schema_version") != 1:
        raise ValueError("Browser capture schema_version must be 1")
    capture_id = _required_text(capture, "capture_id", "capture")
    source = capture.get("source")
    artifact = capture.get("artifact")
    account = capture.get("account")
    if not isinstance(source, Mapping) or not isinstance(artifact, Mapping) or not isinstance(account, Mapping):
        raise ValueError("Browser capture requires source, artifact, and account objects")
    provider = _required_text(source, "provider", "source")
    captured_at = _datetime(source.get("captured_at"), "source.captured_at")
    method = _required_text(source, "capture_method", "source").upper()
    kind = _required_text(artifact, "kind", "artifact").upper()
    if method not in CAPTURE_METHODS:
        raise ValueError(f"Unsupported browser capture method: {method}")
    if kind not in CAPTURE_KINDS:
        raise ValueError(f"Unsupported browser artifact kind: {kind}")
    approval = _visible_rows_approval(
        capture,
        capture_id=capture_id,
        capture_method=method,
    )
    sanitized_source: dict[str, object] = {
        "provider": provider,
        "site": str(source.get("site") or provider).strip(),
        "url": _safe_url(source.get("url")),
        "page_context": str(source.get("page_context") or "").strip() or None,
        "capture_method": method,
        "captured_at": captured_at.isoformat(),
        "date_range": dict(source.get("date_range") or {}),
        "limitations": [str(value) for value in source.get("limitations", [])],
        "approval": approval,
    }
    resolved_account, account_blockers = _resolve_account(account, config)
    account_name = str(resolved_account["name"]) if resolved_account else None
    card_code = (
        str(resolved_account.get("card_code") or resolved_account["name"]).upper()
        if resolved_account
        else "UNMAPPED_ACCOUNT"
    )
    account_last4 = str(account.get("account_last4") or "").strip() or None
    account_identity = account_name or account_last4 or _required_text(account, "label", "account")
    snapshot = {
        "label": _required_text(account, "label", "account"),
        "actual_account": account_name,
        "card_code": None if not resolved_account else card_code,
        "account_last4": account_last4,
        "currency": str(account.get("currency") or "AED").upper(),
        "balance": None if account.get("balance") in (None, "") else str(money(account["balance"])),
        "available_balance": (
            None
            if account.get("available_balance") in (None, "")
            else str(money(account["available_balance"]))
        ),
        "balance_as_of": str(account.get("balance_as_of") or "").strip() or None,
        "balance_posting_allowed": False,
    }
    artifact_summary = {
        "kind": kind,
        "local_path": str(artifact.get("local_path") or "").strip() or None,
        "file_name": str(artifact.get("file_name") or "").strip() or None,
        "mime_type": str(artifact.get("mime_type") or "").strip() or None,
        "download_reference": str(artifact.get("download_reference") or "").strip() or None,
    }
    if kind in {"ACCOUNT_SNAPSHOT", "STATEMENT_PDF"}:
        status = "ACCOUNT_REVIEW_REQUIRED" if kind == "ACCOUNT_SNAPSHOT" else "ROUTE_TO_STATEMENT_PIPELINE"
        blockers = account_blockers if kind == "ACCOUNT_SNAPSHOT" else ()
        if kind == "STATEMENT_PDF" and not artifact_summary["local_path"]:
            raise ValueError("artifact.local_path is required for STATEMENT_PDF")
        return BrowserIngestionRun(
            schema_version=1,
            capture_id=capture_id,
            source=sanitized_source,
            artifact=artifact_summary,
            account_snapshot=snapshot,
            statement_check={"available": False, "balance_tied": False},
            staging_status=status,
            review_count=1,
            import_blockers=tuple(blockers),
            transactions=(),
            rule_trace=(),
            history_trace=(),
            ai_trace=(),
            envelopes=(),
        )

    raw_rows = capture.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows or any(not isinstance(row, Mapping) for row in raw_rows):
        raise ValueError(f"{kind} requires a non-empty rows list")
    if (ai_engine is None) != (ai_resolver is None):
        raise ValueError("ai_engine and ai_resolver must be supplied together")
    seen_source_ids: set[str] = set()
    identities: dict[tuple[object, ...], int] = {}
    transactions: list[Transaction] = []
    for index, row in enumerate(raw_rows, start=1):
        row_context = f"rows[{index - 1}]"
        when = _date(row.get("transaction_date"), f"{row_context}.transaction_date")
        description = _required_text(row, "description", row_context)
        amount_aed = abs(money(row.get("amount_aed")))
        if amount_aed == 0:
            raise ValueError(f"{row_context}.amount_aed must be non-zero")
        direction = _required_text(row, "direction", row_context).upper()
        if direction not in {"CREDIT", "DEBIT"}:
            raise ValueError(f"{row_context}.direction must be CREDIT or DEBIT")
        source_id = str(row.get("source_id") or "").strip()
        if source_id:
            if source_id in seen_source_ids:
                raise ValueError(f"Duplicate browser source_id: {source_id}")
            seen_source_ids.add(source_id)
        identity = (
            when,
            " ".join(description.upper().split()),
            amount_aed,
            direction,
        )
        occurrence = identities.get(identity, 0) + 1
        identities[identity] = occurrence
        row_last4 = str(row.get("account_last4") or account_last4 or "").strip() or None
        currency = str(row.get("currency") or snapshot["currency"] or "AED").upper()
        amount_original = _optional_money(row.get("amount_original"))
        explicit_type = str(row.get("transaction_type") or "").strip().upper()
        review_reasons: list[str] = []
        if bool(row.get("review_required", False)):
            review_reasons.append("SOURCE_REVIEW_REQUIRED")
        if not explicit_type and direction == "CREDIT":
            explicit_type = "CREDIT"
            review_reasons.append("UNCLASSIFIED_CREDIT")
        transaction_type = explicit_type or "PURCHASE"
        if currency != "AED" and amount_original is None:
            review_reasons.append("MISSING_AED_EQUIVALENT")
        if method == "VISIBLE_ROWS" and approval is None:
            review_reasons.append("VISIBLE_ROWS_REQUIRE_REVIEW")
        if resolved_account is None:
            review_reasons.append("UNMAPPED_ACCOUNT")
        transaction = Transaction(
            transaction_id=_stable_transaction_id(
                provider,
                f"{account_identity}:{row_last4 or ''}",
                row,
                when,
                amount_aed,
                direction,
                occurrence,
            ),
            transaction_at=datetime.combine(when, time.min),
            card=card_code,
            account=account_name,
            owner=None,
            institution=provider,
            account_last4=row_last4,
            merchant_raw=description,
            amount_aed=amount_aed,
            currency=currency,
            amount_original=amount_original,
            channel=str(row.get("channel") or "UNKNOWN"),
            source_type="browser_portal",
            transaction_type=transaction_type,
            review_required=bool(review_reasons),
            is_refund=transaction_type == "REFUND",
            tags={
                "browser-import",
                *(
                    {str(row.get("card_role")).strip().casefold()}
                    if str(row.get("card_role") or "").strip()
                    else set()
                ),
            },
            metadata={
                "import_status": "STAGED",
                "capture_id": capture_id,
                "browser_provider": provider,
                "browser_capture_method": method,
                "browser_page_context": sanitized_source["page_context"],
                "browser_source_url": sanitized_source["url"],
                "browser_source_id": source_id or None,
                "browser_reference": str(row.get("reference") or "").strip() or None,
                "browser_post_date": str(row.get("post_date") or "").strip() or None,
                "browser_direction": direction,
                "browser_status": str(row.get("status") or "").strip().upper() or None,
                "browser_review_reasons": review_reasons,
                "browser_review_resolutions": (
                    ["OWNER_APPROVED_VISIBLE_CAPTURE"] if approval is not None else []
                ),
                "browser_capture_limitations": sanitized_source["limitations"],
                "ledger_reconciled": False,
                "locked_fields": [
                    "transaction_id",
                    "transaction_at",
                    "amount_aed",
                    "amount_original",
                    "source_message_id",
                ],
            },
        )
        transactions.append(transaction)

    statement_check = _statement_check(
        capture,
        transactions,
        str((resolved_account or {}).get("type") or "checking"),
    )
    authoritative_statement = (
        kind == "STATEMENT_ROWS"
        and method == "OFFICIAL_EXPORT"
        and statement_check.get("balance_tied") is True
        and bool(statement_check.get("statement_reference"))
    )
    if kind == "STATEMENT_ROWS" and not authoritative_statement:
        for transaction in transactions:
            transaction.review_required = True
    if authoritative_statement:
        for transaction in transactions:
            transaction.source_type = "browser_statement"

    engine = RuleEngine(rules)
    rule_traces = []
    history_traces: list[HistoryTrace] = []
    ai_traces: list[AITrace] = []
    owner_by_card = account_owner_map(dict(config))
    for transaction in transactions:
        transaction.owner = owner_by_card.get(transaction.card)
        rule_traces.extend(engine.apply_stages(
            transaction,
            (
                "TRANSACTION_NORMALIZATION",
                "VENDOR_NORMALIZATION",
                "CLASSIFICATION",
                "TAGGING",
                "EVIDENCE",
                "CASHBACK",
            ),
        ))
        review_reasons = list(transaction.metadata.get("browser_review_reasons", []))
        if (
            "UNCLASSIFIED_CREDIT" in review_reasons
            and transaction.transaction_type in {
                "TRANSFER",
                "INCOME",
                "REWARD_CREDIT",
                "REFUND",
            }
            and transaction.category
        ):
            review_reasons.remove("UNCLASSIFIED_CREDIT")
            transaction.metadata["browser_review_reasons"] = review_reasons
            transaction.metadata.setdefault("browser_review_resolutions", []).append(
                "STATIC_RULE_CLASSIFIED_CREDIT"
            )
            transaction.review_required = bool(review_reasons)
        if history_index and (history_trace := apply_history_match(transaction, history_index)):
            history_traces.append(history_trace)
        if ai_engine and ai_resolver:
            ai_traces.extend(ai_engine.enrich(transaction, ai_resolver))

    _match_exact_refunds(transactions)

    blockers = list(account_blockers)
    if kind == "STATEMENT_ROWS" and not authoritative_statement:
        blockers.append("Statement rows are not authoritative until an official export ties to balances and has a statement reference")
    if blockers:
        envelopes: tuple[dict[str, object], ...] = ()
        status = "UNMAPPED_ACCOUNT" if account_blockers else "REVIEW_REQUIRED"
    else:
        envelopes = tuple(asdict(item) for item in ActualBudgetAdapter().serialize_import(transactions))
        status = "REVIEW_REQUIRED" if any(row.review_required for row in transactions) else "READY_FOR_APPROVAL"
    return BrowserIngestionRun(
        schema_version=1,
        capture_id=capture_id,
        source=sanitized_source,
        artifact=artifact_summary,
        account_snapshot=snapshot,
        statement_check=statement_check,
        staging_status=status,
        review_count=sum(row.review_required for row in transactions),
        import_blockers=tuple(blockers),
        transactions=tuple(row.to_dict() for row in transactions),
        rule_trace=tuple(asdict(trace) for trace in rule_traces),
        history_trace=tuple(asdict(trace) for trace in history_traces),
        ai_trace=tuple({**asdict(trace), "decision_status": trace.decision_status} for trace in ai_traces),
        envelopes=envelopes,
    )


def export_browser_capture_for_actual(
    capture_path: str | Path,
    config_path: str | Path,
    output_path: str | Path,
    *,
    rules_path: str | Path | None = None,
    history_path: str | Path | None = None,
    ai_policies_path: str | Path | None = None,
    ai_provider_path: str | Path | None = None,
    ai_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> BrowserIngestionRun:
    capture = json.loads(Path(capture_path).read_text(encoding="utf-8"))
    ai_engine = None
    resolved_ai_resolver = ai_resolver
    if resolved_ai_resolver is not None:
        if not ai_policies_path:
            raise ValueError("An AI resolver requires an AI policies file")
        ai_engine = AIEnrichmentEngine(load_ai_policies(ai_policies_path))
    elif ai_policies_path or ai_provider_path:
        if not ai_policies_path or not ai_provider_path:
            raise ValueError("AI enrichment requires both a policies file and provider configuration")
        ai_engine = AIEnrichmentEngine(load_ai_policies(ai_policies_path))
        resolved_ai_resolver = load_ai_provider(ai_provider_path)
    run = build_browser_ingestion_run(
        capture,
        load_actual_config(config_path),
        load_compiled_rules(rules_path),
        history_index=load_history_index(history_path) if history_path else None,
        ai_engine=ai_engine,
        ai_resolver=resolved_ai_resolver,
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    return run
