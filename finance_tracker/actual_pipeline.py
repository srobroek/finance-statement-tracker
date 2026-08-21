from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .ingestion import stage_statement
from .cashback import load_program_configuration, programs_from_config, purchase_type_from_config
from .ai_rules import (
    AIEnrichmentEngine,
    AITrace,
    load_ai_policies,
    load_ai_provider,
)
from .history import HistoryDecision, HistoryTrace, apply_history_match, load_history_index
from .platforms import ActualBudgetAdapter
from .properties import PropertyRegistry, load_property_registry, project_property_tags
from .rules import RuleAction, RuleCondition, RuleEngine, StaticRule
from .statements import NormalizedStatement, parse_statement_pdf
from .transaction_semantics import CASHBACK_TOPICS, finalize_transaction_topic
from .classification_audit import enforce_transaction_invariants


@dataclass(frozen=True, slots=True)
class ActualStatementRun:
    """Auditable hand-off from the deterministic parser to Actual's API."""

    schema_version: int
    source_file: str
    bank: str
    adapter: str
    statement: dict[str, object]
    staging_status: str
    review_count: int
    rule_trace: tuple[dict[str, object], ...]
    history_trace: tuple[dict[str, object], ...]
    ai_trace: tuple[dict[str, object], ...]
    envelopes: tuple[dict[str, object], ...]
    cashback_reconciliation: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_actual_config(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Actual bootstrap config must be an object")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ValueError("Actual bootstrap config schema_version must be 1")
    currency = payload.get("currency")
    if currency is not None and (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
    ):
        raise ValueError("Actual bootstrap config currency must be a three-letter code")
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("Actual bootstrap config requires a non-empty accounts list")
    for index, account in enumerate(accounts):
        if not isinstance(account, dict):
            raise ValueError(f"Actual bootstrap config account {index} must be an object")
        name = account.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Actual bootstrap config account {index} requires a name")
        for field in ("card_last4", "aliases"):
            values = account.get(field, [])
            if not isinstance(values, list):
                raise ValueError(f"Actual bootstrap config account {index} {field} must be a list")
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"Actual bootstrap config account {index} {field} values must be strings"
                    )
                if field == "card_last4" and (
                    len(value) != 4 or not value.isascii() or not value.isdigit()
                ):
                    raise ValueError(
                        f"Actual bootstrap config account {index} card_last4 values must be four digits"
                    )
        for field in ("card_code", "owner"):
            value = account.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"Actual bootstrap config account {index} {field} must be a string"
                )
    retired_accounts = payload.get("retired_accounts", [])
    if not isinstance(retired_accounts, list) or any(
        not isinstance(value, str) or not value.strip() for value in retired_accounts
    ):
        raise ValueError("Actual bootstrap config retired_accounts must be a list of strings")
    return payload


def account_maps(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return card-last4 -> card code and card code -> Actual account name."""
    card_by_last4: dict[str, str] = {}
    account_by_card: dict[str, str] = {}
    for account in config["accounts"]:
        name = str(account["name"])
        card_code = str(account.get("card_code") or name).upper()
        account_by_card[card_code] = name
        for last4 in account.get("card_last4", []):
            token = str(last4).strip()
            if token in card_by_last4 and card_by_last4[token] != card_code:
                raise ValueError(f"Card suffix {token} is mapped more than once")
            card_by_last4[token] = card_code
    return card_by_last4, account_by_card


def account_owner_map(config: dict[str, Any]) -> dict[str, str]:
    return {
        str(account.get("card_code") or account["name"]).upper(): str(account["owner"])
        for account in config["accounts"]
        if account.get("owner")
    }


def _condition(raw: dict[str, Any], group: int) -> RuleCondition:
    return RuleCondition(
        field=str(raw["field"]),
        operator=str(raw["operator"]),
        value=raw.get("value"),
        second_value=raw.get("second_value"),
        group=int(raw.get("group", group)),
        negate=bool(raw.get("negate", False)),
        case_sensitive=bool(raw.get("case_sensitive", False)),
    )


def load_compiled_rules(path: str | Path | None) -> list[StaticRule]:
    """Load the canonical portable AutoCat-style JSON rule contract."""
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else [payload]
    rules: list[StaticRule] = []
    for row in rows:
        raw_rule_sets = row.get("rule_sets", ["FULL_LEDGER"])
        if isinstance(raw_rule_sets, str):
            raw_rule_sets = [raw_rule_sets]
        conditions: list[RuleCondition] = []
        for group_index, group in enumerate(row.get("match", {}).get("any", []), start=1):
            conditions.extend(_condition(item, group_index) for item in group.get("all", []))
        actions = [
            RuleAction(
                action=str(item["action"]),
                field=item.get("field"),
                value=item.get("value"),
                sequence=int(item.get("sequence", 10)),
            )
            for item in row.get("actions", [])
        ]
        rules.append(
            StaticRule(
                rule_id=str(row["rule_id"]),
                name=str(row["name"]),
                stage=str(row["stage"]),
                priority=int(row["priority"]),
                conditions=conditions,
                actions=actions,
                stop_on_match=bool(row.get("stop_on_match", True)),
                schema_version=int(row.get("schema_version", 1)),
                rule_sets=tuple(str(value) for value in raw_rule_sets),
            )
        )
    return rules


def build_actual_statement_run(
    statement: NormalizedStatement,
    config: dict[str, Any],
    rules: Iterable[StaticRule] = (),
    *,
    source_message_id: str | None = None,
    history_index: dict[str, HistoryDecision] | None = None,
    ai_engine: AIEnrichmentEngine | None = None,
    ai_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    property_registry: PropertyRegistry | None = None,
) -> ActualStatementRun:
    card_by_last4, account_by_card = account_maps(config)
    owner_by_card = account_owner_map(config)
    staged = stage_statement(statement, card_by_last4, source_message_id=source_message_id)
    missing = sorted(
        {
            str(row.metadata.get("statement_card_last4") or "unknown")
            for row in staged.transactions
            if row.card == "UNMAPPED_CARD"
        }
    )
    if missing:
        raise ValueError("Unmapped card suffixes: " + ", ".join(missing))

    engine = RuleEngine(rules)
    traces = []
    history_traces: list[HistoryTrace] = []
    ai_traces: list[AITrace] = []
    if (ai_engine is None) != (ai_resolver is None):
        raise ValueError("ai_engine and ai_resolver must be supplied together")
    for transaction in staged.transactions:
        transaction.account = account_by_card[transaction.card]
        transaction.owner = owner_by_card.get(transaction.card)
        traces.extend(engine.apply_stages(transaction, ("TRANSACTION_NORMALIZATION",)))
        finalize_transaction_topic(transaction)
        traces.extend(
            engine.apply_stages(
                transaction,
                (
                    "VENDOR_NORMALIZATION",
                    "CLASSIFICATION",
                    "TAGGING",
                    "EVIDENCE",
                    "CASHBACK",
                ),
            )
        )
        if history_index and (history_trace := apply_history_match(transaction, history_index)):
            history_traces.append(history_trace)
        if ai_engine and ai_resolver:
            ai_traces.extend(ai_engine.enrich(transaction, ai_resolver))
        if property_registry:
            project_property_tags(transaction, property_registry)
        enforce_transaction_invariants(transaction)

    envelopes = ActualBudgetAdapter().serialize_import(staged.transactions)
    period_start = statement.period_start or min(
        transaction.transaction_at.date() for transaction in staged.transactions
    )
    period_end = statement.period_end or max(
        transaction.transaction_at.date() for transaction in staged.transactions
    )
    try:
        cashback_config = load_program_configuration()
        supported_cashback_cards = {
            program.card
            for program in programs_from_config(cashback_config, period_end)
        }
    except ValueError as error:
        if "no active programs" not in str(error):
            raise
        # Never back-apply a newer reward programme to an older statement.
        cashback_config = load_program_configuration()
        supported_cashback_cards = set()
    cashback_rows: dict[str, list[dict[str, object]]] = {}
    for transaction in staged.transactions:
        if transaction.card not in supported_cashback_cards:
            continue
        transaction_type = transaction.transaction_type.upper()
        if transaction_type not in CASHBACK_TOPICS:
            continue
        purchase_type = str(
            transaction.metadata.get("purchase_type")
            or purchase_type_from_config(
                cashback_config,
                transaction.category,
                transaction.vendor or transaction.merchant_raw,
            )
        ).upper().replace(" ", "_")
        cashback_rows.setdefault(transaction.card, []).append(
            {
                "statement_transaction_id": transaction.transaction_id,
                "occurred_at": transaction.transaction_at.isoformat(),
                "amount_aed": str(abs(transaction.amount_aed)),
                "currency": transaction.currency,
                "purchase_type": purchase_type,
                "channel": transaction.channel,
                "merchant": transaction.vendor or transaction.merchant_raw,
                "bucket_code": transaction.reward_bucket,
                "event_type": transaction_type,
                "tags": sorted(transaction.tags),
                "review_required": transaction.review_required,
            }
        )
    reconciliation = tuple(
        {
            "statement_reference": (
                f"{statement.bank}:{card}:{period_start.isoformat()}:{period_end.isoformat()}"
            ),
            "card_code": card,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "transactions": rows,
        }
        for card, rows in sorted(cashback_rows.items())
    )
    final_review_count = staged.review_count
    final_staging_status = (
        "READY_FOR_LEDGER_MATCH"
        if statement.balance_tied and final_review_count == 0
        else "REVIEW_REQUIRED"
    )
    return ActualStatementRun(
        schema_version=1,
        source_file=statement.source_file,
        bank=statement.bank,
        adapter=statement.adapter,
        statement={
            "statement_date": statement.statement_date.isoformat() if statement.statement_date else None,
            "period_start": statement.period_start.isoformat() if statement.period_start else None,
            "period_end": statement.period_end.isoformat() if statement.period_end else None,
            "payment_due_date": statement.payment_due_date.isoformat() if statement.payment_due_date else None,
            "opening_balance_aed": None if statement.opening_balance_aed is None else str(statement.opening_balance_aed),
            "closing_balance_aed": None if statement.closing_balance_aed is None else str(statement.closing_balance_aed),
            "balance_difference_aed": None if statement.balance_difference_aed is None else str(statement.balance_difference_aed),
            "balance_tied": statement.balance_tied,
            "transaction_count": len(statement.transactions),
            "warnings": list(statement.warnings),
        },
        staging_status=final_staging_status,
        review_count=final_review_count,
        rule_trace=tuple(asdict(trace) for trace in traces),
        history_trace=tuple(asdict(trace) for trace in history_traces),
        ai_trace=tuple({**asdict(trace), "decision_status": trace.decision_status} for trace in ai_traces),
        envelopes=tuple(asdict(envelope) for envelope in envelopes),
        cashback_reconciliation=reconciliation,
    )


def runtime_secret(name: str) -> str | None:
    """Resolve a runtime secret without placing its value in job payloads.

    ``<NAME>_FILE`` takes precedence over the legacy direct environment
    variable. Containers should mount a read-only secret file and set only the
    file path in their environment.
    """
    secret_path = os.environ.get(f"{name}_FILE")
    if secret_path:
        path = Path(secret_path)
        if not path.is_file():
            raise ValueError(f"Runtime secret file for {name} does not exist")
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError(f"Runtime secret file for {name} is empty")
        return value
    return os.environ.get(name)


def export_statement_for_actual(
    pdf_path: str | Path,
    config_path: str | Path,
    output_path: str | Path,
    *,
    password_env: str | None = "STATEMENT_PASSWORD",
    adapter_code: str | None = None,
    source_message_id: str | None = None,
    rules_path: str | Path | None = None,
    history_path: str | Path | None = None,
    ai_policies_path: str | Path | None = None,
    ai_provider_path: str | Path | None = None,
    ai_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    allow_unbalanced: bool = False,
) -> ActualStatementRun:
    """Create a durable run manifest without logging or persisting the PDF password."""
    statement = parse_statement_pdf(
        pdf_path,
        password=runtime_secret(password_env) if password_env else None,
        adapter_code=adapter_code,
    )
    if not statement.balance_tied and not allow_unbalanced:
        difference = statement.balance_difference_aed
        raise ValueError(
            "Statement arithmetic did not tie"
            + ("" if difference is None else f" (difference AED {difference})")
        )
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
    property_config = Path(config_path).parent / "properties.json"
    run = build_actual_statement_run(
        statement,
        load_actual_config(config_path),
        load_compiled_rules(rules_path),
        source_message_id=source_message_id,
        history_index=load_history_index(history_path) if history_path else None,
        ai_engine=ai_engine,
        ai_resolver=resolved_ai_resolver,
        property_registry=(
            load_property_registry(property_config) if property_config.is_file() else None
        ),
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    return run
