from __future__ import annotations

import importlib.util
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "integrations" / "n8n" / "generate_data_table_migration.py"


def load_module():
    spec = importlib.util.spec_from_file_location("data_table_migration", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load migration module: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DataTableMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = load_module()

    def test_target_schema_has_four_tables_and_thirteen_locator_fields(self) -> None:
        matrix = json.loads(
            (ROOT / "integrations/n8n/data-table-migration-matrix.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(matrix["target_schemas"]), set(self.migration.TARGETS))
        ingestion = matrix["target_schemas"]["finance_ingestion_state"]["columns"]
        batches = matrix["target_schemas"]["finance_actual_batches"]["columns"]
        self.assertEqual(
            [field for field in ingestion if field.startswith("inventory_")],
            [
                "inventory_run_id",
                "inventory_fence",
                "inventory_sha256",
                "inventory_item_id",
                "inventory_path",
                "inventory_etag",
                "inventory_schema_version",
                "inventory_length_bytes",
            ],
        )
        self.assertEqual(
            [field for field in batches if field.startswith("verification_artifact_")],
            [
                "verification_artifact_sha256",
                "verification_artifact_item_id",
                "verification_artifact_path",
                "verification_artifact_etag",
                "verification_artifact_schema_version",
                "verification_artifact_length_bytes",
            ],
        )
        self.assertEqual(
            len([field for field in ingestion if field.startswith("inventory_")])
            + len([field for field in batches if field.startswith("verification_artifact_")])
            - 1,
            13,
        )
        self.assertRegex(self.migration.generated_target_schema_digest(), r"^[0-9a-f]{64}$")

    def test_fence_allocation_has_one_concurrent_winner_and_stale_rejection(self) -> None:
        fences = self.migration.FenceStore()

        def acquire(owner: str):
            try:
                return fences.acquire("inventory:source", owner)
            except self.migration.FenceConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = list(pool.map(acquire, ("a", "b")))
        winner = next(fence for fence in winners if fence is not None)
        self.assertEqual(sum(fence is not None for fence in winners), 1)
        with self.assertRaises(self.migration.FenceConflict):
            fences.assert_current(self.migration.Fence("inventory:source", "stale", winner.token + 1))
        self.assertTrue(fences.release(winner))
        next_fence = fences.acquire("inventory:source", "next")
        self.assertEqual(next_fence.token, winner.token + 1)

    def test_alias_hit_miss_collision_and_replay(self) -> None:
        identity = self.migration.document_identity("MAIL_LINKED", ["a" * 64, "message-1", "NO_ATTACHMENT"])
        other_identity = self.migration.document_identity(
            "MAIL_LINKED", ["b" * 64, "message-2", "NO_ATTACHMENT"]
        )
        entry = {
            "alias_kind": "legacy_document_id",
            "alias_value": "old-1",
            "canonical_document_id": identity["document_id"],
            "identity_kind": "MAIL_LINKED",
            "canonical_identity_sha256": identity["identity_sha256"],
        }
        bundle = self.migration.build_alias_bundle([entry], source_commit="abc")
        resolver = self.migration.AliasResolver(bundle, expected_source_commit="abc")
        self.assertEqual(resolver.lookup("legacy_document_id", "old-1")["outcome"], "hit")
        self.assertEqual(
            resolver.lookup(
                "legacy_document_id",
                "old-1",
                expected_identity_sha256=identity["identity_sha256"],
                replay_document_id=identity["document_id"],
            )["outcome"],
            "replay",
        )
        with self.assertRaises(self.migration.AliasResolutionError) as miss:
            resolver.resolve("legacy_document_id", "missing")
        self.assertIn("ALIAS_MISS", str(miss.exception))
        with self.assertRaises(self.migration.AliasResolutionError) as collision:
            self.migration.build_alias_bundle(
                [entry, {**entry, "canonical_document_id": other_identity["document_id"], "canonical_identity_sha256": other_identity["identity_sha256"]}], source_commit="abc"
            )
        self.assertIn("ALIAS_COLLISION", str(collision.exception))
        with self.assertRaises(self.migration.AliasResolutionError) as mismatch:
            self.migration.build_alias_bundle(
                [{**entry, "canonical_document_id": other_identity["document_id"]}], source_commit="abc"
            )
        self.assertIn("ID_HASH_MISMATCH", str(mismatch.exception))
        with self.assertRaises(self.migration.AliasResolutionError):
            resolver.lookup("legacy_document_id", "old-1", replay_document_id="different")

    def test_inventory_restart_readback_and_replay_are_idempotent(self) -> None:
        migration = self.migration
        resolver = migration.InventoryResolver()
        inventory = {
            "schema_version": "inventory-v1",
            "inventory_run_id": "run-1",
            "source_code": "MAIL",
            "window_start": "2026-08-01T00:00:00Z",
            "run_upper_bound": "2026-08-02T00:00:00Z",
            "messages": [],
            "attachment_identity_keys": [],
            "empty_inventory": True,
            "immutable_inventory": True,
            "attachment_ids_verified": True,
        }
        fence = resolver.fences.acquire("cursor:MAIL", "writer")
        first = resolver.commit(inventory, fence=fence, cursor_version=3)
        replay = resolver.commit(inventory, fence=fence, cursor_version=3)
        self.assertTrue(first["readback_verified"])
        self.assertFalse(replay["changed"])
        restarted_resolver = migration.InventoryResolver(store=resolver.store, fences=resolver.fences)
        restarted = restarted_resolver.restart_readback("run-1", "MAIL")
        self.assertTrue(restarted["restart_rehydrated"])
        self.assertFalse(restarted_resolver.restart_readback("run-1", "MAIL")["restart_rehydrated"])
        self.assertTrue(resolver.fences.release(fence))

    def test_verification_hash_readback_rejects_pointer_drift(self) -> None:
        resolver = self.migration.VerificationResolver()
        payload = {
            "schema_version": "actual-verification-v2",
            "verification_version": 2,
            "actual_file_id": "actual-file",
            "account_id": "account",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": "a" * 64,
            "observed_payload_sha256": "a" * 64,
            "expected_count": 1,
            "observed_count": 1,
            "expected_amount_sum_minor": 123,
            "observed_amount_sum_minor": 123,
            "invariants_passed": True,
        }
        pointer = resolver.write(payload)
        self.assertEqual(resolver.readback(pointer, expected_sha256=pointer["verification_artifact_sha256"]), payload)
        with self.assertRaises(self.migration.MigrationError):
            resolver.readback({**pointer, "verification_artifact_etag": "stale"}, expected_sha256=pointer["verification_artifact_sha256"])

    def test_reconciliation_join_requires_exact_hash_and_projects_target_fields(self) -> None:
        outbox = [{
            "outbox_id": "out-1", "imported_id": "import-1", "run_id": "run-1",
            "payload_sha256": "b" * 64, "state": "COMMITTED", "actual_file_id": "actual",
            "source_code": "MAIL", "period_key": "2026-08",
        }]
        verification_payload = {
            "schema_version": "actual-verification-v2",
            "verification_version": 1,
            "actual_file_id": "actual",
            "account_id": "account",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": "a" * 64,
            "observed_payload_sha256": "a" * 64,
            "expected_count": 1,
            "observed_count": 1,
            "expected_amount_sum_minor": 10,
            "observed_amount_sum_minor": 10,
            "invariants_passed": True,
        }
        verification_resolver = self.migration.VerificationResolver()
        verification_pointer = verification_resolver.write(verification_payload)
        verification = [{
            "outbox_id": "out-1", **verification_payload, **verification_pointer,
        }]
        reconciliation = [{
            "source_code": "MAIL", "period_key": "2026-08", "reconciliation_version": 1,
            "statement_sha256": "c" * 64, "actual_verification_sha256": verification_pointer["verification_artifact_sha256"],
            "state": "COMMITTED", "difference_minor": 0, "verified_at": "2026-09-01",
        }]
        rows = self.migration.reconcile_actual_batches(
            outbox, verification, reconciliation, verification_resolver=verification_resolver
        )
        self.assertEqual(rows[0]["idempotency_key"], "import-1")
        self.assertEqual(rows[0]["verification_artifact_sha256"], verification_pointer["verification_artifact_sha256"])
        self.assertNotIn("outbox_id", rows[0])
        with self.assertRaises(self.migration.MigrationError):
            self.migration.reconcile_actual_batches(
                outbox,
                verification,
                [{**reconciliation[0], "actual_verification_sha256": "d" * 64}],
                verification_resolver=verification_resolver,
            )
        with self.assertRaises(self.migration.MigrationError) as missing_pointer:
            self.migration.reconcile_actual_batches(
                outbox,
                [{key: value for key, value in verification[0].items() if key not in verification_pointer}],
                reconciliation,
                verification_resolver=verification_resolver,
            )
        self.assertIn("POINTER_REQUIRED", str(missing_pointer.exception))

    def test_second_run_noop_reverse_rehearsal_and_backup_digest(self) -> None:
        source = {
            "finance_source_cursors": [
                {
                    "source_code": "MAIL",
                    "cursor_value": "2026-08-01",
                    "committed_run_id": "run-1",
                    "cursor_version": 2,
                    "readback_verified": True,
                }
            ],
            "finance_acquisition_receipts": [],
            "finance_archive_receipts": [],
            "finance_document_operations": [],
            "finance_actual_outbox": [],
            "finance_actual_verifications": [],
            "finance_reconciliations": [],
            "finance_agent_jobs": [],
        }
        runner = self.migration.MigrationRunner(source)
        backup = runner.backup_digest()
        first = runner.run()
        second = runner.run()
        receipt_schema = json.loads(
            (ROOT / "integrations/n8n/data-table-migration-receipt.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(receipt_schema).validate(first)
        Draft202012Validator(receipt_schema).validate(second)
        self.assertEqual(first["backup_digest"], backup)
        self.assertTrue(second["second_run_noop"])
        self.assertFalse(second["changed"])
        self.assertEqual(runner.reverse_rehearsal()["source_digest"], backup)
        self.assertTrue(runner.reverse_rehearsal()["target_tables_untouched"])
        rehearsal = runner.reverse_rehearsal()
        self.assertTrue(rehearsal["restore_roundtrip"])
        self.assertEqual(rehearsal["restored_source_digest"], backup)
        self.assertEqual(rehearsal["restored_source_schemas"], rehearsal["source_schemas"])
        tampered_backup = runner.backup_snapshot()
        tampered_backup["tables"]["finance_source_cursors"]["schema"]["columns"] = {}
        with self.assertRaises(self.migration.MigrationError):
            runner.restore_backup(tampered_backup)
        with self.assertRaises(self.migration.MigrationError):
            runner.delete_old_tables()

    def test_duplicate_precedence_and_logical_keys_fail_closed(self) -> None:
        resolver = self.migration.VerificationResolver()
        payload = {
            "schema_version": "actual-verification-v2", "verification_version": 1,
            "actual_file_id": "actual", "account_id": "account", "period_start": "2026-08-01",
            "period_end": "2026-08-31", "expected_payload_sha256": "a" * 64,
            "observed_payload_sha256": "a" * 64, "expected_count": 1, "observed_count": 1,
            "expected_amount_sum_minor": 1, "observed_amount_sum_minor": 1,
            "invariants_passed": True,
        }
        pointer = resolver.write(payload)
        winner = {"outbox_id": "out-1", **payload, **pointer}
        outbox = [{"outbox_id": "out-1", "imported_id": "same", "payload_sha256": "b" * 64}]
        with self.assertRaises(self.migration.MigrationError):
            self.migration.reconcile_actual_batches(
                outbox, [winner, winner], [], verification_resolver=resolver
            )
        runner = self.migration.MigrationRunner({
            "finance_actual_outbox": [
                {"outbox_id": "out-1", "imported_id": "same"},
                {"outbox_id": "out-2", "imported_id": "same"},
            ]
        })
        with self.assertRaises(self.migration.MigrationError) as duplicate:
            runner.build_targets()
        self.assertIn("duplicate-logical-key", str(duplicate.exception))
        duplicate_cursor_runner = self.migration.MigrationRunner({
            "finance_source_cursors": [
                {"source_code": "MAIL"},
                {"source_code": "MAIL"},
            ]
        })
        with self.assertRaises(self.migration.MigrationError):
            duplicate_cursor_runner.build_targets()

    def test_dual_read_prefers_target_and_falls_back_without_cutover(self) -> None:
        dual = self.migration.DualReadWrite()
        dual.write("finance_documents", "doc-1", {"document_id": "doc-1", "state": "ARCHIVED"})
        self.assertEqual(dual.read("finance_documents", "doc-1")[1], "target")
        dual.target.pop(("finance_documents", "doc-1"))
        self.assertEqual(dual.read("finance_documents", "doc-1")[1], "legacy_fallback")
        with self.assertRaises(self.migration.MigrationError):
            dual.delete_old("finance_documents")


if __name__ == "__main__":
    unittest.main()
