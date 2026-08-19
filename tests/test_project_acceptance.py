import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectAcceptanceTests(unittest.TestCase):
    def test_acceptance_matrix_is_complete_and_fail_closed(self) -> None:
        payload = json.loads(
            (ROOT / "config" / "project-acceptance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["schema_version"], 1)
        rows = payload["requirements"]
        identities = [row["id"] for row in rows]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(all(row["evidence"] for row in rows))

        statuses = {
            "VERIFIED", "IMPLEMENTED_NOT_DEPLOYED", "PARTIAL",
            "BLOCKED", "MISSING", "SUPERSEDED",
        }
        for row in rows:
            self.assertIn(row["status"], statuses)
            if row["status"] in {"PARTIAL", "BLOCKED", "MISSING"}:
                self.assertTrue(row.get("blockers"), row["id"])

        production_blockers = [
            row for row in rows
            if row["production_gate"] and row["status"] != "VERIFIED"
        ]
        self.assertTrue(production_blockers)

    def test_required_financial_acceptance_dimensions_exist(self) -> None:
        payload = json.loads(
            (ROOT / "config" / "project-acceptance.json").read_text(encoding="utf-8")
        )
        identities = {row["id"] for row in payload["requirements"]}
        self.assertTrue({
            "fab-complete-inventory",
            "sarwa-wealth-accounts",
            "adcb-closed-zero",
            "locked-transaction-semantics",
            "canonical-display-notes",
            "manual-state-preservation",
            "disposable-idempotent-rebuild",
            "actual-ui-api-parity",
            "operational-restore-and-restart",
        }.issubset(identities))


if __name__ == "__main__":
    unittest.main()
