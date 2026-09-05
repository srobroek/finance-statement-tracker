from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit


_KEY = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subscription(source: dict[str, Any]) -> dict[str, str]:
    endpoint = str(source.get("endpoint") or "").strip()
    keys = source.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("Push subscription keys are required")
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or len(endpoint) > 4096:
        raise ValueError("Push endpoint must be a valid HTTPS URL")
    if not p256dh or len(p256dh) > 512 or not _KEY.fullmatch(p256dh):
        raise ValueError("Push p256dh key is invalid")
    if not auth or len(auth) > 256 or not _KEY.fullmatch(auth):
        raise ValueError("Push auth key is invalid")
    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


class WebPushStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS push_subscriptions (
                        endpoint TEXT PRIMARY KEY,
                        p256dh TEXT NOT NULL,
                        auth TEXT NOT NULL,
                        user_agent TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        last_success_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS push_deliveries (
                        notification_key TEXT NOT NULL,
                        endpoint TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(notification_key, endpoint)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS push_state (
                        state_key TEXT PRIMARY KEY,
                        state_value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def upsert_subscription(self, source: dict[str, Any], user_agent: str | None = None) -> dict[str, Any]:
        item = _subscription(source)
        now = _now()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO push_subscriptions (
                        endpoint, p256dh, auth, user_agent, enabled,
                        failure_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, 0, ?, ?)
                    ON CONFLICT(endpoint) DO UPDATE SET
                        p256dh=excluded.p256dh,
                        auth=excluded.auth,
                        user_agent=excluded.user_agent,
                        enabled=1,
                        failure_count=0,
                        updated_at=excluded.updated_at
                    """,
                    (item["endpoint"], item["p256dh"], item["auth"], user_agent, now, now),
                )
        return {"endpoint": item["endpoint"], "enabled": True}

    def remove_subscription(self, endpoint: object) -> dict[str, Any]:
        value = str(endpoint or "").strip()
        if not value:
            raise ValueError("Push endpoint is required")
        with closing(self._connect()) as connection:
            with connection:
                deleted = connection.execute(
                    "DELETE FROM push_subscriptions WHERE endpoint = ?", (value,)
                ).rowcount
        return {"endpoint": value, "removed": bool(deleted)}

    def subscriptions(self, endpoint: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE enabled = 1"
        parameters: tuple[object, ...] = ()
        if endpoint:
            query += " AND endpoint = ?"
            parameters = (endpoint,)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}}
            for row in rows
        ]

    def delivered(self, notification_key: str, endpoint: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM push_deliveries
                WHERE notification_key = ? AND endpoint = ? AND status = 'SENT'
                """,
                (notification_key, endpoint),
            ).fetchone()
        return row is not None

    def record_delivery(
        self,
        notification_key: str,
        endpoint: str,
        payload: str,
        status: str,
        error: str | None = None,
    ) -> None:
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        now = _now()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO push_deliveries (
                        notification_key, endpoint, payload_hash, status, error, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(notification_key, endpoint) DO UPDATE SET
                        payload_hash=excluded.payload_hash,
                        status=excluded.status,
                        error=excluded.error,
                        created_at=excluded.created_at
                    """,
                    (notification_key, endpoint, payload_hash, status, error, now),
                )
                if status == "SENT":
                    connection.execute(
                        """
                        UPDATE push_subscriptions
                        SET failure_count = 0, last_success_at = ?, updated_at = ?
                        WHERE endpoint = ?
                        """,
                        (now, now, endpoint),
                    )

    def record_failure(self, endpoint: str, *, disable: bool = False) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE push_subscriptions
                    SET failure_count = failure_count + 1,
                        enabled = CASE WHEN ? THEN 0 ELSE enabled END,
                        updated_at = ?
                    WHERE endpoint = ?
                    """,
                    (int(disable), _now(), endpoint),
                )

    def swap_state(self, key: str, value: str) -> str | None:
        with closing(self._connect()) as connection:
            previous = connection.execute(
                "SELECT state_value FROM push_state WHERE state_key = ?", (key,)
            ).fetchone()
            with connection:
                connection.execute(
                    """
                    INSERT INTO push_state (state_key, state_value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(state_key) DO UPDATE SET
                        state_value=excluded.state_value,
                        updated_at=excluded.updated_at
                    """,
                    (key, value, _now()),
                )
        return None if previous is None else str(previous["state_value"])

    def stats(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            subscriptions = connection.execute(
                "SELECT COUNT(*) AS count FROM push_subscriptions WHERE enabled = 1"
            ).fetchone()["count"]
            sent = connection.execute(
                "SELECT COUNT(*) AS count FROM push_deliveries WHERE status = 'SENT'"
            ).fetchone()["count"]
        return {"subscription_count": int(subscriptions), "sent_count": int(sent)}


@dataclass(frozen=True, slots=True)
class PushCandidate:
    key: str
    title: str
    body: str
    screen: str


def _routing_map(dashboard: dict[str, Any]) -> dict[str, str]:
    rows = dashboard.get("routing_graphs") or dashboard.get("recommendations") or []
    result: dict[str, str] = {}
    for item in rows:
        if item.get("active") is False:
            continue
        label = str(item.get("label") or item.get("purchase_type") or "Spend")
        channel = str(item.get("channel") or "")
        currency = str(item.get("currency") or "AED")
        result[f"{label}|{channel}|{currency}"] = str(item.get("use_card") or "")
    return result


def notification_candidates(
    dashboard: dict[str, Any],
    previous_routing_json: str | None,
) -> tuple[list[PushCandidate], str]:
    cards = {str(card["card"]): card for card in dashboard.get("cards") or []}
    card_names = {
        code: str(card.get("short_name") or card.get("name") or code.replace("_", " ").title())
        for code, card in cards.items()
    }
    acknowledged = set((dashboard.get("data_status") or {}).get("acknowledged_alerts") or [])
    candidates: list[PushCandidate] = []
    for alert in dashboard.get("alerts") or []:
        key = str(alert.get("key") or "")
        if key in acknowledged:
            continue
        if key.startswith("bucket:") and key.endswith(":full"):
            parts = key.split(":")
            card = cards.get(parts[1]) or {}
            period = f"{card.get('period_start')}:{card.get('period_end')}"
            candidates.append(PushCandidate(
                key=f"{key}:{period}",
                title=str(alert["title"]),
                body=str(alert["detail"]),
                screen="cards",
            ))
        elif key.startswith("close:"):
            candidates.append(PushCandidate(
                key=key,
                title=str(alert["title"]),
                body=str(alert["detail"]),
                screen="cards",
            ))

    data_status = dashboard.get("data_status") or {}
    if data_status.get("is_stale") and "feed:stale" not in acknowledged:
        last_success = str(data_status.get("last_successful_check_at") or data_status.get("last_successful_ingest_at") or "never")
        stale_after = int(data_status.get("stale_after_minutes") or 90)
        candidates.append(PushCandidate(
            key=f"feed:stale:{last_success}",
            title="Cashback feed is stale",
            body=(
                (f"The scheduled transaction check is overdue after its {stale_after}-minute grace period. "
                 if data_status.get("freshness_basis") == "SCHEDULE"
                 else f"No successful transaction scan was recorded within {stale_after} minutes. ")
                +
                "Live card routing may be incomplete."
            ),
            screen="routing",
        ))

    routing = _routing_map(dashboard)
    routing_json = json.dumps(routing, sort_keys=True, separators=(",", ":"))
    if previous_routing_json and previous_routing_json != routing_json:
        previous = json.loads(previous_routing_json)
        changes = [
            f"{key.split('|', 1)[0]} → {card_names.get(card, card.replace('_', ' ').title())}"
            for key, card in routing.items()
            if previous.get(key) != card
        ]
        if changes:
            digest = hashlib.sha256(routing_json.encode("utf-8")).hexdigest()[:16]
            candidates.append(PushCandidate(
                key=f"routing:{digest}",
                title="Card routing changed",
                body=" · ".join(changes[:3]),
                screen="routing",
            ))
    return candidates, routing_json


class WebPushDispatcher:
    def __init__(
        self,
        store: WebPushStore,
        *,
        public_key: str = "",
        private_key: str = "",
        subject: str = "",
        public_url: str = "",
        sender: Callable[..., Any] | None = None,
    ):
        self.store = store
        self.public_key = public_key.strip()
        self.private_key = private_key.strip()
        self.subject = subject.strip()
        self.public_url = public_url.rstrip("/")
        self._sender = sender

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.private_key and self.subject and self.public_url)

    def config(self) -> dict[str, Any]:
        # Subscription counts and delivery state are operational metadata, not
        # browser configuration. Keep them behind the private store boundary.
        return {"enabled": self.enabled, "public_key": self.public_key}

    def evaluate(self, dashboard: dict[str, Any]) -> dict[str, int]:
        routing_json = json.dumps(_routing_map(dashboard), sort_keys=True, separators=(",", ":"))
        previous = self.store.swap_state("routing-map", routing_json)
        candidates, _ = notification_candidates(dashboard, previous)
        return self.send(candidates)

    def send_test(self, endpoint: str) -> dict[str, int]:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()[:12]
        return self.send(
            [PushCandidate(
                f"test:{endpoint_hash}:{timestamp}",
                "Cashback alerts enabled",
                "Live bucket, cycle and routing notifications are active.",
                "routing",
            )],
            endpoint=endpoint,
        )

    def send(self, candidates: Iterable[PushCandidate], endpoint: str | None = None) -> dict[str, int]:
        if not self.enabled:
            return {"sent": 0, "failed": 0, "skipped": 0}
        subscriptions = self.store.subscriptions(endpoint)
        sent = failed = skipped = 0
        for candidate in candidates:
            payload = json.dumps({
                "web_push": 8030,
                "notification": {
                    "title": candidate.title,
                    "body": candidate.body,
                    "navigate": f"{self.public_url}/?screen={candidate.screen}",
                    "tag": candidate.key,
                    "silent": False,
                    "app_badge": "1",
                },
            }, separators=(",", ":"))
            for subscription in subscriptions:
                target = str(subscription["endpoint"])
                if self.store.delivered(candidate.key, target):
                    skipped += 1
                    continue
                try:
                    self._send(subscription, payload)
                except Exception as error:  # network/library boundary
                    response = getattr(error, "response", None)
                    status_code = getattr(response, "status_code", None)
                    disable = status_code in {404, 410}
                    detail = f"{type(error).__name__}: {error}"[:500]
                    self.store.record_delivery(candidate.key, target, payload, "FAILED", detail)
                    self.store.record_failure(target, disable=disable)
                    failed += 1
                else:
                    self.store.record_delivery(candidate.key, target, payload, "SENT")
                    sent += 1
        return {"sent": sent, "failed": failed, "skipped": skipped}

    def _send(self, subscription: dict[str, Any], payload: str) -> Any:
        sender = self._sender
        if sender is None:
            from pywebpush import webpush

            sender = webpush
        return sender(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=self.private_key,
            vapid_claims={"sub": self.subject},
            ttl=3600,
            headers={"Urgency": "high"},
            timeout=15,
        )
