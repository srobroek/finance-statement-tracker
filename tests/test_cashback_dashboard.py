from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from http import HTTPStatus
from finance_tracker.actual_snapshot import (
    _build_card_state,
    _build_recommendations,
    _build_routing_graphs,
    cashback_dashboard,
    eligible_card_codes,
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

    def test_phase_composition_preserves_dashboard_shape(self) -> None:
        rows = self.rows()
        as_of = date(2026, 8, 16)
        intents = [
            PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS"),
            PaymentIntent("AMAZON", money("250"), "AED", "ONLINE"),
        ]
        memberships = (
            {"card_code": "SC_PLATINUM_X", "coverage": "HELD", "sc_held": True},
        )
        programs = tuple(
            program
            for program in configured_programs()
            if program.card in {"RAK_WORLD", "SC_PLATINUM_X"}
        )
        rows = [
            row
            for row in rows
            if row.card in eligible_card_codes(programs, memberships)
        ]
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
            configured_programs(),
            rows,
            as_of,
            intents,
            routing_profiles=self.config["routing_profiles"],
            route_policies=self.config["route_policies"],
            memberships=memberships,
        )

        self.assertEqual(composed, public)

    def test_selected_card_period_filters_all_dashboard_projections(self) -> None:
        rows = [
            Transaction(
                "rak-historical-grocery",
                datetime(2026, 7, 31, tzinfo=UTC),
                "RAK_WORLD",
                "Historical groceries",
                money("3000"),
                category="GROCERY",
                channel="PHYSICAL_POS",
                reward_bucket="RAK_GROCERY",
            ),
            Transaction(
                "rak-current-grocery",
                datetime(2026, 8, 10, tzinfo=UTC),
                "RAK_WORLD",
                "Current groceries",
                money("100"),
                category="GROCERY",
                channel="PHYSICAL_POS",
                reward_bucket="RAK_GROCERY",
            ),
        ]
        dashboard = cashback_dashboard(
            configured_programs(),
            rows,
            date(2026, 8, 16),
            [PaymentIntent("GROCERY", money("100"), "AED", "PHYSICAL_POS")],
            periods_by_card={"RAK_WORLD": (date(2026, 8, 1), date(2026, 8, 16))},
            routing_profiles=self.config["routing_profiles"],
            route_policies=self.config["route_policies"],
        )

        rak_card = next(
            card for card in dashboard["cards"] if card["card"] == "RAK_WORLD"
        )
        self.assertEqual(rak_card["total_spend_aed"], "100")
        recommendation = dashboard["recommendations"][0]
        rak_recommendation = next(
            candidate
            for candidate in recommendation["ranked_cards"]
            if candidate["card"] == "RAK_WORLD"
        )
        self.assertEqual(recommendation["use_card"], "RAK_WORLD")
        self.assertEqual(rak_recommendation["card_spend_aed"], "100")
        grocery = next(
            graph for graph in dashboard["routing_graphs"] if graph["code"] == "GROCERY"
        )
        rak_route = next(
            candidate
            for candidate in grocery["ranked_cards"]
            if candidate["card"] == "RAK_WORLD" and candidate["bucket"] == "RAK_GROCERY"
        )
        self.assertEqual(grocery["use_card"], "RAK_WORLD")
        self.assertEqual(rak_route["card_spend_aed"], "100")
        self.assertEqual(rak_route["bucket_spend_aed"], "100")

    def test_expected_cashback_is_net_of_current_refund_deduction(self) -> None:
        rows = self.rows() + [
            Transaction(
                "sc-refund",
                datetime(2026, 8, 14, tzinfo=UTC),
                "SC_PLATINUM_X",
                "Online refund",
                money("100"),
                category="GENERAL",
                channel="ONLINE",
                reward_bucket="SC_ONLINE",
                transaction_type="REFUND",
                is_refund=True,
            )
        ]

        dashboard = cashback_dashboard(
            configured_programs(),
            rows,
            date(2026, 8, 16),
            [],
            route_policies=self.config["route_policies"],
            memberships=(
                {"card_code": "SC_PLATINUM_X", "coverage": "HELD", "sc_held": True},
            ),
        )
        sc_card = next(
            card for card in dashboard["cards"] if card["card"] == "SC_PLATINUM_X"
        )

        self.assertEqual(sc_card["total_spend_aed"], "4000")
        self.assertEqual(sc_card["expected_cashback_aed"], "97.00")

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
                [
                    {
                        "category": "GROCERY",
                        "routes": [
                            {
                                "card": "RAK_WORLD",
                                "channel": "PHYSICAL_POS",
                                "bucket": "RAK_GROCERY",
                                "policy": "missing",
                            }
                        ],
                    }
                ],
                {},
            )

    def test_unavailable_cards_are_excluded_from_all_dashboard_projections(
        self,
    ) -> None:
        rows = [
            Transaction(
                "sc-unavailable",
                datetime(2026, 8, 13, tzinfo=UTC),
                "SC_PLATINUM_X",
                "Online",
                money("4000"),
                category="GENERAL",
                channel="ONLINE",
                reward_bucket="SC_ONLINE",
            ),
            Transaction(
                "ei-statement",
                datetime(2026, 8, 14, tzinfo=UTC),
                "EI_AMAZON",
                "Amazon",
                money("100"),
                category="AMAZON",
                channel="ONLINE",
                reward_bucket="EI_AMAZON",
            ),
        ]
        unavailable = cashback_dashboard(
            configured_programs(),
            rows,
            date(2026, 8, 16),
            [PaymentIntent("AMAZON", money("100"), "AED", "ONLINE")],
            memberships=(),
            routing_profiles=self.config["routing_profiles"],
            route_policies=self.config["route_policies"],
        )
        self.assertEqual(
            {card["card"] for card in unavailable["cards"]},
            {"RAK_WORLD"},
        )
        candidates = [
            candidate
            for graph in unavailable["routing_graphs"]
            for candidate in graph["ranked_cards"]
        ]
        self.assertNotIn("SC_PLATINUM_X", {item["card"] for item in candidates})
        self.assertNotIn("EI_AMAZON", {item["card"] for item in candidates})
        self.assertNotIn(
            "SC_PLATINUM_X",
            {
                candidate["card"]
                for item in unavailable["recommendations"]
                for candidate in item["ranked_cards"]
            },
        )

        held = cashback_dashboard(
            configured_programs(),
            rows,
            date(2026, 8, 16),
            [],
            memberships=[
                {"card_code": "SC_PLATINUM_X", "coverage": "HELD", "sc_held": True}
            ],
            routing_profiles=self.config["routing_profiles"],
            route_policies=self.config["route_policies"],
        )
        self.assertEqual(
            {card["card"] for card in held["cards"]},
            {"RAK_WORLD", "SC_PLATINUM_X"},
        )

    def test_historical_periods_partition_derived_cycles_around_stored_periods(
        self,
    ) -> None:
        server_path = (
            Path(__file__).resolve().parent.parent
            / "apps"
            / "cashback-control"
            / "server.py"
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
                    "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
                    "CASHBACK_PUBLIC_URL": "http://127.0.0.1",
                },
            ),
        ):
            server_root = str(server_path.parent)
            sys.path.insert(0, server_root)
            try:
                spec = importlib.util.spec_from_file_location(
                    "cashback_dashboard_overlap_history_test", server_path
                )
                if spec is None or spec.loader is None:
                    raise AssertionError("Unable to load cashback server module")
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)
            finally:
                sys.path.remove(server_root)

        with tempfile.TemporaryDirectory() as temporary:
            store = server.CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.ensure_period(
                {
                    "period_id": "stored-august",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-08-16T12:00:00Z",
                }
            )
            store.upsert(
                [
                    {
                        "source_event_id": "stored-event",
                        "occurred_at": "2026-08-10T12:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "100",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Stored Period Market",
                    },
                    {
                        "source_event_id": "stored-boundary-event",
                        "occurred_at": "2026-08-16T10:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "50",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Stored Boundary Market",
                    },
                    {
                        "source_event_id": "pre-effective-start-event",
                        "occurred_at": "2026-07-31T23:59:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "75",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Pre-effective Market",
                    },
                    {
                        "source_event_id": "derived-event",
                        "occurred_at": "2026-08-20T12:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "200",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Derived Period Market",
                    },
                ]
            )
            with (
                patch.object(server, "STORE", store),
                patch.object(server, "_load_memberships", return_value=()),
            ):
                history = server.historical_periods(
                    memberships=(), as_of=date(2026, 9, 14)
                )

            self.assertEqual(len(history), 2)
            self.assertNotIn(
                "2026-07-31",
                {period["period_end"] for period in history},
            )
            stored = next(
                period for period in history if period["period_start"] == "2026-08-01"
            )
            derived = next(
                period for period in history if period["period_end"] == "2026-09-05"
            )
            self.assertEqual(stored["period_end"], "2026-08-16")
            self.assertEqual(stored["summary"]["transaction_count"], 2)
            self.assertEqual(stored["summary"]["total_spend_aed"], "150")
            self.assertEqual(derived["period_start"], "2026-08-16")
            self.assertEqual(derived["period_end"], "2026-09-05")
            self.assertEqual(derived["summary"]["transaction_count"], 1)
            self.assertEqual(derived["summary"]["total_spend_aed"], "200")
            self.assertEqual(
                [row["status"] for row in store.period_rows()],
                ["OPEN"],
            )

    def test_historical_periods_retain_same_day_stored_periods_and_gap(self) -> None:
        server_path = (
            Path(__file__).resolve().parent.parent
            / "apps"
            / "cashback-control"
            / "server.py"
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
                    "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
                    "CASHBACK_PUBLIC_URL": "http://127.0.0.1",
                },
            ),
        ):
            server_root = str(server_path.parent)
            sys.path.insert(0, server_root)
            try:
                spec = importlib.util.spec_from_file_location(
                    "cashback_dashboard_same_day_history_test", server_path
                )
                if spec is None or spec.loader is None:
                    raise AssertionError("Unable to load cashback server module")
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)
            finally:
                sys.path.remove(server_root)

        with tempfile.TemporaryDirectory() as temporary:
            store = server.CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.ensure_period(
                {
                    "period_id": "same-day-morning",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-16T00:00:00Z",
                    "period_end": "2026-08-16T12:00:00Z",
                }
            )
            store.ensure_period(
                {
                    "period_id": "same-day-evening",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-16T14:00:00Z",
                    "period_end": "2026-08-17T00:00:00Z",
                }
            )
            store.upsert(
                [
                    {
                        "source_event_id": "same-day-morning-event",
                        "occurred_at": "2026-08-16T10:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "100",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Morning Market",
                    },
                    {
                        "source_event_id": "same-day-gap-event",
                        "occurred_at": "2026-08-16T13:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "200",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Midday Market",
                    },
                    {
                        "source_event_id": "same-day-evening-event",
                        "occurred_at": "2026-08-16T15:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "300",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Evening Market",
                    },
                ]
            )
            with (
                patch.object(server, "STORE", store),
                patch.object(server, "_load_memberships", return_value=()),
            ):
                history = server.historical_periods(
                    memberships=(), as_of=date(2026, 9, 14)
                )

            self.assertEqual(len(history), 3)
            self.assertEqual(
                sorted(period["summary"]["total_spend_aed"] for period in history),
                ["100", "200", "300"],
            )
            for period in history:
                self.assertEqual(period["period_start"], "2026-08-16")
                self.assertEqual(period["period_end"], "2026-08-16")
            self.assertEqual(
                {
                    period["summary"]["total_spend_aed"]: period["status"]
                    for period in history
                },
                {"100": "OPEN", "200": "OPEN", "300": "OPEN"},
            )

    def test_historical_periods_attach_events_to_ended_open_period(self) -> None:
        server_path = (
            Path(__file__).resolve().parent.parent
            / "apps"
            / "cashback-control"
            / "server.py"
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
                    "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
                    "CASHBACK_PUBLIC_URL": "http://127.0.0.1",
                },
            ),
        ):
            server_root = str(server_path.parent)
            sys.path.insert(0, server_root)
            try:
                spec = importlib.util.spec_from_file_location(
                    "cashback_dashboard_open_history_test", server_path
                )
                if spec is None or spec.loader is None:
                    raise AssertionError("Unable to load cashback server module")
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)
            finally:
                sys.path.remove(server_root)

        with tempfile.TemporaryDirectory() as temporary:
            store = server.CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.ensure_period(
                {
                    "period_id": "ended-open-period",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-08-16T00:00:00Z",
                }
            )
            store.upsert(
                [
                    {
                        "source_event_id": "ended-open-event",
                        "occurred_at": "2026-08-10T12:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "100",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Ended Open Market",
                    }
                ]
            )
            with (
                patch.object(server, "STORE", store),
                patch.object(server, "_load_memberships", return_value=()),
            ):
                history = server.historical_periods(
                    memberships=(), as_of=date(2026, 8, 31)
                )

            self.assertEqual(len(history), 1)
            period = history[0]
            self.assertEqual(period["period_start"], "2026-08-01")
            self.assertEqual(period["period_end"], "2026-08-15")
            self.assertEqual(period["status"], "OPEN")
            self.assertEqual(period["summary"]["transaction_count"], 1)
            self.assertEqual(period["summary"]["total_spend_aed"], "100")
            self.assertEqual(store.period_rows()[0]["status"], "OPEN")

    def test_historical_periods_include_event_derived_open_cycle(self) -> None:
        server_path = (
            Path(__file__).resolve().parent.parent
            / "apps"
            / "cashback-control"
            / "server.py"
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
                    "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
                    "CASHBACK_PUBLIC_URL": "http://127.0.0.1",
                },
            ),
        ):
            server_root = str(server_path.parent)
            sys.path.insert(0, server_root)
            try:
                spec = importlib.util.spec_from_file_location(
                    "cashback_dashboard_event_history_test", server_path
                )
                if spec is None or spec.loader is None:
                    raise AssertionError("Unable to load cashback server module")
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)
            finally:
                sys.path.remove(server_root)

        with tempfile.TemporaryDirectory() as temporary:
            store = server.CashbackEventStore(Path(temporary) / "events.sqlite3")
            store.upsert(
                [
                    {
                        "source_event_id": "pre-effective-event",
                        "occurred_at": "2026-07-31T12:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "75",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Pre-effective Market",
                    },
                    {
                        "source_event_id": "event-only-history",
                        "occurred_at": "2026-08-02T12:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "100",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Event History Market",
                    },
                ]
            )
            self.assertEqual(store.period_rows(), [])

            live = server.build_live_dashboard(store, date(2026, 9, 14), memberships=())
            live_rak = next(
                card for card in live["cards"] if card["card"] == "RAK_WORLD"
            )
            self.assertEqual(live_rak["total_spend_aed"], "0")
            self.assertNotIn(
                "EI_AMAZON",
                {card["card"] for card in live["cards"]},
            )

            with (
                patch.object(server, "STORE", store),
                patch.object(server, "_load_memberships", return_value=()),
            ):
                history = server.historical_periods(
                    memberships=(), as_of=date(2026, 9, 14)
                )

            self.assertEqual(len(history), 1)
            period = history[0]
            self.assertEqual(period["card"], "RAK_WORLD")
            self.assertEqual(period["period_start"], "2026-08-01")
            self.assertEqual(period["period_end"], "2026-08-05")
            self.assertEqual(period["status"], "OPEN")
            self.assertEqual(period["reconciliation_status"], "UNMATCHED")
            self.assertIsNone(period["statement_reference"])
            self.assertIsNone(period["finalized_at"])
            self.assertEqual(period["summary"]["transaction_count"], 1)
            self.assertEqual(period["summary"]["total_spend_aed"], "100")
            self.assertEqual(store.period_rows(), [])

    def test_previous_bank_cycle_uses_finalized_history_and_live_eligibility(
        self,
    ) -> None:
        server_path = (
            Path(__file__).resolve().parent.parent
            / "apps"
            / "cashback-control"
            / "server.py"
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
                    "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
                    "CASHBACK_PUBLIC_URL": "http://127.0.0.1",
                },
            ),
        ):
            server_root = str(server_path.parent)
            sys.path.insert(0, server_root)
            try:
                spec = importlib.util.spec_from_file_location(
                    "cashback_dashboard_bank_history_test",
                    server_path,
                )
                if spec is None or spec.loader is None:
                    raise AssertionError("Unable to load cashback server module")
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)
            finally:
                sys.path.remove(server_root)

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = server.CashbackEventStore(database)
            store.ensure_period(
                {
                    "period_id": "rak-finalized-august",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-06T00:00:00Z",
                    "period_end": "2026-09-06T00:00:00Z",
                }
            )
            store.upsert(
                [
                    {
                        "source_event_id": "rak-finalized-history-event",
                        "occurred_at": "2026-08-10T12:00:00Z",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "100",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Historical Bank Market",
                    }
                ]
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE card_periods
                    SET status = 'FINALIZED', finalized_at = ?
                    WHERE period_id = ?
                    """,
                    ("2026-09-07T00:00:00+00:00", "rak-finalized-august"),
                )
            store.record_bank_statement_receipt(
                {
                    "source": "outlook",
                    "source_message_id": "rak-finalized-message",
                    "received_at": "2026-09-06T00:00:00Z",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-06",
                    "period_end": "2026-09-05",
                }
            )
            store.record_bank_statement_receipt(
                {
                    "source": "outlook",
                    "source_message_id": "statement-only-message",
                    "received_at": "2026-09-06T00:00:00Z",
                    "card_code": "EI_AMAZON",
                }
            )
            with (
                patch.object(server, "STORE", store),
                patch.object(server, "_load_memberships", return_value=()),
            ):
                previous = server._previous_statement_cycles(as_of=date(2026, 9, 14))
                visible_receipts = server._eligible_statement_receipts()

            self.assertEqual([cycle["card"] for cycle in previous], ["RAK_WORLD"])
            cycle = previous[0]
            self.assertEqual(cycle["settlement_state"], "FINALIZED")
            self.assertEqual(cycle["summary"]["total_spend_aed"], "100")
            self.assertEqual(
                {receipt["card_code"] for receipt in visible_receipts},
                {"RAK_WORLD"},
            )

    def test_previous_bank_cycle_bounds_summary_to_receipt_interval(self) -> None:
        server_path = (
            Path(__file__).resolve().parent.parent
            / "apps"
            / "cashback-control"
            / "server.py"
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
                    "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
                    "CASHBACK_PUBLIC_URL": "http://127.0.0.1",
                },
            ),
        ):
            server_root = str(server_path.parent)
            sys.path.insert(0, server_root)
            try:
                spec = importlib.util.spec_from_file_location(
                    "cashback_dashboard_bank_receipt_bounds_test",
                    server_path,
                )
                if spec is None or spec.loader is None:
                    raise AssertionError("Unable to load cashback server module")
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)
            finally:
                sys.path.remove(server_root)

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = server.CashbackEventStore(database)
            store.ensure_period(
                {
                    "period_id": "rak-wide-august",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-09-06T00:00:00Z",
                }
            )
            store.upsert(
                [
                    {
                        "source_event_id": "rak-before-receipt",
                        "occurred_at": "2026-08-02T12:00:00Z",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "50",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Before Receipt Market",
                    },
                    {
                        "source_event_id": "rak-within-receipt",
                        "occurred_at": "2026-08-10T12:00:00Z",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "100",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Within Receipt Market",
                    },
                ]
            )
            store.record_bank_statement_receipt(
                {
                    "source": "outlook",
                    "source_message_id": "rak-narrow-receipt",
                    "received_at": "2026-09-06T00:00:00Z",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-06",
                    "period_end": "2026-09-05",
                }
            )
            with (
                patch.object(server, "STORE", store),
                patch.object(server, "_load_memberships", return_value=()),
            ):
                previous = server._previous_statement_cycles(as_of=date(2026, 9, 14))

            self.assertEqual(len(previous), 1)
            cycle = previous[0]
            self.assertEqual(cycle["settlement_state"], "UNFINALIZED")
            self.assertEqual(cycle["period_start"], "2026-08-06")
            self.assertEqual(cycle["period_end"], "2026-09-05")
            self.assertEqual(cycle["summary"]["transaction_count"], 1)
            self.assertEqual(cycle["summary"]["total_spend_aed"], "100")
            self.assertEqual(store.period_rows()[0]["status"], "OPEN")

    def test_available_as_of_dates_require_stored_evidence(self) -> None:
        class EvidenceStore:
            def __init__(
                self,
                periods: list[dict[str, object]],
                receipts: list[dict[str, object]],
                events: list[dict[str, object]],
            ) -> None:
                self._periods = periods
                self._receipts = receipts
                self._events = events

            def period_rows(self) -> list[dict[str, object]]:
                return self._periods

            def receipt_rows(self) -> list[dict[str, object]]:
                return self._receipts

            def rows(self, start: date, end: date) -> list[dict[str, object]]:
                return self._events

        server_path = (
            Path(__file__).resolve().parent.parent
            / "apps"
            / "cashback-control"
            / "server.py"
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
                    "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
                    "CASHBACK_PUBLIC_URL": "http://127.0.0.1",
                },
            ),
        ):
            server_root = str(server_path.parent)
            sys.path.insert(0, server_root)
            try:
                spec = importlib.util.spec_from_file_location(
                    "cashback_dashboard_server_test", server_path
                )
                if spec is None or spec.loader is None:
                    raise AssertionError("Unable to load cashback server module")
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)
            finally:
                sys.path.remove(server_root)

        config_path = Path("config/cashback-programs.json")
        as_of = date(2026, 9, 14)
        no_evidence = EvidenceStore([], [], [])
        self.assertEqual(
            server._available_as_of_dates(
                store=no_evidence, config_path=config_path, as_of=as_of
            ),
            [],
        )

        evidenced = EvidenceStore(
            [
                {
                    "period_id": "open-receipt-period",
                    "card_code": "RAK_WORLD",
                    "period_end": "2026-08-31",
                    "status": "OPEN",
                },
                {
                    "period_id": "finalized-period",
                    "card_code": "RAK_WORLD",
                    "period_end": "2026-09-05",
                    "status": "FINALIZED",
                },
                {
                    "period_id": "canonical-finalized-period",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-09-06T00:00:00Z",
                    "period_end": "2026-09-11T00:00:00Z",
                    "status": "FINALIZED",
                },
                {
                    "period_id": "excluded-period",
                    "card_code": "EI_AMAZON",
                    "period_end": "2026-09-01",
                    "status": "FINALIZED",
                },
            ],
            [{"card_code": "RAK_WORLD", "period_id": "open-receipt-period"}],
            [
                {
                    "card_code": "RAK_WORLD",
                    "occurred_at": "2026-08-10T12:00:00+00:00",
                }
            ],
        )
        expected = ["2026-09-10", "2026-09-05", "2026-08-31"]
        self.assertEqual(
            server._available_as_of_dates(
                store=evidenced, config_path=config_path, as_of=as_of
            ),
            expected,
        )
        self.assertEqual(
            server._available_as_of_dates(
                store=evidenced, config_path=config_path, as_of=as_of
            ),
            expected,
        )

        self.assertEqual(
            server._reporting_date("2026-08-01T00:00:00.000000Z"),
            date(2026, 8, 1),
        )
        self.assertEqual(
            server._reporting_date("2026-09-01T00:00:00.000000Z", period_end=True),
            date(2026, 8, 31),
        )
        self.assertEqual(
            server._reporting_date("2026-09-01T12:00:00.000000Z", period_end=True),
            date(2026, 9, 1),
        )
        self.assertEqual(
            server._reporting_date("2026-08-31"),
            date(2026, 8, 31),
        )
        for invalid in (
            "2026-8-16",
            " 2026-08-16",
            "2026-08-16T00:00:00",
            "2026-08-16T00:00:00+04:00",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    server._reporting_date(invalid, field_name="as_of")

        class HistoricalStore:
            def period_rows(self) -> list[dict[str, object]]:
                return [
                    {
                        "period_id": "canonical-period",
                        "card_code": "RAK_WORLD",
                        "period_start": "2026-08-01T00:00:00Z",
                        "period_end": "2026-09-01T00:00:00Z",
                        "status": "FINALIZED",
                        "reconciliation_status": "RECONCILED",
                        "statement_reference": "statement-1",
                        "finalized_at": "2026-09-02T00:00:00Z",
                    }
                ]

        with (
            patch.object(server, "STORE", HistoricalStore()),
            patch.object(
                server,
                "build_live_dashboard",
                return_value={"cards": [{"card": "RAK_WORLD"}]},
            ) as build_dashboard,
        ):
            historical = server.historical_periods(memberships=())
        self.assertEqual(
            historical[0]["period_start"],
            "2026-08-01",
        )
        self.assertEqual(historical[0]["period_end"], "2026-08-31")
        self.assertEqual(build_dashboard.call_args.args[1], date(2026, 8, 31))

        def get_dashboard(query: str) -> list[tuple[HTTPStatus, object]]:
            responses: list[tuple[HTTPStatus, object]] = []
            handler = object.__new__(server.CashbackHandler)
            handler.path = f"/api/dashboard?as_of={query}"
            handler._authorize_operational_read = lambda **_: True
            handler._json = lambda status, payload: responses.append((status, payload))
            handler.do_GET()
            return responses

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "events.sqlite3"
            store = server.CashbackEventStore(database)
            store.ensure_period(
                {
                    "period_id": "finalized-period",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-16T00:00:00Z",
                    "period_end": "2026-09-01T00:00:00Z",
                }
            )
            store.ensure_period(
                {
                    "period_id": "closed-period",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-01T00:00:00Z",
                    "period_end": "2026-08-16T00:00:00Z",
                }
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    UPDATE card_periods
                    SET status = 'FINALIZED',
                        reconciliation_status = 'RECONCILED',
                        statement_reference = 'statement-1',
                        finalized_at = '2026-09-02T00:00:00Z'
                    WHERE period_id = 'finalized-period'
                    """
                )
            store.upsert(
                [
                    {
                        "source_event_id": "closed-purchase",
                        "occurred_at": "2026-08-10T12:00:00+00:00",
                        "card_code": "RAK_WORLD",
                        "amount_aed": "100",
                        "purchase_type": "GROCERY",
                        "channel": "PHYSICAL_POS",
                        "merchant": "Closed Period Market",
                    }
                ]
            )
            closed = store.record_statement_receipt(
                {
                    "receipt_id": "closed-receipt",
                    "source_identity": "mail:rak:closed",
                    "card_code": "RAK_WORLD",
                    "original_received_at": "2026-08-16T00:00:00Z",
                    "period_id": "closed-period",
                }
            )
            self.assertEqual(closed["status"], "CLOSED")
            with (
                patch.object(server, "STORE", store),
                patch.object(server, "_load_memberships", return_value=()),
            ):
                historical = server.historical_periods(memberships=())
                response = get_dashboard("2026-08-10")
            self.assertEqual(historical[0]["period_end"], "2026-08-31")
            self.assertEqual(
                {period["status"] for period in historical},
                {"FINALIZED", "CLOSED"},
            )
            closed_dashboard = response[0][1]
            self.assertIn(
                "RAK_WORLD",
                {card["card"] for card in closed_dashboard["cards"]},
            )
            rak = next(
                card
                for card in closed_dashboard["cards"]
                if card["card"] == "RAK_WORLD"
            )
            self.assertEqual(rak["total_spend_aed"], "100")
            self.assertEqual(rak["transaction_count"], 1)

        for invalid in (
            "2026-8-16",
            "2026-08-16T00:00:00",
            "2026-08-16T00:00:00+04:00",
            (date.today() + timedelta(days=1)).isoformat(),
        ):
            with self.subTest(query=invalid):
                response = get_dashboard(invalid)
                self.assertEqual(response[0][0], HTTPStatus.BAD_REQUEST)

        payload = {"as_of": "2000-01-01"}
        with (
            patch.object(server, "_load_memberships", return_value=()),
            patch.object(server, "_historical_period_ids", return_value=set()),
            patch.object(
                server, "build_live_dashboard", return_value=payload
            ) as build_dashboard,
        ):
            response = get_dashboard("2000-01-01")
        self.assertEqual(response, [(HTTPStatus.OK, payload)])
        self.assertEqual(build_dashboard.call_args.args[1], date(2000, 1, 1))


if __name__ == "__main__":
    import unittest

    unittest.main()
