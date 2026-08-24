import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERIC_BASE_DIGEST = "sha256:5b29937c5cfdb906e583706c7da5b72e4137532065a10bbf91dc7e74f03a40a6"
GENERIC_SOURCE_COMMIT = "9bd6b55e88deade27591080e14f1a7c4bdc9808b"


class N8nCustomImageTests(unittest.TestCase):
    def test_finance_inherits_approved_generic_platform_image_without_duplicate_patch(self):
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        base_reference = (ROOT / "packages/n8n-nodes-finance/base-image.txt").read_text(encoding="utf-8").strip()
        provenance = json.loads(
            (ROOT / "packages/n8n-nodes-finance/base-image-provenance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(base_reference, provenance["reference"])
        self.assertEqual(provenance["digest"], GENERIC_BASE_DIGEST)
        self.assertEqual(provenance["source_repository"], "https://github.com/srobroek/n8n")
        self.assertEqual(provenance["source_commit"], GENERIC_SOURCE_COMMIT)
        self.assertEqual(base_reference.rsplit("@", 1)[1], GENERIC_BASE_DIGEST)
        self.assertIn("ARG N8N_BASE_IMAGE=" + base_reference, dockerfile)
        self.assertIn("FROM ${N8N_BASE_IMAGE}", dockerfile)
        self.assertIn('io.finance.n8n.base-source="https://github.com/srobroek/n8n@' + GENERIC_SOURCE_COMMIT, dockerfile)
        for duplicate_marker in (
            "nodemailer-security-patch",
            "npm pack nodemailer",
            "nodemailer@8.0.10",
            "nodemailer@9.0.1",
            "nodemailer-smoke",
        ):
            self.assertNotIn(duplicate_marker, dockerfile)
        self.assertIn("AS node-builder", dockerfile)
        self.assertIn("/opt/finance-n8n/custom-extensions/n8n-nodes-finance", dockerfile)
        self.assertIn("/opt/finance-n8n/community-extensions", dockerfile)

    def test_finance_extension_is_immutable_and_outside_persistent_state(self):
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        base_image = (ROOT / "packages/n8n-nodes-finance/base-image.txt").read_text(encoding="utf-8").strip()
        self.assertIn("/opt/finance-n8n/custom-extensions/n8n-nodes-finance", dockerfile)
        self.assertNotIn("/home/node/.n8n/nodes/node_modules/n8n-nodes-finance", dockerfile)
        self.assertNotIn("ENV N8N_CUSTOM_EXTENSIONS", dockerfile)
        self.assertIn("ARG N8N_BASE_IMAGE", dockerfile)
        self.assertIn(f"ARG N8N_BASE_IMAGE={base_image}", dockerfile)
        self.assertIn("FROM ${N8N_BASE_IMAGE}", dockerfile)
        self.assertIn('ENTRYPOINT ["tini", "--", "/opt/finance-n8n/finance-entrypoint.sh"]', dockerfile)

    def test_finance_image_builder_uses_only_immutable_base_and_writes_external_receipt(self):
        builder = (ROOT / "packages/n8n-nodes-finance/scripts/build-finance-n8n-image.sh").read_text(encoding="utf-8")
        receipt = json.loads(
            (ROOT / "packages/n8n-nodes-finance/finance-image-build-receipt.json").read_text(encoding="utf-8")
        )
        self.assertIn("N8N_BASE_IMAGE", builder)
        self.assertIn("FINANCE_SOURCE_COMMIT", builder)
        self.assertIn("FINANCE_BASE_IMAGE_MUST_BE_IMMUTABLE", builder)
        self.assertIn("FINANCE_BASE_IMAGE_PROVENANCE_MISSING", builder)
        self.assertIn("FINANCE_BASE_IMAGE_PROVENANCE_MISMATCH", builder)
        self.assertIn("FINANCE_BASE_SOURCE_COMMIT", builder)
        self.assertIn("FINANCE_RUNTIME_RECEIPT_MUST_BE_EXTERNAL", builder)
        self.assertIn("FINANCE_SOURCE_TREE_MUST_BE_CLEAN", builder)
        self.assertIn("${TMPDIR:-/tmp}/finance-n8n-image-build-receipt.json", builder)
        self.assertNotIn('receipt="${package_dir}/finance-image-build-receipt.json"', builder)
        self.assertNotIn("docker push", builder)
        self.assertEqual(receipt["status"], "SPEC_ONLY")
        self.assertIsNone(receipt["image"]["image_digest"])
        self.assertEqual(
            receipt["image"]["local_image_id"],
            "sha256:4b44e25305c0ee39aada1993ca57dc24e4f3198245ef1347fb8d3e23ad084bb6",
        )
        self.assertEqual(receipt["base_image"]["digest"], GENERIC_BASE_DIGEST)
        self.assertEqual(receipt["base_image"]["source_commit"], GENERIC_SOURCE_COMMIT)
        self.assertEqual(
            receipt["base_image"]["source_repository"],
            "https://github.com/srobroek/n8n",
        )
        self.assertEqual(receipt["attestation"]["status"], "VERIFIED")
        self.assertEqual(
            receipt["blockers"],
            ["LIVE_REGISTRY_DIGEST_REQUIRED", "DISPOSABLE_IMAGE_IMPORT_REQUIRED"],
        )

    def test_package_test_does_not_rebuild_production_output(self):
        package = json.loads((ROOT / "packages/n8n-nodes-finance/package.json").read_text(encoding="utf-8"))
        self.assertNotIn("npm run build", package["scripts"]["test"])
        self.assertEqual(package["scripts"]["prepack"], "npm run build")

    def test_runtime_assertion_requires_all_reviewed_types(self):
        assertion = (ROOT / "packages/n8n-nodes-finance/scripts/assert-runtime-registration.cjs").read_text(
            encoding="utf-8"
        )
        for node_type in (
            "n8n-nodes-finance.actualBudget",
            "n8n-nodes-finance.financePdf",
            "n8n-nodes-finance.financeRules",
            "n8n-nodes-finance.financeStatement",
            "n8n-nodes-prodex.prodex",
            "n8n-nodes-prodex.prodexChatModel",
            "n8n-nodes-prodex.prodexSetup",
        ):
            self.assertIn(node_type, assertion)
        for credential_type in ("actualBudgetApi", "financeStatementPassword", "prodexAuthApi"):
            self.assertIn(credential_type, assertion)
        self.assertIn("FINANCE_CUSTOM_DIRECTORY_NAMESPACE_FORBIDDEN", assertion)
        self.assertIn("FINANCE_EXTENSION_LINK_TARGET_MISMATCH", assertion)
        self.assertIn("n8n-nodes-prodex", assertion)
        self.assertNotIn("claude", assertion.lower())
        self.assertIn("FINANCE_NODE_NOT_REGISTERED", assertion)
        self.assertIn("FINANCE_CREDENTIAL_NOT_REGISTERED", assertion)

    def test_entrypoint_is_idempotent_and_rejects_mutable_substitution(self):
        entrypoint = (ROOT / "packages/n8n-nodes-finance/scripts/finance-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("ln -s", entrypoint)
        self.assertIn("readlink", entrypoint)
        self.assertIn("FINANCE_EXTENSION_MUTABLE_PATH_REJECTED", entrypoint)
        self.assertNotIn("cp ", entrypoint)
        verifier = (ROOT / "packages/n8n-nodes-finance/scripts/verify-immutable-extension.cjs").read_text(encoding="utf-8")
        self.assertIn("FINANCE_EXTENSION_VERSION_MISMATCH", verifier)
        self.assertIn("FINANCE_EXTENSION_TREE_HASH_MISMATCH", verifier)

    def test_ci_registration_smoke_uses_initialized_persistent_state(self):
        workflow = (ROOT / ".github/workflows/phase1-finance-artifacts.yml").read_text(encoding="utf-8")
        first_start = 'docker run --rm -v "$state_dir:/home/node/.n8n"'
        registration = "/opt/finance-n8n/assert-runtime-registration.cjs export:nodes"
        self.assertIn("finance extension registration verified: 7 nodes, 3 credentials", workflow)
        self.assertIn("--entrypoint node", workflow)
        self.assertIn('-v "$state_dir:/home/node/.n8n"', workflow)
        self.assertLess(workflow.index(first_start), workflow.index(registration))

    def test_community_ai_closure_is_exact_audited_and_hardened(self):
        package = json.loads(
            (ROOT / "packages/n8n-nodes-finance/community-ai/package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(package["dependencies"]["n8n-nodes-prodex"], "0.5.1")
        self.assertEqual(package["overrides"], {"nanoid": "3.3.18", "uuid": "11.1.1"})
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        self.assertNotIn("claude", dockerfile.lower())
        lock = json.loads(
            (ROOT / "packages/n8n-nodes-finance/community-ai/package-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            lock["packages"]["node_modules/n8n-nodes-prodex"]["integrity"],
            "sha512-T3Wmr2vl/jnTDFHXDwTVhMvrWf7oU5VEtyLGPzKSJn4t/XsroYdpVuKOItiYmT0c54yyzepz+Q487ZL+nah7EQ==",
        )
        self.assertNotIn("node_modules/@ggomez91npm/n8n-nodes-claude-code", lock["packages"])
        hardener = (ROOT / "packages/n8n-nodes-finance/scripts/harden-community-ai.cjs").read_text(
            encoding="utf-8"
        )
        for marker in (
            "FINANCE_PRODEX_OPERATION_BLOCKED",
            "FINANCE_PRODEX_TOKEN_CREDENTIAL_BLOCKED",
            "FINANCE_PRODEX_SKILLS_BLOCKED",
            "FINANCE_PRODEX_OUTPUT_SCHEMA_REQUIRED",
            "FINANCE_PRODEX_CHAT_MODEL_BLOCKED_USE_SCHEMA_NODE",
            "FINANCE_PRODEX_SETUP_DISABLED_USE_MOUNTED_LOGIN",
        ):
            self.assertIn(marker, hardener)
        self.assertFalse((ROOT / "packages/n8n-nodes-finance/scripts/claude-finance-wrapper.cjs").exists())


if __name__ == "__main__":
    unittest.main()
