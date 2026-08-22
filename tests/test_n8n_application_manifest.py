from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest
from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from integrations.n8n.generate_credential_bindings import (
    build_contract,
    validate_current,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
MANIFEST_PATH = N8N / "application-manifest.json"
MANIFEST_SCHEMA_PATH = N8N / "application-manifest.schema.json"
BINDINGS_PATH = N8N / "credential-bindings.json"
BINDINGS_SCHEMA_PATH = N8N / "credential-bindings.schema.json"
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


def verified_image_lock(document: dict, status: str) -> dict:
    verified = deepcopy(document)
    source_commit = "a" * 40
    image_digest = "sha256:" + "b" * 64
    verified.update(
        status=status,
        generated_at_utc="2026-08-20T00:00:00Z",
        source_commit=source_commit,
    )
    extension = verified["extension_image"]
    extension.update(
        reference="ghcr.io/srobroek/finance-n8n@" + image_digest,
        image_digest=image_digest,
        source_commit=source_commit,
        sbom_sha256="c" * 64,
        scan_sha256="d" * 64,
        scan={"tool": "trivy", "result": "PASS", "high": 0, "critical": 0},
        attestation={
            "type": "https://in-toto.io/Statement/v1",
            "predicate_type": "https://slsa.dev/provenance/v1",
            "subject_digest": image_digest,
            "source_commit": source_commit,
            "sha256": "e" * 64,
            "status": "VERIFIED",
        },
    )
    return verified


def verified_manifest(document: dict, status: str) -> dict:
    verified = deepcopy(document)
    verified["contract_status"] = status
    verified["finance_commit"] = "a" * 40
    verified["extension_image"].update(
        reference="ghcr.io/srobroek/finance-n8n@sha256:" + "b" * 64,
        digest="sha256:" + "b" * 64,
    )
    return verified


class N8nApplicationManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_json(MANIFEST_PATH)
        cls.manifest_schema = load_json(MANIFEST_SCHEMA_PATH)
        cls.bindings = load_json(BINDINGS_PATH)
        cls.bindings_schema = load_json(BINDINGS_SCHEMA_PATH)
        cls.image_lock = load_json(IMAGE_LOCK_PATH)
        cls.image_lock_schema = load_json(IMAGE_LOCK_SCHEMA_PATH)

    def test_manifest_and_image_lock_match_strict_schemas(self) -> None:
        for document, schema in (
            (self.manifest, self.manifest_schema),
            (self.image_lock, self.image_lock_schema),
        ):
            errors = schema_errors(document, schema)
            self.assertEqual(errors, [], "schema errors: " + "; ".join(error.message for error in errors))

    def test_locked_schemas_reject_unverified_or_unexpected_fields(self) -> None:
        manifest_with_commit = deepcopy(self.manifest)
        manifest_with_commit["finance_commit"] = None
        self.assertTrue(schema_errors(manifest_with_commit, self.manifest_schema))

        lock_with_scan = deepcopy(self.image_lock)
        lock_with_scan["extension_image"]["scan"]["result"] = "NOT_RUN"
        self.assertTrue(schema_errors(lock_with_scan, self.image_lock_schema))

        lock_with_extra_field = deepcopy(self.image_lock)
        lock_with_extra_field["unexpected"] = True
        self.assertTrue(schema_errors(lock_with_extra_field, self.image_lock_schema))

    def test_verified_disposable_and_production_fixtures_match_both_schemas(self) -> None:
        for status in ("LOCKED_DISPOSABLE", "LOCKED_PRODUCTION"):
            self.assertEqual(
                schema_errors(verified_image_lock(self.image_lock, status), self.image_lock_schema),
                [],
            )
        for status in ("DISPOSABLE_VERIFIED", "PRODUCTION_VERIFIED"):
            self.assertEqual(
                schema_errors(verified_manifest(self.manifest, status), self.manifest_schema),
                [],
            )

    def test_verified_fixtures_reject_tag_references_and_missing_digests(self) -> None:
        lock_with_tag = verified_image_lock(self.image_lock, "LOCKED_DISPOSABLE")
        lock_with_tag["extension_image"]["reference"] = "ghcr.io/srobroek/finance-n8n:verified"
        self.assertTrue(schema_errors(lock_with_tag, self.image_lock_schema))

        manifest_without_digest = verified_manifest(self.manifest, "PRODUCTION_VERIFIED")
        manifest_without_digest["extension_image"]["digest"] = None
        self.assertTrue(schema_errors(manifest_without_digest, self.manifest_schema))

    def test_manifest_binds_checked_in_source_artifacts(self) -> None:
        for key in ("image_lock", "workflow_manifest", "fixture_manifest"):
            artifact = self.manifest[key]
            self.assertEqual(artifact["sha256"], sha256(ROOT / artifact["path"]))
        self.assertEqual(
            self.manifest["mcp"]["contract"]["sha256"],
            sha256(ROOT / self.manifest["mcp"]["contract"]["path"]),
        )
        self.assertEqual(
            self.manifest["mcp"]["path"], "/mcp/finance-operations-v1"
        )
        binding_contract = self.manifest["credentials"]["binding_contract"]
        self.assertEqual(binding_contract["path"], "integrations/n8n/credential-bindings.json")
        self.assertEqual(binding_contract["sha256"], sha256(ROOT / binding_contract["path"]))
        self.assertEqual(
            self.manifest["mcp"]["operations"],
            [
                "finance.status.v1",
                "finance.document.request.v1",
                "finance.reviewed-artifact.handoff.v1",
            ],
        )
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
        self.assertEqual(
            self.manifest["finance_commit"],
            "170328b7f6aefa068da9c9a864cd03ca91635c70",
        )
        self.assertEqual(
            self.manifest["extension_image"]["digest"],
            "sha256:5452c78e52ac7053bc6f1d21877ece89b5f26e85eeee63d1ecd33d4b5d26d696",
        )
        self.assertEqual(self.manifest["contract_status"], "DISPOSABLE_VERIFIED")

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

    def test_credential_binding_contract_is_current_and_schema_valid(self) -> None:
        errors = schema_errors(self.bindings, self.bindings_schema)
        self.assertEqual(errors, [], "schema errors: " + "; ".join(error.message for error in errors))
        self.assertEqual(self.bindings["workflow_code_metadata_key"], "financeWorkflowCode")
        self.assertNotIn("workflow_code_metadata_key", self.manifest["credentials"])
        self.assertEqual(self.bindings, build_contract())
        validate_current(self.bindings)

        w11 = next(row for row in self.bindings["bindings"] if row["placeholder"] == "BIND_ONEDRIVE")
        self.assertIn(
            {
                "workflow": {
                    "code": "INTERACTIVE_ARTIFACT_HANDOFF",
                    "file": "11-interactive-artifact-handoff.json",
                    "id": "10000000-0000-4000-8000-000000000011",
                },
                "node": {"id": "11007", "name": "Download Archived Browser Capture"},
            },
            w11["nodes"],
        )

    def test_generic_verifier_uses_declared_finance_workflow_metadata_key(self) -> None:
        key = self.bindings["workflow_code_metadata_key"]
        workflows = [load_json(path) for path in (N8N / "workflows").glob("*.json")]
        self.assertEqual(
            {workflow["meta"].get(key) for workflow in workflows},
            {workflow["meta"]["financeWorkflowCode"] for workflow in workflows},
        )
        self.assertTrue(all(isinstance(workflow["meta"].get(key), str) for workflow in workflows))

    def test_workflow_metadata_contract_rejects_missing_alternate_or_duplicate_declaration(self) -> None:
        missing = deepcopy(self.bindings)
        del missing["workflow_code_metadata_key"]
        self.assertTrue(schema_errors(missing, self.bindings_schema))

        alternate = deepcopy(self.bindings)
        alternate["workflow_code_metadata_key"] = "archiveWorkflowCode"
        self.assertTrue(schema_errors(alternate, self.bindings_schema))

        duplicate = deepcopy(self.manifest)
        duplicate["credentials"]["workflow_code_metadata_key"] = "financeWorkflowCode"
        self.assertTrue(schema_errors(duplicate, self.manifest_schema))

    def test_credential_binding_contract_rejects_omitted_node_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workflow_root = pathlib.Path(temporary) / "workflows"
            workflow_root.mkdir()
            for source in (N8N / "workflows").glob("*.json"):
                (workflow_root / source.name).write_bytes(source.read_bytes())
            w11_path = workflow_root / "11-interactive-artifact-handoff.json"
            w11 = load_json(w11_path)
            next(node for node in w11["nodes"] if node["id"] == "11007").pop("credentials")
            w11_path.write_text(json.dumps(w11, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "CREDENTIAL_BINDING_CONTRACT_DRIFT"):
                validate_current(self.bindings, workflow_root)

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
