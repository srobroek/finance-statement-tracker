from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "config" / "actual-account-taxonomy.json"
BOOTSTRAP = ROOT / "config" / "actual-bootstrap.json"
COMPLETENESS = ROOT / "config" / "account-completeness.json"
WORKER = ROOT / "config" / "actual-worker-taxonomy.json"
GENERATOR = ROOT / "scripts" / "generate-actual-account-taxonomy.py"


class ActualAccountTaxonomyTests(unittest.TestCase):
    def _generator(self):
        spec = importlib.util.spec_from_file_location("actual_account_taxonomy_generator", GENERATOR)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_projections_are_current_and_reproducible(self) -> None:
        first = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = {path: path.read_bytes() for path in (BOOTSTRAP, COMPLETENESS, WORKER)}
        subprocess.run(
            [sys.executable, str(GENERATOR), "--write"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        after = {path: path.read_bytes() for path in (BOOTSTRAP, COMPLETENESS, WORKER)}
        self.assertEqual(before, after)

    def test_static_bootstrap_drift_fails_closed(self) -> None:
        generator = self._generator()
        manifest = json.loads(CANONICAL.read_text(encoding="utf-8"))
        current = json.loads(BOOTSTRAP.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(current)
        mutated["category_groups"][0]["name"] = "Unapproved category group"
        with self.assertRaisesRegex(ValueError, "static contract drifted"):
            generator._build_bootstrap(manifest, mutated)

    def test_canonical_accounts_reconcile_exactly_to_projections(self) -> None:
        canonical = json.loads(CANONICAL.read_text(encoding="utf-8"))
        bootstrap = json.loads(BOOTSTRAP.read_text(encoding="utf-8"))
        completeness = json.loads(COMPLETENESS.read_text(encoding="utf-8"))

        canonical_ids = {row["provider_account_id"] for row in canonical["accounts"]}
        completeness_ids = {row["provider_account_id"] for row in completeness["accounts"]}
        self.assertEqual(canonical_ids, completeness_ids)
        self.assertEqual(
            {row["name"] for row in bootstrap["accounts"]},
            {
                row["actual"]["name"]
                for row in canonical["accounts"]
                if row["actual"]["bootstrap"]
            },
        )
        self.assertNotIn("ADCB Credit Card · 8833 / 6838", {
            row["name"] for row in bootstrap["accounts"]
        })
        self.assertNotIn("Sarwa Classic", {row["name"] for row in bootstrap["accounts"]})

    def test_lifecycle_balance_and_data_boundary_fields_are_explicit(self) -> None:
        canonical = json.loads(CANONICAL.read_text(encoding="utf-8"))
        rows = {row["provider_account_id"]: row for row in canonical["accounts"]}
        fab_mortgage = rows["fab:loan:mortgage-0203"]
        self.assertEqual(fab_mortgage["lifecycle_status"], "ACTIVE")
        self.assertEqual(fab_mortgage["actual_offbudget"], True)
        self.assertEqual(fab_mortgage["balance_evidence_status"], "EVIDENCED")
        self.assertEqual(fab_mortgage["expected_balance_minor"], -260595200)

        adib = rows["adib:loan:bluewaters-b7-306"]
        self.assertEqual(adib["lifecycle_status"], "PLANNED")
        self.assertEqual(adib["balance_evidence_status"], "UNAVAILABLE")
        self.assertIsNone(adib["expected_balance_minor"])
        self.assertEqual(adib["actual"]["initial_balance_minor"], -260000000)

        sarwa = rows["sarwa:invest:personal"]
        self.assertEqual(sarwa["owner"], "Personal")
        self.assertEqual(sarwa["actual_offbudget"], True)
        self.assertEqual(sarwa["balance_evidence_status"], "EVIDENCED")
        self.assertIsNone(sarwa["expected_balance_minor"])

    def test_payee_and_tag_ownership_is_explicit_and_worker_projection_is_scoped(self) -> None:
        canonical = json.loads(CANONICAL.read_text(encoding="utf-8"))
        worker = json.loads(WORKER.read_text(encoding="utf-8"))
        for key in ("tags", "payees"):
            self.assertTrue(all(row["bootstrap"] or row["worker"] for row in canonical[key]))
            expected = {
                (row["tag"] if key == "tags" else row["name"])
                for row in canonical[key]
                if row["worker"]
            }
            actual = {
                (row["tag"] if key == "tags" else row["name"])
                for row in worker[key]
            }
            self.assertEqual(actual, expected)
            self.assertTrue(all(row["worker"] for row in worker[key]))

    def test_manifest_never_persists_full_account_numbers(self) -> None:
        rendered = CANONICAL.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b[0-9]{12,}\b", rendered))
        canonical = json.loads(rendered)
        for row in canonical["accounts"]:
            self.assertTrue(not row["last4"] or re.fullmatch(r"[0-9]{4}", row["last4"]))
            for last4 in row["actual"].get("card_last4", []):
                self.assertRegex(last4, r"^[0-9]{4}$")


if __name__ == "__main__":
    unittest.main()
