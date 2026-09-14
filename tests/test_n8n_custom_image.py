import json
import hashlib
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OFFICIAL_BASE_DIGEST = "sha256:307d6065be25619aa24cfc63a7c2f04ca56d084a08c05c8e9f189a89f353b1ec"
OFFICIAL_SOURCE_COMMIT = "5542b8b6419cb6925cca8f11b270c9bfbe09d85e"
OVERLAY_SOURCE_REPOSITORY = "https://github.com/srobroek/finance-statement-tracker"
OVERLAY_SOURCE_COMMIT = "c0e5253515c052c57d4198e0fa2fe074adab70cb"
OVERLAY_DOCKERFILE_BLOB = "bad0e94d0b70e541c726d37e441daea706514efe"
OVERLAY_SMOKE_BLOB = "296c57da94232a974428c59cf531e68a3b09a556"
NODEMAILER_TARBALL_SHA256 = "fa0d4044a699101fff3706651423c4174ac41d59414a4cc039acebd348f102db"
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
    "@tiptap/core": {
        "version": "3.30.5",
        "integrity": "sha512-3O7N0FyKIfuLV+xrdWyDM3V5eUY/q2CgLjhhMwOAbM1Pu7VPp9VP+TpEYOdH8aRyB+h1vj5hX5A747D8ZrPfHA==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/@tiptap+core@3.27.0_@tiptap+pm@3.27.0/node_modules/@tiptap/core",
    },
    "@tiptap/pm": {
        "version": "3.30.5",
        "integrity": "sha512-gufkLkW2tA6PZPjivYxDiGzTIIftwqhmYI6lvvKu2S4FbhcysJgMAe/GXVSywzCRVVex9SrvCe6RFYrqnwRitQ==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/@tiptap+pm@3.27.0/node_modules/@tiptap/pm",
    },
    "prosemirror-model": {
        "version": "1.25.11",
        "integrity": "sha512-QWg9RhnpLlogAmp3p96uEFrE5txQpFynd4vhBAELkwgOCWQs/X0yCzB3/hrHqiPwf91RG5KyWq6553zs9JqIOQ==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/prosemirror-model@1.25.10/node_modules/prosemirror-model",
    },
    "prosemirror-view": {
        "version": "1.41.9",
        "integrity": "sha512-clTunTX+eaLbr87L1V1QPheRlEQJyTlL3gXe9x3jQIk3rL0RVWxviDGz8tFaydwIVm+hKhYCyr+R/zBtWr9s6A==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/prosemirror-view@1.41.8/node_modules/prosemirror-view",
    },
    "@xmldom/xmldom": {
        "version": "0.8.15",
        "integrity": "sha512-/5NV/vDALVFDXgLmfsy9TRCBlKwO2LNBFzpzvb9iIj+jR+eSc6DLYYvVOdivT/jm7MtU6TebYuRmzEOI7w40UA==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/@xmldom+xmldom@0.8.14/node_modules/@xmldom/xmldom",
    },
    "js-yaml": {
        "version": "4.3.2",
        "integrity": "sha512-SFNOvSJ+Dgf/9An904Yx+CgSlIPCkIpao4qo51lpee25TIRejdH3rhR4EZMGoNx3/TP3O+wzWuiTFl4sqbltzA==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/js-yaml@4.3.1/node_modules/js-yaml",
    },
    "multer": {
        "version": "2.3.0",
        "integrity": "sha512-cjNbm3sttszgZeGfJR124D+jFEfkXCVAsoPBmFn9X7UxmDSFHWqE2CoEj0vrmSpuAFnqWR1Szcm9QTsiHr60Xw==",
        "replaced_path": "/usr/local/lib/node_modules/n8n/node_modules/.pnpm/multer@2.2.0/node_modules/multer",
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
        self.assertEqual(provenance["version"], "2.37.10")
        self.assertEqual(provenance["release_channel"], "stable")
        self.assertEqual(provenance["bundled_n8n_workflow"], "2.37.4")
        self.assertEqual(
            provenance["release_evidence"],
            {
                "tag": "n8n@2.37.10",
                "url": "https://github.com/n8n-io/n8n/releases/tag/n8n%402.37.10",
                "prerelease": False,
                "published_at": "2026-09-04T09:13:04Z",
            },
        )
        self.assertEqual(base_reference.rsplit("@", 1)[1], OFFICIAL_BASE_DIGEST)
        self.assertIn("ARG N8N_BASE_IMAGE=" + base_reference, dockerfile)
        self.assertIn("FROM ${N8N_BASE_IMAGE}", dockerfile)
        self.assertIn('org.opencontainers.image.source="https://github.com/srobroek/finance-statement-tracker"', dockerfile)
        self.assertIn('io.finance.n8n.base-source="https://github.com/n8n-io/n8n@' + OFFICIAL_SOURCE_COMMIT, dockerfile)
        self.assertIn('io.finance.n8n.nodemailer-recipe="' + OVERLAY_SOURCE_REPOSITORY + "@" + OVERLAY_SOURCE_COMMIT, dockerfile)
        overlay = provenance["nodemailer_overlay"]
        self.assertEqual(overlay["source_repository"], OVERLAY_SOURCE_REPOSITORY)
        self.assertEqual(overlay["source_commit"], OVERLAY_SOURCE_COMMIT)
        self.assertEqual(overlay["dockerfile_blob"], OVERLAY_DOCKERFILE_BLOB)
        self.assertEqual(overlay["smoke_blob"], OVERLAY_SMOKE_BLOB)
        self.assertEqual(overlay["tarball_sha256"], NODEMAILER_TARBALL_SHA256)
        self.assertIn("npm pack nodemailer@9.1.0", dockerfile)
        self.assertIn(NODEMAILER_TARBALL_SHA256, dockerfile)
        self.assertIn(".pnpm/nodemailer@8.0.10/node_modules/nodemailer", dockerfile)
        self.assertIn("node /tmp/nodemailer-smoke.cjs", dockerfile)
        smoke = ROOT / "packages/n8n-nodes-finance/scripts/nodemailer-smoke.cjs"
        self.assertIn("assert.equal(packageJson.version, '9.1.0')", smoke.read_text(encoding="utf-8"))
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
            "public.ecr.aws/docker/library/node:26.5.1-alpine@sha256:233761595746769ebfdb6090f44fc7cdf818ae0ce62d2b37e0367723b9823e36",
        )
        self.assertEqual(overlay["installer"], "apk-tools-static=3.0.8-r0")
        self.assertEqual(overlay["repository"], "https://dl-cdn.alpinelinux.org/alpine/v3.24/main")
        self.assertEqual(overlay["verification"], "Alpine repository signature via apk.static")
        self.assertEqual(overlay["packages"], ALPINE_SECURITY_PACKAGES)
        self.assertIn("AS alpine-security-overlay", dockerfile)
        self.assertIn("apk add --no-cache apk-tools-static=3.0.8-r0", dockerfile)
        self.assertIn("cp -R /etc/apk/keys /tmp/security-apks/keys", dockerfile)
        self.assertIn(
            "/tmp/security-apks/apk.static --keys-dir /tmp/security-apks/keys add --no-cache",
            dockerfile,
        )
        self.assertIn("--repository https://dl-cdn.alpinelinux.org/alpine/v3.24/main", dockerfile)
        self.assertNotIn("--allow-untrusted", dockerfile)
        for package, version in ALPINE_SECURITY_PACKAGES.items():
            self.assertIn(f"{package}={version}", dockerfile)
            self.assertIn(f"info --exists {package}={version}", dockerfile)
        self.assertIn("test ! -e /sbin/apk", dockerfile)
        self.assertIn("ln -s /tmp/security-apks/apk.static /sbin/apk", dockerfile)
        self.assertIn("! /tmp/security-apks/apk.static info --exists libcrypto3=3.5.7-r1", dockerfile)
        self.assertIn("rm -f /sbin/apk", dockerfile)
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
        self.assertIn("FINANCE_NODEMAILER_DOCKERFILE_BLOB_INVALID", builder)
        self.assertIn("FINANCE_NODEMAILER_SMOKE_BLOB_INVALID", builder)
        self.assertIn("FINANCE_RUNTIME_RECEIPT_MUST_BE_EXTERNAL", builder)
        self.assertIn("FINANCE_SOURCE_TREE_MUST_BE_CLEAN", builder)
        self.assertIn("${TMPDIR:-/tmp}/finance-n8n-image-build-receipt.json", builder)
        self.assertNotIn('receipt="${package_dir}/finance-image-build-receipt.json"', builder)
        self.assertNotIn("docker push", builder)
        self.assertEqual(receipt["status"], "TESTED_IN_DISPOSABLE")
        self.assertEqual(receipt["image"]["requested_reference"], "ghcr.io/srobroek/finance-n8n@sha256:a3b39fe2c0a3a987d91c2b97fd2adfe21707134790223dc470ef76a57f1c0d4b")
        self.assertEqual(receipt["image"]["image_digest"], "sha256:a3b39fe2c0a3a987d91c2b97fd2adfe21707134790223dc470ef76a57f1c0d4b")
        self.assertEqual(receipt["image"]["local_image_id"], "sha256:cc835d2eb8dff22a99c813c58332f7a2718cdfccf66e0182898e5d48294e5f4e")
        self.assertEqual(receipt["source_commit"], "d160aa0c29a1564c9e54ab4eec1ab4f28be89dd0")
        self.assertEqual(receipt["base_image"]["reference"], "ghcr.io/n8n-io/n8n:2.37.10@" + OFFICIAL_BASE_DIGEST)
        self.assertEqual(receipt["base_image"]["provenance_sha256"], "fcd628b0805ad628adb6cf5485af8e2860a36de109057c07e599278e66365e5a")
        self.assertEqual(receipt["base_image"]["nodemailer_overlay"]["recipe_commit"], OVERLAY_SOURCE_COMMIT)
        self.assertEqual(receipt["scan"], {
            "tool": "Trivy 0.74.0 (local immutable-reference scan)",
            "result": "PASS",
            "high": 0,
            "critical": 0,
            "artifact_sha256": "1b773709e469ec277d4a3717c50c5fa0a7aad37cefe984325f518dad7ac1ce82",
        })
        self.assertEqual(receipt["local_scan"]["artifact_sha256"], "ecb00b5e50040fc944b89b8aa174d167cc2ac6dfe0266542147ba8ce85876a4f")
        self.assertEqual(receipt["build"]["tool"], "Podman 4.9.3 / Buildah")
        self.assertEqual(receipt["attestation"]["status"], "NOT_AVAILABLE")
        self.assertIn("GITHUB_ACTIONS_VERIFIED_CI_RECEIPT_REQUIRED", receipt["limitations"])

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
