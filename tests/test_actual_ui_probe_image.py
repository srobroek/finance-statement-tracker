from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
PROBE_ROOT = ROOT / "deploy/actual/probe"
PROBE = PROBE_ROOT / "actual-ui-sync-probe.mjs"
DOCKERFILE = PROBE_ROOT / "Dockerfile"
ENVELOPE = PROBE_ROOT / "expected-envelope.json"
ENVELOPE_CONTRACT = PROBE_ROOT / "expected-envelope.contract.json"
PACKAGE = PROBE_ROOT / "package.json"
LOCKFILE = PROBE_ROOT / "package-lock.json"


class ActualUiProbeImageTests(unittest.TestCase):
    def test_derived_image_preflights_both_dependencies_from_probe_root(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        lockfile = json.loads(LOCKFILE.read_text(encoding="utf-8"))
        self.assertIn("WORKDIR /probe", dockerfile)
        self.assertIn("npm ci --omit=dev --no-audit --no-fund", dockerfile)
        self.assertNotIn("--ignore-scripts", dockerfile)
        self.assertIn("native-db-preflight.mjs", dockerfile)
        self.assertIn("preflightNativeDb", dockerfile)
        self.assertIn("better-sqlite3", dockerfile)
        self.assertGreaterEqual(dockerfile.count("await import('playwright')"), 2)
        self.assertGreaterEqual(dockerfile.count("await import('@actual-app/api')"), 2)
        self.assertEqual(package["dependencies"], {"@actual-app/api": "26.8.1", "playwright": "1.59.1"})
        root_dependencies = lockfile["packages"][""]["dependencies"]
        self.assertEqual(root_dependencies, package["dependencies"])
        self.assertEqual(lockfile["packages"]["node_modules/@actual-app/api"]["version"], "26.8.1")
        self.assertEqual(lockfile["packages"]["node_modules/playwright"]["version"], "1.59.1")

    def test_native_preflight_opens_database_and_preserves_missing_binding_regression(self) -> None:
        source = (PROBE_ROOT / "native-db-preflight.mjs").read_text(encoding="utf-8")
        probe_source = PROBE.read_text(encoding="utf-8")
        self.assertIn("createRequire('/probe/package.json')", source)
        self.assertIn("requireProbe('better-sqlite3')", source)
        self.assertIn("new Database(databasePath)", source)
        self.assertIn("database.prepare('CREATE TABLE native_binding_preflight", source)
        self.assertIn("database.prepare('INSERT INTO native_binding_preflight", source)
        self.assertIn("database.close()", source)
        self.assertIn("await stat(databasePath)", source)
        self.assertIn("await preflightNativeDb();", probe_source)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text('{"type":"commonjs"}\n', encoding="utf-8")
            module_dir = root / "node_modules/better-sqlite3"
            module_dir.mkdir(parents=True)
            (module_dir / "index.js").write_text(
                "module.exports = class Database { constructor() { throw new Error('Could not locate the bindings file'); } };\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "node",
                    "-e",
                    "const Database = require('better-sqlite3'); new Database('/tmp/native-binding-regression.sqlite')",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Could not locate the bindings file", result.stderr)

    def test_probe_rejects_loader_patching_and_tmp_esm_imports(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        for forbidden in (
            "pathToFileURL",
            "actual-ui-sync-probe-",
            "writeFile(generatedPath",
            "getBudgets()",
            "'/probe/expected.json'",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotRegex(source, r"import\([^)]*/tmp/")
        self.assertIn("createRequire('/probe/package.json')", source)

    def test_probe_uses_protected_sync_id_and_direct_sync_contract(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        sync_guard = source.index("if (!syncId) throw new Error('ACTUAL_SYNC_ID missing');")
        download_call = source.index("await actualApi.downloadBudget(syncId);")
        sync_call = source.index("await actualApi.sync();")
        self.assertLess(sync_guard, download_call)
        self.assertLess(download_call, sync_call)
        self.assertIn("if (typeof actualApi.sync !== 'function')", source)
        self.assertNotIn("getBudgets", source)

    def test_expected_envelope_contract_binds_path_hash_and_runtime_mode(self) -> None:
        envelope = json.loads(ENVELOPE.read_text(encoding="utf-8"))
        contract = json.loads(ENVELOPE_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(envelope["schema_version"], 1)
        self.assertEqual(len(envelope["accounts"]), 12)
        self.assertEqual(len(envelope["representative_transactions"]), 5)
        self.assertEqual(contract["path"], "/probe/expected-envelope.json")
        self.assertEqual(contract["sha256"], hashlib.sha256(ENVELOPE.read_bytes()).hexdigest())
        self.assertEqual(contract["mode"], "0600")
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("chmod 0600 /probe/expected-envelope.json /probe/expected-envelope.contract.json", dockerfile)

    def test_probe_preserves_api_ui_and_checkpoint_receipt_interfaces(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        for required in (
            "schema_version: 2",
            "api: apiProof.result",
            "ui: { accounts: canonicalizeAccounts(expected.accounts), representative_transactions: canonicalUiTransactions }",
            "expected_envelope: expectedEnvelopeEvidence",
            "ACTUAL_RESTORE_CHECKPOINT_PATH",
            "await writeFile(checkpointPath",
            "[data-testid=\"transaction-table\"]",
            "Closed accounts...",
            "querySelector('[data-testid=\"table\"]')",
        ):
            self.assertIn(required, source)
        self.assertGreaterEqual(len(re.findall(r"checkpoint\('", source)), 5)

    def test_image_cleans_only_exact_probe_pycache_paths(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "find /probe -type d -name __pycache__ -prune -exec rm -rf -- {} +",
            dockerfile,
        )
        self.assertIn(
            'test -z "$(find /probe -type d -name __pycache__ -print -quit)"',
            dockerfile,
        )
        self.assertNotIn("find / -", dockerfile)
        self.assertNotIn("rm -rf /probe", dockerfile)


if __name__ == "__main__":
    unittest.main()
