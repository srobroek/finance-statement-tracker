from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest import TestCase

from finance_tracker.ai_rules import (
    AIPolicy,
    AIEnrichmentEngine,
    OpenAICompatibleResolver,
    load_ai_policies,
    record_ai_review,
    validate_policy,
)
from finance_tracker.models import Transaction


ROOT = Path(__file__).resolve().parent.parent


class AIRuleTests(TestCase):
    def setUp(self) -> None:
        self.engine = AIEnrichmentEngine(load_ai_policies(ROOT / "config" / "ai-policies.json"))
        self.transaction = Transaction(
            "ai-1",
            datetime(2026, 8, 16),
            "SC_PLATINUM_X",
            "UNKNOWN MARKETPLACE 42",
            "100",
        )

    def test_ai_accepts_allowed_unresolved_category_and_tag(self) -> None:
        def resolver(request):
            if request["policy_id"] == "classify-unresolved":
                return {"provider": "test", "model": "fake-model", "proposals": [
                    {"field": "category", "value": "Online Shopping", "confidence": 0.93, "rationale": "Marketplace descriptor"},
                    {"field": "tags", "value": ["online"], "confidence": 0.91, "rationale": "Online marketplace"},
                ]}
            return {"proposals": []}

        traces = self.engine.enrich(self.transaction, resolver)

        self.assertEqual(self.transaction.category, "Online Shopping")
        self.assertIn("online", self.transaction.tags)
        self.assertTrue(all(trace.accepted for trace in traces))
        self.assertTrue(self.transaction.metadata["ai_trace"])
        self.assertEqual(self.transaction.metadata["ai_trace"][0]["provider"], "test")
        self.assertEqual(self.transaction.metadata["ai_trace"][0]["model"], "fake-model")
        self.assertEqual(self.transaction.metadata["ai_trace"][0]["policy_version"], 1)

    def test_ai_cannot_modify_protected_facts(self) -> None:
        def resolver(request):
            return {"proposals": [
                {"field": "amount_aed", "value": "1", "confidence": 1, "rationale": "unsafe"}
            ]}

        traces = self.engine.enrich(self.transaction, resolver)

        self.assertEqual(str(self.transaction.amount_aed), "100")
        self.assertTrue(traces)
        self.assertTrue(all(not trace.accepted for trace in traces))
        self.assertTrue(self.transaction.review_required)

    def test_ai_does_not_overwrite_static_or_manual_category(self) -> None:
        self.transaction.category = "Groceries"
        self.transaction.metadata["locked_fields"] = ["category"]
        requests = []

        def resolver(request):
            requests.append(request)
            return {"proposals": []}

        self.engine.enrich(self.transaction, resolver)

        classification = next(request for request in requests if request["policy_id"] == "classify-unresolved")
        self.assertNotIn("category", classification["allowed_fields"])
        self.assertEqual(self.transaction.category, "Groceries")

    def test_low_confidence_proposal_is_rejected_for_review(self) -> None:
        def resolver(request):
            if request["policy_id"] == "classify-unresolved":
                return {"proposals": [
                    {"field": "category", "value": "General Retail", "confidence": 0.4, "rationale": "guess"}
                ]}
            return {"proposals": []}

        traces = self.engine.enrich(self.transaction, resolver)

        rejected = next(trace for trace in traces if trace.field == "category")
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, "below_confidence_threshold")
        self.assertIsNone(self.transaction.category)
        self.assertTrue(self.transaction.review_required)

    def test_ai_can_enrich_unresolved_channel_and_bucket_without_reward_math(self) -> None:
        def resolver(request):
            if request["policy_id"] == "enrich-cashback-classification":
                return {"provider": "test", "model": "fake-model", "proposals": [
                    {"field": "channel", "value": "ONLINE", "confidence": 0.98, "rationale": "Explicit online marker"},
                    {"field": "reward_bucket", "value": "SC_ONLINE", "confidence": 0.99, "rationale": "Configured card and online channel"},
                ]}
            return {"proposals": []}

        self.engine.enrich(self.transaction, resolver)

        self.assertEqual(self.transaction.channel, "ONLINE")
        self.assertEqual(self.transaction.reward_bucket, "SC_ONLINE")

    def test_configured_policy_scopes_avoid_irrelevant_adcb_requests(self) -> None:
        transaction = Transaction(
            "ai-adcb",
            datetime(2026, 8, 16),
            "ADCB_CASHBACK",
            "CARREFOUR",
            "125",
            vendor="Carrefour",
            category="Groceries",
        )
        requests = []

        self.engine.enrich(transaction, lambda request: requests.append(request) or {"proposals": []})

        self.assertEqual([], requests)

    def test_property_policy_only_accepts_configured_property_identities(self) -> None:
        transaction = Transaction(
            "property-ai",
            datetime(2026, 8, 16),
            "ADCB_CASHBACK",
            "EMPOWER",
            "207.90",
            category="District Cooling",
        )

        def resolver(request):
            if request["policy_id"] == "enrich-property":
                return {"proposals": [
                    {
                        "field": "property_code",
                        "value": "LT713",
                        "confidence": 0.99,
                        "rationale": "Explicit account reference in linked bill",
                        "source_refs": ["outlook-message"],
                    },
                    {
                        "field": "rental_unit",
                        "value": "LT713",
                        "confidence": 0.99,
                        "rationale": "Explicit unit in linked bill",
                        "source_refs": ["outlook-message"],
                    },
                ]}
            return {"proposals": []}

        traces = self.engine.enrich(transaction, resolver)

        self.assertEqual(transaction.property_code, "LT713")
        self.assertEqual(transaction.rental_unit, "LT713")
        self.assertTrue(all(trace.accepted for trace in traces))

    def test_property_policy_rejects_unconfigured_unit(self) -> None:
        transaction = Transaction(
            "property-ai-unknown",
            datetime(2026, 8, 16),
            "ADCB_CASHBACK",
            "DEWA",
            "500",
            category="Electricity & Water",
        )

        traces = self.engine.enrich(
            transaction,
            lambda request: {
                "proposals": [{
                    "field": "rental_unit",
                    "value": "Invented99",
                    "confidence": 1,
                    "rationale": "Unsupported",
                }]
            } if request["policy_id"] == "enrich-property" else {"proposals": []},
        )

        rejected = next(trace for trace in traces if trace.field == "rental_unit")
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, "value_not_allowed")
        self.assertIsNone(transaction.rental_unit)

    def test_high_value_unresolved_purchase_requests_only_relevant_policies(self) -> None:
        transaction = Transaction(
            "ai-adcb-high-value",
            datetime(2026, 8, 16),
            "ADCB_CASHBACK",
            "UNKNOWN DURABLE GOODS MERCHANT",
            "1500",
        )
        requests = []

        self.engine.enrich(transaction, lambda request: requests.append(request) or {"proposals": []})

        self.assertEqual(
            ["classify-unresolved", "find-purchase-evidence"],
            [request["policy_id"] for request in requests],
        )

    def test_trigger_fields_must_be_policy_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "trigger fields must also be target fields"):
            validate_policy(AIPolicy(
                policy_id="bad-trigger",
                name="Bad trigger",
                priority=1,
                instruction="Do nothing",
                target_fields=("category",),
                trigger_fields=("vendor",),
            ))

    def test_human_correction_is_recorded_and_locked(self) -> None:
        review = record_ai_review(
            self.transaction,
            policy_id="classify-unresolved",
            field="category",
            final_value="Groceries",
            reviewer="owner",
            reason="Known merchant",
        )

        self.assertEqual(review["decision_status"], "CORRECTED")
        self.assertEqual(self.transaction.category, "Groceries")
        self.assertIn("category", self.transaction.metadata["locked_fields"])

    def test_openai_compatible_resolver_uses_runtime_secret_and_validates_json(self) -> None:
        import os
        response = b'{"choices":[{"message":{"content":"{\\"proposals\\":[]}"}}]}'
        captured = {}

        def transport(request, timeout):
            captured["authorization"] = request.headers["Authorization"]
            captured["timeout"] = timeout
            return response

        resolver = OpenAICompatibleResolver(
            {
                "provider": "openrouter",
                "endpoint": "https://example.invalid/v1/chat/completions",
                "model": "cheap-model",
                "api_key_env": "TEST_OPENROUTER_KEY",
                "timeout_seconds": 5,
            },
            transport=transport,
        )
        previous = os.environ.get("TEST_OPENROUTER_KEY")
        try:
            os.environ["TEST_OPENROUTER_KEY"] = "runtime-only"
            result = resolver({"response_contract": {}, "allowed_fields": []})
        finally:
            if previous is None:
                os.environ.pop("TEST_OPENROUTER_KEY", None)
            else:
                os.environ["TEST_OPENROUTER_KEY"] = previous

        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual(result["model"], "cheap-model")
        self.assertEqual(captured["authorization"], "Bearer runtime-only")
        self.assertEqual(captured["timeout"], 5)
