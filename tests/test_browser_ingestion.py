import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from finance_tracker.actual_pipeline import load_compiled_rules
from finance_tracker.browser_ingestion import (
    _enrich_browser_transactions,
    _match_browser_transactions,
    _normalize_browser_capture,
    _serialize_browser_ingestion_run,
    _validate_browser_capture,
    build_browser_ingestion_run,
    export_browser_capture_for_actual,
)
from finance_tracker.classification_audit import enforce_transaction_invariants


ROOT = Path(__file__).resolve().parents[1]


class BrowserIngestionTests(TestCase):
    def test_private_phases_reconstruct_the_exported_run_exactly(self):
        capture = self.capture()
        validated = _validate_browser_capture(capture)
        normalized = _normalize_browser_capture(
            validated,
            self.config(),
            ai_engine=None,
            ai_resolver=None,
        )
        rule_traces, history_traces, ai_traces = _enrich_browser_transactions(
            normalized.transactions,
            self.config(),
            (),
            history_index=None,
            ai_engine=None,
            ai_resolver=None,
            property_registry=None,
        )
        _match_browser_transactions(normalized.transactions)
        for transaction in normalized.transactions:
            enforce_transaction_invariants(transaction)

        phased = _serialize_browser_ingestion_run(
            normalized,
            rule_traces,
            history_traces,
            ai_traces,
        )

        self.assertEqual(
            phased.to_dict(),
            build_browser_ingestion_run(capture, self.config()).to_dict(),
        )

    def test_inactive_n8n_handoff_validates_browser_capture_before_writes(self):
        workflow = json.loads(
            (ROOT / "integrations" / "n8n" / "workflows" / "11-interactive-artifact-handoff.json")
            .read_text(encoding="utf-8")
        )
        self.assertFalse(workflow["active"])
        handoff = workflow["meta"]["browserHandoff"]
        self.assertEqual("BROWSER_CAPTURE_V1", handoff["document_profile"])
        self.assertEqual("browser-capture-schema-v1", handoff["capture_schema"])
        self.assertEqual("N8N", handoff["headless_owner"])
        self.assertTrue(handoff["actual_mutation_forbidden"])
        self.assertTrue(handoff["cashback_mutation_forbidden"])
        names = {node["name"]: node for node in workflow["nodes"]}
        self.assertEqual("N8N", handoff["archive_owner"])
        self.assertEqual("BOUNDED_BINARY_UPLOAD", handoff["archive_mode"])
        self.assertEqual("AJV_REQUIRED_FAIL_CLOSED", handoff["validation_runtime"])
        self.assertEqual("SHARED_STATEMENT_PIPELINE", handoff["headless_workflow_code"])
        self.assertIn("Archive Browser Capture in OneDrive", names)
        self.assertIn("Read Back Durable Browser Archive Receipt", names)
        self.assertIn("Extract Browser Capture JSON", names)
        self.assertIn("Build Browser Headless Handoff", names)
        self.assertIn("Dispatch Browser Capture to Headless Pipeline", names)
        self.assertNotIn("Require Typed Browser Capture Validator", names)
        self.assertNotIn("stopAndError", {node["type"] for node in workflow["nodes"]})
        handoff = workflow["connections"]["Validate Browser Capture Schema"]["main"][0][0]
        self.assertEqual("Load Existing Browser Archive Receipt", handoff["node"])
        dispatch = workflow["connections"]["Build Browser Headless Handoff"]["main"][0][0]
        self.assertEqual("Dispatch Browser Capture to Headless Pipeline", dispatch["node"])
        receipt = names["Upsert Durable Browser Archive Receipt"]["parameters"]
        self.assertIn("source_sha256", receipt["columns"]["value"])
        self.assertIn("output_sha256", receipt["columns"]["value"])
        verify_archive = names["Verify Browser Archive Receipt"]["parameters"]["jsCode"]
        self.assertIn("input_sha256", verify_archive)
        self.assertIn("archived_sha256", verify_archive)
        self.assertEqual(
            "10000000-0000-4000-8000-000000000003",
            names["Dispatch Browser Capture to Headless Pipeline"]["parameters"]["workflowId"]["value"],
        )
        validator = names["Validate Browser Capture Schema"]
        self.assertEqual("n8n-nodes-base.code", validator["type"])
        code = validator["parameters"]["jsCode"]
        self.assertIn("require('ajv')", code)
        self.assertIn(".compile(schema)", code)
        self.assertIn("BROWSER_CAPTURE_SCHEMA_VALIDATOR_UNAVAILABLE", code)
        self.assertIn("BROWSER_CAPTURE_PROVENANCE_MISMATCH", code)
        self.assertIn("BROWSER_CAPTURE_BINARY_HASH_MISMATCH", code)
        self.assertIn("source_content_sha256", code)
        self.assertIn("minLength: 1", code)
        self.assertIn("actual_mutation: false", code)
        self.assertIn("cashback_mutation: false", code)
        self.assertNotIn("n8n-nodes-finance.actualBudget", {node["type"] for node in workflow["nodes"]})
    def config(self):
        return {
            "schema_version": 1,
            "accounts": [
                {
                    "name": "Wio Current Account · 4113",
                    "type": "checking",
                    "card_code": "WIO_4113",
                    "card_last4": ["4113"],
                    "owner": "Owner A",
                },
                {
                    "name": "Emirates Islamic Amazon Credit Card · 0082",
                    "type": "credit",
                    "card_code": "EI_AMAZON",
                    "card_last4": ["0082"],
                },
                {
                    "name": "FAB Credit Card · 6031",
                    "type": "credit",
                    "card_code": "FAB_6031",
                    "card_last4": ["6031"],
                },
            ],
        }

    def capture(self):
        return {
            "schema_version": 1,
            "capture_id": "wio-4113-2026-08",
            "source": {
                "provider": "Wio",
                "site": "Wio",
                "url": "https://bank.example.test/accounts/4113?token=must-not-persist#transactions",
                "page_context": "Transactions",
                "captured_at": "2026-08-16T12:00:00Z",
                "capture_method": "VISIBLE_ROWS",
                "date_range": {"start": "2026-08-01", "end": "2026-08-16"},
                "limitations": ["Pending rows unavailable"],
            },
            "artifact": {"kind": "TRANSACTION_ROWS"},
            "account": {
                "label": "Wio Current Account ending 4113",
                "account_last4": "4113",
                "currency": "AED",
                "balance": "1000.00",
                "balance_as_of": "2026-08-16T12:00:00Z",
            },
            "rows": [
                {
                    "transaction_date": "2026-08-15",
                    "description": "EXAMPLE MERCHANT",
                    "amount_aed": "25.50",
                    "direction": "DEBIT",
                    "channel": "ONLINE",
                }
            ],
        }

    def test_visible_rows_are_stable_reviewable_and_do_not_keep_url_secrets(self):
        first = build_browser_ingestion_run(self.capture(), self.config())
        second = build_browser_ingestion_run(self.capture(), self.config())

        self.assertEqual(first.staging_status, "REVIEW_REQUIRED")
        self.assertEqual(first.review_count, 1)
        self.assertEqual(first.source["url"], "https://bank.example.test/accounts/4113")
        self.assertEqual(
            first.transactions[0]["transaction_id"],
            second.transactions[0]["transaction_id"],
        )
        self.assertEqual(first.transactions[0]["source_type"], "browser_portal")
        self.assertFalse(first.envelopes[0]["default_cleared"])
        self.assertNotIn("#browser-import", first.envelopes[0]["records"][0]["notes"])
        self.assertNotIn("#primary", first.envelopes[0]["records"][0]["notes"])
        self.assertIn("#owner-owner-a", first.envelopes[0]["records"][0]["notes"])

    def test_owner_approval_clears_only_the_visible_row_review_reason(self):
        capture = self.capture()
        capture["approval"] = {
            "status": "OWNER_APPROVED",
            "scope": "ALL_VISIBLE_ROWS",
            "capture_id": capture["capture_id"],
            "approved_by": "OWNER",
            "approved_at": "2026-08-18T09:20:00Z",
        }

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "REVIEW_REQUIRED")
        self.assertEqual(run.review_count, 1)
        self.assertTrue(run.transactions[0]["review_required"])
        self.assertIn("UNCATEGORIZED", run.transactions[0]["metadata"]["classification_review_reasons"])
        self.assertEqual(
            run.transactions[0]["metadata"]["browser_review_resolutions"],
            ["OWNER_APPROVED_VISIBLE_CAPTURE"],
        )
        self.assertEqual(run.source["approval"]["capture_id"], capture["capture_id"])

    def test_owner_approval_does_not_clear_an_unclassified_credit(self):
        capture = self.capture()
        capture["approval"] = {
            "status": "OWNER_APPROVED",
            "scope": "ALL_VISIBLE_ROWS",
            "capture_id": capture["capture_id"],
            "approved_by": "OWNER",
            "approved_at": "2026-08-18T09:20:00Z",
        }
        capture["rows"][0]["direction"] = "CREDIT"

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.review_count, 1)
        self.assertEqual(
            run.transactions[0]["metadata"]["browser_review_reasons"],
            ["UNCLASSIFIED_CREDIT"],
        )

    def test_owner_approval_must_match_the_exact_capture(self):
        capture = self.capture()
        capture["approval"] = {
            "status": "OWNER_APPROVED",
            "scope": "ALL_VISIBLE_ROWS",
            "capture_id": "different-capture",
            "approved_by": "OWNER",
            "approved_at": "2026-08-18T09:20:00Z",
        }

        with self.assertRaisesRegex(ValueError, "must match capture.capture_id"):
            build_browser_ingestion_run(capture, self.config())

    def test_tied_official_statement_rows_are_authoritative_and_cleared(self):
        capture = self.capture()
        capture["capture_id"] = "ei-0082-2026-08"
        capture["source"]["provider"] = "Emirates Islamic"
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["artifact"]["kind"] = "STATEMENT_ROWS"
        capture["account"] = {
            "label": "EI Amazon ending 0082",
            "account_last4": "0082",
            "currency": "AED",
        }
        capture["statement"] = {
            "statement_reference": "EI-0082-2026-08",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "opening_balance_aed": "100.00",
            "closing_balance_aed": "120.00",
            "balance_convention": "LIABILITY",
        }
        capture["rows"] = [
            {
                "source_id": "purchase-1",
                "transaction_date": "2026-08-15",
                "description": "EXAMPLE PURCHASE",
                "amount_aed": "30.00",
                "direction": "DEBIT",
                "transaction_type": "PURCHASE",
            },
            {
                "source_id": "payment-1",
                "transaction_date": "2026-08-16",
                "description": "PAYMENT RECEIVED",
                "amount_aed": "10.00",
                "direction": "CREDIT",
                "transaction_type": "PAYMENT",
            },
        ]

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "REVIEW_REQUIRED")
        self.assertEqual(run.review_count, 2)
        self.assertTrue(run.statement_check["balance_tied"])
        self.assertTrue(run.envelopes[0]["default_cleared"])
        self.assertTrue(all(row["source_type"] == "browser_statement" for row in run.transactions))

    def test_mortgage_uses_liability_balance_convention_and_preserves_signed_balance(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["artifact"]["kind"] = "STATEMENT_ROWS"
        capture["account"] = {
            "label": "FAB Mortgage ending 0203",
            "account_last4": "0203",
            "currency": "AED",
            "balance": "-2550.00",
        }
        capture["statement"] = {
            "statement_reference": "FAB-MORTGAGE-2026-08",
            "opening_balance_aed": "2500.00",
            "closing_balance_aed": "2525.50",
        }
        capture["rows"] = [
            {
                "source_id": "mortgage-payment-1",
                "transaction_date": "2026-08-15",
                "description": "MORTGAGE PAYMENT",
                "amount_aed": "25.50",
                "direction": "DEBIT",
                "transaction_type": "PAYMENT",
            }
        ]
        config = deepcopy(self.config())
        config["accounts"].append(
            {
                "name": "FAB Mortgage · 0203",
                "type": "mortgage",
                "card_code": "FAB_MORTGAGE_0203",
                "card_last4": ["0203"],
            }
        )

        run = build_browser_ingestion_run(capture, config)

        self.assertEqual(run.account_snapshot["balance"], "-2550.00")
        self.assertEqual(run.statement_check["balance_convention"], "LIABILITY")
        self.assertEqual(run.statement_check["calculated_closing_balance_aed"], "2525.50")
        self.assertTrue(run.statement_check["balance_tied"])
        self.assertEqual(
            run.transactions[0]["metadata"]["account_balance_convention"], "LIABILITY"
        )

    def test_unbalanced_statement_rows_are_blocked_from_partial_import(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["artifact"]["kind"] = "STATEMENT_ROWS"
        capture["statement"] = {
            "statement_reference": "WIO-4113-2026-08",
            "opening_balance_aed": "100.00",
            "closing_balance_aed": "999.00",
            "balance_convention": "ASSET",
        }

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "REVIEW_REQUIRED")
        self.assertEqual(run.envelopes, ())
        self.assertTrue(run.import_blockers)

    def test_account_snapshot_never_creates_a_balance_transaction(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "ACCOUNT_OVERVIEW"
        capture["artifact"]["kind"] = "ACCOUNT_SNAPSHOT"
        capture.pop("rows")

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "ACCOUNT_REVIEW_REQUIRED")
        self.assertEqual(run.envelopes, ())
        self.assertFalse(run.account_snapshot["balance_posting_allowed"])

    def test_statement_pdf_routes_to_existing_statement_pipeline(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "STATEMENT_DOWNLOAD"
        capture["artifact"] = {"kind": "STATEMENT_PDF", "local_path": "statement.pdf"}
        capture.pop("rows")

        run = build_browser_ingestion_run(capture, self.config())

        self.assertEqual(run.staging_status, "ROUTE_TO_STATEMENT_PIPELINE")
        self.assertEqual(run.envelopes, ())

    def test_sensitive_browser_state_is_rejected(self):
        capture = self.capture()
        capture["source"]["cookies"] = "secret-cookie"

        with self.assertRaisesRegex(ValueError, "forbidden sensitive field"):
            build_browser_ingestion_run(capture, self.config())

    def test_source_url_rejects_embedded_credentials(self):
        capture = self.capture()
        capture["source"]["url"] = "https://user:password@bank.example/accounts"

        with self.assertRaisesRegex(ValueError, "must not contain credentials"):
            build_browser_ingestion_run(capture, self.config())

    def test_duplicate_portal_source_ids_are_rejected(self):
        capture = self.capture()
        row = capture["rows"][0]
        row["source_id"] = "duplicate"
        capture["rows"].append(deepcopy(row))

        with self.assertRaisesRegex(ValueError, "Duplicate browser source_id"):
            build_browser_ingestion_run(capture, self.config())

    def test_static_rules_clear_only_deterministically_classified_credit_reviews(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["rows"] = [
            {
                "transaction_date": "2026-08-15",
                "description": "CASHBACK REWARD CREDIT",
                "amount_aed": "25.00",
                "direction": "CREDIT",
            },
            {
                "transaction_date": "2026-08-16",
                "description": "UNKNOWN CREDIT",
                "amount_aed": "10.00",
                "direction": "CREDIT",
            },
        ]

        run = build_browser_ingestion_run(
            capture,
            self.config(),
            load_compiled_rules(ROOT / "config" / "static-rules.seed.json"),
        )

        self.assertFalse(run.transactions[0]["review_required"])
        self.assertIn(
            "STATIC_RULE_CLASSIFIED_CREDIT",
            run.transactions[0]["metadata"]["browser_review_resolutions"],
        )
        self.assertTrue(run.transactions[1]["review_required"])
        self.assertEqual(run.review_count, 1)

    def test_static_rules_classify_known_fab_credit_families(self):
        capture = self.capture()
        capture["source"]["provider"] = "FAB"
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["account"]["card_code"] = "FAB_6031"
        capture["account"]["account_last4"] = "6031"
        capture["rows"] = [
            {
                "transaction_date": "2026-08-01",
                "description": "FT123-FAB CARD PMT AUH",
                "amount_aed": "1000.00",
                "direction": "CREDIT",
            },
            {
                "transaction_date": "2026-08-02",
                "description": "ALFT REWARD RDMPTION FEE REV",
                "amount_aed": "31.50",
                "direction": "CREDIT",
            },
            {
                "transaction_date": "2026-08-03",
                "description": "Salary Transfer-REF SALARY FOR",
                "amount_aed": "10000.00",
                "direction": "CREDIT",
            },
            {
                "transaction_date": "2026-08-04",
                "description": "Inward IPP Payment--Utility Bill Payments",
                "amount_aed": "500.00",
                "direction": "CREDIT",
            },
            {
                "transaction_date": "2026-08-05",
                "description": "Transfer within UAE",
                "amount_aed": "750.00",
                "direction": "CREDIT",
            },
            {
                "transaction_date": "2026-08-06",
                "description": "INSTQ2MKKOB7QB00",
                "amount_aed": "536.00",
                "direction": "CREDIT",
            },
        ]

        run = build_browser_ingestion_run(
            capture,
            self.config(),
            load_compiled_rules(ROOT / "config" / "static-rules.seed.json"),
        )

        self.assertEqual(run.review_count, 2)
        self.assertEqual(
            [row["transaction_type"] for row in run.transactions],
            ["TRANSFER", "REWARD_CREDIT", "INCOME", "REFUND", "INCOME", "REFUND"],
        )
        self.assertEqual(run.transactions[4]["category"], "Needs Review")
        self.assertEqual(run.transactions[5]["category"], "Needs Review")
        self.assertTrue(all(record["amount"] > 0 for record in run.envelopes[0]["records"]))
        self.assertTrue(
            all(
                row["metadata"]["statement_direction"] == "CREDIT"
                for row in run.transactions
            )
        )

    def test_static_rules_classify_known_fab_purchase_families(self):
        capture = self.capture()
        capture["source"]["provider"] = "FAB"
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["account"]["card_code"] = "FAB_6031"
        capture["account"]["account_last4"] = "6031"
        capture["rows"] = [
            {"transaction_date": "2026-08-01", "description": "ACE DUBAI HILLS MALL", "amount_aed": "20", "direction": "DEBIT"},
            {"transaction_date": "2026-08-02", "description": "Debit Card Purchase-www.getstake.com Dubai", "amount_aed": "700", "direction": "DEBIT"},
            {"transaction_date": "2026-08-03", "description": "Debit Card Purchase-CABINCAMP RES 131023 OSLO", "amount_aed": "6000", "direction": "DEBIT"},
            {"transaction_date": "2026-08-04", "description": "IPP Charges-AC-123-CRP", "amount_aed": "0.49", "direction": "DEBIT"},
            {"transaction_date": "2026-08-05", "description": "IPP Transfer-MOB-LOCAL FT-CRP", "amount_aed": "100", "direction": "DEBIT"},
            {"transaction_date": "2026-08-06", "description": "Transfer to other Bank Credit Card", "amount_aed": "2500", "direction": "DEBIT"},
            {"transaction_date": "2026-08-07", "description": "UNKNOWN MARKETPLACE DUBAI ARE", "amount_aed": "25", "direction": "DEBIT"},
            {"transaction_date": "2026-08-08", "description": "FAB BLUE payment [P277544183]", "amount_aed": "536", "direction": "DEBIT"},
        ]

        run = build_browser_ingestion_run(
            capture,
            self.config(),
            load_compiled_rules(ROOT / "config" / "static-rules.seed.json"),
        )

        self.assertEqual(
            [row["category"] for row in run.transactions],
            ["Maintenance & Repairs", "Investments", "Accommodation", "Bank Fees", "Needs Review", "Card Payments", None, "Card Payments"],
        )
        self.assertEqual(run.transactions[4]["transaction_type"], "TRANSFER")
        self.assertEqual(run.transactions[5]["transaction_type"], "TRANSFER")
        self.assertEqual(run.transactions[7]["transaction_type"], "TRANSFER")
        self.assertIsNone(run.transactions[6]["vendor"])
        self.assertTrue(all(record["amount"] < 0 for record in run.envelopes[0]["records"]))

    def test_unique_exact_opposite_direction_pair_is_a_refund(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["rows"] = [
            {
                "transaction_date": "2026-08-01",
                "description": "CARREFOUR MARKET",
                "amount_aed": "153.29",
                "direction": "DEBIT",
            },
            {
                "transaction_date": "2026-08-09",
                "description": "CARREFOUR MARKET",
                "amount_aed": "153.29",
                "direction": "CREDIT",
            },
        ]

        run = build_browser_ingestion_run(
            capture,
            self.config(),
            load_compiled_rules(ROOT / "config" / "static-rules.seed.json"),
        )

        refund = run.transactions[1]
        self.assertEqual(refund["transaction_type"], "REFUND")
        self.assertTrue(refund["is_refund"])
        self.assertFalse(refund["review_required"])
        self.assertEqual(refund["category"], "Groceries")
        self.assertEqual(
            refund["metadata"]["browser_review_resolutions"],
            ["EXACT_UNIQUE_REFUND_PAIR"],
        )

    def test_ambiguous_credit_defaults_to_refund_but_stays_in_review(self):
        capture = self.capture()
        capture["source"]["capture_method"] = "OFFICIAL_EXPORT"
        capture["rows"] = [
            {
                "transaction_date": "2026-08-01",
                "description": "CARREFOUR MARKET",
                "amount_aed": "153.29",
                "direction": "DEBIT",
            },
            {
                "transaction_date": "2026-08-02",
                "description": "CARREFOUR MARKET",
                "amount_aed": "153.29",
                "direction": "DEBIT",
            },
            {
                "transaction_date": "2026-08-09",
                "description": "CARREFOUR MARKET",
                "amount_aed": "153.29",
                "direction": "CREDIT",
            },
        ]

        run = build_browser_ingestion_run(
            capture,
            self.config(),
            load_compiled_rules(ROOT / "config" / "static-rules.seed.json"),
        )

        credit = run.transactions[2]
        self.assertEqual(credit["transaction_type"], "REFUND")
        self.assertTrue(credit["is_refund"])
        self.assertTrue(credit["review_required"])
        self.assertEqual(run.review_count, 1)

    def test_export_writes_portable_actual_handoff(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            capture_path = root / "capture.json"
            config_path = root / "config.json"
            output_path = root / "browser-run.json"
            capture_path.write_text(json.dumps(self.capture()), encoding="utf-8")
            config_path.write_text(json.dumps(self.config()), encoding="utf-8")

            run = export_browser_capture_for_actual(capture_path, config_path, output_path)

            self.assertTrue(output_path.is_file())
            saved = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["capture_id"], run.capture_id)
            self.assertEqual(saved["envelopes"][0]["account"], "Wio Current Account · 4113")
