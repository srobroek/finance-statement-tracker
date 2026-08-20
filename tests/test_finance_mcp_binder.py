from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINDER_PATH = ROOT / "deploy" / "finance-runtime" / "bind-finance-mcp-facade.py"
WORKFLOW = ROOT / "integrations" / "n8n" / "workflows" / "15-finance-mcp-facade.json"


def load_binder():
    spec = importlib.util.spec_from_file_location("finance_mcp_binder", BINDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FinanceMcpBinderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binder = load_binder()

    def test_uuid5_identity_is_stable_and_internal(self):
        first = self.binder.deterministic_credential_id()
        second = self.binder.deterministic_credential_id()
        self.assertEqual(first, second)
        self.assertNotIn(first, json.dumps({"status": "VERIFIED", "ids": "REDACTED"}))

    def test_export_is_one_encrypted_bearer_shape(self):
        export = self.binder.credential_export("unit-test-secret")
        self.assertEqual(len(export), 1)
        self.assertEqual(export[0]["type"], "httpBearerAuth")
        self.assertEqual(export[0]["name"], "Finance MCP Facade Bearer")
        self.assertEqual(export[0]["data"], {"token": "unit-test-secret"})

    def test_bound_workflow_changes_only_placeholder(self):
        credential_id = self.binder.deterministic_credential_id()
        original = json.loads(WORKFLOW.read_text())
        bound = self.binder.bound_workflow(WORKFLOW, credential_id)
        original["nodes"][0]["credentials"]["httpBearerAuth"]["id"] = credential_id
        self.assertEqual(json.dumps(bound, sort_keys=True), json.dumps(original, sort_keys=True))
        self.assertFalse(bound["active"])
        self.assertIsNone(bound.get("activeVersionId"))

    def test_private_file_writer_uses_mode_0600_and_exclusive_create(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "credential.json"
            self.binder._write_private(target, b"redacted-test-payload")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                self.binder._write_private(target, b"second-payload")

    def test_secret_is_required_from_runtime_environment(self):
        old = os.environ.pop(self.binder.ENVIRONMENT_NAME, None)
        try:
            with self.assertRaises(self.binder.ContractError):
                self.binder._secret(os.environ.get(self.binder.ENVIRONMENT_NAME))
        finally:
            if old is not None:
                os.environ[self.binder.ENVIRONMENT_NAME] = old

    def test_import_commands_pin_project_inactive_state_and_cleanup(self):
        source = BINDER_PATH.read_text()
        self.assertIn('"import:credentials"', source)
        self.assertIn('"import:workflow"', source)
        self.assertIn('"--activeState=false"', source)
        self.assertIn('f"--projectId={args.project_id}"', source)
        self.assertIn("credential:owner", source)
        self.assertIn("PINNED_N8N_VERSION", source)
        self.assertIn("os.environ.pop(ENVIRONMENT_NAME, None)", source)

    def test_wrong_secret_and_newline_fail_closed(self):
        for value in ("", "secret\nvalue"):
            with self.assertRaises(self.binder.ContractError):
                self.binder._secret(value)


if __name__ == "__main__":
    unittest.main()
