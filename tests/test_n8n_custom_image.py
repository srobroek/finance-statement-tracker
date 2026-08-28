import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class N8nCustomImageTests(unittest.TestCase):
    def test_finance_extension_is_immutable_and_outside_persistent_state(self):
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        self.assertIn("/opt/finance-n8n/custom-extensions/n8n-nodes-finance", dockerfile)
        self.assertNotIn("/home/node/.n8n/nodes/node_modules/n8n-nodes-finance", dockerfile)
        self.assertNotIn("ENV N8N_CUSTOM_EXTENSIONS", dockerfile)
        self.assertIn('ENTRYPOINT ["tini", "--", "/opt/finance-n8n/finance-entrypoint.sh"]', dockerfile)

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
        self.assertEqual(package["dependencies"], {"n8n-nodes-prodex": "0.5.1"})
        self.assertEqual(package["overrides"], {"nanoid": "3.3.18", "uuid": "11.1.1"})
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        self.assertIn("ab8bdd84372cb54955930722db668f878865b86aa3520117ad92c4febe1af2a3", dockerfile)
        self.assertIn("sha256sum -c -", dockerfile)
        self.assertNotIn("ADD --checksum", dockerfile)
        lock = json.loads(
            (ROOT / "packages/n8n-nodes-finance/community-ai/package-lock.json").read_text(encoding="utf-8")
        )
        self.assertEqual(lock["packages"][""]["dependencies"], {"n8n-nodes-prodex": "0.5.1"})
        self.assertEqual(
            lock["packages"]["node_modules/n8n-nodes-prodex"]["integrity"],
            "sha512-T3Wmr2vl/jnTDFHXDwTVhMvrWf7oU5VEtyLGPzKSJn4t/XsroYdpVuKOItiYmT0c54yyzepz+Q487ZL+nah7EQ==",
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
        ):
            self.assertIn(marker, hardener)


if __name__ == "__main__":
    unittest.main()
