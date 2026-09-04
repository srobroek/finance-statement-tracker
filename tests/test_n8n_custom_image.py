import json
import hashlib
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OFFICIAL_BASE_DIGEST = "sha256:be13ef936c03ce0f2d58426afa06e7f1ba2a1d50e4f19ebf3e8488435bf5e386"
OFFICIAL_SOURCE_COMMIT = "bc9090e8c61d0dc84aa85528e62142dfb7001243"
OVERLAY_SOURCE_COMMIT = "9bd6b55e88deade27591080e14f1a7c4bdc9808b"
NODEMAILER_TARBALL_SHA256 = "ab8bdd84372cb54955930722db668f878865b86aa3520117ad92c4febe1af2a3"
ALPINE_SECURITY_PACKAGES = {
    "libcrypto3": "3.5.8-r0",
    "libssl3": "3.5.8-r0",
    "libexpat": "2.8.4-r0",
    "openssh": "10.3_p1-r1",
    "openssh-client-common": "10.3_p1-r1",
    "openssh-client-default": "10.3_p1-r1",
    "openssh-keygen": "10.3_p1-r1",
    "openssh-server": "10.3_p1-r1",
    "openssh-server-common": "10.3_p1-r1",
    "openssh-sftp-server": "10.3_p1-r1",
}
JAVASCRIPT_SECURITY_PACKAGES = {
    "fast-uri": {
        "version": "3.1.6",
        "integrity": "sha512-7Ical1vFEMr0onbVzEDIreM22I4khW+fzyQPwvAFWBp1iwdshSZRsL4jjRvPG9JP1uiqMHRto+YU6R2/CzDz5Q==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/fast-uri@3.1.5/node_modules/fast-uri",
    },
    "toml": {
        "version": "4.2.0",
        "integrity": "sha512-TvAJjbHZlYmI323+srtqHQFyJsoWy6mI09ppkuj9+iRsqsVKG9fvTcOP7FHF2UCb0QSYtjEavffrKzdd0XgClg==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/toml@3.0.0/node_modules/toml",
    },
}


class N8nCustomImageTests(unittest.TestCase):
    def test_finance_builds_reviewed_nodemailer_overlay_on_official_immutable_base(self):
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        base_reference = (ROOT / "packages/n8n-nodes-finance/base-image.txt").read_text(encoding="utf-8").strip()
        provenance = json.loads(
            (ROOT / "packages/n8n-nodes-finance/base-image-provenance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(base_reference, provenance["reference"])
        self.assertEqual(provenance["digest"], OFFICIAL_BASE_DIGEST)
        self.assertEqual(provenance["source_repository"], "https://github.com/n8n-io/n8n")
        self.assertEqual(provenance["source_commit"], OFFICIAL_SOURCE_COMMIT)
        self.assertEqual(base_reference.rsplit("@", 1)[1], OFFICIAL_BASE_DIGEST)
        self.assertIn("ARG N8N_BASE_IMAGE=" + base_reference, dockerfile)
        self.assertIn("FROM ${N8N_BASE_IMAGE}", dockerfile)
        self.assertIn('org.opencontainers.image.source="https://github.com/srobroek/finance-statement-tracker"', dockerfile)
        self.assertIn('io.finance.n8n.base-source="https://github.com/n8n-io/n8n@' + OFFICIAL_SOURCE_COMMIT, dockerfile)
        overlay = provenance["nodemailer_overlay"]
        self.assertEqual(overlay["source_commit"], OVERLAY_SOURCE_COMMIT)
        self.assertEqual(overlay["tarball_sha256"], NODEMAILER_TARBALL_SHA256)
        self.assertEqual(overlay["smoke_blob"], "cdb2c9c08500e798ab7881818707fdf710709213")
        self.assertIn("npm pack nodemailer@9.0.1", dockerfile)
        self.assertIn(NODEMAILER_TARBALL_SHA256, dockerfile)
        self.assertIn(".pnpm/nodemailer@8.0.10/node_modules/nodemailer", dockerfile)
        self.assertIn("node /tmp/nodemailer-smoke.cjs", dockerfile)
        smoke = ROOT / "packages/n8n-nodes-finance/scripts/nodemailer-smoke.cjs"
        import subprocess
        self.assertEqual(subprocess.check_output(["git", "hash-object", smoke], text=True).strip(), overlay["smoke_blob"])
        self.assertIn("AS node-builder", dockerfile)
        self.assertIn("/opt/finance-n8n/custom-extensions/n8n-nodes-finance", dockerfile)
        self.assertIn("/opt/finance-n8n/community-extensions", dockerfile)

    def test_finance_image_applies_exact_signed_alpine_security_overlay(self):
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        provenance = json.loads(
            (ROOT / "packages/n8n-nodes-finance/base-image-provenance.json").read_text(encoding="utf-8")
        )
        overlay = provenance["os_security_overlay"]
        self.assertEqual(overlay["distribution"], "Alpine Linux 3.24")
        self.assertEqual(
            overlay["builder_image"],
            "public.ecr.aws/docker/library/node:24-alpine@sha256:2a49bdf71e9fd965a58c1703fd9ddd205b34e5782b692a72dd1d248abb0beb43",
        )
        self.assertEqual(overlay["repository"], "https://dl-cdn.alpinelinux.org/alpine/v3.24/main")
        self.assertEqual(overlay["verification"], "Alpine repository signature via apk.static")
        self.assertEqual(overlay["packages"], ALPINE_SECURITY_PACKAGES)
        self.assertIn("AS alpine-security-overlay", dockerfile)
        self.assertIn("apk add --no-cache apk-tools-static", dockerfile)
        self.assertIn("apk --no-cache fetch --output /tmp/security-apks", dockerfile)
        self.assertIn("cp -R /etc/apk/keys /tmp/security-apks/keys", dockerfile)
        self.assertIn(
            "/tmp/security-apks/apk.static --keys-dir /tmp/security-apks/keys add --no-cache --no-network",
            dockerfile,
        )
        self.assertNotIn("--allow-untrusted", dockerfile)
        for package, version in ALPINE_SECURITY_PACKAGES.items():
            self.assertIn(f"{package}={version}", dockerfile)
            self.assertIn(
                f'info -v {package})" = "{package}-{version}"',
                dockerfile,
            )
        self.assertIn("rm -rf /tmp/security-apks", dockerfile)
        self.assertIn(
            'io.finance.n8n.os-security-overlay="alpine-v3.24:openssl-3.5.8-r0,expat-2.8.4-r0,openssh-10.3_p1-r1"',
            dockerfile,
        )

    def test_finance_image_applies_integrity_pinned_javascript_security_overlay(self):
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        provenance = json.loads(
            (ROOT / "packages/n8n-nodes-finance/base-image-provenance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(provenance["javascript_security_overlay"]["packages"], JAVASCRIPT_SECURITY_PACKAGES)
        self.assertIn("AS javascript-security-overlay", dockerfile)
        for package, details in JAVASCRIPT_SECURITY_PACKAGES.items():
            self.assertIn(f"npm pack {package}@{details['version']}", dockerfile)
            self.assertIn(details["integrity"].removeprefix("sha512-"), dockerfile)
            self.assertIn(details["replaced_path"], dockerfile)
        smoke_path = ROOT / "packages/n8n-nodes-finance/scripts/javascript-security-overlay-smoke.cjs"
        smoke = smoke_path.read_text(encoding="utf-8")
        self.assertIn("Maximum nesting depth of 500 exceeded", smoke)
        self.assertIn("Object.getPrototypeOf", smoke)
        self.assertIn("ajv.compile", smoke)
        self.assertIn("require.resolve('fast-uri/package.json'", smoke)
        self.assertIn("require.resolve('toml/package.json'", smoke)
        self.assertIn("snowflake-sdk@2.1.0_", smoke)
        self.assertIn("FINANCE_WH", smoke)
        self.assertGreaterEqual(dockerfile.count("node /tmp/javascript-security-overlay-smoke.cjs"), 2)
        self.assertIn(".pnpm/ajv@8.20.0/node_modules/ajv", dockerfile)

    def test_finance_extension_is_immutable_and_outside_persistent_state(self):
        dockerfile = (ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n").read_text(encoding="utf-8")
        base_image = (ROOT / "packages/n8n-nodes-finance/base-image.txt").read_text(encoding="utf-8").strip()
        self.assertIn("/opt/finance-n8n/custom-extensions/n8n-nodes-finance", dockerfile)
        self.assertNotIn("/home/node/.n8n/nodes/node_modules/n8n-nodes-finance", dockerfile)
        self.assertNotIn("ENV N8N_CUSTOM_EXTENSIONS", dockerfile)
        self.assertIn("ARG N8N_BASE_IMAGE", dockerfile)
        self.assertIn(f"ARG N8N_BASE_IMAGE={base_image}", dockerfile)
        self.assertIn("FROM ${N8N_BASE_IMAGE}", dockerfile)
        self.assertIn("WORKDIR /home/node\nUSER node", dockerfile)
        self.assertIn(
            "ln -s /opt/finance-n8n/community-extensions/node_modules /home/node/node_modules",
            dockerfile,
        )
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
        self.assertIsNone(receipt["image"]["local_image_id"])
        self.assertEqual(receipt["base_image"]["digest"], OFFICIAL_BASE_DIGEST)
        self.assertEqual(receipt["base_image"]["source_commit"], OFFICIAL_SOURCE_COMMIT)
        self.assertEqual(
            receipt["base_image"]["source_repository"],
            "https://github.com/n8n-io/n8n",
        )
        self.assertEqual(receipt["base_image"]["nodemailer_overlay"]["tarball_sha256"], NODEMAILER_TARBALL_SHA256)
        self.assertEqual(receipt["attestation"]["status"], "NOT_AVAILABLE")
        self.assertEqual(
            receipt["blockers"],
            ["LIVE_REGISTRY_DIGEST_REQUIRED", "SBOM_SCAN_ATTESTATION_REQUIRED", "DISPOSABLE_IMAGE_IMPORT_REQUIRED"],
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
        self.assertIn("sdk_link=/home/node/node_modules", entrypoint)
        self.assertIn("sdk_immutable=/opt/finance-n8n/community-extensions/node_modules", entrypoint)
        self.assertIn('ensure_link "${sdk_link}" "${sdk_immutable}"', entrypoint)
        self.assertIn("FINANCE_EXTENSION_MUTABLE_PATH_REJECTED", entrypoint)
        self.assertNotIn("cp ", entrypoint)
        verifier = (ROOT / "packages/n8n-nodes-finance/scripts/verify-immutable-extension.cjs").read_text(encoding="utf-8")
        self.assertIn("FINANCE_EXTENSION_VERSION_MISMATCH", verifier)
        self.assertIn("FINANCE_EXTENSION_TREE_HASH_MISMATCH", verifier)
        self.assertIn("HASH_CHUNK_SIZE = 64 * 1024", verifier)
        self.assertIn("fs.readSync", verifier)
        self.assertNotIn("fs.readFileSync(absolute)", verifier)

    def test_ci_registration_smoke_uses_initialized_persistent_state(self):
        workflow = (ROOT / ".github/workflows/phase1-finance-artifacts.yml").read_text(encoding="utf-8")
        first_start = 'docker run --rm -v "$state_dir:/home/node/.n8n"'
        registration = "/opt/finance-n8n/assert-runtime-registration.cjs export:nodes"
        self.assertIn("finance extension registration verified: 7 nodes, 3 credentials", workflow)
        self.assertIn("--entrypoint node", workflow)
        self.assertIn('-v "$state_dir:/home/node/.n8n"', workflow)
        self.assertIn("cleanup_state_dir()", workflow)
        self.assertIn("trap cleanup_state_dir EXIT", workflow)
        self.assertIn("--entrypoint sh", workflow)
        self.assertIn("rm /home/node/.n8n/nodes/node_modules/n8n-nodes-finance", workflow)
        self.assertIn(
            "FINANCE_EXTENSION_MUTABLE_PATH_REJECTED: expected absent path or exact symlink",
            workflow,
        )
        self.assertIn('sudo rm -rf -- "$state_dir"', workflow)
        self.assertNotIn('rm "$state_dir/nodes/node_modules/n8n-nodes-finance"', workflow)
        self.assertLess(workflow.index(first_start), workflow.index(registration))

    def test_ci_image_smoke_imports_prodex_sdk_from_n8n_process_context(self):
        workflow = (ROOT / ".github/workflows/phase1-finance-artifacts.yml").read_text(encoding="utf-8")
        self.assertIn("--input-type=module", workflow)
        self.assertIn("process.cwd() !== '/home/node'", workflow)
        self.assertIn("import { Codex } from '@openai/codex-sdk'", workflow)
        self.assertIn("typeof Codex !== 'function'", workflow)
        self.assertIn("custom smoke checkpoint: reject mutable ProDex SDK path", workflow)
        self.assertIn("Expected rejection exit code 1, got $status", workflow)
        self.assertIn("docker builder prune --all --force", workflow)
        self.assertIn("FINANCE_EXTENSION_MUTABLE_PATH_REJECTED: unexpected symlink target", workflow)
        self.assertIn("COMMUNITY_AI_API_KEY_FORBIDDEN:OPENAI_API_KEY", workflow)
        self.assertIn("assert_rejected()", workflow)
        self.assertIn('if [ "$status" -ne 1 ]; then', workflow)
        self.assertIn("id: build_custom_image", workflow)
        self.assertIn("!cancelled() && steps.build_custom_image.outcome == 'success'", workflow)

    def test_bounded_extension_tree_hash_matches_legacy_digest(self):
        """The streaming implementation must preserve the old tree digest byte-for-byte."""
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            finance = root / "finance"
            community = root / "community"
            (finance / "dist" / "nodes").mkdir(parents=True)
            (community / "node_modules" / "n8n-nodes-prodex").mkdir(parents=True)
            (finance / "package.json").write_bytes(b'{"name":"n8n-nodes-finance"}\n')
            # Cross multiple 64 KiB reads and include every byte value so the
            # comparison covers binary data rather than just UTF-8 text.
            (finance / "dist" / "nodes" / "vendor.bin").write_bytes(bytes(range(256)) * 1025)
            (community / "package.json").write_bytes(b'{"dependencies":{"n8n-nodes-prodex":"0.5.1"}}\n')
            (community / "node_modules" / "n8n-nodes-prodex" / "package.json").write_bytes(
                b'{"version":"0.5.1"}\n'
            )
            (community / "node_modules" / "n8n-nodes-prodex" / "link-target.txt").write_text(
                "target\n", encoding="utf-8"
            )
            os.symlink("link-target.txt", community / "node_modules" / "n8n-nodes-prodex" / "link.txt")

            def legacy_update(digest, directory, relative=""):
                for entry in sorted(directory.iterdir(), key=lambda item: item.name):
                    rel = f"{relative}/{entry.name}" if relative else entry.name
                    stat = entry.lstat()
                    if stat.st_mode & 0o170000 == 0o040000:
                        digest.update(f"d\0{rel}\0".encode())
                        legacy_update(digest, entry, rel)
                    elif stat.st_mode & 0o170000 == 0o120000:
                        digest.update(f"l\0{rel}\0{os.readlink(entry)}\0".encode())
                    elif stat.st_mode & 0o170000 == 0o100000:
                        digest.update(f"f\0{rel}\0".encode())
                        digest.update(entry.read_bytes())
                        digest.update(b"\0")
                    else:
                        self.fail(f"unexpected fixture entry: {entry}")

            expected = hashlib.sha256()
            expected.update(b"finance\0")
            legacy_update(expected, finance)
            expected.update(b"community\0")
            legacy_update(expected, community)
            script = ROOT / "packages/n8n-nodes-finance/scripts/verify-immutable-extension.cjs"
            observed = subprocess.check_output(
                [
                    "node",
                    "-e",
                    "const v=require(process.argv[1]); process.stdout.write(v.hashExtensionTrees(process.argv[2], process.argv[3]));",
                    str(script),
                    str(finance),
                    str(community),
                ],
                text=True,
            )
            self.assertEqual(observed, expected.hexdigest())

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
