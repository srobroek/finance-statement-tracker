import hashlib
import json
import re
import tarfile
import unittest
from pathlib import Path, PurePosixPath


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
        security_update = lock["security_module_update"]
        self.assertEqual(security_update["reason"], "GHSA-hrxh-6v49-42gf")
        self.assertEqual(security_update["resolved_modules"]["google.golang.org/grpc"], "v1.82.1")
        self.assertRegex(security_update["patch_sha256"], r"^[a-f0-9]{64}$")
        patch = SERVICE / security_update["patch"]
        self.assertTrue(patch.is_file())
        self.assertEqual(hashlib.sha256(patch.read_bytes()).hexdigest(), security_update["patch_sha256"])
        overlay = SERVICE / security_update["overlay"]
        self.assertTrue(overlay.is_file())
        self.assertEqual(hashlib.sha256(overlay.read_bytes()).hexdigest(), security_update["overlay_sha256"])
        self.assertTrue((SERVICE / security_update["overlay_generator"]).is_file())
        with tarfile.open(overlay, mode="r:gz") as archive:
            members = archive.getmembers()
            names = {member.name for member in members}
            self.assertTrue(all(not PurePosixPath(name).is_absolute() for name in names))
            self.assertTrue(all(".." not in PurePosixPath(name).parts for name in names))
            manifest_file = archive.extractfile("overlay-manifest.json")
            self.assertIsNotNone(manifest_file)
            manifest = json.loads(manifest_file.read())
            self.assertEqual(set(manifest["files"]), names - {"overlay-manifest.json"})
            for name, expected_sha256 in manifest["files"].items():
                content = archive.extractfile(name)
                self.assertIsNotNone(content)
                self.assertEqual(hashlib.sha256(content.read()).hexdigest(), expected_sha256)
        self.assertEqual(lock["runtime"], "scratch")
        self.assertEqual(lock["entrypoint"], ["cloudflared"])
        self.assertEqual(lock["compose_command"], ["tunnel", "--no-autoupdate", "run"])

    def test_dockerfile_is_reproducible_nonroot_and_has_no_default_command(self) -> None:
        lock = json.loads((SERVICE / "source-lock.json").read_text(encoding="utf-8"))
        dockerfile = (SERVICE / "Dockerfile").read_text(encoding="utf-8")
        for value in (
            lock["version"], lock["source_commit"], lock["source_archive_sha256"],
            lock["go_version"], lock["go_builder_image"], str(lock["source_date_epoch"]),
            lock["security_module_update"]["overlay_sha256"],
            lock["security_module_update"]["patched_go_mod_sha256"],
            lock["security_module_update"]["patched_go_sum_sha256"],
            lock["security_module_update"]["patched_vendor_modules_sha256"],
        ):
            self.assertIn(value, dockerfile)
        for marker in (
            "GOTOOLCHAIN=local", "GOFLAGS=-mod=vendor", "-trimpath",
            "-buildvcs=false", "-buildid=", "FROM scratch",
            "USER 65532:65532", 'ENTRYPOINT ["cloudflared"]',
            "go list -mod=vendor -m all", "google.golang.org/grpc v1.82.1",
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
