from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.automation_manifest import audit_automations, load_automation_manifest


ROOT = Path(__file__).resolve().parents[1]


def _toml(row: dict[str, object]) -> str:
    fields = (
        "id",
        "kind",
        "name",
        "prompt",
        "status",
        "rrule",
        "model",
        "reasoning_effort",
        "notification_policy",
    )
    return "version = 1\n" + "\n".join(
        f"{field} = {json.dumps(row[field], ensure_ascii=False)}" for field in fields
    ) + "\n"


class AutomationManifestTests(unittest.TestCase):
    def test_repository_manifest_is_valid_and_runbooks_exist(self) -> None:
        manifest = load_automation_manifest(
            ROOT / "config" / "codex-automations.json",
            ROOT,
        )

        self.assertEqual(len(manifest["automations"]), 3)
        self.assertEqual(
            next(row for row in manifest["automations"] if row["id"] == "rakbank-morning-cashback-scan")["rrule"],
            "FREQ=DAILY;BYHOUR=8;BYMINUTE=5",
        )

    def test_exact_installed_automations_pass(self) -> None:
        manifest = load_automation_manifest(ROOT / "config" / "codex-automations.json", ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            automation_root = Path(temporary)
            for row in manifest["automations"]:
                directory = automation_root / str(row["id"])
                directory.mkdir()
                (directory / "automation.toml").write_text(_toml(row), encoding="utf-8")

            result = audit_automations(manifest, automation_root)

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.expected_count, 3)
            self.assertEqual(result.actual_count, 3)
            self.assertEqual(result.drift, ())

    def test_schedule_model_prompt_and_extra_drift_are_reported(self) -> None:
        manifest = load_automation_manifest(ROOT / "config" / "codex-automations.json", ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            automation_root = Path(temporary)
            for row in manifest["automations"]:
                installed = dict(row)
                if row["id"] == "rakbank-morning-cashback-scan":
                    installed["rrule"] = "FREQ=DAILY;BYHOUR=20;BYMINUTE=5"
                    installed["model"] = "gpt-5.6-sol"
                    installed["prompt"] = "stale prompt"
                directory = automation_root / str(row["id"])
                directory.mkdir()
                (directory / "automation.toml").write_text(_toml(installed), encoding="utf-8")
            extra = automation_root / "unexpected"
            extra.mkdir()
            (extra / "automation.toml").write_text(
                _toml({
                    **manifest["automations"][0],
                    "id": "unexpected",
                    "name": "Unexpected",
                }),
                encoding="utf-8",
            )

            result = audit_automations(manifest, automation_root)

            self.assertEqual(result.status, "drift")
            self.assertEqual(result.extra, ("unexpected",))
            self.assertEqual(
                {item["field"] for item in result.drift},
                {"rrule", "model", "prompt"},
            )

    def test_missing_runbook_is_rejected(self) -> None:
        source = json.loads((ROOT / "config" / "codex-automations.json").read_text(encoding="utf-8"))
        source["automations"][0]["runbook"] = "agents/automations/missing.md"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(source), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "runbook does not exist"):
                load_automation_manifest(path, ROOT)


if __name__ == "__main__":
    unittest.main()
