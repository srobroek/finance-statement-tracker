from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
SCRIPT = N8N / "generate_data_table_migration_matrix.py"
MATRIX_PATH = N8N / "data-table-migration-matrix.json"
SCHEMA_PATH = N8N / "data-table-migration-matrix.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_generator():
    spec = importlib.util.spec_from_file_location("data_table_matrix_generator", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load generator: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DataTableMigrationMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = load_generator()
        cls.matrix = load_json(MATRIX_PATH)

    def test_generated_matrix_is_schema_valid(self) -> None:
        schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(self.matrix)

    def test_provenance_excludes_generator_commit_cycle(self) -> None:
        snapshot = self.generator.source_snapshot()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
        self.assertNotEqual(snapshot["finance_commit"], head)
        self.assertIn("data-tables.json", snapshot["source_ref_selection"])
        self.assertIn("scan-root directories", snapshot["source_ref_selection"])
        self.assertIn("Normalize CRLF to LF", snapshot["node_scan_digest_method"])

    def test_crlf_normalization_covers_hashing_and_emission(self) -> None:
        self.assertEqual(self.generator.normalize_lf(b"one\r\ntwo\r\n"), b"one\ntwo\n")
        rendered = self.generator.render({"line": "one\ntwo"})
        self.assertNotIn("\r", rendered)
        self.assertEqual(rendered.encode("utf-8"), self.generator.normalize_lf(rendered.encode("utf-8")))

    def test_source_counts_and_dispositions_match_current_corpus(self) -> None:
        invariants = self.matrix["invariants"]
        self.assertEqual(
            {
                key: invariants[key]
                for key in (
                    "source_tables",
                    "source_columns",
                    "node_references",
                    "consumer_node_edges",
                    "filter_only_consumer_columns",
                    "filter_only_consumer_edges",
                    "write_reference_edges",
                    "producer_node_edges",
                )
            },
            {
                "source_tables": 15,
                "source_columns": 215,
                "node_references": 144,
                "consumer_node_edges": 1229,
                "filter_only_consumer_columns": 39,
                "filter_only_consumer_edges": 121,
                "write_reference_edges": 478,
                "producer_node_edges": 693,
            },
        )
        self.assertEqual(invariants["dispositions"], {"keep": 101, "transform": 58, "remove": 56})
        tables = load_json(N8N / "data-tables.json")["tables"]
        self.assertEqual([row["source_table"] for row in self.matrix["tables"]], [row["name"] for row in tables])
        self.assertEqual(
            [column["source_column"] for column in self.matrix["tables"][0]["columns"]],
            list(tables[0]["columns"]),
        )

    def test_reference_scan_preserves_exact_operations_and_ordered_filters(self) -> None:
        references = {
            (reference["file"], reference["node"]): reference
            for table in self.matrix["tables"]
            for reference in table["node_references"]
        }
        repeated = references[
            (
                "integrations/n8n/disposable/generated/102-derived-recovery-core.json",
                "Read Nonterminal Actual Outbox",
            )
        ]
        self.assertEqual(repeated["operation"], "get")
        self.assertEqual(repeated["read_columns"], ["*"])
        self.assertEqual(repeated["filter_keys"], ["state", "state", "state"])
        update = references[
            (
                "integrations/n8n/workflows/12-outlook-message-sweep.json",
                "CAS Update Source Cursor",
            )
        ]
        self.assertEqual(update["operation"], "update")
        self.assertEqual(update["write_columns"], sorted(update["write_columns"]))
        self.assertEqual(update["filter_keys"], ["source_code", "cursor_version"])
        insert = references[
            (
                "integrations/n8n/workflows/12-outlook-message-sweep.json",
                "Insert Source Cursor v0",
            )
        ]
        self.assertEqual(insert["operation"], "insert")
        self.assertEqual(insert["write_columns"], sorted(insert["write_columns"]))
        self.assertEqual(insert["filter_keys"], [])

    def test_consumer_and_producer_unions_are_explicit(self) -> None:
        columns = {
            (table["source_table"], column["source_column"]): column
            for table in self.matrix["tables"]
            for column in table["columns"]
        }
        references = {
            (reference["table"], f"{reference['file']}#{reference['node']}"): reference
            for table in self.matrix["tables"]
            for reference in table["node_references"]
        }
        cursor = columns[("finance_source_cursors", "cursor_version")]
        self.assertIn(
            "integrations/n8n/workflows/12-outlook-message-sweep.json#CAS Update Source Cursor",
            cursor["consumer_nodes"],
        )
        self.assertIn(
            "integrations/n8n/workflows/19-platform-data-table-bootstrap.json#Create or Reuse finance_source_cursors",
            cursor["producer_nodes"],
        )
        for (table_name, source_column), column in columns.items():
            table_refs = [reference for key, reference in references.items() if key[0] == table_name]
            expected_consumers = sorted(
                f"{reference['file']}#{reference['node']}"
                for reference in table_refs
                if "*" in reference["read_columns"]
                or source_column in reference["read_columns"]
                or source_column in reference["filter_keys"]
            )
            expected_producers = sorted(
                f"{reference['file']}#{reference['node']}"
                for reference in table_refs
                if reference["operation"] == "create" or source_column in reference["write_columns"]
            )
            self.assertEqual(column["consumer_nodes"], expected_consumers, (table_name, source_column))
            self.assertEqual(column["producer_nodes"], expected_producers, (table_name, source_column))

    def test_generator_output_is_byte_identical_and_check_passes(self) -> None:
        first = self.generator.render(self.generator.build_matrix())
        second = self.generator.render(self.generator.build_matrix())
        self.assertEqual(first, second)
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(MATRIX_PATH.read_text(encoding="utf-8"), first)

    def test_stale_digest_and_coverage_are_rejected(self) -> None:
        stale = deepcopy(self.matrix)
        stale["source_snapshot"]["node_scan_corpus_sha256"] = "0" * 64
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_matrix(stale)
        incomplete = deepcopy(self.matrix)
        incomplete["tables"][1]["columns"][0]["consumer_nodes"] = []
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_matrix(incomplete)

    def test_exact_target_schemas_and_explicit_merge_bindings(self) -> None:
        expected_targets = {
            "finance_ingestion_state",
            "finance_documents",
            "finance_actual_batches",
            "finance_ai_reviews",
        }
        self.assertEqual(set(self.matrix["target_schemas"]), expected_targets)
        for target, schema in self.matrix["target_schemas"].items():
            self.assertTrue(set(schema["logical_key"]) <= set(schema["columns"]), target)
            for field, definition in schema["columns"].items():
                self.assertTrue(definition["source_bindings"], (target, field))

        actual_verifications = {
            column["source_column"]: column
            for table in self.matrix["tables"]
            if table["source_table"] == "finance_actual_verifications"
            for column in table["columns"]
        }
        self.assertEqual(actual_verifications["actual_file_id"]["target_field"], "actual_file_id")
        self.assertEqual(actual_verifications["card_code"]["target_field"], "card_code")
        self.assertEqual(
            actual_verifications["expected_payload_sha256"]["target_field"],
            "expected_payload_sha256",
        )
        reconciliations = {
            column["source_column"]: column
            for table in self.matrix["tables"]
            if table["source_table"] == "finance_reconciliations"
            for column in table["columns"]
        }
        self.assertEqual(
            reconciliations["actual_verification_sha256"]["target_field"],
            "verification_artifact_sha256",
        )
        ingestion_columns = self.matrix["target_schemas"]["finance_ingestion_state"]["columns"]
        self.assertEqual(
            ingestion_columns["attachment_verification_barrier"]["source_bindings"],
            ["finance_acquisition_receipts.attachment_verification_barrier"],
        )
        self.assertEqual(
            ingestion_columns["email_evidence_receipt_barrier"]["source_bindings"],
            ["finance_acquisition_receipts.email_evidence_receipt_barrier"],
        )

        document_identities = self.matrix["target_schemas"]["finance_documents"]["identity_derivations"]
        archive_identity = next(
            row for row in document_identities if row["source_table"] == "finance_archive_receipts"
        )
        processing_identity = next(
            row for row in document_identities if row["source_table"] == "finance_document_operations"
        )
        self.assertEqual(archive_identity["strategy"], "versioned_length_prefixed_sha256")
        self.assertEqual(processing_identity["strategy"], archive_identity["strategy"])
        self.assertEqual(archive_identity["identity_kind"], "MAIL_LINKED")
        self.assertEqual(processing_identity["identity_kind"], archive_identity["identity_kind"])
        self.assertEqual(archive_identity["version"], "document-identity-v1")
        self.assertEqual(processing_identity["version"], archive_identity["version"])
        self.assertEqual(archive_identity["length_prefix"], "uint64_be")
        self.assertEqual(processing_identity["length_prefix"], archive_identity["length_prefix"])
        self.assertEqual(
            processing_identity["source_fields"], archive_identity["source_fields"]
        )
        self.assertEqual(
            archive_identity["tuple_encoding"], "versioned_length_prefixed_binary"
        )
        self.assertEqual(archive_identity["digest_encoding"], "base64url_unpadded")
        self.assertEqual(archive_identity["alias_fields"], [])
        self.assertEqual(
            processing_identity["legacy_to_canonical"],
            {
                "adapter": "document-identity-alias-v1",
                "legacy_fields": ["document_id"],
                "canonical_target": "finance_documents.document_id",
            },
        )
        self.assertEqual(
            processing_identity["fallback_identity"]["identity_kind"], "PROCESSING_ONLY"
        )
        self.assertEqual(
            processing_identity["fallback_identity"]["source_fields"],
            ["source_sha256", "document_profile", "requested_schema_version"],
        )
        self.assertEqual(processing_identity["alias_fields"], ["document_id"])
        self.assertNotIn("separator", archive_identity)
        batch_identities = self.matrix["target_schemas"]["finance_actual_batches"]["identity_derivations"]
        verification_identity = next(
            row for row in batch_identities
            if row["source_table"] == "finance_actual_verifications"
        )
        self.assertEqual(verification_identity["join_steps"][0]["left_fields"], ["outbox_id"])
        reconciliation_identity = next(
            row for row in batch_identities
            if row["source_table"] == "finance_reconciliations"
        )
        self.assertEqual(reconciliation_identity["cardinality"], "exactly_one")
        self.assertEqual(
            reconciliation_identity["join_key"]["source_fields"],
            ["source_code", "period_key", "actual_verification_sha256"],
        )
        self.assertEqual(
            reconciliation_identity["join_key"]["target_fields"],
            ["source_code", "period_key", "verification_artifact_sha256"],
        )
        self.assertEqual(
            reconciliation_identity["join_steps"][0]["right_fields"],
            ["verification_artifact_sha256"],
        )
        processing_document_id = next(
            column
            for table in self.matrix["tables"]
            if table["source_table"] == "finance_document_operations"
            for column in table["columns"]
            if column["source_column"] == "document_id"
        )
        self.assertIsNone(processing_document_id["target_table"])
        self.assertEqual(processing_document_id["target_artifact"], "document-identity-aliases-v1")
        self.assertEqual(processing_document_id["target_field"], "legacy_to_canonical.document_id")

        source_tables = self.generator.load_source_tables()
        missing_archive_identity = deepcopy(self.matrix["target_schemas"])
        missing_archive_identity["finance_documents"]["identity_derivations"] = [
            row for row in missing_archive_identity["finance_documents"]["identity_derivations"]
            if row["source_table"] != "finance_archive_receipts"
        ]
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_identity_derivations(source_tables, missing_archive_identity)

        broken_join = deepcopy(self.matrix["target_schemas"])
        broken_join["finance_actual_batches"]["identity_derivations"][1]["join_steps"][0]["left_fields"] = [
            "missing_outbox_id"
        ]
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_identity_derivations(source_tables, broken_join)

        numeric_rhs = deepcopy(self.matrix["target_schemas"])
        numeric_rhs["finance_actual_batches"]["identity_derivations"][2]["join_steps"][0]["right_fields"] = [
            "verification_version"
        ]
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_identity_derivations(source_tables, numeric_rhs)

        divergent_document_identity = deepcopy(self.matrix["target_schemas"])
        divergent_document_identity["finance_documents"]["identity_derivations"][1] = {
            "source_table": "finance_document_operations",
            "strategy": "direct",
            "target_key": ["document_id"],
            "source_fields": ["document_id"],
        }
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_identity_derivations(source_tables, divergent_document_identity)

        malformed_document_tuple = deepcopy(self.matrix["target_schemas"])
        for identity in malformed_document_tuple["finance_documents"]["identity_derivations"]:
            identity["source_fields"] = ["source_sha256", "source_message_id"]
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_identity_derivations(source_tables, malformed_document_tuple)

        missing_alias_adapter = deepcopy(self.matrix["target_schemas"])
        missing_alias_adapter["finance_documents"]["identity_derivations"][1].pop("legacy_to_canonical")
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_identity_derivations(source_tables, missing_alias_adapter)

        reversed_alias_adapter = deepcopy(self.matrix["target_schemas"])
        reversed_alias_adapter["finance_documents"]["identity_derivations"][1][
            "legacy_to_canonical"
        ]["canonical_target"] = "finance_documents.source_sha256"
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_identity_derivations(source_tables, reversed_alias_adapter)

        direct_document_binding = deepcopy(self.matrix)
        for table in direct_document_binding["tables"]:
            if table["source_table"] == "finance_document_operations":
                for column in table["columns"]:
                    if column["source_column"] == "document_id":
                        column.update(
                            {
                                "disposition": "keep",
                                "target_table": "finance_documents",
                                "target_artifact": None,
                                "target_field": "document_id",
                            }
                        )
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_matrix(direct_document_binding)

        duplicate_target = deepcopy(self.matrix)
        for table in duplicate_target["tables"]:
            if table["source_table"] == "finance_actual_verifications":
                for column in table["columns"]:
                    if column["source_column"] == "expected_count":
                        column["target_field"] = "verification_artifact_sha256"
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_matrix(duplicate_target)

        # A SHA-256 source is still a string and must never be silently
        # coerced into the numeric verification version column.
        wrong_type = deepcopy(self.matrix)
        for table in wrong_type["tables"]:
            if table["source_table"] == "finance_reconciliations":
                for column in table["columns"]:
                    if column["source_column"] == "actual_verification_sha256":
                        column["target_field"] = "verification_version"
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_matrix(wrong_type)

        missing_target = deepcopy(self.matrix)
        del missing_target["target_schemas"]["finance_ai_reviews"]
        with self.assertRaises(self.generator.MatrixError):
            self.generator.validate_matrix(missing_target)


if __name__ == "__main__":
    unittest.main()
