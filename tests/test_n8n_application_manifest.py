from __future__ import annotations

import hashlib
import json
import pathlib
import unittest
from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker


ROOT = pathlib.Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
MANIFEST_PATH = N8N / "application-manifest.json"
MANIFEST_SCHEMA_PATH = N8N / "application-manifest.schema.json"
IMAGE_LOCK_PATH = N8N / "application-images.lock.json"
IMAGE_LOCK_SCHEMA_PATH = N8N / "application-images.lock.schema.json"


def load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def corpus_sha256(directory: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.json")):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def schema_errors(document: dict, schema: dict) -> list:
    return sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=str,
    )


class N8nApplicationManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_json(MANIFEST_PATH)
        cls.manifest_schema = load_json(MANIFEST_SCHEMA_PATH)
        cls.image_lock = load_json(IMAGE_LOCK_PATH)
        cls.image_lock_schema = load_json(IMAGE_LOCK_SCHEMA_PATH)

    def test_manifest_and_image_lock_match_strict_schemas(self) -> None:
        for document, schema in (
            (self.manifest, self.manifest_schema),
            (self.image_lock, self.image_lock_schema),
        ):
            errors = schema_errors(document, schema)
            self.assertEqual(errors, [], "schema errors: " + "; ".join(error.message for error in errors))

    def test_spec_only_schemas_reject_unverified_or_unexpected_fields(self) -> None:
        manifest_with_commit = deepcopy(self.manifest)
        manifest_with_commit["finance_commit"] = "0" * 40
        self.assertTrue(schema_errors(manifest_with_commit, self.manifest_schema))

        lock_with_scan = deepcopy(self.image_lock)
        lock_with_scan["extension_image"]["scan"]["result"] = "PASS"
        self.assertTrue(schema_errors(lock_with_scan, self.image_lock_schema))

        lock_with_extra_field = deepcopy(self.image_lock)
        lock_with_extra_field["unexpected"] = True
        self.assertTrue(schema_errors(lock_with_extra_field, self.image_lock_schema))

    def test_manifest_binds_checked_in_source_artifacts(self) -> None:
        for key in ("image_lock", "workflow_manifest", "fixture_manifest"):
            artifact = self.manifest[key]
            self.assertEqual(artifact["sha256"], sha256(ROOT / artifact["path"]))
        corpus = self.manifest["inactive_corpus"]
        self.assertEqual(corpus["file_count"], len(list((N8N / "workflows").glob("*.json"))))
        self.assertEqual(corpus["sha256"], corpus_sha256(N8N / "workflows"))
        self.assertTrue(corpus["required_state"]["active"] is False)
        self.assertTrue(corpus["required_state"]["published"] is False)

    def test_manifest_binds_immutable_base_and_receipt(self) -> None:
        base_path = ROOT / self.manifest["base_image"]["path"]
        base_reference = base_path.read_text(encoding="utf-8").strip()
        self.assertEqual(self.manifest["base_image"]["reference"], base_reference)
        self.assertEqual(self.manifest["base_image"]["digest"], base_reference.rsplit("@", 1)[1])
        self.assertEqual(self.image_lock["base_image"]["reference"], base_reference)
        self.assertEqual(self.image_lock["base_image"]["digest"], base_reference.rsplit("@", 1)[1])
        self.assertEqual(
            self.manifest["extension_image"]["base_digest"],
            self.image_lock["extension_image"]["base_image_digest"],
        )
        self.assertEqual(self.manifest["extension_image"]["base_digest"], self.manifest["base_image"]["digest"])
        receipt = self.manifest["extension_image"]["receipt"]
        self.assertEqual(receipt["sha256"], sha256(ROOT / receipt["path"]))
        self.assertIsNone(self.manifest["finance_commit"])
        self.assertIsNone(self.manifest["extension_image"]["digest"])
        self.assertEqual(self.manifest["contract_status"], "SPEC_ONLY")

    def test_image_lock_binds_package_and_community_integrity(self) -> None:
        package = self.image_lock["finance_package"]
        self.assertEqual(package["lockfile_sha256"], sha256(ROOT / package["lockfile"]))
        community = self.image_lock["community_node_lock"]
        self.assertEqual(community["sha256"], sha256(ROOT / community["path"]))
        registration = load_json(ROOT / community["path"])["registration"]
        self.assertEqual(registration["status"], "SPEC_ONLY")
        self.assertEqual(
            set(registration["nodes"]),
            {
                "n8n-nodes-prodex.prodex",
                "n8n-nodes-prodex.prodexChatModel",
                "n8n-nodes-prodex.prodexSetup",
                "@ggomez91npm/n8n-nodes-claude-code.claude",
            },
        )

    def test_credential_bindings_are_placeholders_without_secret_fields(self) -> None:
        credentials = self.manifest["credentials"]
        self.assertFalse(credentials["values_included"])
        self.assertIn("id", credentials["forbidden_fields"])
        self.assertIn("token", credentials["forbidden_fields"])
        for placeholder in credentials["placeholders"]:
            self.assertRegex(placeholder["binding"], r"^BIND_[A-Z0-9_]+$")
            self.assertNotIn("id", placeholder)
            self.assertNotIn("value", placeholder)
            self.assertNotIn("secret", placeholder)

    def test_workflow_registry_and_fixture_manifest_remain_inactive(self) -> None:
        registry = load_json(N8N / "pipeline-registry.json")
        self.assertEqual(len(registry["workflows"]), self.manifest["inactive_corpus"]["file_count"])
        self.assertTrue(all(row["status"] == "SPEC_ONLY" for row in registry["workflows"]))
        self.assertTrue(all(load_json(path).get("active") is False for path in (N8N / "workflows").glob("*.json")))
        fixture_manifest = load_json(N8N / "disposable" / "fixture-manifest.json")
        self.assertTrue(fixture_manifest["production_import_forbidden"])
        self.assertEqual(fixture_manifest["contract_status"], "DISPOSABLE_ONLY")


if __name__ == "__main__":
    unittest.main()
