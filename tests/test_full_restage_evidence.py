from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from finance_tracker.full_restage_evidence import build_evidence_links


class FullRestageEvidenceTests(TestCase):
    def test_groups_exact_transaction_links_by_source_and_skips_statements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifests = root / "manifests"
            output = root / "links"
            manifests.mkdir()
            (manifests / "source-a.json").write_text(
                json.dumps(
                    {"envelopes": [{"records": [{"imported_id": "tx-1"}]}]}
                ),
                encoding="utf-8",
            )
            catalogue = root / "catalogue.json"
            catalogue.write_text(
                json.dumps(
                    [
                        {
                            "evidence_id": "sha256:" + "a" * 64,
                            "transaction_id": "tx-1",
                            "document_type": "receipt",
                            "relative_path": "Finance Evidence/2026/08/vendor/file.pdf",
                        },
                        {
                            "evidence_id": "sha256:" + "b" * 64,
                            "transaction_id": "tx-1",
                            "document_type": "statement",
                            "relative_path": "Finance Evidence/2026/08/bank/statement.pdf",
                        },
                        {
                            "evidence_id": "sha256:" + "c" * 64,
                            "transaction_ids": ["tx-missing"],
                            "document_type": "warranty",
                            "relative_path": "Finance Evidence/2026/08/vendor/warranty.pdf",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            report = build_evidence_links(manifests, catalogue, output)

            links = json.loads((output / "source-a.json").read_text(encoding="utf-8"))
            self.assertEqual(report["link_count"], 1)
            self.assertEqual(report["unmatched_transaction_ids"], ["tx-missing"])
            self.assertEqual(links[0]["transaction_id"], "tx-1")
            self.assertEqual(links[0]["document_type"], "receipt")
