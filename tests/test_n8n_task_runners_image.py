import json
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "n8n-task-runners"


class N8nTaskRunnersImageContractTests(unittest.TestCase):
    def test_upstream_sources_and_bases_are_immutable(self):
        lock = json.loads((SERVICE / "upstream.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["status"], "SPEC_ONLY")
        self.assertEqual(lock["n8n"]["version"], "2.36.2")
        self.assertEqual(lock["n8n"]["javascript_runner_package"], "@n8n/task-runner@2.36.1")
        self.assertEqual(lock["launcher"]["version"], "1.4.7")
        self.assertEqual(lock["launcher"]["security_overrides"], {"golang.org/x/text": "0.39.0"})
        for commit in (lock["n8n"]["source_commit"], lock["launcher"]["source_commit"]):
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
        for image in lock["base_images"].values():
            self.assertRegex(image, r"@sha256:[0-9a-f]{64}$")
        self.assertRegex(lock["compatible_n8n_image"], r"@sha256:[0-9a-f]{64}$")

    def test_launcher_is_source_built_with_a_narrow_auditable_patch(self):
        dockerfile = (SERVICE / "Dockerfile").read_text(encoding="utf-8")
        patcher = (SERVICE / "patch_launcher.py").read_text(encoding="utf-8")
        self.assertNotIn("FROM n8nio/runners", dockerfile)
        self.assertIn("go test ./...", dockerfile)
        self.assertIn("CGO_ENABLED=0 go build", dockerfile)
        self.assertNotIn("pnpm add moment", dockerfile)
        self.assertIn("javascript-extras/package-lock.json", dockerfile)
        self.assertIn("node_modules/moment ./node_modules/moment", dockerfile)
        self.assertIn("'@n8n/di'", dockerfile)
        self.assertIn("require.resolve(name)", dockerfile)
        self.assertIn("golang.org/x/text v0.14.0", patcher)
        self.assertIn("golang.org/x/text v0.39.0", patcher)
        self.assertIn("SOURCE_SHA256", patcher)
        self.assertIn("PATCHED_SHA256", patcher)
        self.assertNotIn("subprocess", patcher)

        extras_lock = json.loads(
            (SERVICE / "javascript-extras" / "package-lock.json").read_text(encoding="utf-8")
        )
        moment = extras_lock["packages"]["node_modules/moment"]
        self.assertEqual(moment["version"], "2.30.1")
        self.assertRegex(moment["integrity"], r"^sha512-")

    def test_protocol_smoke_exercises_both_runners(self):
        workflow = json.loads((SERVICE / "protocol-smoke.json").read_text(encoding="utf-8"))
        code_nodes = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.code"]
        self.assertEqual({node["parameters"]["language"] for node in code_nodes}, {"javaScript", "pythonNative"})
        smoke = (SERVICE / "protocol-smoke.sh").read_text(encoding="utf-8")
        self.assertIn("N8N_RUNNERS_MODE=external", smoke)
        self.assertIn("publish:workflow --id=finance-task-runners-protocol-smoke", smoke)
        self.assertIn("http://127.0.0.1:5679/healthz", smoke)
        self.assertIn("--network-alias broker", smoke)
        self.assertIn("http://127.0.0.1:5678/webhook/finance-task-runners-protocol-smoke", smoke)
        self.assertIn("Connected: ws://broker:5679/", smoke)
        self.assertIn("sed \"s/$auth_token/[REDACTED]/g\"", smoke)
        self.assertIn("if(r.status===404)process.exit(44)", smoke)
        self.assertIn('if [ "$request_status" -ne 44 ]', smoke)
        self.assertIn("python_runner", smoke)
        self.assertIn("js_runner", smoke)

        workflow = (ROOT / ".github" / "workflows" / "phase1-finance-artifacts.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "--format '{{ index .Config.Labels \"finance.n8n.source_commit\" }}'",
            workflow,
        )
        self.assertNotIn(
            "--format '{{ index .Config.Labels \\\"finance.n8n.source_commit\\\" }}'",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
