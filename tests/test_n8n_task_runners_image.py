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
        patch = (SERVICE / "launcher-x-text-0.39.0.patch").read_text(encoding="utf-8")
        self.assertNotIn("FROM n8nio/runners", dockerfile)
        self.assertIn("go test ./...", dockerfile)
        self.assertIn("CGO_ENABLED=0 go build", dockerfile)
        self.assertIn("golang.org/x/text", patch)
        self.assertIn("v0.39.0", patch)
        self.assertEqual(set(re.findall(r"^diff --git a/(\S+) b/\S+", patch, re.MULTILINE)), {"go.mod", "go.sum"})

    def test_protocol_smoke_exercises_both_runners(self):
        workflow = json.loads((SERVICE / "protocol-smoke.json").read_text(encoding="utf-8"))
        code_nodes = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.code"]
        self.assertEqual({node["parameters"]["language"] for node in code_nodes}, {"javaScript", "pythonNative"})
        smoke = (SERVICE / "protocol-smoke.sh").read_text(encoding="utf-8")
        self.assertIn("N8N_RUNNERS_MODE=external", smoke)
        self.assertIn("execute --id=finance-task-runners-protocol-smoke --rawOutput", smoke)
        self.assertIn("python_runner", smoke)
        self.assertIn("js_runner", smoke)


if __name__ == "__main__":
    unittest.main()
