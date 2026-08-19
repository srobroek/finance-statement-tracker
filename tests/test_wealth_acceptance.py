from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from finance_tracker.wealth_acceptance import (
    load_wealth_acceptance_bundle,
    validate_wealth_acceptance_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "wealth-acceptance"
BUNDLE = FIXTURE_ROOT / "bundle.valid.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WealthAcceptanceTests(unittest.TestCase):
    def _copy_fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict]:
        temporary = tempfile.TemporaryDirectory()
        target = Path(temporary.name) / "wealth-acceptance"
        shutil.copytree(FIXTURE_ROOT, target)
        bundle_path = target / "bundle.valid.json"
        return temporary, target, json.loads(bundle_path.read_text(encoding="utf-8"))

    def _artifact(self, bundle: dict, identity: str) -> dict:
        return next(row for row in bundle["artifacts"] if row["id"] == identity)

    def _mutate_artifact(
        self,
        fixture_root: Path,
        bundle: dict,
        identity: str,
        mutate,
        *,
        refresh_hashes: bool = True,
    ) -> None:
        reference = self._artifact(bundle, identity)
        source = fixture_root / reference["source_path"]
        archive = fixture_root / reference["archive_path"]
        payload = json.loads(source.read_text(encoding="utf-8"))
        mutate(payload)
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        source.write_text(rendered, encoding="utf-8")
        archive.write_text(rendered, encoding="utf-8")
        if refresh_hashes:
            reference["source_sha256"] = _sha256(source)
            reference["archive_sha256"] = _sha256(archive)

    def test_disposable_bundle_passes_every_wealth_gate(self) -> None:
        bundle = load_wealth_acceptance_bundle(BUNDLE)

        report = validate_wealth_acceptance_bundle(bundle, base_dir=FIXTURE_ROOT)

        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["production_write_allowed"])
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["requirements"]["fab-complete-inventory"], "PASS")
        self.assertEqual(report["requirements"]["sarwa-wealth-accounts"], "PASS")
        self.assertEqual(report["requirements"]["adcb-closed-zero"], "PASS")
        self.assertEqual(report["requirements"]["actual-ui-api-parity"], "PASS")
        self.assertEqual(report["requirements"]["wealth-net-worth"], "PASS")

    def test_source_hash_tamper_blocks_relevant_gate(self) -> None:
        temporary, fixture_root, bundle = self._copy_fixture()
        self.addCleanup(temporary.cleanup)
        reference = self._artifact(bundle, "sarwa-t2")
        source = fixture_root / reference["source_path"]
        source.write_bytes(source.read_bytes() + b"\n")

        report = validate_wealth_acceptance_bundle(
            bundle, base_dir=fixture_root, requirement_id="sarwa-wealth-accounts"
        )

        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("SOURCE_SHA256_MISMATCH", {row["code"] for row in report["issues"]})

    def test_archive_hash_tamper_is_detected_separately(self) -> None:
        temporary, fixture_root, bundle = self._copy_fixture()
        self.addCleanup(temporary.cleanup)
        reference = self._artifact(bundle, "fab-inventory")
        archive = fixture_root / reference["archive_path"]
        archive.write_bytes(archive.read_bytes() + b"\n")

        report = validate_wealth_acceptance_bundle(
            bundle, base_dir=fixture_root, requirement_id="fab-complete-inventory"
        )

        self.assertIn("ARCHIVE_SHA256_MISMATCH", {row["code"] for row in report["issues"]})

    def test_incomplete_fab_inventory_blocks(self) -> None:
        temporary, fixture_root, bundle = self._copy_fixture()
        self.addCleanup(temporary.cleanup)
        self._mutate_artifact(
            fixture_root, bundle, "fab-inventory",
            lambda payload: payload.__setitem__("inventory_complete", False),
        )

        report = validate_wealth_acceptance_bundle(
            bundle, base_dir=fixture_root, requirement_id="fab-complete-inventory"
        )

        self.assertIn("FAB_INVENTORY_INCOMPLETE", {row["code"] for row in report["issues"]})

    def test_position_total_mismatch_blocks(self) -> None:
        temporary, fixture_root, bundle = self._copy_fixture()
        self.addCleanup(temporary.cleanup)

        def mutate(payload: dict) -> None:
            payload["portfolios"][0]["positions"][0]["market_value"] = "899.00"

        self._mutate_artifact(fixture_root, bundle, "sarwa-t2", mutate)
        report = validate_wealth_acceptance_bundle(
            bundle, base_dir=fixture_root, requirement_id="sarwa-wealth-accounts"
        )

        self.assertIn("POSITION_TOTAL_MISMATCH", {row["code"] for row in report["issues"]})

    def test_stale_and_future_fx_are_rejected(self) -> None:
        for observed_at, expected in (
            ("2026-08-01T00:00:00Z", "FX_SNAPSHOT_STALE"),
            ("2026-08-18T00:00:01Z", "FX_AS_OF_IN_FUTURE"),
        ):
            with self.subTest(observed_at=observed_at):
                temporary, fixture_root, bundle = self._copy_fixture()
                try:
                    self._mutate_artifact(
                        fixture_root,
                        bundle,
                        "fx-t2",
                        lambda payload: payload.__setitem__("observed_at", observed_at),
                    )
                    report = validate_wealth_acceptance_bundle(
                        bundle,
                        base_dir=fixture_root,
                        requirement_id="sarwa-wealth-accounts",
                    )
                    self.assertIn(expected, {row["code"] for row in report["issues"]})
                finally:
                    temporary.cleanup()

    def test_stable_identity_drift_between_snapshots_blocks(self) -> None:
        temporary, fixture_root, bundle = self._copy_fixture()
        self.addCleanup(temporary.cleanup)

        def mutate(payload: dict) -> None:
            payload["portfolios"][0]["provider_account_id"] = "sarwa:invest:renamed"

        self._mutate_artifact(fixture_root, bundle, "sarwa-t2", mutate)
        report = validate_wealth_acceptance_bundle(
            bundle, base_dir=fixture_root, requirement_id="sarwa-wealth-accounts"
        )

        self.assertIn("STABLE_ACCOUNT_IDENTITY_MISMATCH", {row["code"] for row in report["issues"]})

    def test_t2_must_be_delta_and_replays_must_be_idempotent(self) -> None:
        for field, expected in (
            ("delta_minor", "VALUATION_DELTA_MISMATCH"),
            ("t2_replay_state_hash", "VALUATION_REPLAY_DRIFT"),
        ):
            with self.subTest(field=field):
                temporary, fixture_root, bundle = self._copy_fixture()
                try:
                    def mutate(payload: dict) -> None:
                        if field == "delta_minor":
                            t2 = next(
                                row for row in payload["valuations"]
                                if row["snapshot_id"] == "sarwa-disposable-t2"
                                and row["provider_account_id"] == "sarwa:invest:personal"
                            )
                            t2["delta_minor"] += 1
                        else:
                            payload["replay"][field] = "f" * 64

                    self._mutate_artifact(fixture_root, bundle, "actual-api", mutate)
                    report = validate_wealth_acceptance_bundle(
                        bundle,
                        base_dir=fixture_root,
                        requirement_id="sarwa-wealth-accounts",
                    )
                    self.assertIn(expected, {row["code"] for row in report["issues"]})
                finally:
                    temporary.cleanup()

    def test_adcb_requires_non_synthetic_exact_zero_evidence(self) -> None:
        for field, value, expected in (
            ("synthetic_adjustment", True, "ADCB_SYNTHETIC_BALANCING_PROHIBITED"),
            ("closing_balance_minor", 1, "ADCB_CLOSING_BALANCE_NOT_ZERO"),
        ):
            with self.subTest(field=field):
                temporary, fixture_root, bundle = self._copy_fixture()
                try:
                    def mutate(payload: dict) -> None:
                        payload[field] = value
                        if field == "closing_balance_minor":
                            payload["statement_chain"][-1]["closing_balance_minor"] = value

                    self._mutate_artifact(fixture_root, bundle, "adcb-zero", mutate)
                    report = validate_wealth_acceptance_bundle(
                        bundle,
                        base_dir=fixture_root,
                        requirement_id="adcb-closed-zero",
                    )
                    self.assertIn(expected, {row["code"] for row in report["issues"]})
                finally:
                    temporary.cleanup()

    def test_actual_ui_api_balance_or_sync_drift_blocks(self) -> None:
        temporary, fixture_root, bundle = self._copy_fixture()
        self.addCleanup(temporary.cleanup)

        def mutate(payload: dict) -> None:
            payload["sync_id"] = "different-sync-id"
            payload["accounts"][0]["balance_minor"] += 1

        self._mutate_artifact(fixture_root, bundle, "actual-ui", mutate)
        report = validate_wealth_acceptance_bundle(
            bundle, base_dir=fixture_root, requirement_id="actual-ui-api-parity"
        )

        codes = {row["code"] for row in report["issues"]}
        self.assertIn("ACTUAL_SYNC_ID_MISMATCH", codes)
        self.assertIn("ACTUAL_ACCOUNT_BALANCE_MISMATCH", codes)

    def test_actual_cannot_contain_an_unreviewed_sarwa_account(self) -> None:
        temporary, fixture_root, bundle = self._copy_fixture()
        self.addCleanup(temporary.cleanup)

        def mutate(payload: dict) -> None:
            payload["accounts"].append({
                "actual_account_id": "actual-sarwa-unreviewed",
                "balance_minor": 0,
                "closed": False,
                "offbudget": True,
                "provider_account_id": "sarwa:invest:unreviewed",
            })
            payload["aggregate_net_worth_minor"] += 0

        self._mutate_artifact(fixture_root, bundle, "actual-api", mutate)
        report = validate_wealth_acceptance_bundle(
            bundle, base_dir=fixture_root, requirement_id="sarwa-wealth-accounts"
        )

        self.assertIn(
            "SARWA_ACTUAL_ACCOUNT_SET_MISMATCH",
            {row["code"] for row in report["issues"]},
        )

    def test_evidence_bundle_schema_is_versioned(self) -> None:
        schema = json.loads(
            (ROOT / "config" / "wealth-acceptance-evidence-schema-v1.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(
            set(schema["required"]),
            {"schema_version", "environment", "artifacts", "policy"},
        )

    def test_acceptance_ledger_points_real_gates_at_missing_runtime_bundle(self) -> None:
        payload = json.loads(
            (ROOT / "config" / "project-acceptance.json").read_text(encoding="utf-8")
        )
        rows = {row["id"]: row for row in payload["requirements"]}
        for identity in (
            "fab-complete-inventory",
            "sarwa-wealth-accounts",
            "adcb-closed-zero",
            "actual-ui-api-parity",
            "wealth-net-worth",
        ):
            row = rows[identity]
            self.assertIn("verify-wealth-acceptance.py", row["verifier"]["command"])
            self.assertIn("runtime/audit/wealth-acceptance-evidence.json", row["verifier"]["command"])
            self.assertNotIn(row["status"], {"VERIFIED", "PRODUCTION"})
            self.assertTrue(row["blockers"])
            self.assertEqual(row["evidence"], [])


if __name__ == "__main__":
    unittest.main()
