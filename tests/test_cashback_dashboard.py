from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from finance_tracker.actual_snapshot import (
    _build_card_state,
    _build_recommendations,
    _build_routing_graphs,
    cashback_dashboard,
)
from finance_tracker.cashback import PaymentIntent, configured_programs
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
        programs = configured_programs()
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
            "reward_estimate": {
                "label": "Estimated rewards based on configured terms",
                "authority": "NON_AUTHORITATIVE",
            },
            "cards": cards,
            "recommendations": recommendations,
            "routing_graphs": routing_graphs,
            "alerts": alerts,
        }
        public = cashback_dashboard(
            configured_programs(),
            rows,
            as_of,
            intents,
            routing_profiles=self.config["routing_profiles"],
            route_policies=self.config["route_policies"],
        )

        self.assertEqual(composed, public)
        self.assertEqual(public["reward_estimate"]["authority"], "NON_AUTHORITATIVE")
        ei = next(card for card in public["cards"] if card["card"] == "EI_AMAZON")
        self.assertIn("qualifying Prime membership", ei["position_detail"])
        self.assertIn("Amazon Reward Points", ei["position_detail"])
        self.assertEqual(ei["provenance_authority"], "NON_AUTHORITATIVE")
        digest = hashlib.sha256(
            json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(
            digest,
            "00b6db442f4f04e38d88fe7cd2ee13b30e60e26477184703b60db1df38420495",
        )

    def test_routes_disclose_configured_fx_fee_only_for_foreign_currency(self) -> None:
        rows = self.rows()
        cards, programs, _ = _build_card_state(configured_programs(), rows, date(2026, 8, 16), None, "AED")
        graphs = _build_routing_graphs(programs, cards, rows, self.config["routing_profiles"], self.config["route_policies"])
        foreign = next(graph for graph in graphs if graph["code"] == "FOREIGN")
        sc = next(candidate for candidate in foreign["ranked_cards"] if candidate["card"] == "SC_PLATINUM_X")
        self.assertEqual(Decimal(sc["configured_fx_fee_percent"]), Decimal("2.99"))
        for graph in graphs:
            if graph["currency"] == "AED":
                for candidate in graph["ranked_cards"]:
                    self.assertEqual(candidate["configured_fx_fee_percent"], "0")
        recommendations = _build_recommendations(programs, rows, [PaymentIntent("GENERAL", money("100"), "USD", "ONLINE")])
        sc_quote = next(candidate for candidate in recommendations[0]["ranked_cards"] if candidate["card"] == "SC_PLATINUM_X")
        self.assertEqual(Decimal(sc_quote["configured_fx_fee_percent"]), Decimal("2.99"))

    def test_web_discloses_estimate_and_evidence_status(self) -> None:
        root = Path("apps/cashback-control/web")
        shell = (root / "index.html").read_text(encoding="utf-8")
        app = (root / "app.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="reward-disclosure"', shell)
        self.assertIn("Estimates based on configured rewards · Card terms not fully verified", shell)
        self.assertIn('authority === "AUTHORITATIVE" ? "Issuer terms verified" : "Card terms not fully verified"', app)
        self.assertNotIn("Evidence: ${authority}", app)
        self.assertIn("renderRewardDisclosure(payload.reward_estimate)", app)
        self.assertIn("const routeItems = Array.isArray(items) ? items : [];", app)
        self.assertEqual(app.count("const routeItems = Array.isArray(items) ? items : [];"), 2)
        self.assertIn('"No eligible card route"', app)
        self.assertIn("cardEvidenceNode(card)", app)
        self.assertIn("provenance_authority", app)
        self.assertIn("provenance_reason", app)
        self.assertIn("grid-auto-rows: minmax(78px, auto);", styles)
        self.assertIn("#decision-tree", styles)
        self.assertIn("flex: 0 0 auto;", styles)

    def test_routing_graph_phase_keeps_policy_errors(self) -> None:
        rows = self.rows()
        cards, routing_programs, _ = _build_card_state(
            configured_programs(),
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
