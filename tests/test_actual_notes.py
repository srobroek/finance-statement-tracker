import unittest

from finance_tracker.actual_notes import (
    add_actual_document,
    build_actual_note_cleanup_plan,
    canonicalize_actual_notes,
    format_actual_notes,
    validate_actual_notes,
)


class ActualNoteContractTests(unittest.TestCase):
    def test_tags_are_first_deduplicated_and_namespaced(self) -> None:
        notes = format_actual_notes(
            tags=["Shared", "rental:LT713", "shared"],
            documents=["Finance Evidence/2026/08/dewa/receipt.pdf"],
            fx=["NOK 340.00"],
        )
        self.assertEqual(
            notes,
            "#rental:lt713 #shared | "
            "Doc: Finance Evidence/2026/08/dewa/receipt.pdf | FX: NOK 340.00",
        )
        validate_actual_notes(notes)

    def test_technical_tags_are_rejected_before_import(self) -> None:
        for tag in ("browser-import", "primary", "evidence", "statement"):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "forbidden"):
                format_actual_notes(tags=[tag])

    def test_unknown_metadata_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported Actual note field"):
            validate_actual_notes("#shared | source:statement")

    def test_legacy_clutter_is_removed_and_evidence_is_canonicalized(self) -> None:
        original = (
            "source:statement | currency:NOK | original:340.00 | "
            "message:opaque | evidence:Finance Evidence/2026/08/vendor/receipt.pdf | "
            "#browser-import #primary #foreign #travel"
        )
        canonical = canonicalize_actual_notes(original)
        self.assertEqual(
            canonical,
            "#foreign #travel | "
            "Doc: Finance Evidence/2026/08/vendor/receipt.pdf | FX: NOK 340.00",
        )
        self.assertEqual(canonicalize_actual_notes(canonical), canonical)

    def test_document_addition_preserves_contract_and_is_idempotent(self) -> None:
        path = "Finance Evidence/2026/08/vendor/warranty.pdf"
        first = add_actual_document("#warranty #shared", path)
        second = add_actual_document(first, path)
        self.assertEqual(
            second,
            "#shared #warranty | Doc: Finance Evidence/2026/08/vendor/warranty.pdf",
        )
        validate_actual_notes(second)

    def test_document_must_live_under_finance_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "Finance Evidence"):
            format_actual_notes(documents=["random/receipt.pdf"])

    def test_cleanup_plan_is_exact_and_skips_unsafe_rows(self) -> None:
        snapshot = {
            "generated_at": "2026-08-18T12:00:00Z",
            "transactions": [
                {
                    "id": "actual-1",
                    "imported_id": "statement:one",
                    "account_name": "Card",
                    "date": "2026-08-01",
                    "amount": -100,
                    "notes": "source:statement | #primary #shared",
                },
                {
                    "id": "actual-2",
                    "imported_id": "statement:two",
                    "account_name": "Card",
                    "date": "2026-08-02",
                    "amount": -200,
                    "notes": "#shared",
                },
                {
                    "id": "actual-3",
                    "account_name": "Card",
                    "date": "2026-08-03",
                    "amount": -300,
                    "notes": "source:manual | #shared",
                },
            ],
        }

        plan, audit = build_actual_note_cleanup_plan(snapshot)

        self.assertEqual(len(plan["changes"]), 1)
        self.assertEqual(plan["changes"][0]["desired_notes"], "#shared")
        self.assertEqual(plan["changes"][0]["expected_current_notes"], "source:statement | #primary #shared")
        self.assertEqual(audit["scanned_count"], 3)
        self.assertEqual(audit["skipped"][0]["reason"], "NO_IMPORTED_ID")


if __name__ == "__main__":
    unittest.main()
