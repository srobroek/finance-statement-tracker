"""Static release checks for the PDF utility's security-sensitive image build."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "services/pdf-utility/Dockerfile"
README = ROOT / "services/pdf-utility/README.md"
WORKFLOW = ROOT / ".github/workflows/phase1-finance-artifacts.yml"


class PdfUtilityImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_base_images_remain_digest_pinned(self):
        refs = re.findall(r"^ARG PYTHON_(?:BUILD|RUNTIME)_IMAGE=(.+)$", self.dockerfile, re.MULTILINE)
        self.assertEqual(len(refs), 2)
        self.assertTrue(all("@sha256:" in ref for ref in refs))
        self.assertIn(
            "cgr.dev/chainguard/python:latest-dev@sha256:afdbadf8d697739ab8e10a4d355d0850daa439cba3e6f0e39a73f7f2d3d839b7",
            refs[0],
        )
        self.assertIn(
            "cgr.dev/chainguard/python:latest@sha256:eca30c0ac647bf28beaec7442388609d14fd100984fa63397e6015eaffe22aa1",
            refs[1],
        )

    def test_runtime_does_not_add_a_package_manager_or_copy_untracked_libraries(self):
        build, runtime = self.dockerfile.split("\nFROM ${PYTHON_RUNTIME_IMAGE}", 1)
        self.assertNotIn("apk add", build)
        self.assertNotIn("COPY --from=build /out/security-libs/", runtime)
        self.assertNotIn("apk add", runtime)

    def test_runtime_keeps_restricted_user_and_entrypoint(self):
        self.assertIn("USER 1000:1000", self.dockerfile)
        self.assertIn('ENTRYPOINT ["python", "/opt/platform-pdf/server.py"]', self.dockerfile)

    def test_provenance_is_documented_and_scan_stays_blocking(self):
        self.assertIn("latest-dev@sha256:afdbadf8d697739ab8e10a4d355d0850daa439cba3e6f0e39a73f7f2d3d839b7", self.readme)
        self.assertIn("latest@sha256:eca30c0ac647bf28beaec7442388609d14fd100984fa63397e6015eaffe22aa1", self.readme)
        self.assertIn("CVE-2026-14456", self.readme)
        self.assertIn('exit-code: "1"', self.workflow)
        self.assertIn("severity: HIGH,CRITICAL", self.workflow)
        self.assertIn("ignore-unfixed: false", self.workflow)
        self.assertNotRegex(self.workflow, r"trivy[^\n]*(?:ignore|ignore-unfixed)")


if __name__ == "__main__":
    unittest.main()
