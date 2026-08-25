from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
SOURCE = N8N / "application-manifest.json"
SCHEMA = N8N / "schemas" / "runtime-bound-application-v1.schema.json"
CONVERTER_PATH = N8N / "setup-workflows" / "runner" / "convert_microsoft_oauth_runtime_manifest.py"


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("finance_runtime_manifest_converter", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeManifestConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.converter = load_module(CONVERTER_PATH)
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        cls.source_bytes = SOURCE.read_bytes()
        cls.source_sha256 = hashlib.sha256(cls.source_bytes).hexdigest()
        cls.ids = {
            "n8n-nodes-base.microsoftOutlook": "outlookCredential123",
            "n8n-nodes-base.microsoftOneDrive": "onedriveCredential123",
        }

    def convert(self, root: Path, destination: Path) -> Path:
        return self.converter.convert_manifest(
            SOURCE,
            destination,
            "a" * 40,
            self.ids,
            source_root=root,
            expected_source_manifest_sha256=self.source_sha256,
        )

    def test_conversion_is_deterministic_schema_valid_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_path = self.convert(ROOT, Path(first) / "runtime-bound.json")
            second_path = self.convert(ROOT, Path(second) / "runtime-bound.json")
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(
                (first_path.parent / "runtime-bound.runtime/23-microsoft-oauth-refresh-proof.json").read_bytes(),
                (second_path.parent / "runtime-bound.runtime/23-microsoft-oauth-refresh-proof.json").read_bytes(),
            )
            manifest = json.loads(first_path.read_text(encoding="utf-8"))
            errors = list(Draft202012Validator(self.schema).iter_errors(manifest))
            self.assertEqual(errors, [], "; ".join(error.message for error in errors))
            self.assertEqual(manifest["status"], "RUNTIME_BOUND")
            self.assertFalse(manifest["retained_state_replaced"])
            self.assertFalse(manifest["production_mutation"])
            self.assertNotIn("outlookCredential123", first_path.read_text(encoding="utf-8"))
            self.assertNotIn("onedriveCredential123", first_path.read_text(encoding="utf-8"))
            self.assertEqual(first_path.stat().st_mode & 0o777, 0o600)

    def test_conversion_binds_exact_provider_ids_only_in_runtime_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = self.convert(ROOT, Path(temporary) / "runtime-bound.json")
            workflow = json.loads(
                (destination.parent / "runtime-bound.runtime/23-microsoft-oauth-refresh-proof.json").read_text(
                    encoding="utf-8"
                )
            )
            nodes = {node["type"]: node for node in workflow["nodes"]}
            self.assertEqual(
                nodes["n8n-nodes-base.microsoftOutlook"]["credentials"]["microsoftOutlookOAuth2Api"]["id"],
                "outlookCredential123",
            )
            self.assertEqual(
                nodes["n8n-nodes-base.microsoftOneDrive"]["credentials"]["microsoftOneDriveOAuth2Api"]["id"],
                "onedriveCredential123",
            )
            receipt = destination.parent / "runtime-bound.runtime/binding-receipt.json"
            self.assertNotIn("outlookCredential123", receipt.read_text(encoding="utf-8"))
            self.assertNotIn("onedriveCredential123", receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
            self.assertEqual(SOURCE.read_bytes(), self.source_bytes)

    def test_conversion_rejects_stale_source_and_missing_or_malformed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            stale = temporary / "application-manifest.json"
            source = json.loads(SOURCE.read_text(encoding="utf-8"))
            source["inactive_corpus"]["file_count"] = 18
            stale.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "SOURCE_MANIFEST_STALE"):
                self.converter.convert_manifest(
                    stale,
                    temporary / "stale-output.json",
                    "a" * 40,
                    self.ids,
                    source_root=ROOT,
                    expected_source_manifest_sha256=self.source_sha256,
                )
            missing = deepcopy(self.ids)
            del missing["n8n-nodes-base.microsoftOneDrive"]
            with self.assertRaisesRegex(SystemExit, "EXACT_MICROSOFT_CREDENTIAL_IDS_REQUIRED"):
                self.converter.convert_manifest(
                    SOURCE,
                    temporary / "missing-output.json",
                    "a" * 40,
                    missing,
                    source_root=ROOT,
                    expected_source_manifest_sha256=self.source_sha256,
                )
            malformed = dict(self.ids)
            malformed["n8n-nodes-base.microsoftOutlook"] = "id with spaces"
            with self.assertRaisesRegex(SystemExit, "EXACT_MICROSOFT_CREDENTIAL_IDS_REQUIRED"):
                self.converter.convert_manifest(
                    SOURCE,
                    temporary / "malformed-output.json",
                    "a" * 40,
                    malformed,
                    source_root=ROOT,
                    expected_source_manifest_sha256=self.source_sha256,
                )

            duplicate = dict(self.ids)
            duplicate["n8n-nodes-base.microsoftOneDrive"] = duplicate["n8n-nodes-base.microsoftOutlook"]
            with self.assertRaisesRegex(SystemExit, "EXACT_MICROSOFT_CREDENTIAL_IDS_REQUIRED"):
                self.converter.convert_manifest(
                    SOURCE,
                    temporary / "duplicate-output.json",
                    "a" * 40,
                    duplicate,
                    source_root=ROOT,
                    expected_source_manifest_sha256=self.source_sha256,
                )

    def test_conversion_requires_protected_manifest_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            SystemExit, "EXPECTED_SOURCE_MANIFEST_SHA256_REQUIRED"
        ):
            self.converter.convert_manifest(
                SOURCE,
                Path(temporary) / "runtime-bound.json",
                "a" * 40,
                self.ids,
                source_root=ROOT,
                expected_source_manifest_sha256=None,
            )

    def test_cli_requires_protected_manifest_digest(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["convert_microsoft_oauth_runtime_manifest.py", str(SOURCE), "output", "--finance-commit", "a" * 40],
        ), self.assertRaises(SystemExit):
            self.converter.parse_args()

    def test_conversion_rejects_manifest_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            SystemExit, "SOURCE_MANIFEST_SHA256_MISMATCH"
        ):
            self.converter.convert_manifest(
                SOURCE,
                Path(temporary) / "runtime-bound.json",
                "a" * 40,
                self.ids,
                source_root=ROOT,
                expected_source_manifest_sha256="0" * 64,
            )


if __name__ == "__main__":
    unittest.main()
