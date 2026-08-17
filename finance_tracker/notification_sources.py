from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SOURCE_STATUSES = frozenset({"ACTIVE", "DISABLED", "PLACEHOLDER"})
SUBJECT_MATCH_MODES = frozenset({"EXACT", "PREFIX"})


@dataclass(frozen=True, slots=True)
class NotificationSource:
    code: str
    institution: str
    card_code: str
    status: str
    evidence_semantics: str
    adapter: str | None
    mail_folder: str | None
    senders: tuple[str, ...]
    subjects: tuple[str, ...]
    subject_match: str
    notes: str

    @property
    def active(self) -> bool:
        return self.status == "ACTIVE"


def _strings(value: object, field: str, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Notification source {code} {field} must be a string list")
    normalized = tuple(item.strip() for item in value if item.strip())
    if len(set(item.casefold() for item in normalized)) != len(normalized):
        raise ValueError(f"Notification source {code} {field} contains duplicates")
    return normalized


def load_notification_sources(path: Path) -> tuple[NotificationSource, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported transaction-email source schema version")
    rows = payload.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Transaction-email source registry must contain sources")

    sources: list[NotificationSource] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each transaction-email source must be an object")
        code = str(row.get("code") or "").strip().upper()
        if not code or code in seen:
            raise ValueError(f"Notification source code is blank or duplicated: {code}")
        seen.add(code)
        status = str(row.get("status") or "").strip().upper()
        if status not in SOURCE_STATUSES:
            raise ValueError(f"Notification source {code} has invalid status {status}")
        institution = str(row.get("institution") or "").strip()
        card_code = str(row.get("card_code") or "").strip().upper()
        semantics = str(row.get("evidence_semantics") or "").strip().upper()
        adapter = str(row.get("adapter") or "").strip() or None
        mail_folder = str(row.get("mail_folder") or "").strip() or None
        senders = _strings(row.get("senders"), "senders", code)
        subjects = _strings(row.get("subjects"), "subjects", code)
        subject_match = str(row.get("subject_match") or "EXACT").strip().upper()
        if subject_match not in SUBJECT_MATCH_MODES:
            raise ValueError(
                f"Notification source {code} subject_match must be one of {sorted(SUBJECT_MATCH_MODES)}"
            )
        if not institution or not card_code or not semantics:
            raise ValueError(f"Notification source {code} requires institution, card_code, and evidence_semantics")
        if status == "ACTIVE" and (not adapter or not mail_folder or not senders or not subjects):
            raise ValueError(
                f"Active notification source {code} requires an adapter, mail folder, sender, and subject"
            )
        if status == "PLACEHOLDER" and (adapter or mail_folder or senders or subjects):
            raise ValueError(
                f"Placeholder notification source {code} must remain unmatchable until verified"
            )
        sources.append(
            NotificationSource(
                code=code,
                institution=institution,
                card_code=card_code,
                status=status,
                evidence_semantics=semantics,
                adapter=adapter,
                mail_folder=mail_folder,
                senders=senders,
                subjects=subjects,
                subject_match=subject_match,
                notes=str(row.get("notes") or "").strip(),
            )
        )
    return tuple(sources)


def validate_notification_adapter_coverage(
    sources: Iterable[NotificationSource], adapter_codes: Iterable[str]
) -> None:
    configured = {source.adapter for source in sources if source.active and source.adapter}
    implemented = {str(code).strip() for code in adapter_codes if str(code).strip()}
    missing = configured - implemented
    if missing:
        raise ValueError(f"Configured notification adapters are not implemented: {sorted(missing)}")
