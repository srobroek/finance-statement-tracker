from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


STATUSES = frozenset({"ACTIVE", "PLACEHOLDER"})


@dataclass(frozen=True, slots=True)
class StatementSource:
    card_code: str
    institution: str
    adapter_status: str
    adapter: str | None
    password_env: str | None
    email_status: str
    email_senders: tuple[str, ...]
    email_subjects: tuple[str, ...]
    notes: str

    @property
    def adapter_active(self) -> bool:
        return self.adapter_status == "ACTIVE"


def _list(value: object, field: str, card: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Statement source {card} {field} must be a string list")
    return tuple(item.strip() for item in value if item.strip())


def load_statement_sources(path: Path) -> tuple[StatementSource, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported statement source schema version")
    rows = payload.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Statement source registry must contain sources")
    result: list[StatementSource] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Statement source rows must be objects")
        card = str(row.get("card_code") or "").strip().upper()
        if not card or card in seen:
            raise ValueError(f"Statement card_code is blank or duplicated: {card}")
        seen.add(card)
        adapter_status = str(row.get("adapter_status") or "").strip().upper()
        email_status = str(row.get("email_status") or "").strip().upper()
        if adapter_status not in STATUSES or email_status not in STATUSES:
            raise ValueError(f"Statement source {card} has an invalid status")
        adapter = str(row.get("adapter") or "").strip() or None
        raw_password_env = row.get("password_env")
        password_env = None if raw_password_env is None else str(raw_password_env).strip() or None
        if password_env and not re.fullmatch(r"[A-Z][A-Z0-9_]*", password_env):
            raise ValueError(
                f"Statement source {card} password_env must be an uppercase environment name"
            )
        senders = _list(row.get("email_senders"), "email_senders", card)
        subjects = _list(row.get("email_subjects"), "email_subjects", card)
        if adapter_status == "ACTIVE" and not adapter:
            raise ValueError(f"Active statement source {card} requires an adapter")
        if adapter_status == "PLACEHOLDER" and adapter:
            raise ValueError(f"Placeholder statement source {card} cannot name an adapter")
        if email_status == "ACTIVE" and (not senders or not subjects):
            raise ValueError(f"Active statement email source {card} requires sender and subject filters")
        if email_status == "PLACEHOLDER" and (senders or subjects):
            raise ValueError(f"Placeholder statement email source {card} must remain unmatchable")
        result.append(
            StatementSource(
                card_code=card,
                institution=str(row.get("institution") or "").strip(),
                adapter_status=adapter_status,
                adapter=adapter,
                password_env=password_env,
                email_status=email_status,
                email_senders=senders,
                email_subjects=subjects,
                notes=str(row.get("notes") or "").strip(),
            )
        )
    return tuple(result)


def validate_statement_adapter_coverage(
    sources: Iterable[StatementSource], adapter_codes: Iterable[str]
) -> None:
    configured = {source.adapter for source in sources if source.adapter_active and source.adapter}
    implemented = {str(code).strip() for code in adapter_codes if str(code).strip()}
    missing = configured - implemented
    undeclared = implemented - configured
    if missing:
        raise ValueError(f"Configured statement adapters are not implemented: {sorted(missing)}")
    if undeclared:
        raise ValueError(f"Implemented statement adapters are not declared: {sorted(undeclared)}")


def require_active_statement_adapter(
    sources: Iterable[StatementSource], card_code: str, requested_adapter: str | None
) -> str:
    card = str(card_code or "").strip().upper()
    source = next((item for item in sources if item.card_code == card), None)
    if source is None:
        raise ValueError(f"Statement source is not configured for {card}")
    if not source.adapter_active or not source.adapter:
        raise ValueError(f"Statement adapter for {card} is a placeholder and cannot ingest")
    if requested_adapter and requested_adapter != source.adapter:
        raise ValueError(
            f"Statement adapter {requested_adapter} does not match configured adapter {source.adapter} for {card}"
        )
    return source.adapter


def require_active_statement_source(
    sources: Iterable[StatementSource], card_code: str, requested_adapter: str | None
) -> StatementSource:
    materialized = tuple(sources)
    adapter = require_active_statement_adapter(materialized, card_code, requested_adapter)
    card = str(card_code or "").strip().upper()
    source = next(item for item in materialized if item.card_code == card)
    if source.adapter != adapter:  # Defensive: require_active_statement_adapter already enforces this.
        raise ValueError(f"Statement adapter resolution failed for {card}")
    return source
