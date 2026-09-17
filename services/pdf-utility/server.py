from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile

SOCKET_PATH = "/run/platform-pdf/pdf.sock"
MAX_BYTES = 25 * 1024 * 1024
OPERATIONS = {"/v1/validate": "validate", "/v1/unlock": "unlock", "/v1/profile": "profile"}
WORKER = "/opt/platform-pdf/worker.py"


class PdfHandler(BaseHTTPRequestHandler):
    server_version = "finance-pdf/0.1"

    def log_message(self, format: str, *args: object) -> None:
        # Do not log request headers, filenames, passwords, or document facts.
        sys.stderr.write(f"pdf-utility status={args[1] if len(args) > 1 else '-'}\n")

    def _json(self, code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._json(404, {"error": "not found"})
            return
        self._json(200, {"status": "ok"})

    def do_POST(self) -> None:
        operation = OPERATIONS.get(self.path)
        if operation is None:
            self._json(404, {"error": "unknown fixed operation"})
            return
        if self.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/pdf":
            self._json(415, {"error": "application/pdf required"})
            return
        if self.headers.get("transfer-encoding"):
            self._json(400, {"error": "chunked requests are forbidden"})
            return
        try:
            length = int(self.headers.get("content-length", "-1"))
        except ValueError:
            length = -1
        if length < 8 or length > MAX_BYTES:
            self._json(413, {"error": "PDF size is outside the fixed limit"})
            return
        if operation == "profile" and self.headers.get("x-pdf-profile") != "statement-v1":
            self._json(400, {"error": "unknown PDF profile"})
            return
        encoded_password = self.headers.get("x-statement-password")
        if operation != "unlock" and encoded_password:
            self._json(400, {"error": "password is only accepted for unlock"})
            return
        try:
            password = base64.b64decode(encoded_password, validate=True).decode("utf-8") if encoded_password else ""
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid password encoding"})
            return
        if len(password.encode("utf-8")) > 1024:
            self._json(400, {"error": "password exceeds fixed limit"})
            return
        data = self.rfile.read(length)
        if len(data) != length:
            self._json(400, {"error": "truncated body"})
            return
        try:
            with tempfile.TemporaryDirectory(prefix="finance-pdf-") as temp:
                root = Path(temp)
                source = root / "source.pdf"
                output = root / "output.pdf"
                result = root / "result.json"
                source.write_bytes(data)
                source.chmod(0o600)
                command = [sys.executable, WORKER, "--operation", operation, "--input", str(source), "--result", str(result)]
                if password:
                    password_file = root / "password"
                    password_file.write_text(password, encoding="utf-8")
                    password_file.chmod(0o600)
                    command.extend(["--password-file", str(password_file)])
                if operation == "unlock":
                    command.extend(["--output", str(output)])
                completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30, check=False, env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "/opt/platform-pdf"})
                if completed.returncode != 0 or not result.is_file():
                    self._json(422, {"error": "PDF validation or extraction failed"})
                    return
                metadata = json.loads(result.read_text(encoding="utf-8"))
                if operation == "unlock":
                    body = output.read_bytes()
                    self.send_response(200)
                    self.send_header("content-type", "application/pdf")
                    self.send_header("content-length", str(len(body)))
                    self.send_header("x-source-sha256", str(metadata["sha256"]))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json(200, metadata)
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "PDF operation timed out"})
        except Exception:
            self._json(500, {"error": "PDF operation failed"})


_UnixStreamServer = getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)


class UnixServer(_UnixStreamServer):
    allow_reuse_address = False


def main() -> None:
    if not hasattr(socketserver, "UnixStreamServer"):
        raise RuntimeError("finance PDF utility requires Unix-domain sockets")
    socket = Path(SOCKET_PATH)
    socket.parent.mkdir(parents=True, exist_ok=True)
    if socket.exists():
        socket.unlink()
    server = UnixServer(SOCKET_PATH, PdfHandler)
    os.chmod(SOCKET_PATH, 0o660)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
