"""Deterministic, provider-free real-mail ingestion acceptance contract.

This harness is intentionally separate from n8n and from the provider
connectors.  It models the durable boundaries that the eventual Outlook ->
OneDrive -> n8n -> Actual/Cashback run must satisfy:

* immutable message and attachment identities are enumerated once;
* email and every attachment are archived and hash-read back before a cursor
  can move;
* accepted PDFs produce exactly one outbox, Actual, and optional Cashback row;
* a cursor is advanced with compare-and-swap (CAS), and replay is a no-op;
* process restarts can occur at four durable checkpoints; and
* a receipt says explicitly that this is synthetic evidence, never provider
  proof.

No network, container, credential, subprocess, or secret access is performed
by this module.  The command-line entry point only writes a caller-selected
receipt file.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "real-mail-e2e-receipt-v1.schema.json"
BUNDLE_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "real-mail-e2e-fixture-bundle-v1.schema.json"
MAX_SYNTHETIC_COUNT = 1024
EXPECTED_WORKFLOW_CHAIN = ("W12", "W01", "W22", "W17", "W03", "W20")
EXPECTED_TABLE_NAMES = ("email_archive", "attachment_archive", "pipeline_outbox", "cursor_cas")
SENSITIVE_KEY_MARKERS = (
    "access_token",
    "api_key",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_cookie",
    "token",
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?:access[_ -]?token|api[_ -]?key|client[_ -]?secret|password|refresh[_ -]?token|secret|token)\s*[:=]", re.IGNORECASE),
    re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9+/=._~-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|ghp|glpat|xox[baprs])-[-A-Za-z0-9_]{12,}\b", re.IGNORECASE),
)

# These are deliberately named after the durable boundaries, rather than
# process implementation details.  A restart at each point must recover on a
# second invocation without creating a duplicate external effect.
RESTART_INJECTION_POINTS = (
    "after_archive",
    "after_actual",
    "after_cashback",
    "after_cursor_cas",
)

DEFAULT_WORKFLOW_PATHS = (
    "integrations/n8n/workflows/12-outlook-message-sweep.json",
    "integrations/n8n/workflows/01-outlook-finance-acquisition.json",
    "integrations/n8n/workflows/03-shared-statement-pipeline.json",
    "integrations/n8n/workflows/04-ei-monthly-statement.json",
    "integrations/n8n/workflows/05-wio-monthly-statement.json",
    "integrations/n8n/workflows/22-shared-monthly-statement-cycle.json",
    "integrations/n8n/workflows/17-actual-outbox-recovery.json",
    "integrations/n8n/workflows/20-actual-outbox-apply.json",
)

DEFAULT_SOURCE_PATHS = (
    "integrations/n8n/e2e/real_mail_e2e.py",
    "integrations/n8n/e2e/generate_real_mail_fixtures.py",
    "integrations/n8n/schemas/real-mail-e2e-receipt-v1.schema.json",
    "integrations/n8n/schemas/real-mail-e2e-fixture-bundle-v1.schema.json",
)

DEFAULT_CONFIG_PATHS = (
    "integrations/n8n/pipeline-registry.json",
    "integrations/n8n/application-manifest.json",
    "config/statement-sources.json",
    "config/transaction-email-sources.json",
)


class ContractError(ValueError):
    """Raised when a synthetic acceptance invariant fails closed."""


class InjectedRestart(RuntimeError):
    """Controlled process-stop used only by the restart matrix."""

    def __init__(self, point: str) -> None:
        super().__init__(f"SYNTHETIC_RESTART_INJECTED:{point}")
        self.point = point


def canonical_json(value: Any) -> str:
    """Return the single JSON representation used for every contract hash."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _hex_digest(value: object, field: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ContractError(f"{field}_SHA256_INVALID")
    return digest


def _utc(value: object, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ContractError(f"{field}_REQUIRED")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ContractError(f"{field}_INVALID") from error
    if parsed.tzinfo is None:
        raise ContractError(f"{field}_TIMEZONE_REQUIRED")
    return parsed.astimezone(UTC)


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _reject_sensitive_plaintext(value: Any) -> None:
    """Reject credential-shaped keys and values without echoing their content."""

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                key_text = str(key).strip().lower().replace("-", "_")
                if any(marker in key_text for marker in SENSITIVE_KEY_MARKERS) and child not in (None, "", False, 0, [], {}):
                    raise ContractError("SENSITIVE_PLAINTEXT_REJECTED")
                visit(child)
            return
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if isinstance(node, str) and any(pattern.search(node) for pattern in SENSITIVE_VALUE_PATTERNS):
            raise ContractError("SENSITIVE_PLAINTEXT_REJECTED")

    visit(value)


def _artifact_path(base: Path, relative: object, *, field: str) -> Path:
    """Resolve an allowlisted repository file while rejecting path escapes."""

    if not isinstance(relative, str) or not relative:
        raise ContractError(f"{field}_PATH_INVALID")
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute() or any(part in {"", ".", ".."} for part in candidate_relative.parts):
        raise ContractError(f"{field}_PATH_FORBIDDEN")
    candidate = base / candidate_relative
    if candidate.is_symlink():
        raise ContractError(f"{field}_SYMLINK_FORBIDDEN")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as error:
        raise ContractError(f"{field}_OUT_OF_ROOT") from error
    if not candidate.is_file():
        raise ContractError(f"{field}_MISSING")
    return candidate


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_b64(value: object, field: str) -> bytes:
    try:
        decoded = base64.b64decode(str(value or ""), validate=True)
    except (ValueError, TypeError) as error:
        raise ContractError(f"{field}_BASE64_INVALID") from error
    if not decoded:
        raise ContractError(f"{field}_EMPTY")
    return decoded


def _message_id(message: Mapping[str, Any]) -> str:
    value = str(message.get("id") or "").strip()
    if not value:
        raise ContractError("MESSAGE_ID_REQUIRED")
    return value


def _attachments(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = message.get("attachments", [])
    if not isinstance(value, list):
        raise ContractError("MESSAGE_ATTACHMENTS_MUST_BE_ARRAY")
    return value


def _sender(message: Mapping[str, Any]) -> str:
    sender = message.get("from")
    if isinstance(sender, Mapping):
        address = sender.get("emailAddress")
        if isinstance(address, Mapping):
            sender = address.get("address")
    sender_text = str(sender or "").strip().lower()
    if not sender_text or "@" not in sender_text:
        raise ContractError(f"MESSAGE_SENDER_INVALID:{_message_id(message)}")
    return sender_text


def fingerprint_message(message: Mapping[str, Any]) -> dict[str, str]:
    """Fingerprint the immutable message envelope, excluding attachments."""

    message_id = _message_id(message)
    received = _utc(message.get("receivedDateTime"), f"MESSAGE_RECEIVED:{message_id}")
    body = str(message.get("body", message.get("bodyPreview", "")))
    envelope = {
        "id": message_id,
        "receivedDateTime": received.isoformat(),
        "from": _sender(message),
        "subject": str(message.get("subject") or "").strip(),
        "body": body,
    }
    return {"message_id": message_id, "sha256": sha256_json(envelope)}


def message_record(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable envelope needed to recompute a message fingerprint."""

    message_id = _message_id(message)
    received = _utc(message.get("receivedDateTime"), f"MESSAGE_RECEIVED:{message_id}")
    attachments = []
    for attachment in _attachments(message):
        attachment_id = str(attachment.get("id") or "").strip()
        content_base64 = attachment.get("content_base64")
        if not attachment_id or not isinstance(content_base64, str):
            raise ContractError(f"ATTACHMENT_SOURCE_INVALID:{message_id}")
        attachments.append(
            {
                "id": attachment_id,
                "name": str(attachment.get("name") or "").strip(),
                "content_type": str(attachment.get("content_type") or "").strip().lower(),
                "content_base64": content_base64,
                "amount_minor": attachment.get("amount_minor"),
            }
        )
    return {
        "id": message_id,
        "receivedDateTime": received.isoformat(),
        "from": _sender(message),
        "subject": str(message.get("subject") or "").strip(),
        "body": str(message.get("body", message.get("bodyPreview", ""))),
        "attachments": attachments,
    }


def fingerprint_attachment(message_id: str, attachment: Mapping[str, Any]) -> dict[str, Any]:
    """Fingerprint an immutable attachment and its content bytes."""

    attachment_id = str(attachment.get("id") or "").strip()
    if not attachment_id:
        raise ContractError(f"ATTACHMENT_ID_REQUIRED:{message_id}")
    content_base64 = attachment.get("content_base64")
    if not isinstance(content_base64, str):
        raise ContractError(f"ATTACHMENT_CONTENT_REQUIRED:{attachment_id}")
    content = _decode_b64(content_base64, f"ATTACHMENT_CONTENT:{attachment_id}")
    content_sha256 = sha256_bytes(content)
    row = {
        "message_id": message_id,
        "attachment_id": attachment_id,
        "name": str(attachment.get("name") or "").strip(),
        "content_type": str(attachment.get("content_type") or "").strip().lower(),
        "content_base64": content_base64,
        "content_sha256": content_sha256,
        "size_bytes": len(content),
        "amount_minor": attachment.get("amount_minor"),
        "identity_key": f"{message_id}:{attachment_id}",
    }
    row["fingerprint_sha256"] = sha256_json(row)
    return row


def _is_pdf(attachment: Mapping[str, Any]) -> bool:
    content_type = str(attachment.get("content_type") or "").strip().lower()
    name = str(attachment.get("name") or "").strip().lower()
    return content_type == "application/pdf" or name.endswith(".pdf")


class SyntheticOutlook:
    """Immutable in-memory Outlook fixture; never contacts Microsoft."""

    def __init__(self, messages: Iterable[Mapping[str, Any]]) -> None:
        self.messages = [_clone(dict(message)) for message in messages]
        self.enumeration_calls = 0

    def enumerate_messages(self, window_start: str, run_upper_bound: str) -> list[dict[str, Any]]:
        self.enumeration_calls += 1
        start = _utc(window_start, "WINDOW_START")
        end = _utc(run_upper_bound, "RUN_UPPER_BOUND")
        selected = []
        seen: set[str] = set()
        for message in self.messages:
            message_id = _message_id(message)
            if message_id in seen:
                raise ContractError(f"DUPLICATE_MESSAGE_ID:{message_id}")
            seen.add(message_id)
            received = _utc(message.get("receivedDateTime"), f"MESSAGE_RECEIVED:{message_id}")
            if start <= received <= end:
                selected.append(_clone(message))
        return sorted(
            selected,
            key=lambda row: (_utc(row["receivedDateTime"], "MESSAGE_RECEIVED"), _message_id(row)),
        )


class SyntheticOneDrive:
    """Idempotent archive/readback store; never contacts Microsoft."""

    def __init__(self, *, hash_failure_key: str | None = None) -> None:
        self.hash_failure_key = hash_failure_key
        self.email_rows: dict[str, dict[str, Any]] = {}
        self.attachment_rows: dict[str, dict[str, Any]] = {}
        self.email_calls = 0
        self.attachment_calls = 0
        self.email_writes = 0
        self.attachment_writes = 0

    def archive_email(self, row: Mapping[str, Any]) -> dict[str, Any]:
        key = str(row.get("message_id") or "").strip()
        expected = _hex_digest(row.get("source_sha256"), "EMAIL_SOURCE")
        if not key:
            raise ContractError("EMAIL_MESSAGE_ID_REQUIRED")
        self.email_calls += 1
        existing = self.email_rows.get(key)
        if existing is not None:
            if existing["source_sha256"] != expected:
                raise ContractError(f"EMAIL_ARCHIVE_IDENTITY_MISMATCH:{key}")
            return _clone(existing)
        receipt = {
            "message_id": key,
            "source_sha256": expected,
            "readback_sha256": expected,
            "onedrive_item_id": f"synthetic-email:{key}",
        }
        self.email_rows[key] = receipt
        self.email_writes += 1
        return _clone(receipt)

    def archive_attachment(self, row: Mapping[str, Any]) -> dict[str, Any]:
        key = str(row.get("identity_key") or "").strip()
        expected = _hex_digest(row.get("content_sha256"), "ATTACHMENT_SOURCE")
        if not key:
            raise ContractError("ATTACHMENT_IDENTITY_KEY_REQUIRED")
        self.attachment_calls += 1
        existing = self.attachment_rows.get(key)
        if existing is not None:
            if existing["source_sha256"] != expected:
                raise ContractError(f"ATTACHMENT_ARCHIVE_IDENTITY_MISMATCH:{key}")
            return _clone(existing)
        observed = "0" * 64 if key == self.hash_failure_key else expected
        receipt = {
            "identity_key": key,
            "message_id": str(row["message_id"]),
            "attachment_id": str(row["attachment_id"]),
            "source_sha256": expected,
            "readback_sha256": observed,
            "onedrive_item_id": f"synthetic-attachment:{key}",
        }
        if observed != expected:
            raise ContractError(f"ATTACHMENT_ARCHIVE_READBACK_MISMATCH:{key}")
        self.attachment_rows[key] = receipt
        self.attachment_writes += 1
        return _clone(receipt)


class SyntheticActual:
    """Minimal economic/API/UI readback model for disposable acceptance."""

    def __init__(self, source_code: str) -> None:
        self.source_code = source_code
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls = 0
        self.writes = 0

    def apply(self, row: Mapping[str, Any]) -> dict[str, Any]:
        key = str(row.get("identity_key") or "").strip()
        if not key:
            raise ContractError("ACTUAL_IDENTITY_KEY_REQUIRED")
        amount_minor = row.get("amount_minor")
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor <= 0:
            raise ContractError(f"ACTUAL_AMOUNT_MINOR_INVALID:{key}")
        self.calls += 1
        transaction = {
            "idempotency_key": f"actual:{self.source_code}:{key}",
            "identity_key": key,
            "account_id": f"synthetic-account:{self.source_code}",
            "amount_minor": amount_minor,
            "currency": "AED",
            "topic": "PURCHASE",
            "readback_verified": True,
        }
        existing = self.rows.get(key)
        if existing is not None:
            if existing != transaction:
                raise ContractError(f"ACTUAL_IDENTITY_MISMATCH:{key}")
            return _clone(existing)
        self.rows[key] = transaction
        self.writes += 1
        return _clone(transaction)

    def readback(self) -> dict[str, Any]:
        rows = [_clone(self.rows[key]) for key in sorted(self.rows)]
        digest = sha256_json(rows)
        # The API and UI readbacks intentionally share the same economic rows;
        # a verifier rejects a receipt where one surface drifts from the other.
        return {
            "status": "VERIFIED",
            "economic_rows": rows,
            "economic_readback_sha256": digest,
            "api_readback_sha256": digest,
            "ui_readback_sha256": digest,
            "write_count": self.writes,
        }


class SyntheticCashback:
    """Optional idempotent cashback companion projection."""

    def __init__(self, source_code: str) -> None:
        self.source_code = source_code
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls = 0
        self.writes = 0

    def apply(self, row: Mapping[str, Any]) -> dict[str, Any]:
        key = str(row.get("identity_key") or "").strip()
        if not key:
            raise ContractError("CASHBACK_IDENTITY_KEY_REQUIRED")
        self.calls += 1
        event = {
            "event_id": f"cashback:{self.source_code}:{key}",
            "identity_key": key,
            "source_code": self.source_code,
            "amount_minor": int(row["amount_minor"]),
            "readback_verified": True,
        }
        existing = self.rows.get(key)
        if existing is not None:
            if existing != event:
                raise ContractError(f"CASHBACK_IDENTITY_MISMATCH:{key}")
            return _clone(existing)
        self.rows[key] = event
        self.writes += 1
        return _clone(event)

    def readback(self) -> dict[str, Any]:
        rows = [_clone(self.rows[key]) for key in sorted(self.rows)]
        return {
            "status": "VERIFIED",
            "rows": rows,
            "readback_sha256": sha256_json(rows),
            "write_count": self.writes,
        }


class SyntheticCursor:
    """Compare-and-swap cursor table used by the synthetic pipeline."""

    def __init__(self) -> None:
        self.version = 0
        self.value: str | None = None
        self.run_id: str | None = None
        self.writes = 0
        self.conflicts = 0

    def force_advance(self, value: str = "synthetic:external") -> None:
        self.version += 1
        self.value = value
        self.run_id = "synthetic:external"
        self.writes += 1

    def compare_and_swap(self, expected_version: int, value: str, run_id: str) -> dict[str, Any]:
        if self.version != expected_version:
            self.conflicts += 1
            raise ContractError("CURSOR_CAS_CONFLICT")
        self.version += 1
        self.value = value
        self.run_id = run_id
        self.writes += 1
        return {
            "source_code": "synthetic",
            "prior_version": expected_version,
            "next_version": self.version,
            "cursor": value,
            "run_id": run_id,
            "readback_verified": True,
        }


@dataclass
class _PipelineState:
    messages: list[dict[str, Any]] = field(default_factory=list)
    message_fingerprints: list[dict[str, str]] = field(default_factory=list)
    attachment_fingerprints: list[dict[str, Any]] = field(default_factory=list)
    archive_rows: list[dict[str, Any]] = field(default_factory=list)
    accepted_rows: list[dict[str, Any]] = field(default_factory=list)
    outbox_rows: list[dict[str, Any]] = field(default_factory=list)
    actual_rows: list[dict[str, Any]] = field(default_factory=list)
    cashback_rows: list[dict[str, Any]] = field(default_factory=list)
    enumerated: bool = False
    archive_complete: bool = False
    actual_complete: bool = False
    cashback_complete: bool = False
    cursor_committed: bool = False
    restart_seen: set[str] = field(default_factory=set)


class SyntheticMailPipeline:
    """Durable synthetic W12/W01/W22/Actual/Cashback pipeline."""

    def __init__(
        self,
        *,
        source_code: str,
        messages: Iterable[Mapping[str, Any]],
        include_cashback: bool,
        restart_at: str | None = None,
        hash_failure_key: str | None = None,
        cas_conflict: bool = False,
    ) -> None:
        if source_code not in {"EI_AMAZON", "WIO_CREDIT"}:
            raise ContractError("SOURCE_CODE_NOT_ALLOWLISTED")
        if restart_at is not None and restart_at not in RESTART_INJECTION_POINTS:
            raise ContractError("RESTART_POINT_NOT_ALLOWLISTED")
        self.source_code = source_code
        self.run_id = f"synthetic:{source_code}:2026-08-22"
        self.window_start = "2026-08-21T00:00:00+00:00"
        self.run_upper_bound = "2026-08-22T00:00:00+00:00"
        self.outlook = SyntheticOutlook(messages)
        self.onedrive = SyntheticOneDrive(hash_failure_key=hash_failure_key)
        self.actual = SyntheticActual(source_code)
        self.cashback = SyntheticCashback(source_code) if include_cashback else None
        self.cursor = SyntheticCursor()
        self.state = _PipelineState()
        self.restart_at = restart_at
        self.cas_conflict = cas_conflict
        self._conflict_injected = False

    def _checkpoint(self, point: str) -> None:
        if self.restart_at == point and point not in self.state.restart_seen:
            self.state.restart_seen.add(point)
            raise InjectedRestart(point)

    def _enumerate(self) -> None:
        if self.state.enumerated:
            return
        messages = self.outlook.enumerate_messages(self.window_start, self.run_upper_bound)
        fingerprints = [fingerprint_message(message) for message in messages]
        attachments: list[dict[str, Any]] = []
        for message in messages:
            for attachment in _attachments(message):
                attachments.append(fingerprint_attachment(_message_id(message), attachment))
        self.state.messages = messages
        self.state.message_fingerprints = fingerprints
        self.state.attachment_fingerprints = attachments
        self.state.enumerated = True

    def _archive(self) -> None:
        if self.state.archive_complete:
            return
        for message, message_fp in zip(self.state.messages, self.state.message_fingerprints):
            email_receipt = self.onedrive.archive_email(
                {"message_id": message_fp["message_id"], "source_sha256": message_fp["sha256"]}
            )
            self.state.archive_rows.append({"kind": "email", **email_receipt})
            for attachment_fp in [
                row
                for row in self.state.attachment_fingerprints
                if row["message_id"] == message_fp["message_id"]
            ]:
                attachment_receipt = self.onedrive.archive_attachment(attachment_fp)
                self.state.archive_rows.append({"kind": "attachment", **attachment_receipt})
        self.state.archive_complete = True
        self._checkpoint("after_archive")

    def _build_accepted_rows(self) -> None:
        if self.state.accepted_rows:
            return
        attachments_by_key = {
            row["identity_key"]: row for row in self.state.attachment_fingerprints
        }
        for message in self.state.messages:
            message_id = _message_id(message)
            for attachment in _attachments(message):
                fp = attachments_by_key[f"{message_id}:{attachment['id']}"]
                if not _is_pdf(attachment):
                    continue
                amount_minor = attachment.get("amount_minor")
                if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
                    raise ContractError(f"PDF_AMOUNT_MINOR_REQUIRED:{fp['identity_key']}")
                self.state.accepted_rows.append(
                    {
                        "identity_key": fp["identity_key"],
                        "message_id": message_id,
                        "attachment_id": str(attachment["id"]),
                        "content_sha256": fp["content_sha256"],
                        "amount_minor": amount_minor,
                    }
                )

    def _actual(self) -> None:
        if self.state.actual_complete:
            return
        self._build_accepted_rows()
        self.state.actual_rows = [self.actual.apply(row) for row in self.state.accepted_rows]
        self.state.outbox_rows = [
            {
                "idempotency_key": row["identity_key"],
                "workflow": "W22_SHARED_MONTHLY_STATEMENT_CYCLE",
                "source_code": self.source_code,
                "actual_identity_key": f"actual:{self.source_code}:{row['identity_key']}",
                "state": "COMMITTED",
            }
            for row in self.state.accepted_rows
        ]
        self.state.actual_complete = True
        self._checkpoint("after_actual")

    def _cashback(self) -> None:
        if self.state.cashback_complete:
            return
        if self.cashback is not None:
            self.state.cashback_rows = [
                self.cashback.apply(row) for row in self.state.accepted_rows
            ]
        self.state.cashback_complete = True
        self._checkpoint("after_cashback")

    def _commit_cursor(self) -> None:
        if self.state.cursor_committed:
            return
        if self.cas_conflict and not self._conflict_injected:
            self._conflict_injected = True
            self.cursor.force_advance()
        self.cursor.compare_and_swap(0, self.run_upper_bound, self.run_id)
        self.state.cursor_committed = True
        self._checkpoint("after_cursor_cas")

    def run_once(self) -> None:
        self._enumerate()
        self._archive()
        self._actual()
        self._cashback()
        self._commit_cursor()

    def run_to_completion(self) -> None:
        while True:
            try:
                self.run_once()
                return
            except InjectedRestart:
                continue


def _default_messages(source_code: str, count: int = 1) -> list[dict[str, Any]]:
    sender = (
        "estatement@emiratesislamic.ae"
        if source_code == "EI_AMAZON"
        else "communications@mail.wio.io"
    )
    subject = (
        "Statement of your Emirates Islamic Credit Card"
        if source_code == "EI_AMAZON"
        else "Your Wio Credit statement for this month"
    )
    rows = []
    base = datetime(2026, 8, 21, tzinfo=UTC)
    for index in range(count):
        content = f"%PDF-synthetic-{source_code}-{index:03d}%".encode("ascii")
        rows.append(
            {
                "id": f"message-{index:03d}",
                "receivedDateTime": (base + timedelta(minutes=index)).isoformat(),
                "from": {"emailAddress": {"address": sender}},
                "subject": subject,
                "body": f"Synthetic {source_code} statement {index}",
                "attachments": [
                    {
                        "id": f"statement-{index:03d}",
                        "name": f"statement-{index:03d}.pdf",
                        "content_type": "application/pdf",
                        "content_base64": _b64(content),
                        "amount_minor": 100 + index,
                    }
                ],
            }
        )
    return rows


def build_source_bindings(root: Path | None = None) -> dict[str, Any]:
    """Bind source/config/image/workflow bytes without contacting a provider."""

    base = Path(root or ROOT).resolve()
    if not base.is_dir():
        raise ContractError("SOURCE_ROOT_INVALID")
    artifact_paths = (
        DEFAULT_SOURCE_PATHS
        + DEFAULT_CONFIG_PATHS
        + ("integrations/n8n/application-images.lock.json",)
        + DEFAULT_WORKFLOW_PATHS
    )
    artifacts: list[dict[str, str]] = []
    for relative in artifact_paths:
        path = _artifact_path(base, relative, field="SOURCE_ARTIFACT")
        content = path.read_bytes().replace(b"\r\n", b"\n")
        artifacts.append({"path": relative, "sha256": sha256_bytes(content)})
    by_path = {row["path"]: row["sha256"] for row in artifacts}
    workflows = {
        path: by_path[path]
        for path in DEFAULT_WORKFLOW_PATHS
    }
    image_lock = _artifact_path(
        base,
        "integrations/n8n/application-images.lock.json",
        field="IMAGE_LOCK",
    )
    image_digest = "sha256:" + by_path["integrations/n8n/application-images.lock.json"]
    try:
        candidate = json.loads(image_lock.read_text(encoding="utf-8"))["base_image"]["digest"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        candidate = None
    if isinstance(candidate, str) and candidate.startswith("sha256:"):
        image_digest = candidate
    return {
        "source_sha256": sha256_json({"artifacts": artifacts}),
        "config_sha256": sha256_json(
            {path: by_path[path] for path in DEFAULT_CONFIG_PATHS}
        ),
        "image_digest": image_digest,
        "workflow_sha256": workflows,
        "source_artifacts": artifacts,
    }


def _table(name: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [_clone(dict(row)) for row in rows]
    normalized.sort(key=canonical_json)
    return {
        "table": name,
        "row_count": len(normalized),
        "rows": normalized,
        "rows_sha256": sha256_json(normalized),
    }


def _restart_matrix(
    *, source_code: str, messages: Sequence[Mapping[str, Any]], include_cashback: bool
) -> list[dict[str, Any]]:
    results = []
    for point in RESTART_INJECTION_POINTS:
        pipeline = SyntheticMailPipeline(
            source_code=source_code,
            messages=messages,
            include_cashback=include_cashback,
            restart_at=point,
        )
        injected = False
        try:
            pipeline.run_once()
        except InjectedRestart as error:
            injected = True
            if error.point != point:
                raise ContractError("RESTART_POINT_MISMATCH")
        pipeline.run_to_completion()
        replay_before = (
            pipeline.onedrive.email_writes,
            pipeline.onedrive.attachment_writes,
            pipeline.actual.writes,
            pipeline.cashback.writes if pipeline.cashback else 0,
            pipeline.cursor.writes,
        )
        pipeline.run_to_completion()
        replay_after = (
            pipeline.onedrive.email_writes,
            pipeline.onedrive.attachment_writes,
            pipeline.actual.writes,
            pipeline.cashback.writes if pipeline.cashback else 0,
            pipeline.cursor.writes,
        )
        results.append(
            {
                "point": point,
                "injected": injected,
                "status": "RECOVERED",
                "idempotent_replay": replay_before == replay_after,
                "archive_writes": pipeline.onedrive.email_writes + pipeline.onedrive.attachment_writes,
                "actual_writes": pipeline.actual.writes,
                "cashback_writes": pipeline.cashback.writes if pipeline.cashback else 0,
                "cursor_writes": pipeline.cursor.writes,
            }
        )
    return results


def _make_receipt(
    pipeline: SyntheticMailPipeline,
    *,
    source_bindings: Mapping[str, Any],
    restart_results: Sequence[Mapping[str, Any]],
    replay_before: tuple[int, int, int, int, int],
) -> dict[str, Any]:
    state = pipeline.state
    email_rows = [row for row in pipeline.onedrive.email_rows.values()]
    attachment_rows = [row for row in pipeline.onedrive.attachment_rows.values()]
    archive_rows = [
        {"kind": "email", **row} for row in email_rows
    ] + [
        {"kind": "attachment", **row} for row in attachment_rows
    ]
    archive_rows.sort(key=canonical_json)
    actual_readback = pipeline.actual.readback()
    pdf_by_key = {
        f"{_message_id(message)}:{attachment['id']}": _is_pdf(attachment)
        for message in state.messages
        for attachment in _attachments(message)
    }
    if pipeline.cashback is None:
        cashback_readback: dict[str, Any] = {
            "status": "N/A",
            "reason": "SOURCE_HAS_NO_CASHBACK_ROUTE_IN_SYNTHETIC_FIXTURE",
            "rows": [],
            "readback_sha256": sha256_json([]),
            "write_count": 0,
        }
    else:
        cashback_readback = pipeline.cashback.readback()

    tables = {
        "email_archive": _table("email_archive", email_rows),
        "attachment_archive": _table("attachment_archive", attachment_rows),
        "pipeline_outbox": _table("pipeline_outbox", state.outbox_rows),
        "cursor_cas": _table(
            "cursor_cas",
            [
                {
                    "source_code": pipeline.source_code,
                    "cursor_version": pipeline.cursor.version,
                    "cursor": pipeline.cursor.value,
                    "run_id": pipeline.cursor.run_id,
                }
            ],
        ),
    }
    first_counts = replay_before
    final_counts = (
        pipeline.onedrive.email_writes,
        pipeline.onedrive.attachment_writes,
        pipeline.actual.writes,
        pipeline.cashback.writes if pipeline.cashback else 0,
        pipeline.cursor.writes,
    )
    receipt: dict[str, Any] = {
        "schema_version": "real-mail-e2e-receipt-v1",
        "proof_kind": "SYNTHETIC_OFFLINE",
        "provider_proof": False,
        "provider_proof_status": "NOT_PROVEN",
        "external_provider_calls": False,
        "run_id": pipeline.run_id,
        "source": {
            "source_code": pipeline.source_code,
            "mailbox": "synthetic://outlook/inbox",
            "folder_id": "synthetic-outlook-inbox",
            "window_start": pipeline.window_start,
            "run_upper_bound": pipeline.run_upper_bound,
        },
        "source_bindings": _clone(dict(source_bindings)),
        "enumeration": {
            "workflow_chain": list(EXPECTED_WORKFLOW_CHAIN),
            "scanned_count": len(state.messages),
            "matched_count": len(state.messages),
            "ordered_message_ids": [row["message_id"] for row in state.message_fingerprints],
            "message_records": [message_record(message) for message in state.messages],
            "message_fingerprints": _clone(state.message_fingerprints),
            "attachment_fingerprints": _clone(state.attachment_fingerprints),
            "enumerated_exactly_once": pipeline.outlook.enumeration_calls == 1,
        },
        "archive": {
            "email_rows": _clone(email_rows),
            "attachment_rows": _clone(attachment_rows),
            "rows_sha256": sha256_json(archive_rows),
            "email_writes": pipeline.onedrive.email_writes,
            "attachment_writes": pipeline.onedrive.attachment_writes,
            "non_pdf_count": sum(
                not pdf_by_key[row["identity_key"]]
                for row in state.attachment_fingerprints
            ),
            "readback_verified": all(
                row["source_sha256"] == row["readback_sha256"]
                for row in archive_rows
            ),
            "duplicate_writes": 0,
        },
        "pipeline": {
            "state": "COMMITTED" if state.cursor_committed else "INCOMPLETE",
            "accepted_count": len(state.accepted_rows),
            "outbox_rows": _clone(state.outbox_rows),
            "outbox_rows_sha256": sha256_json(state.outbox_rows),
            "actual_readback": actual_readback,
            "cashback_readback": cashback_readback,
            "terminal_readback_verified": state.cursor_committed,
        },
        "data_tables": tables,
        "actual": actual_readback,
        "cashback": cashback_readback,
        "cursor_cas": {
            "source_code": pipeline.source_code,
            "before_version": 0,
            "after_version": pipeline.cursor.version,
            "cursor": pipeline.cursor.value,
            "writes": pipeline.cursor.writes,
            "conflicts": pipeline.cursor.conflicts,
            "readback_verified": pipeline.state.cursor_committed,
        },
        "replay": {
            "idempotent": first_counts == final_counts,
            "archive_new_writes": final_counts[0] - first_counts[0],
            "attachment_new_writes": final_counts[1] - first_counts[1],
            "actual_new_writes": final_counts[2] - first_counts[2],
            "cashback_new_writes": final_counts[3] - first_counts[3],
            "cursor_new_writes": final_counts[4] - first_counts[4],
            "first_run_counts": {
                "email_archive": first_counts[0],
                "attachment_archive": first_counts[1],
                "actual": first_counts[2],
                "cashback": first_counts[3],
                "cursor": first_counts[4],
            },
            "final_counts": {
                "email_archive": final_counts[0],
                "attachment_archive": final_counts[1],
                "actual": final_counts[2],
                "cashback": final_counts[3],
                "cursor": final_counts[4],
            },
        },
        "restart": {
            "injections": [_clone(dict(row)) for row in restart_results],
            "all_recovered": all(
                row["status"] == "RECOVERED" and row["idempotent_replay"]
                for row in restart_results
            ),
        },
        "cleanup": {
            "status": "CLEAN",
            "containers": 0,
            "networks": 0,
            "volumes": 0,
            "provider_sessions": 0,
            "production_writes": 0,
        },
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    return receipt


def run_synthetic_e2e(
    *,
    source_code: str = "EI_AMAZON",
    messages: Iterable[Mapping[str, Any]] | None = None,
    count: int = 1,
    include_cashback: bool | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Execute the complete offline contract and return a verified receipt."""

    if not isinstance(count, int) or isinstance(count, bool) or count < 0 or count > MAX_SYNTHETIC_COUNT:
        raise ContractError(f"COUNT_OUT_OF_RANGE:0..{MAX_SYNTHETIC_COUNT}")
    if include_cashback is None:
        include_cashback = source_code == "EI_AMAZON"
    fixture_messages = _default_messages(source_code, count) if messages is None else list(messages)
    pipeline = SyntheticMailPipeline(
        source_code=source_code,
        messages=fixture_messages,
        include_cashback=include_cashback,
    )
    pipeline.run_to_completion()
    before_replay = (
        pipeline.onedrive.email_writes,
        pipeline.onedrive.attachment_writes,
        pipeline.actual.writes,
        pipeline.cashback.writes if pipeline.cashback else 0,
        pipeline.cursor.writes,
    )
    pipeline.run_to_completion()
    restart_results = _restart_matrix(
        source_code=source_code,
        messages=fixture_messages,
        include_cashback=include_cashback,
    )
    receipt = _make_receipt(
        pipeline,
        source_bindings=build_source_bindings(root),
        restart_results=restart_results,
        replay_before=before_replay,
    )
    verify_receipt(receipt, root=root)
    return receipt


def _verify_source_bindings(bindings: Mapping[str, Any], base: Path) -> None:
    expected_paths = (
        DEFAULT_SOURCE_PATHS
        + DEFAULT_CONFIG_PATHS
        + ("integrations/n8n/application-images.lock.json",)
        + DEFAULT_WORKFLOW_PATHS
    )
    artifacts = bindings.get("source_artifacts")
    if not isinstance(artifacts, list):
        raise ContractError("SOURCE_ARTIFACTS_MUST_BE_ARRAY")
    paths: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise ContractError("SOURCE_ARTIFACT_RECORD_INVALID")
        path = artifact.get("path")
        _artifact_path(base, path, field="SOURCE_ARTIFACT")
        if path in paths:
            raise ContractError("SOURCE_ARTIFACT_DUPLICATE")
        paths.append(path)
    if set(paths) != set(expected_paths):
        raise ContractError("SOURCE_ARTIFACT_SET_MISMATCH")
    if tuple(paths) != expected_paths:
        raise ContractError("SOURCE_ARTIFACT_ORDER_MISMATCH")
    workflow_bindings = bindings.get("workflow_sha256")
    if not isinstance(workflow_bindings, Mapping):
        raise ContractError("WORKFLOW_BINDINGS_INVALID")
    if set(workflow_bindings) != set(DEFAULT_WORKFLOW_PATHS):
        raise ContractError("WORKFLOW_BINDING_SET_MISMATCH")
    for path, digest in workflow_bindings.items():
        _artifact_path(base, path, field="WORKFLOW")
        _hex_digest(digest, f"WORKFLOW:{path}")
    _hex_digest(bindings["source_sha256"], "SOURCE")
    _hex_digest(bindings["config_sha256"], "CONFIG")
    image_digest = str(bindings["image_digest"])
    if not image_digest.startswith("sha256:"):
        raise ContractError("IMAGE_DIGEST_INVALID")
    _hex_digest(image_digest.removeprefix("sha256:"), "IMAGE")
    expected = build_source_bindings(base)
    if dict(bindings) != expected:
        raise ContractError("SOURCE_BINDINGS_MISMATCH")


def _verify_table(
    table: Mapping[str, Any], *, name: str, expected_rows: Sequence[Mapping[str, Any]]
) -> None:
    rows = table.get("rows")
    if not isinstance(rows, list):
        raise ContractError(f"DATA_TABLE_ROWS_INVALID:{name}")
    normalized_expected = sorted((_clone(dict(row)) for row in expected_rows), key=canonical_json)
    if table.get("table") != name:
        raise ContractError(f"DATA_TABLE_NAME_MISMATCH:{name}")
    if table.get("row_count") != len(rows) or rows != normalized_expected:
        raise ContractError(f"DATA_TABLE_ROWS_MISMATCH:{name}")
    if table.get("rows_sha256") != sha256_json(rows):
        raise ContractError(f"DATA_TABLE_HASH_MISMATCH:{name}")


def _expected_actual_rows(source_code: str, attachments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for attachment in attachments:
        if not _is_pdf(attachment):
            continue
        amount_minor = attachment.get("amount_minor")
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor <= 0:
            raise ContractError(f"PDF_AMOUNT_MINOR_REQUIRED:{attachment.get('identity_key', '')}")
        identity_key = str(attachment["identity_key"])
        rows.append(
            {
                "idempotency_key": f"actual:{source_code}:{identity_key}",
                "identity_key": identity_key,
                "account_id": f"synthetic-account:{source_code}",
                "amount_minor": amount_minor,
                "currency": "AED",
                "topic": "PURCHASE",
                "readback_verified": True,
            }
        )
    return sorted(rows, key=lambda row: row["identity_key"])


def _expected_cashback_rows(source_code: str, attachments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "event_id": f"cashback:{source_code}:{attachment['identity_key']}",
            "identity_key": attachment["identity_key"],
            "source_code": source_code,
            "amount_minor": attachment["amount_minor"],
            "readback_verified": True,
        }
        for attachment in _expected_actual_rows(source_code, attachments)
    ]


def _verify_enumeration(enumeration: Mapping[str, Any], source_code: str) -> list[dict[str, Any]]:
    if tuple(enumeration["workflow_chain"]) != EXPECTED_WORKFLOW_CHAIN:
        raise ContractError("WORKFLOW_CHAIN_MISMATCH")
    records = enumeration["message_records"]
    if not isinstance(records, list):
        raise ContractError("MESSAGE_RECORDS_INVALID")
    ordered_records = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ContractError("MESSAGE_RECORD_INVALID")
        if message_record(record) != dict(record):
            raise ContractError("MESSAGE_RECORD_MISMATCH")
        ordered_records.append(dict(record))
    expected_order = sorted(
        ordered_records,
        key=lambda row: (_utc(row["receivedDateTime"], "MESSAGE_RECEIVED"), row["id"]),
    )
    if ordered_records != expected_order:
        raise ContractError("MESSAGE_ORDER_MISMATCH")
    message_ids = [record["id"] for record in ordered_records]
    if len(message_ids) != len(set(message_ids)):
        raise ContractError("MESSAGE_ID_DUPLICATE")
    if enumeration["scanned_count"] != len(ordered_records):
        raise ContractError("SCANNED_COUNT_MISMATCH")
    if enumeration["matched_count"] != len(ordered_records):
        raise ContractError("MATCHED_COUNT_MISMATCH")
    if enumeration["ordered_message_ids"] != message_ids:
        raise ContractError("ORDERED_MESSAGE_IDS_MISMATCH")
    expected_message_fingerprints = [fingerprint_message(record) for record in ordered_records]
    if enumeration["message_fingerprints"] != expected_message_fingerprints:
        raise ContractError("MESSAGE_FINGERPRINT_MISMATCH")
    attachments: list[dict[str, Any]] = []
    attachment_keys: set[str] = set()
    for record in ordered_records:
        for source_attachment in record["attachments"]:
            fingerprint = fingerprint_attachment(record["id"], source_attachment)
            key = fingerprint["identity_key"]
            if key in attachment_keys:
                raise ContractError("ATTACHMENT_IDENTITY_DUPLICATE")
            attachment_keys.add(key)
            attachments.append(fingerprint)
    if enumeration["attachment_fingerprints"] != attachments:
        raise ContractError("ATTACHMENT_FINGERPRINT_MISMATCH")
    if source_code not in {"EI_AMAZON", "WIO_CREDIT"}:
        raise ContractError("SOURCE_CODE_NOT_ALLOWLISTED")
    return attachments


def _verify_actual_readback(
    actual: Mapping[str, Any], *, source_code: str, attachments: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    expected_rows = _expected_actual_rows(source_code, attachments)
    if actual["economic_rows"] != expected_rows:
        raise ContractError("ACTUAL_ECONOMIC_ROWS_MISMATCH")
    expected_digest = sha256_json(expected_rows)
    if any(actual[field] != expected_digest for field in (
        "economic_readback_sha256", "api_readback_sha256", "ui_readback_sha256"
    )):
        raise ContractError("ACTUAL_READBACK_HASH_MISMATCH")
    if actual["write_count"] != len(expected_rows):
        raise ContractError("ACTUAL_WRITE_COUNT_MISMATCH")
    return expected_rows


def _verify_cashback_readback(
    cashback: Mapping[str, Any], *, source_code: str, attachments: Sequence[Mapping[str, Any]]
) -> None:
    if cashback["status"] == "N/A":
        if cashback.get("reason") != "SOURCE_HAS_NO_CASHBACK_ROUTE_IN_SYNTHETIC_FIXTURE":
            raise ContractError("CASHBACK_NA_REASON_INVALID")
        if cashback["rows"] or cashback["write_count"] != 0 or cashback["readback_sha256"] != sha256_json([]):
            raise ContractError("CASHBACK_NA_READBACK_INVALID")
        return
    expected_rows = _expected_cashback_rows(source_code, attachments)
    if cashback["rows"] != expected_rows:
        raise ContractError("CASHBACK_ROWS_MISMATCH")
    if cashback["readback_sha256"] != sha256_json(expected_rows):
        raise ContractError("CASHBACK_HASH_MISMATCH")
    if cashback["write_count"] != len(expected_rows):
        raise ContractError("CASHBACK_WRITE_COUNT_MISMATCH")


def verify_receipt(receipt: Mapping[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    """Validate schema and cross-field invariants; fail closed on drift."""

    if not isinstance(receipt, Mapping):
        raise ContractError("RECEIPT_MUST_BE_OBJECT")
    value = _clone(dict(receipt))
    _reject_sensitive_plaintext(value)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        raise ContractError("RECEIPT_SCHEMA_INVALID:" + ";".join(error.message for error in errors))
    expected_receipt_hash = value.pop("receipt_sha256")
    if sha256_json(value) != expected_receipt_hash:
        raise ContractError("RECEIPT_SHA256_MISMATCH")
    if value["proof_kind"] != "SYNTHETIC_OFFLINE" or value["provider_proof"] is not False:
        raise ContractError("PROVIDER_PROOF_MUST_REMAIN_UNPROVEN")
    if value["external_provider_calls"] is not False:
        raise ContractError("EXTERNAL_PROVIDER_CALLS_FORBIDDEN")
    source = value["source"]
    source_code = source["source_code"]
    if value["run_id"] != f"synthetic:{source_code}:2026-08-22":
        raise ContractError("RUN_ID_SOURCE_MISMATCH")
    if source["window_start"] != "2026-08-21T00:00:00+00:00" or source["run_upper_bound"] != "2026-08-22T00:00:00+00:00":
        raise ContractError("SOURCE_WINDOW_MISMATCH")
    base = Path(root or ROOT).resolve()
    if not base.is_dir():
        raise ContractError("SOURCE_ROOT_INVALID")
    bindings = value["source_bindings"]
    _verify_source_bindings(bindings, base)
    enumeration = value["enumeration"]
    if not enumeration["enumerated_exactly_once"]:
        raise ContractError("ENUMERATION_NOT_EXACTLY_ONCE")
    attachment_fingerprints = _verify_enumeration(enumeration, source_code)
    archive = value["archive"]
    message_fingerprints = enumeration["message_fingerprints"]
    expected_email_rows = [
        {
            "message_id": fingerprint["message_id"],
            "source_sha256": fingerprint["sha256"],
            "readback_sha256": fingerprint["sha256"],
            "onedrive_item_id": f"synthetic-email:{fingerprint['message_id']}",
        }
        for fingerprint in message_fingerprints
    ]
    expected_attachment_rows = [
        {
            "identity_key": fingerprint["identity_key"],
            "message_id": fingerprint["message_id"],
            "attachment_id": fingerprint["attachment_id"],
            "source_sha256": fingerprint["content_sha256"],
            "readback_sha256": fingerprint["content_sha256"],
            "onedrive_item_id": f"synthetic-attachment:{fingerprint['identity_key']}",
        }
        for fingerprint in attachment_fingerprints
    ]
    if archive["email_rows"] != expected_email_rows or archive["attachment_rows"] != expected_attachment_rows:
        raise ContractError("ARCHIVE_ROWS_MISMATCH")
    if archive["email_writes"] != len(expected_email_rows) or archive["attachment_writes"] != len(expected_attachment_rows):
        raise ContractError("ARCHIVE_WRITE_COUNT_MISMATCH")
    archive_rows = sorted(
        [{"kind": "email", **row} for row in expected_email_rows]
        + [{"kind": "attachment", **row} for row in expected_attachment_rows],
        key=canonical_json,
    )
    if archive["rows_sha256"] != sha256_json(archive_rows):
        raise ContractError("ARCHIVE_ROWS_HASH_MISMATCH")
    if not archive["readback_verified"] or archive["duplicate_writes"] != 0:
        raise ContractError("ARCHIVE_READBACK_OR_DUPLICATE_FAILURE")
    expected_non_pdf = sum(not _is_pdf(attachment) for attachment in attachment_fingerprints)
    if archive["non_pdf_count"] != expected_non_pdf:
        raise ContractError("ARCHIVE_NON_PDF_COUNT_MISMATCH")
    pipeline = value["pipeline"]
    if pipeline["state"] != "COMMITTED" or not pipeline["terminal_readback_verified"]:
        raise ContractError("PIPELINE_TERMINAL_READBACK_REQUIRED")
    expected_actual_rows = _verify_actual_readback(value["actual"], source_code=source_code, attachments=attachment_fingerprints)
    _verify_cashback_readback(value["cashback"], source_code=source_code, attachments=attachment_fingerprints)
    if pipeline["actual_readback"] != value["actual"] or pipeline["cashback_readback"] != value["cashback"]:
        raise ContractError("PIPELINE_READBACK_NESTING_MISMATCH")
    expected_outbox_rows = [
        {
            "idempotency_key": attachment["identity_key"],
            "workflow": "W22_SHARED_MONTHLY_STATEMENT_CYCLE",
            "source_code": source_code,
            "actual_identity_key": f"actual:{source_code}:{attachment['identity_key']}",
            "state": "COMMITTED",
        }
        for attachment in attachment_fingerprints
        if _is_pdf(attachment)
    ]
    if pipeline["accepted_count"] != len(expected_actual_rows) or pipeline["outbox_rows"] != expected_outbox_rows:
        raise ContractError("PIPELINE_OUTBOX_MISMATCH")
    if pipeline["outbox_rows_sha256"] != sha256_json(expected_outbox_rows):
        raise ContractError("PIPELINE_OUTBOX_HASH_MISMATCH")
    cursor = value["cursor_cas"]
    expected_cursor = {
        "source_code": source_code,
        "before_version": 0,
        "after_version": 1,
        "cursor": source["run_upper_bound"],
        "writes": 1,
        "conflicts": 0,
        "readback_verified": True,
    }
    if cursor != expected_cursor:
        raise ContractError("CURSOR_CAS_READBACK_INVALID")
    expected_tables = {
        "email_archive": expected_email_rows,
        "attachment_archive": expected_attachment_rows,
        "pipeline_outbox": expected_outbox_rows,
        "cursor_cas": [
            {
                "source_code": source_code,
                "cursor_version": 1,
                "cursor": source["run_upper_bound"],
                "run_id": value["run_id"],
            }
        ],
    }
    data_tables = value["data_tables"]
    if set(data_tables) != set(EXPECTED_TABLE_NAMES):
        raise ContractError("DATA_TABLE_SET_MISMATCH")
    for name in EXPECTED_TABLE_NAMES:
        _verify_table(data_tables[name], name=name, expected_rows=expected_tables[name])
    replay = value["replay"]
    final_counts = {
        "email_archive": archive["email_writes"],
        "attachment_archive": archive["attachment_writes"],
        "actual": value["actual"]["write_count"],
        "cashback": value["cashback"]["write_count"],
        "cursor": cursor["writes"],
    }
    if replay["final_counts"] != final_counts or replay["first_run_counts"] != final_counts:
        raise ContractError("REPLAY_COUNT_PARITY_INVALID")
    deltas = {
        "archive_new_writes": final_counts["email_archive"] - replay["first_run_counts"]["email_archive"],
        "attachment_new_writes": final_counts["attachment_archive"] - replay["first_run_counts"]["attachment_archive"],
        "actual_new_writes": final_counts["actual"] - replay["first_run_counts"]["actual"],
        "cashback_new_writes": final_counts["cashback"] - replay["first_run_counts"]["cashback"],
        "cursor_new_writes": final_counts["cursor"] - replay["first_run_counts"]["cursor"],
    }
    if not replay["idempotent"] or any(replay[key] != value for key, value in deltas.items()) or any(deltas.values()):
        raise ContractError("REPLAY_NOT_IDEMPOTENT")
    restart = value["restart"]
    points = [row["point"] for row in restart["injections"]]
    if points != list(RESTART_INJECTION_POINTS) or len(set(points)) != len(points) or not restart["all_recovered"]:
        raise ContractError("RESTART_MATRIX_INCOMPLETE")
    expected_restart_counts = {
        "archive_writes": archive["email_writes"] + archive["attachment_writes"],
        "actual_writes": value["actual"]["write_count"],
        "cashback_writes": value["cashback"]["write_count"],
        "cursor_writes": 1,
    }
    for row in restart["injections"]:
        for key, expected in expected_restart_counts.items():
            if row[key] != expected:
                raise ContractError("RESTART_COUNTS_MISMATCH")
    cleanup = value["cleanup"]
    if cleanup != {
        "status": "CLEAN",
        "containers": 0,
        "networks": 0,
        "volumes": 0,
        "provider_sessions": 0,
        "production_writes": 0,
    }:
        raise ContractError("SYNTHETIC_CLEANUP_INVALID")
    return {"status": "VERIFIED", "provider_proof": False, "receipt_sha256": expected_receipt_hash}


def verify_bundle(bundle: Mapping[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    """Validate a complete two-source bundle and every nested receipt."""

    if not isinstance(bundle, Mapping):
        raise ContractError("BUNDLE_MUST_BE_OBJECT")
    value = _clone(dict(bundle))
    _reject_sensitive_plaintext(value)
    schema = json.loads(BUNDLE_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        raise ContractError("BUNDLE_SCHEMA_INVALID:" + ";".join(error.message for error in errors))
    receipts = value["receipts"]
    if len(receipts) != 2:
        raise ContractError("BUNDLE_RECEIPT_COUNT_INVALID")
    source_codes = []
    receipt_hashes = []
    for receipt in receipts:
        verify_receipt(receipt, root=root)
        source_codes.append(receipt["source"]["source_code"])
        receipt_hashes.append(receipt["receipt_sha256"])
    if set(source_codes) != {"EI_AMAZON", "WIO_CREDIT"}:
        raise ContractError("BUNDLE_SOURCE_SET_INVALID")
    if len(set(receipt_hashes)) != len(receipt_hashes):
        raise ContractError("BUNDLE_RECEIPT_HASH_DUPLICATE")
    return {"status": "VERIFIED", "provider_proof": False, "receipt_count": len(receipts)}


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline real-mail n8n acceptance contract")
    parser.add_argument("--source", choices=("EI_AMAZON", "WIO_CREDIT"), default="EI_AMAZON")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.count < 0 or args.count > MAX_SYNTHETIC_COUNT:
        parser.error(f"--count must be between 0 and {MAX_SYNTHETIC_COUNT}")
    receipt = run_synthetic_e2e(source_code=args.source, count=args.count)
    text = canonical_json(receipt) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
