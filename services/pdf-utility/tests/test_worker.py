from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pikepdf

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "worker.py"


def make_pdf(path: Path, *, pages: int = 1, password: str | None = None) -> None:
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(100, 100))
    if password is None:
        pdf.save(path)
    else:
        pdf.save(path, encryption=pikepdf.Encryption(user=password, owner=password, R=6))


class WorkerTests(unittest.TestCase):
    def invoke(self, operation: str, source: Path, result: Path, *, output: Path | None = None, password_file: Path | None = None) -> subprocess.CompletedProcess[bytes]:
        command = [sys.executable, str(WORKER), "--operation", operation, "--input", str(source), "--result", str(result)]
        if output is not None:
            command.extend(["--output", str(output)])
        if password_file is not None:
            command.extend(["--password-file", str(password_file)])
        return subprocess.run(command, capture_output=True, check=False)

    def test_validate_and_profile_valid_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.pdf"
            result = Path(temp) / "result.json"
            make_pdf(source)
            completed = self.invoke("profile", source, result)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(payload["pages"], 1)
            self.assertEqual(payload["profile"], "statement-v1")
            self.assertEqual(payload["status"], "profiled")
            self.assertEqual(payload["extracted_text"], "")

    def test_unlock_encrypted_pdf_with_shell_metacharacters_in_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output, result, password_file = (root / name for name in ("source.pdf", "output.pdf", "result.json", "password"))
            password = "'; touch /tmp/finance-pdf-injection; #"
            make_pdf(source, password=password)
            password_file.write_text(password, encoding="utf-8")
            completed = self.invoke("unlock", source, result, output=output, password_file=password_file)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with pikepdf.open(output) as pdf:
                self.assertEqual(len(pdf.pages), 1)
                self.assertFalse(pdf.is_encrypted)
            self.assertFalse(Path("/tmp/finance-pdf-injection").exists())

    def test_unlock_passwordless_wio_pdf_with_nonempty_ei_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output, result, password_file = (root / name for name in ("source.pdf", "output.pdf", "result.json", "password"))
            make_pdf(source)
            password_file.write_text("ei-statement-password", encoding="utf-8")
            completed = self.invoke("unlock", source, result, output=output, password_file=password_file)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with pikepdf.open(output) as pdf:
                self.assertEqual(len(pdf.pages), 1)
                self.assertFalse(pdf.is_encrypted)

    def test_unlock_rejects_encrypted_pdf_with_wrong_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output, result, password_file = (root / name for name in ("source.pdf", "output.pdf", "result.json", "password"))
            make_pdf(source, password="ei-statement-password")
            password_file.write_text("wrong-password", encoding="utf-8")
            completed = self.invoke("unlock", source, result, output=output, password_file=password_file)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(output.exists())
            self.assertFalse(result.exists())

    def test_validate_classifies_locked_pdf_without_persisting_or_requiring_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, result = root / "locked.pdf", root / "result.json"
            make_pdf(source, password="secret")
            completed = self.invoke("validate", source, result)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(payload["encrypted"])
            self.assertIsNone(payload["pages"])

    def test_rejects_invalid_header_corrupt_polyglot_active_content_and_page_bomb(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cases: list[Path] = []
            invalid = root / "invalid.pdf"; invalid.write_bytes(b"not a pdf\n%%EOF"); cases.append(invalid)
            corrupt = root / "corrupt.pdf"; corrupt.write_bytes(b"%PDF-1.7\nnot objects\n%%EOF"); cases.append(corrupt)
            valid = root / "base.pdf"; make_pdf(valid)
            polyglot = root / "polyglot.pdf"; polyglot.write_bytes(valid.read_bytes() + b"PK\x03\x04payloadPK\x05\x06"); cases.append(polyglot)
            active = root / "active.pdf"; active.write_bytes(valid.read_bytes().replace(b"%%EOF", b"/JavaScript\n%%EOF")); cases.append(active)
            bomb = root / "bomb.pdf"; make_pdf(bomb, pages=101); cases.append(bomb)
            for source in cases:
                with self.subTest(source=source.name):
                    result = root / f"{source.stem}.json"
                    completed = self.invoke("validate", source, result)
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertFalse(result.exists())

    def test_cli_rejects_unknown_operation_and_extra_argument(self) -> None:
        completed = subprocess.run([sys.executable, str(WORKER), "--operation", "shell", "--input", "x", "--result", "y"], capture_output=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        extra = subprocess.run([sys.executable, str(WORKER), "--operation", "validate", "--input", "x", "--result", "y", "--command", "id"], capture_output=True, check=False)
        self.assertNotEqual(extra.returncode, 0)


if __name__ == "__main__":
    unittest.main()
