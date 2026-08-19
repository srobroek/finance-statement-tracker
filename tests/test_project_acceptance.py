import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectAcceptanceTests(unittest.TestCase):
    def test_acceptance_matrix_is_complete_and_fail_closed(self) -> None:
        payload = json.loads(
            (ROOT / "config" / "project-acceptance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["schema_version"], 2)
        schema = json.loads(
            (ROOT / "config" / "project-acceptance-schema-v2.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
        self.assertTrue(payload["environment"]["id"])
        rows = payload["requirements"]
        identities = [row["id"] for row in rows]
        self.assertEqual(len(identities), len(set(identities)))

        statuses = {
            "SPEC_ONLY", "IMPLEMENTED_NOT_DEPLOYED", "TESTED_IN_DISPOSABLE",
            "SHADOW", "PRODUCTION", "VERIFIED", "PARTIAL", "BLOCKED",
            "MISSING", "SUPERSEDED",
        }
        sha256 = re.compile(r"^[0-9a-f]{64}$")
        git_sha = re.compile(r"^[0-9a-f]{40}$")
        for row in rows:
            self.assertIn(row["status"], statuses)
            self.assertTrue(row["invariant"])
            self.assertIn(row["verifier"]["kind"], {
                "TEST", "SCRIPT", "RUNTIME_READBACK", "MANUAL_REVIEW",
            })
            self.assertTrue(row["verifier"]["command"])
            self.assertTrue(row["verifier"]["expected"])
            self.assertTrue(set(row["dependencies"]).issubset(set(identities)))
            if row["status"] not in {"VERIFIED", "SUPERSEDED"}:
                self.assertTrue(row.get("blockers"), row["id"])
            if row["status"] == "VERIFIED":
                self.assertTrue(row["evidence"], row["id"])
            for evidence in row["evidence"]:
                self.assertRegex(evidence["sha256"], sha256)
                self.assertRegex(evidence["git_commit"], git_sha)
                self.assertTrue(evidence["artifact_uri"])
                self.assertTrue(evidence["environment_id"])
                self.assertTrue(evidence["observed_at"])
                self.assertTrue(evidence["reviewer"])
                self.assertIs(evidence["non_empty"], True)

        production_blockers = [
            row for row in rows
            if row["production_gate"] and row["status"] not in {"VERIFIED", "PRODUCTION"}
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
            "mail-cursor-completeness",
            "actual-outbox-recovery",
            "rule-ownership",
            "n8n-secrets",
            "cloudflare-route-security",
            "bounded-mcp-facade",
            "cashback-reusability-mobile-push",
            "wealth-net-worth",
        }.issubset(identities))


if __name__ == "__main__":
    unittest.main()
