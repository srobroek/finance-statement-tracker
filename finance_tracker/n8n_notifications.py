"""Canonical notification normalization inside the immutable n8n runner image."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .actual_pipeline import account_maps, load_actual_config, load_compiled_rules
from .cashback import load_program_configuration
from .notification_sources import load_notification_sources, validate_notification_adapter_coverage
from .notifications import DEFAULT_NOTIFICATION_ADAPTERS, parse_outlook_notifications


def normalize_archived_mailbox(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return compact events and one explicit disposition per archived message.

    Configuration always comes from this source/image, never the input payload.
    No persistence, network calls, AI enrichment, or cursor mutation occurs here.
    """
    root = Path(__file__).resolve().parent.parent
    messages = envelope.get("messages")
    if not isinstance(messages, list) or any(not isinstance(row, dict) for row in messages):
        raise ValueError("ARCHIVED_MESSAGES_REQUIRED")
    ids = [row.get("id") for row in messages]
    if any(not isinstance(value, str) or not value.strip() for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("ARCHIVED_MESSAGE_IDENTITIES_INVALID")
    if envelope.get("matched_count") != len(messages):
        raise ValueError("ARCHIVED_MESSAGE_COUNT_MISMATCH")
    context = {key: envelope.get(key) for key in ("source", "completed_at", "cursor")}
    if any(not isinstance(value, str) or not value.strip() for value in context.values()):
        raise ValueError("FROZEN_SCAN_CONTEXT_REQUIRED")
    if context["completed_at"] != context["cursor"]:
        raise ValueError("FROZEN_SCAN_CURSOR_MISMATCH")
    for value in (context["cursor"], envelope.get("window_start")):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("FROZEN_SCAN_TIMEZONE_REQUIRED")
    context["completed_at"] = datetime.fromisoformat(context["completed_at"].replace("Z", "+00:00")).isoformat()
    sources = load_notification_sources(root / "config/transaction-email-sources.json")
    validate_notification_adapter_coverage(sources, (adapter.code for adapter in DEFAULT_NOTIFICATION_ADAPTERS))
    selected = [source for source in sources if source.code == envelope.get("source_code") and source.active]
    if len(selected) != 1:
        raise ValueError("ACTIVE_NOTIFICATION_SOURCE_REQUIRED")
    adapters = tuple(adapter for adapter in DEFAULT_NOTIFICATION_ADAPTERS if adapter.code == selected[0].adapter)
    cards, _ = account_maps(load_actual_config(root / "config/actual-bootstrap.json"))
    config = load_program_configuration(root / "config/cashback-programs.json")
    rule_set = str((config.get("live_ingestion") or {}).get("rule_set") or "").upper()
    rules = [rule for rule in load_compiled_rules(root / "config/static-rules.seed.json") if rule_set in rule.rule_sets]
    if not rule_set or not rules:
        raise ValueError("CANONICAL_LIVE_RULES_REQUIRED")
    batch = parse_outlook_notifications(messages, cards, rules, adapters=adapters, cashback_config=config)
    dispositions = [
        {"message_id": event["source_event_id"][:-2], "status": "ACCEPTED", "source_event_id": event["source_event_id"]}
        for event in batch.events
    ]
    for skipped in batch.skipped:
        reason = skipped["reason"]
        dispositions.append({"message_id": skipped["message_id"],
                             "status": "IGNORED" if reason == "UNSUPPORTED_NOTIFICATION" else "REVIEW",
                             # Retain reason class, never parse-error body fragments.
                             "reason": reason.split(":", 1)[0]})
    if sorted(row["message_id"] for row in dispositions) != sorted(ids):
        raise ValueError("NOTIFICATION_DISPOSITION_COVERAGE_MISMATCH")
    return {**context, "scanned_count": len(messages), "accepted_count": len(batch.events),
            "ignored_count": sum(row["status"] == "IGNORED" for row in dispositions),
            "review_count": sum(row["status"] == "REVIEW" for row in dispositions),
            "message_dispositions": sorted(dispositions, key=lambda row: row["message_id"]),
            "events": list(batch.events)}
