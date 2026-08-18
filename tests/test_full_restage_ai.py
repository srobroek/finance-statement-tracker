from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from finance_tracker.full_restage_ai import build_responses


class FullRestageAIResponseTests(TestCase):
    def test_builds_exact_per_source_responses_from_private_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            output = root / "responses"
            run.mkdir()
            (run / "source-a.json").write_text(
                json.dumps(
                    {
                        "ai_handoff": {
                            "transactions": {
                                "tx-1": {"merchant_raw": "Example Hotel", "category": None}
                            },
                            "requests": [
                                {
                                    "transaction_id": "tx-1",
                                    "transaction_ref": "tx-1",
                                    "policy_id": "classify-unresolved",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            decisions = root / "decisions.json"
            decisions.write_text(
                json.dumps(
                    {
                        "schema_version": "full-restage-ai-decisions-v1",
                        "provider": "codex-interactive",
                        "model": "test-model",
                        "default_proposals_by_policy": {"classify-unresolved": []},
                        "rules": [
                            {
                                "id": "hotel",
                                "policy_id": "classify-unresolved",
                                "merchant_regex": "hotel",
                                "proposals": [
                                    {
                                        "field": "category",
                                        "value": "Accommodation",
                                        "confidence": 0.99,
                                        "rationale": "Explicit hotel descriptor",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_responses(run, decisions, output)

            self.assertEqual(report["response_count"], 1)
            self.assertEqual(report["total_response_count"], 1)
            self.assertEqual(report["rule_hits"], {"hotel": 1})
            response = json.loads((output / "source-a.json").read_text(encoding="utf-8"))
            self.assertEqual(response[0]["proposals"][0]["value"], "Accommodation")
            self.assertEqual(response[0]["proposals"][0]["source_refs"], ["tx-1"])

    def test_fails_closed_without_a_rule_or_explicit_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            (run / "source-a.json").write_text(
                json.dumps(
                    {
                        "ai_handoff": {
                            "transactions": {"tx-1": {"merchant_raw": "Unknown"}},
                            "requests": [
                                {
                                    "transaction_id": "tx-1",
                                    "transaction_ref": "tx-1",
                                    "policy_id": "classify-unresolved",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            decisions = root / "decisions.json"
            decisions.write_text(
                json.dumps(
                    {
                        "schema_version": "full-restage-ai-decisions-v1",
                        "default_proposals_by_policy": {},
                        "rules": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "No proposal decision"):
                build_responses(run, decisions, root / "output")

    def test_merges_responses_across_fixed_point_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            output = root / "responses"
            run.mkdir()
            decisions = root / "decisions.json"
            decisions.write_text(
                json.dumps(
                    {
                        "schema_version": "full-restage-ai-decisions-v1",
                        "default_proposals_by_policy": {
                            "classify-unresolved": [],
                            "detect-subscription": [],
                        },
                        "rules": [],
                    }
                ),
                encoding="utf-8",
            )
            result_path = run / "source-a.json"
            result_path.write_text(
                json.dumps(
                    {
                        "ai_handoff": {
                            "transactions": {"tx-1": {"merchant_raw": "Example"}},
                            "requests": [
                                {
                                    "transaction_id": "tx-1",
                                    "transaction_ref": "tx-1",
                                    "policy_id": "classify-unresolved",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            build_responses(run, decisions, output)
            result_path.write_text(
                json.dumps(
                    {
                        "ai_handoff": {
                            "transactions": {"tx-1": {"merchant_raw": "Example"}},
                            "requests": [
                                {
                                    "transaction_id": "tx-1",
                                    "transaction_ref": "tx-1",
                                    "policy_id": "detect-subscription",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = build_responses(run, decisions, output)

            responses = json.loads((output / "source-a.json").read_text(encoding="utf-8"))
            self.assertEqual(report["new_response_count"], 1)
            self.assertEqual(report["total_response_count"], 2)
            self.assertEqual(
                {response["policy_id"] for response in responses},
                {"classify-unresolved", "detect-subscription"},
            )
