from __future__ import annotations

import base64
from pathlib import Path
import socket
import socketserver
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import pikepdf

import server


def request(socket_path: str, request_bytes: bytes) -> bytes:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(10)
    client.connect(socket_path)
    client.sendall(request_bytes)
    client.shutdown(socket.SHUT_WR)
    chunks: list[bytes] = []
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    client.close()
    return b"".join(chunks)


def pdf_bytes(password: str | None = None) -> bytes:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "document.pdf"
        pdf = pikepdf.Pdf.new(); pdf.add_blank_page(page_size=(100, 100))
        if password is None:
            pdf.save(path)
        else:
            pdf.save(path, encryption=pikepdf.Encryption(user=password, owner=password, R=6))
        return path.read_bytes()


@unittest.skipUnless(hasattr(socketserver, "UnixStreamServer"), "Unix-domain socket server is validated in Linux container CI")
class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.socket_path = str(Path(self.temp.name) / "pdf.sock")
        self.original_worker = server.WORKER
        self.original_tempdir = tempfile.tempdir
        tempfile.tempdir = self.temp.name
        server.WORKER = str(Path(server.__file__).with_name("worker.py"))
        self.server = server.UnixServer(self.socket_path, server.PdfHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
        server.WORKER = self.original_worker
        tempfile.tempdir = self.original_tempdir
        self.temp.cleanup()

    def post(self, path: str, body: bytes, headers: dict[str, str] | None = None) -> bytes:
        values = {"Host": "localhost", "Content-Type": "application/pdf", "Content-Length": str(len(body)), "Connection": "close", **(headers or {})}
        raw_headers = "\r\n".join(f"{key}: {value}" for key, value in values.items())
        return request(self.socket_path, f"POST {path} HTTP/1.1\r\n{raw_headers}\r\n\r\n".encode() + body)

    def test_health_and_validate(self) -> None:
        health = request(self.socket_path, b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        self.assertIn(b" 200 ", health.splitlines()[0])
        validated = self.post("/v1/validate", pdf_bytes())
        self.assertIn(b" 200 ", validated.splitlines()[0])
        self.assertIn(b'"status":"valid"', validated)

    def test_unlock_accepts_opaque_password_without_command_interpretation(self) -> None:
        password = "$(touch /tmp/finance-pdf-server-injection)"
        response = self.post("/v1/unlock", pdf_bytes(password), {"X-Statement-Password": base64.b64encode(password.encode()).decode()})
        self.assertIn(b" 200 ", response.splitlines()[0])
        self.assertIn(b"application/pdf", response)
        self.assertFalse(Path("/tmp/finance-pdf-server-injection").exists())

    def test_rejects_unknown_path_profile_chunking_content_type_and_oversize(self) -> None:
        self.assertIn(b" 404 ", self.post("/v1/../../shell", b"12345678").splitlines()[0])
        self.assertIn(b" 400 ", self.post("/v1/profile", pdf_bytes(), {"X-Pdf-Profile": "arbitrary"}).splitlines()[0])
        wrong = request(self.socket_path, b"POST /v1/validate HTTP/1.1\r\nHost: localhost\r\nContent-Type: text/plain\r\nContent-Length: 8\r\nConnection: close\r\n\r\n12345678")
        self.assertIn(b" 415 ", wrong.splitlines()[0])
        chunked = request(self.socket_path, b"POST /v1/validate HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/pdf\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n0\r\n\r\n")
        self.assertIn(b" 400 ", chunked.splitlines()[0])
        oversized = request(self.socket_path, b"POST /v1/validate HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/pdf\r\nContent-Length: 99999999\r\nConnection: close\r\n\r\n")
        self.assertIn(b" 413 ", oversized.splitlines()[0])

    def test_error_response_redacts_parser_and_document_details(self) -> None:
        response = self.post("/v1/validate", b"not-pdf!\n%%EOF")
        self.assertIn(b" 422 ", response.splitlines()[0])
        self.assertNotIn(b"pikepdf", response.lower())
        self.assertNotIn(b"source.pdf", response.lower())

    def test_timeout_oom_and_post_decryption_failure_are_redacted_and_ephemeral(self) -> None:
        document = pdf_bytes()
        with patch.object(server.subprocess, "run", side_effect=subprocess.TimeoutExpired(["worker"], 30)):
            timed_out = self.post("/v1/validate", document)
        self.assertIn(b" 504 ", timed_out.splitlines()[0])
        killed = subprocess.CompletedProcess(["worker"], -9, b"", b"out of memory with secret details")
        with patch.object(server.subprocess, "run", return_value=killed):
            oom = self.post("/v1/validate", document)
        self.assertIn(b" 422 ", oom.splitlines()[0])
        self.assertNotIn(b"secret details", oom)

        def fail_after_output(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
            output_index = command.index("--output") + 1
            Path(command[output_index]).write_bytes(document)
            return subprocess.CompletedProcess(command, 2, b"", b"simulated failure")

        with patch.object(server.subprocess, "run", side_effect=fail_after_output):
            failed_unlock = self.post("/v1/unlock", document)
        self.assertIn(b" 422 ", failed_unlock.splitlines()[0])
        self.assertEqual(list(Path(self.temp.name).glob("finance-pdf-*")), [], "temporary decrypted artifacts must be removed")


if __name__ == "__main__":
    unittest.main()
