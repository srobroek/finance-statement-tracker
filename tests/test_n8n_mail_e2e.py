from __future__ import annotations

import base64
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.n8n.e2e.generate_real_mail_fixtures import generate_bundle
from integrations.n8n.e2e.real_mail_e2e import (
    MAX_SYNTHETIC_COUNT,
    ContractError,
    SyntheticMailPipeline,
    run_synthetic_e2e,
    sha256_json,
    verify_bundle,
    verify_receipt,
)

FIXTURES = ROOT / "tests" / "fixtures" / "real-mail-e2e-fixtures.json"


def _message(message_id: str, *, received: str, attachments: list[dict] | None = None) -> dict:
    return {
        "id": message_id,
        "receivedDateTime": received,
        "from": {"emailAddress": {"address": "sender@example.test"}},
        "subject": "Synthetic finance statement",
        "body": f"body:{message_id}",
        "attachments": attachments or [],
    }


def _attachment(attachment_id: str, *, name: str = "statement.pdf", content_type: str = "application/pdf", amount_minor: int = 100) -> dict:
    return {
        "id": attachment_id,
        "name": name,
        "content_type": content_type,
        "content_base64": base64.b64encode(f"%PDF-{attachment_id}%".encode()).decode(),
        "amount_minor": amount_minor,
    }


def _rehash(receipt: dict) -> dict:
    receipt["receipt_sha256"] = sha256_json(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )
    return receipt


class N8nMailE2EContractTests(unittest.TestCase):
    def test_manifest_is_explicitly_synthetic_and_covers_required_cases(self) -> None:
        manifest = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "real-mail-e2e-fixtures-v1")
        self.assertEqual(manifest["contract_status"], "SYNTHETIC_OFFLINE")
        self.assertFalse(manifest["provider_proof"])
        scenario_ids = {scenario["id"] for scenario in manifest["scenarios"]}
        self.assertTrue(
            {
                "zero", "one", "one-hundred-one", "one-hundred-one-plus", "late-order", "duplicate-message",
                "attachmentless", "non-pdf", "archive-hash-failure", "cursor-cas-conflict",
            }.issubset(scenario_ids)
        )

    def test_ei_and_wio_receipts_are_schema_bound_and_not_provider_proof(self) -> None:
        for source_code in ("EI_AMAZON", "WIO_CREDIT"):
            with self.subTest(source_code=source_code):
                receipt = run_synthetic_e2e(source_code=source_code, count=1)
                result = verify_receipt(receipt, root=ROOT)
                self.assertEqual(result["status"], "VERIFIED")
                self.assertFalse(result["provider_proof"])
                self.assertFalse(receipt["external_provider_calls"])
                self.assertEqual(receipt["source"]["source_code"], source_code)
                self.assertEqual(receipt["archive"]["email_writes"], 1)
                self.assertEqual(receipt["archive"]["attachment_writes"], 1)
                self.assertEqual(receipt["replay"]["actual_new_writes"], 0)
                self.assertEqual(receipt["replay"]["cursor_new_writes"], 0)

    def test_zero_one_and_101_message_fixtures_have_exact_enumeration_counts(self) -> None:
        for source_code, count in (("EI_AMAZON", 0), ("EI_AMAZON", 1), ("WIO_CREDIT", 101)):
            with self.subTest(source_code=source_code, count=count):
                receipt = run_synthetic_e2e(source_code=source_code, count=count)
                self.assertEqual(receipt["enumeration"]["scanned_count"], count)
                self.assertEqual(receipt["enumeration"]["matched_count"], count)
                self.assertEqual(receipt["archive"]["email_writes"], count)
                self.assertEqual(receipt["archive"]["attachment_writes"], count)
                self.assertEqual(receipt["actual"]["write_count"], count)

    def test_late_order_is_sorted_by_received_time_then_immutable_id(self) -> None:
        messages = [
            _message("m3", received="2026-08-21T00:03:00+00:00", attachments=[_attachment("a3")]),
            _message("m1", received="2026-08-21T00:01:00+00:00", attachments=[_attachment("a1")]),
            _message("m2", received="2026-08-21T00:02:00+00:00", attachments=[_attachment("a2")]),
        ]
        receipt = run_synthetic_e2e(source_code="EI_AMAZON", messages=messages)
        self.assertEqual(receipt["enumeration"]["ordered_message_ids"], ["m1", "m2", "m3"])

    def test_duplicate_message_fails_before_any_archive_write(self) -> None:
        messages = [
            _message("same", received="2026-08-21T00:01:00+00:00", attachments=[_attachment("a1")]),
            _message("same", received="2026-08-21T00:02:00+00:00", attachments=[_attachment("a2")]),
        ]
        pipeline = SyntheticMailPipeline(
            source_code="EI_AMAZON", messages=messages, include_cashback=True
        )
        with self.assertRaisesRegex(ContractError, "DUPLICATE_MESSAGE_ID"):
            pipeline.run_once()
        self.assertEqual(pipeline.onedrive.email_writes, 0)
        self.assertEqual(pipeline.onedrive.attachment_writes, 0)
        self.assertEqual(pipeline.actual.writes, 0)
        self.assertEqual(pipeline.cursor.writes, 0)

    def test_attachmentless_and_non_pdf_are_archived_but_do_not_mutate_finance(self) -> None:
        attachmentless = _message("empty", received="2026-08-21T00:01:00+00:00")
        non_pdf = _message(
            "image", received="2026-08-21T00:02:00+00:00",
            attachments=[_attachment("logo", name="logo.png", content_type="image/png")],
        )
        receipt = run_synthetic_e2e(
            source_code="EI_AMAZON", messages=[attachmentless, non_pdf]
        )
        self.assertEqual(receipt["archive"]["email_writes"], 2)
        self.assertEqual(receipt["archive"]["attachment_writes"], 1)
        self.assertEqual(receipt["archive"]["non_pdf_count"], 1)
        self.assertEqual(receipt["actual"]["write_count"], 0)
        self.assertEqual(receipt["cashback"]["write_count"], 0)
        self.assertTrue(receipt["replay"]["idempotent"])

    def test_archive_hash_failure_is_fail_closed_and_retry_is_idempotent(self) -> None:
        attachment = _attachment("bad", amount_minor=123)
        message = _message("m1", received="2026-08-21T00:01:00+00:00", attachments=[attachment])
        pipeline = SyntheticMailPipeline(
            source_code="EI_AMAZON",
            messages=[message],
            include_cashback=True,
            hash_failure_key="m1:bad",
        )
        with self.assertRaisesRegex(ContractError, "ATTACHMENT_ARCHIVE_READBACK_MISMATCH"):
            pipeline.run_once()
        self.assertFalse(pipeline.state.archive_complete)
        self.assertEqual(pipeline.cursor.writes, 0)
        pipeline.onedrive.hash_failure_key = None
        pipeline.run_to_completion()
        self.assertEqual(pipeline.onedrive.email_writes, 1)
        self.assertEqual(pipeline.onedrive.attachment_writes, 1)
        self.assertEqual(pipeline.cursor.writes, 1)
        writes = (pipeline.onedrive.email_writes, pipeline.onedrive.attachment_writes, pipeline.actual.writes, pipeline.cursor.writes)
        pipeline.run_to_completion()
        self.assertEqual(writes, (pipeline.onedrive.email_writes, pipeline.onedrive.attachment_writes, pipeline.actual.writes, pipeline.cursor.writes))

    def test_cursor_cas_conflict_fails_closed(self) -> None:
        pipeline = SyntheticMailPipeline(
            source_code="WIO_CREDIT",
            messages=[_message("m1", received="2026-08-21T00:01:00+00:00", attachments=[_attachment("a1")])],
            include_cashback=False,
            cas_conflict=True,
        )
        with self.assertRaisesRegex(ContractError, "CURSOR_CAS_CONFLICT"):
            pipeline.run_to_completion()
        self.assertEqual(pipeline.cursor.conflicts, 1)
        self.assertEqual(pipeline.cursor.version, 1)
        self.assertFalse(pipeline.state.cursor_committed)

    def test_all_four_restart_injection_points_recover_without_duplicate_effects(self) -> None:
        receipt = run_synthetic_e2e(source_code="EI_AMAZON", count=2)
        self.assertTrue(receipt["restart"]["all_recovered"])
        points = [row["point"] for row in receipt["restart"]["injections"]]
        self.assertEqual(points, ["after_archive", "after_actual", "after_cashback", "after_cursor_cas"])
        for row in receipt["restart"]["injections"]:
            with self.subTest(point=row["point"]):
                self.assertTrue(row["injected"])
                self.assertTrue(row["idempotent_replay"])
                self.assertEqual(row["cursor_writes"], 1)

    def test_receipt_tampering_and_provider_claims_fail_closed(self) -> None:
        receipt = run_synthetic_e2e(source_code="EI_AMAZON", count=1)
        tampered = copy.deepcopy(receipt)
        tampered["actual"]["ui_readback_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "RECEIPT_SHA256_MISMATCH"):
            verify_receipt(tampered)
        tampered = copy.deepcopy(receipt)
        tampered["provider_proof"] = True
        tampered["receipt_sha256"] = __import__(
            "integrations.n8n.e2e.real_mail_e2e", fromlist=["sha256_json"]
        ).sha256_json({key: value for key, value in tampered.items() if key != "receipt_sha256"})
        with self.assertRaisesRegex(ContractError, "RECEIPT_SCHEMA_INVALID|PROVIDER_PROOF_MUST_REMAIN_UNPROVEN"):
            verify_receipt(tampered)

    def test_self_rehashed_cross_field_mutations_fail_closed(self) -> None:
        mutations = {
            "source binding": lambda receipt: receipt["source_bindings"].update({"config_sha256": "0" * 64}),
            "message record": lambda receipt: receipt["enumeration"]["message_records"][0].update({"body": "forged"}),
            "message fingerprint": lambda receipt: receipt["enumeration"]["message_fingerprints"][0].update({"sha256": "0" * 64}),
            "archive row": lambda receipt: receipt["archive"]["email_rows"][0].update({"readback_sha256": "0" * 64}),
            "archive hash": lambda receipt: receipt["archive"].update({"rows_sha256": "0" * 64}),
            "outbox hash": lambda receipt: receipt["pipeline"].update({"outbox_rows_sha256": "0" * 64}),
            "actual readback": lambda receipt: receipt["actual"].update({"economic_readback_sha256": "0" * 64}),
            "nested actual": lambda receipt: receipt["pipeline"]["actual_readback"].update({"write_count": 0}),
            "cashback readback": lambda receipt: receipt["cashback"].update({"readback_sha256": "0" * 64}),
            "cursor table": lambda receipt: receipt["data_tables"]["cursor_cas"]["rows"][0].update({"cursor_version": 99}),
            "replay counts": lambda receipt: receipt["replay"]["final_counts"].update({"actual": 0}),
            "restart point": lambda receipt: receipt["restart"]["injections"][0].update({"point": "after_actual"}),
            "cleanup": lambda receipt: receipt["cleanup"].update({"production_writes": 1}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                tampered = copy.deepcopy(run_synthetic_e2e(source_code="EI_AMAZON", count=1))
                mutate(tampered)
                with self.assertRaises(ContractError):
                    verify_receipt(_rehash(tampered))

    def test_source_artifact_allowlist_rejects_path_mutations(self) -> None:
        mutations = {
            "absolute": lambda receipt: receipt["source_bindings"]["source_artifacts"][0].update({"path": "/tmp/escape"}),
            "traversal": lambda receipt: receipt["source_bindings"]["source_artifacts"][0].update({"path": "../escape"}),
            "extra": lambda receipt: receipt["source_bindings"]["source_artifacts"].append({"path": "extra.json", "sha256": "0" * 64}),
            "duplicate": lambda receipt: receipt["source_bindings"]["source_artifacts"].append(copy.deepcopy(receipt["source_bindings"]["source_artifacts"][0])),
            "extra workflow": lambda receipt: receipt["source_bindings"]["workflow_sha256"].update({"extra.json": "0" * 64}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                tampered = copy.deepcopy(run_synthetic_e2e(source_code="WIO_CREDIT", count=1))
                mutate(tampered)
                with self.assertRaises(ContractError):
                    verify_receipt(_rehash(tampered))

    def test_sensitive_plaintext_and_bundle_completeness_fail_closed(self) -> None:
        receipt = copy.deepcopy(run_synthetic_e2e(source_code="EI_AMAZON", count=1))
        receipt["enumeration"]["message_records"][0]["body"] = "password=plaintext"
        with self.assertRaisesRegex(ContractError, "SENSITIVE_PLAINTEXT_REJECTED"):
            verify_receipt(_rehash(receipt))

        bundle = generate_bundle(count=1)
        incomplete = copy.deepcopy(bundle)
        del incomplete["receipts"][0]["actual"]
        with self.assertRaises(ContractError):
            verify_bundle(incomplete)
        forged = copy.deepcopy(bundle)
        forged["receipts"][0]["provider_evidence"] = {"provider_proof": True}
        with self.assertRaises(ContractError):
            verify_bundle(forged)
        duplicate = copy.deepcopy(bundle)
        duplicate["receipts"] = [duplicate["receipts"][0], duplicate["receipts"][0]]
        with self.assertRaisesRegex(ContractError, "BUNDLE_SOURCE_SET_INVALID"):
            verify_bundle(duplicate)

    def test_count_102_remains_schema_bound_and_provider_free(self) -> None:
        self.assertGreaterEqual(MAX_SYNTHETIC_COUNT, 102)
        receipt = run_synthetic_e2e(source_code="WIO_CREDIT", count=102)
        self.assertEqual(receipt["enumeration"]["scanned_count"], 102)
        self.assertEqual(verify_receipt(receipt, root=ROOT)["status"], "VERIFIED")

    def test_cli_emits_verified_synthetic_receipt(self) -> None:
        result = subprocess.run(
            ["uv", "run", "python", "-m", "integrations.n8n.e2e.real_mail_e2e", "--source", "WIO_CREDIT", "--count", "1"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["proof_kind"], "SYNTHETIC_OFFLINE")
        self.assertFalse(receipt["provider_proof"])

    def test_generator_emits_two_schema_bound_provider_free_receipts(self) -> None:
        bundle = generate_bundle(count=1)
        self.assertEqual(bundle["schema_version"], "real-mail-e2e-fixture-bundle-v1")
        self.assertEqual(bundle["contract_status"], "SYNTHETIC_OFFLINE")
        self.assertFalse(bundle["provider_proof"])
        self.assertEqual(
            [receipt["source"]["source_code"] for receipt in bundle["receipts"]],
            ["EI_AMAZON", "WIO_CREDIT"],
        )
        for receipt in bundle["receipts"]:
            self.assertEqual(verify_receipt(receipt, root=ROOT)["status"], "VERIFIED")

    def test_contract_module_has_no_provider_or_secret_access(self) -> None:
        source = (ROOT / "integrations/n8n/e2e/real_mail_e2e.py").read_text(encoding="utf-8").lower()
        for forbidden in ("requests", "httpx", "msal", "onepassword", "op read", "n8n api key"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
