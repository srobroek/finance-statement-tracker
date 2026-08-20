import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERIC_BASE_DIGEST = "sha256:a631cd1dcb2b0c8fd609ca480f627193f99a769740c2355cd87dcda2fa9233c9"
GENERIC_SOURCE_COMMIT = "e2579f63f5e16683a45a36b7a58a3b8e99b5a5c7"


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

    def test_finance_image_builder_uses_only_immutable_base_and_writes_spec_receipt(self):
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
        self.assertEqual(receipt["base_image"]["digest"], GENERIC_BASE_DIGEST)
        self.assertEqual(receipt["base_image"]["source_commit"], GENERIC_SOURCE_COMMIT)
        self.assertEqual(
            receipt["base_image"]["source_repository"],
            "https://github.com/srobroek/n8n",
        )
        self.assertIsNone(receipt["image"]["image_digest"])
        self.assertIsNone(receipt["attestation"]["subject_digest"])
        self.assertEqual(receipt["attestation"]["status"], "NOT_AVAILABLE")

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
            "@ggomez91npm/n8n-nodes-claude-code.claude",
        ):
            self.assertIn(node_type, assertion)
        for credential_type in ("actualBudgetApi", "financeStatementPassword", "prodexAuthApi"):
            self.assertIn(credential_type, assertion)
        self.assertIn("FINANCE_CUSTOM_DIRECTORY_NAMESPACE_FORBIDDEN", assertion)
        self.assertIn("FINANCE_EXTENSION_LINK_TARGET_MISMATCH", assertion)
        self.assertIn("n8n-nodes-prodex", assertion)
        self.assertIn("@ggomez91npm/n8n-nodes-claude-code", assertion)
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
        self.assertIn("finance extension registration verified: 8 nodes, 3 credentials", workflow)
        self.assertIn("--entrypoint node", workflow)
        self.assertIn('-v "$state_dir:/home/node/.n8n"', workflow)
        self.assertLess(workflow.index(first_start), workflow.index(registration))

    def test_community_ai_closure_is_exact_audited_and_hardened(self):
        package = json.loads(
            (ROOT / "packages/n8n-nodes-finance/community-ai/package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(package["dependencies"]["n8n-nodes-prodex"], "0.5.1")
        self.assertEqual(package["dependencies"]["@ggomez91npm/n8n-nodes-claude-code"], "0.8.0")
        self.assertEqual(package["dependencies"]["@anthropic-ai/claude-code"], "2.1.235")
        self.assertEqual(package["overrides"], {"nanoid": "3.3.18", "uuid": "11.1.1"})
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        self.assertIn("node_modules/@anthropic-ai/claude-code/install.cjs", dockerfile)
        lock = json.loads(
            (ROOT / "packages/n8n-nodes-finance/community-ai/package-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            lock["packages"]["node_modules/n8n-nodes-prodex"]["integrity"],
            "sha512-T3Wmr2vl/jnTDFHXDwTVhMvrWf7oU5VEtyLGPzKSJn4t/XsroYdpVuKOItiYmT0c54yyzepz+Q487ZL+nah7EQ==",
        )
        self.assertEqual(
            lock["packages"]["node_modules/@ggomez91npm/n8n-nodes-claude-code"]["integrity"],
            "sha512-0Tn5gY3ITdc3Mexz0eUuaxKq8UBp+hRVf0Wsc+zLcMQz1DHwHSszO66xXUkUyVCgewTFhyXlwXcLLstduXLLzA==",
        )
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
            "FINANCE_CLAUDE_BINARY_BOUNDARY_REQUIRED",
        ):
            self.assertIn(marker, hardener)
        wrapper = (ROOT / "packages/n8n-nodes-finance/scripts/claude-finance-wrapper.cjs").read_text(
            encoding="utf-8"
        )
        for flag in ("--no-session-persistence", "--permission-mode", "--tools", "--disallowedTools", "--safe-mode"):
            self.assertIn(flag, wrapper)
        self.assertNotIn("--max-turns", wrapper)
        for forbidden in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CODEX_ACCESS_TOKEN"):
            self.assertNotIn(forbidden, wrapper)


if __name__ == "__main__":
    unittest.main()
