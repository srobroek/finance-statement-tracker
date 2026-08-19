import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "cloudflared"


class CloudflaredArtifactTests(unittest.TestCase):
    def test_source_toolchain_and_runtime_are_content_addressed(self) -> None:
        lock = json.loads((SERVICE / "source-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["version"], "2026.7.3")
        self.assertRegex(lock["tag_object_sha"], r"^[a-f0-9]{40}$")
        self.assertRegex(lock["source_commit"], r"^[a-f0-9]{40}$")
        self.assertRegex(lock["source_tree"], r"^[a-f0-9]{40}$")
        self.assertRegex(lock["source_archive_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(lock["go_version"], "1.26.6")
        self.assertRegex(lock["go_builder_image"], r"@sha256:[a-f0-9]{64}$")
        self.assertEqual(lock["runtime"], "scratch")
        self.assertEqual(lock["entrypoint"], ["cloudflared"])
        self.assertEqual(lock["compose_command"], ["tunnel", "--no-autoupdate", "run"])

    def test_dockerfile_is_reproducible_nonroot_and_has_no_default_command(self) -> None:
        lock = json.loads((SERVICE / "source-lock.json").read_text(encoding="utf-8"))
        dockerfile = (SERVICE / "Dockerfile").read_text(encoding="utf-8")
        for value in (
            lock["version"], lock["source_commit"], lock["source_archive_sha256"],
            lock["go_version"], lock["go_builder_image"], str(lock["source_date_epoch"]),
        ):
            self.assertIn(value, dockerfile)
        for marker in (
            "GOTOOLCHAIN=local", "GOFLAGS=-mod=vendor", "-trimpath",
            "-buildvcs=false", "-buildid=", "FROM scratch",
            "USER 65532:65532", 'ENTRYPOINT ["cloudflared"]',
        ):
            self.assertIn(marker, dockerfile)
        self.assertNotRegex(dockerfile, re.compile(r"^CMD\s", re.MULTILINE))
        self.assertNotIn("token", dockerfile.lower())

    def test_ci_has_unwaived_scan_smokes_sbom_publish_and_receipt(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "phase1-finance-artifacts.yml").read_text(encoding="utf-8")
        self.assertIn("cloudflared:", workflow)
        for marker in (
            "services/cloudflared/Dockerfile", "cloudflared version",
            "tunnel --no-autoupdate --help", "tunnel --no-autoupdate run",
            "severity: HIGH,CRITICAL", 'ignore-unfixed: false', 'exit-code: "1"',
            "finance-cloudflared-${{ github.sha }}.spdx.json",
            "ghcr.io/${{ github.repository_owner }}/finance-cloudflared",
            "finance-cloudflared-image-receipt.json",
        ):
            self.assertIn(marker, workflow)


if __name__ == "__main__":
    unittest.main()
