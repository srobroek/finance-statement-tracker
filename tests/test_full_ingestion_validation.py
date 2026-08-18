import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from finance_tracker.full_ingestion_validation import validate_full_ingestion


class FullIngestionValidationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[dict, dict]:
        evidence = root / "Finance Evidence" / "2026" / "08" / "bank" / "statement.pdf"
        evidence.parent.mkdir(parents=True)
        evidence.write_bytes(b"statement")
        digest = hashlib.sha256(b"statement").hexdigest()
        catalogue = [{
            "entity_type": "CARD_PERIOD",
            "document_type": "statement",
            "bank": "Bank",
            "card_code": "CARD",
            "statement_date": "2026-08-01",
            "relative_path": "Finance Evidence/2026/08/bank/statement.pdf",
            "sha256": digest,
        }]
        (root / "Finance Evidence" / "catalogue.json").write_text(
            json.dumps(catalogue), encoding="utf-8"
        )
        manifest_dir = root / "manifests"
        manifest_dir.mkdir()
        manifest = {
            "statement": {"balance_tied": True, "transaction_count": 2},
            "review_count": 0,
            "source_evidence": {
                "document_sha256": digest,
                "source_filename": "statement.pdf",
            },
            "envelopes": [{
                "account": "Card",
                "records": [
                    {
                        "date": "2026-08-01",
                        "amount": -100,
                        "payee_name": "Shop",
                        "imported_payee": "SHOP",
                        "imported_id": "statement:bank:one",
                        "cleared": True,
                        "notes": "#shared",
                    },
                    {
                        "date": "2026-08-02",
                        "amount": -200,
                        "payee_name": "Other",
                        "imported_payee": "OTHER",
                        "imported_id": "statement:bank:two",
                        "cleared": True,
                        "notes": "",
                    },
                ],
            }],
        }
        (manifest_dir / "statement.json").write_text(json.dumps(manifest), encoding="utf-8")
        config = {
            "evidence_catalogue": "Finance Evidence/catalogue.json",
            "manifest_sources": [{
                "id": "statements",
                "globs": ["manifests/*.json"],
                "accounts": ["Card"],
                "imported_id_prefixes": ["statement:bank:"],
                "require_balance_tied": True,
            }],
            "snapshot_scope": {
                "accounts": ["Card"],
                "imported_id_prefixes": ["statement:bank:", "browser:bank:"],
            },
            "required_exact_fields": ["account", "date", "amount", "imported_payee", "cleared"],
        }
        snapshot = {
            "generated_at": "2026-08-18T00:00:00Z",
            "transactions": [
                {
                    "account_name": "Card",
                    "date": "2026-08-01",
                    "amount": -100,
                    "imported_payee": "SHOP",
                    "imported_id": "statement:bank:one",
                    "cleared": True,
                    "notes": "#shared",
                },
                {
                    "account_name": "Card",
                    "date": "2026-08-02",
                    "amount": -200,
                    "imported_payee": "OTHER",
                    "imported_id": "statement:bank:two",
                    "cleared": True,
                    "notes": "",
                },
            ],
        }
        return config, snapshot

    def test_complete_exact_ingestion_passes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config, snapshot = self._fixture(root)
            report = validate_full_ingestion(root, config, snapshot)

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["counts"]["source_records"], 2)
        self.assertEqual(report["counts"]["statement_evidence_failures"], 0)

    def test_missing_statement_is_accepted_only_for_unique_browser_duplicate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config, snapshot = self._fixture(root)
            snapshot["transactions"] = [
                snapshot["transactions"][0],
                {
                    "account_name": "Card",
                    "date": "2026-08-02",
                    "amount": -200,
                    "imported_payee": "other",
                    "imported_id": "browser:bank:two",
                    "cleared": True,
                    "notes": "",
                },
            ]
            report = validate_full_ingestion(root, config, snapshot)

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["counts"]["suppressed_cross_source_duplicates"], 1)

    def test_noncanonical_notes_and_amount_drift_fail(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config, snapshot = self._fixture(root)
            snapshot["transactions"][0]["amount"] = -101
            snapshot["transactions"][0]["notes"] = "source:statement | #shared"
            report = validate_full_ingestion(root, config, snapshot)

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["counts"]["field_mismatches"], 1)
        self.assertEqual(report["counts"]["note_violations"], 1)

    def test_untracked_manual_row_fails(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config, snapshot = self._fixture(root)
            snapshot["transactions"].append({
                "id": "manual-row",
                "account_name": "Card",
                "date": "2026-08-03",
                "amount": 0,
                "payee_name": "Unknown",
                "notes": "",
            })
            report = validate_full_ingestion(root, config, snapshot)

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["counts"]["untracked_actual_rows"], 1)


if __name__ == "__main__":
    unittest.main()
