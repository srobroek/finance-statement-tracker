from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest import TestCase

from finance_tracker.actual_snapshot import (
    _build_card_state,
    _build_recommendations,
    _build_routing_graphs,
    cashback_dashboard,
)
from finance_tracker.cashback import PaymentIntent, poc_programs
from finance_tracker.models import Transaction, money


class CashbackDashboardPhaseTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            Path("config/cashback-programs.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def rows() -> list[Transaction]:
        return [
            Transaction(
                "rak-grocery-cap",
                datetime(2026, 8, 10, tzinfo=UTC),
                "RAK_WORLD",
                "Groceries",
                money("3000"),
                category="GROCERY",
                channel="PHYSICAL_POS",
                reward_bucket="RAK_GROCERY",
            ),
            Transaction(
                "rak-dining-cap",
                datetime(2026, 8, 11, tzinfo=UTC),
                "RAK_WORLD",
                "Dining",
                money("3000"),
                category="DINING",
                channel="PHYSICAL_POS",
                reward_bucket="RAK_DINING",
            ),
            Transaction(
                "rak-travel-near-cap",
                datetime(2026, 8, 12, tzinfo=UTC),
                "RAK_WORLD",
                "Travel",
                money("3950"),
                category="TRAVEL",
                channel="PHYSICAL_POS",
                reward_bucket="RAK_TRAVEL",
            ),
            Transaction(
                "sc-online-cap",
                datetime(2026, 8, 13, tzinfo=UTC),
                "SC_PLATINUM_X",
                "Online",
                money("4000"),
                category="GENERAL",
                channel="ONLINE",
                reward_bucket="SC_ONLINE",
            ),
        ]

    def test_phase_composition_preserves_dashboard_shape_and_digest(self) -> None:
        rows = self.rows()
        as_of = date(2026, 8, 16)
        intents = [
            PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS"),
            PaymentIntent("AMAZON", money("250"), "AED", "ONLINE"),
        ]
        programs = poc_programs()
        cards, routing_programs, alerts = _build_card_state(
            programs,
            rows,
            as_of,
            None,
            "AED",
        )
        recommendations = _build_recommendations(routing_programs, rows, intents)
        routing_graphs = _build_routing_graphs(
            routing_programs,
            cards,
            rows,
            self.config["routing_profiles"],
            self.config["route_policies"],
        )
        composed = {
            "schema_version": 1,
            "as_of": as_of.isoformat(),
            "currency": "AED",
            "cards": cards,
            "recommendations": recommendations,
            "routing_graphs": routing_graphs,
            "alerts": alerts,
        }
        public = cashback_dashboard(
            poc_programs(),
            rows,
            as_of,
            intents,
            routing_profiles=self.config["routing_profiles"],
            route_policies=self.config["route_policies"],
        )

        self.assertEqual(composed, public)
        digest = hashlib.sha256(
            json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(
            digest,
            "b387f00bc78f538088435a9a41422296a3e8e25fa311b81b35ab1d25ea702cd3",
        )

    def test_routing_graph_phase_keeps_policy_errors(self) -> None:
        rows = self.rows()
        cards, routing_programs, _ = _build_card_state(
            poc_programs(),
            rows,
            date(2026, 8, 16),
            None,
            "AED",
        )
        with self.assertRaisesRegex(ValueError, "Unknown routing policy: missing"):
            _build_routing_graphs(
                routing_programs,
                cards,
                rows,
                [{"category": "GROCERY", "routes": [{"card": "RAK_WORLD", "channel": "PHYSICAL_POS", "bucket": "RAK_GROCERY", "policy": "missing"}]}],
                {},
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
