from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "integrations" / "n8n" / "analyze_data_table_cutover.py"
MATRIX = ROOT / "integrations" / "n8n" / "data-table-migration-matrix.json"


def load_module():
    spec = importlib.util.spec_from_file_location("cutover_feasibility", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cutover feasibility module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DataTableCutoverFeasibilityTests(unittest.TestCase):
    def test_current_workflows_cannot_be_cut_over_by_selector_rewrite(self) -> None:
        report = load_module().analyze(json.loads(MATRIX.read_text(encoding="utf-8")))
        self.assertFalse(report["cutover_ready"])
        self.assertEqual(report["reference_count"], 33)
        self.assertGreater(report["semantic_adapter_required_count"], 0)

        by_source: dict[str, list[dict]] = {}
        for action in report["actions"]:
            by_source.setdefault(action["source_table"], []).append(action)
        self.assertTrue(all("NO_CANONICAL_TABLE" in action["blockers"] for action in by_source["finance_mcp_requests"]))
        self.assertTrue(all("NO_CANONICAL_TABLE" in action["blockers"] for action in by_source["finance_source_contracts"]))
        self.assertTrue(any("FIELD_RENAME_REQUIRES_WORKFLOW_ADAPTER" in action["blockers"] for action in by_source["finance_archive_receipts"]))
        self.assertTrue(any("IDENTITY_DERIVATION_REQUIRES_WORKFLOW_ADAPTER" in action["blockers"] for action in by_source["finance_document_operations"]))

    def test_safe_reference_has_no_hidden_field_or_identity_transform(self) -> None:
        report = load_module().analyze(json.loads(MATRIX.read_text(encoding="utf-8")))
        safe = [action for action in report["actions"] if action["selector_only_safe"]]
        self.assertEqual(
            [(action["source_table"], action["node"]) for action in safe],
            [("finance_source_cursors", "Read Source Cursor Before Commit")],
        )


if __name__ == "__main__":
    unittest.main()
