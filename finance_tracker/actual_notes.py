from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Iterable


_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config" / "actual-note-contract.json"
_TAG_PATTERN = re.compile(r"(?<!\S)#([A-Za-z0-9][A-Za-z0-9_:-]*)")
_VALID_TAG = re.compile(r"^[a-z0-9][a-z0-9_:-]*$")
_LEGACY_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")
_FX = re.compile(r"^FX:?\s+([A-Za-z]{3})(?:\s+(.+))?$", re.I)


def _load_contract() -> dict[str, object]:
    source = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    if source.get("schema_version") != "actual-note-contract-v2":
        raise ValueError("Unsupported Actual note contract")
    return source


CONTRACT = _load_contract()
DELIMITER = str(CONTRACT["delimiter"])
FORBIDDEN_TAGS = frozenset(str(value).casefold() for value in CONTRACT["forbidden_tags"])
FORBIDDEN_TAG_PREFIXES = tuple(
    str(value).casefold() for value in CONTRACT.get("forbidden_tag_prefixes", [])
)
DISCARDED_LEGACY_FIELDS = frozenset(
    str(value).casefold() for value in CONTRACT["discarded_legacy_fields"]
)
DOCUMENT_ROOT = str(CONTRACT["document_root"])


@dataclass(frozen=True, slots=True)
class ActualNoteParts:
    tags: tuple[str, ...] = ()
    documents: tuple[str, ...] = ()
    reviews: tuple[str, ...] = ()
    memos: tuple[str, ...] = ()


def normalize_actual_tag(value: str) -> str:
    """Normalize a semantic Actual tag while retaining namespace colons."""
    token = re.sub(r"[^A-Za-z0-9_:-]+", "-", str(value).strip()).strip("-_: ")
    token = token.casefold()
    if not token or not _VALID_TAG.fullmatch(token):
        raise ValueError(f"Invalid Actual tag: {value!r}")
    if token in FORBIDDEN_TAGS or token.startswith(FORBIDDEN_TAG_PREFIXES):
        raise ValueError(f"Technical Actual tag is forbidden by the note contract: #{token}")
    return token


def _clean_detail(value: object, label: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise ValueError(f"Actual note {label} value cannot be blank")
    if "|" in text or len(text) > 500:
        raise ValueError(f"Actual note {label} value is unsafe")
    return text


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(values), key=str.casefold))


def _format_parts(parts: ActualNoteParts) -> str:
    segments: list[str] = []
    if parts.tags:
        segments.append(" ".join(f"#{tag}" for tag in _unique_sorted(parts.tags)))
    segments.extend(f"Doc: {value}" for value in _unique_sorted(parts.documents))
    segments.extend(f"Review: {value}" for value in _unique_sorted(parts.reviews))
    segments.extend(f"Memo: {value}" for value in _unique_sorted(parts.memos))
    return DELIMITER.join(segments)


def format_actual_notes(
    *,
    tags: Iterable[str] = (),
    documents: Iterable[str] = (),
    reviews: Iterable[str] = (),
    memos: Iterable[str] = (),
) -> str:
    """Format the only note grammar accepted at the Actual import boundary.

    Grammar: ``#tag #tag | Doc: ... | Review: ... | Memo: ...``.
    Collections are de-duplicated and sorted so ingestion is byte-idempotent.
    """
    normalized_documents = []
    for value in documents:
        document = _clean_detail(value, "Doc")
        if not document.startswith(DOCUMENT_ROOT):
            raise ValueError(f"Actual note documents must live below {DOCUMENT_ROOT!r}")
        normalized_documents.append(document)
    parts = ActualNoteParts(
        tags=_unique_sorted(normalize_actual_tag(value) for value in tags),
        documents=_unique_sorted(normalized_documents),
        reviews=_unique_sorted(_clean_detail(value, "Review") for value in reviews),
        memos=_unique_sorted(_clean_detail(value, "Memo") for value in memos),
    )
    result = _format_parts(parts)
    validate_actual_notes(result)
    return result


def parse_actual_notes(notes: str, *, legacy: bool = False) -> ActualNoteParts:
    """Parse canonical notes, or conservatively normalize legacy notes."""
    source = str(notes or "").strip()
    if not source:
        return ActualNoteParts()

    tags: list[str] = []
    documents: list[str] = []
    reviews: list[str] = []
    memos: list[str] = []

    for match in _TAG_PATTERN.finditer(source):
        raw = match.group(1).casefold()
        if (
            raw in FORBIDDEN_TAGS
            or raw.startswith(FORBIDDEN_TAG_PREFIXES)
        ) and legacy:
            continue
        tags.append(normalize_actual_tag(raw))

    without_tags = _TAG_PATTERN.sub("", source)
    for raw_segment in without_tags.split("|"):
        segment = " ".join(raw_segment.split()).strip()
        if not segment:
            continue
        field_match = _LEGACY_FIELD.match(segment)
        if field_match:
            field = field_match.group(1).casefold()
            value = field_match.group(2).strip()
            if field == "doc":
                documents.append(_clean_detail(value, "Doc"))
            elif field == "fx" and legacy:
                continue
            elif field == "review":
                reviews.append(_clean_detail(value, "Review"))
            elif field == "memo":
                memos.append(_clean_detail(value, "Memo"))
            elif legacy and field == "evidence" and value.startswith(DOCUMENT_ROOT):
                documents.append(value)
            elif legacy and field in DISCARDED_LEGACY_FIELDS:
                continue
            elif legacy:
                # Key-like legacy fields are metadata outside the public contract.
                continue
            else:
                raise ValueError(f"Unsupported Actual note field: {field_match.group(1)}")
            continue
        fx_match = _FX.match(segment)
        if fx_match:
            if not legacy:
                raise ValueError(f"Unsupported Actual note segment: {segment!r}")
        elif legacy and segment.casefold() in {"evidence", "primary", "statement"}:
            continue
        elif legacy:
            memos.append(_clean_detail(segment, "Memo"))
        else:
            raise ValueError(f"Unsupported Actual note segment: {segment!r}")

    return ActualNoteParts(
        tags=_unique_sorted(tags),
        documents=_unique_sorted(documents),
        reviews=_unique_sorted(reviews),
        memos=_unique_sorted(memos),
    )


def canonicalize_actual_notes(notes: str) -> str:
    parts = parse_actual_notes(notes, legacy=True)
    return format_actual_notes(
        tags=parts.tags,
        documents=parts.documents,
        reviews=parts.reviews,
        memos=parts.memos,
    )


def add_actual_document(notes: str, relative_path: str) -> str:
    parts = parse_actual_notes(notes, legacy=True)
    return format_actual_notes(
        tags=parts.tags,
        documents=(*parts.documents, relative_path),
        reviews=parts.reviews,
        memos=parts.memos,
    )


def validate_actual_notes(notes: str) -> None:
    source = str(notes or "")
    parts = parse_actual_notes(source, legacy=False)
    rebuilt = _format_parts(parts)
    if source != rebuilt:
        raise ValueError(f"Actual notes are not canonical: {source!r}; expected {rebuilt!r}")


def build_actual_note_cleanup_plan(
    snapshot: dict[str, Any],
    *,
    expected_server_version: str = "26.8.1",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an exact-state, notes-only enrichment plan from an Actual snapshot."""
    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    changes_by_account: Counter[str] = Counter()
    removed_legacy_fields: Counter[str] = Counter()
    removed_technical_tags: Counter[str] = Counter()
    removed_technical_tag_prefixes: Counter[str] = Counter()
    desired_empty_count = 0
    seen_imported_ids: set[str] = set()
    scanned = 0
    for row in snapshot.get("transactions") or []:
        if not isinstance(row, dict) or row.get("tombstone"):
            continue
        scanned += 1
        current = str(row.get("notes") or "")
        desired = canonicalize_actual_notes(current)
        if desired == current:
            continue
        for tag in FORBIDDEN_TAGS:
            removed_technical_tags[tag] += len(
                re.findall(rf"(?<!\S)#{re.escape(tag)}(?=\s|$)", current, re.I)
            )
        for prefix in FORBIDDEN_TAG_PREFIXES:
            removed_technical_tag_prefixes[prefix] += len(
                re.findall(
                    rf"(?<!\S)#{re.escape(prefix)}[A-Za-z0-9_:-]*(?=\s|$)",
                    current,
                    re.I,
                )
            )
        for field in DISCARDED_LEGACY_FIELDS:
            removed_legacy_fields[field] += len(
                re.findall(rf"(?:^|\|)\s*{re.escape(field)}\s*:", current, re.I)
            )
        imported_id = str(row.get("imported_id") or "").strip()
        reason = None
        if not imported_id:
            reason = "NO_IMPORTED_ID"
        elif row.get("transfer_id"):
            reason = "ACTUAL_TRANSFER"
        elif row.get("is_parent") or row.get("is_child"):
            reason = "SPLIT_TRANSACTION"
        elif not str(row.get("account_name") or "").strip():
            reason = "NO_ACCOUNT_NAME"
        elif not isinstance(row.get("amount"), int):
            reason = "INVALID_AMOUNT"
        if reason:
            skipped.append({
                "actual_id": str(row.get("id") or ""),
                "imported_id": imported_id,
                "reason": reason,
            })
            continue
        if imported_id in seen_imported_ids:
            raise ValueError(f"Duplicate imported_id in Actual snapshot: {imported_id}")
        seen_imported_ids.add(imported_id)
        changes_by_account[str(row["account_name"])] += 1
        if not desired:
            desired_empty_count += 1
        changes.append({
            "imported_id": imported_id,
            "account": str(row["account_name"]),
            "date": str(row["date"]),
            "expected_current_amount": row["amount"],
            "expected_current_notes": current,
            "desired_notes": desired,
        })
    plan = {
        "schema_version": "actual-transaction-enrichment-v1",
        "expected_server_version": expected_server_version,
        "reason": "Normalize Actual notes to actual-note-contract-v2",
        "changes": changes,
    }
    audit = {
        "schema_version": "actual-note-cleanup-audit-v1",
        "snapshot_generated_at": snapshot.get("generated_at"),
        "scanned_count": scanned,
        "change_count": len(changes),
        "unchanged_count": scanned - len(changes) - len(skipped),
        "desired_empty_count": desired_empty_count,
        "changes_by_account": dict(sorted(changes_by_account.items())),
        "removed_technical_tags": {
            key: value for key, value in sorted(removed_technical_tags.items()) if value
        },
        "removed_technical_tag_prefixes": {
            key: value
            for key, value in sorted(removed_technical_tag_prefixes.items())
            if value
        },
        "removed_legacy_fields": {
            key: value for key, value in sorted(removed_legacy_fields.items()) if value
        },
        "skipped_count": len(skipped),
        "skipped": skipped,
    }
    return plan, audit
