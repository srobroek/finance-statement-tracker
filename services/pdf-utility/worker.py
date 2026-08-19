from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

try:
    import resource
except ImportError:  # Windows test host; production image is Linux.
    resource = None

import pikepdf
from pypdf import PdfReader

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 100
MAX_EXTRACTED_TEXT_BYTES = 5 * 1024 * 1024
ACTIVE_TOKENS = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction", b"/AA")


def apply_limits() -> None:
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (160 * 1024 * 1024, 160 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))
    except (ValueError, OSError):
        pass


def static_validate(data: bytes) -> None:
    if len(data) < 8 or len(data) > MAX_BYTES:
        raise ValueError("PDF size is outside the fixed limit")
    if not data.startswith(b"%PDF-"):
        raise ValueError("PDF header must begin at byte zero")
    eof = data.rfind(b"%%EOF")
    if eof < 0 or len(data) - (eof + 5) > 4096:
        raise ValueError("PDF EOF marker is absent or has an excessive trailing payload")
    trailer = data[eof + 5 :]
    if b"PK\x05\x06" in trailer or b"PK\x03\x04" in trailer:
        raise ValueError("PDF polyglot trailer is forbidden")
    if any(token in data for token in ACTIVE_TOKENS):
        raise ValueError("active or embedded PDF content is forbidden")


def read_password(path: str | None) -> str:
    if not path:
        return ""
    password_path = Path(path)
    if password_path.stat().st_size > 1024:
        raise ValueError("password exceeds fixed limit")
    return password_path.read_text(encoding="utf-8")


def inspect_pdf(source: Path, password: str, *, allow_locked: bool) -> dict[str, object]:
    data = source.read_bytes()
    static_validate(data)
    try:
        with pikepdf.open(source, password=password) as pdf:
            page_count = len(pdf.pages)
            if page_count < 1 or page_count > MAX_PAGES:
                raise ValueError("PDF page count is outside the fixed limit")
            encrypted = bool(pdf.is_encrypted)
    except pikepdf.PasswordError:
        if not allow_locked or password:
            raise
        page_count = None
        encrypted = True
    return {
        "status": "valid",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "pages": page_count,
        "encrypted": encrypted,
        "profile": "statement-v1",
        "active_content": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--operation", choices=("validate", "unlock", "profile"), required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--password-file")
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    apply_limits()
    source = Path(args.input)
    result_path = Path(args.result)
    password = read_password(args.password_file)
    metadata = inspect_pdf(source, password, allow_locked=args.operation == "validate")
    if args.operation == "profile":
        reader = PdfReader(source, strict=True)
        chunks: list[str] = []
        size = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            size += len(text.encode("utf-8"))
            if size > MAX_EXTRACTED_TEXT_BYTES:
                raise ValueError("extracted PDF text exceeds fixed limit")
            chunks.append(text)
        metadata = {**metadata, "status": "profiled", "extracted_text": "\n\n".join(chunks)}
    if args.operation == "unlock":
        if not args.output:
            raise ValueError("unlock requires the fixed output slot")
        with pikepdf.open(source, password=password) as pdf:
            pdf.save(args.output, encryption=False, linearize=False)
        unlocked = Path(args.output).read_bytes()
        static_validate(unlocked)
        metadata = {**metadata, "status": "unlocked", "output_sha256": hashlib.sha256(unlocked).hexdigest(), "output_bytes": len(unlocked)}
    result_path.write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"pdf operation failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2)
