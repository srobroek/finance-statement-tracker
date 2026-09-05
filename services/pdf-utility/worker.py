from __future__ import annotations

import argparse
import hashlib
import json
import math
from decimal import Decimal
import os
from pathlib import Path
import sys

try:
    import resource
except ImportError:  # Windows test host; production image is Linux.
    resource = None

import pikepdf
import pdfplumber

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 100
MAX_EXTRACTED_TEXT_BYTES = 5 * 1024 * 1024
ACTIVE_TOKENS = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/AA")


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


def validate_structure(pdf: pikepdf.Pdf) -> None:
    """Inspect decoded objects; raw token checks cannot see compressed names."""
    page_ids = {page.obj.objgen for page in pdf.pages}
    forbidden = {"/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/AA"}
    stack = [(pdf.Root, 0), *((obj, 0) for obj in pdf.objects)]
    visited: set[tuple[int, int]] = set()
    examined = 0
    while stack:
        obj, depth = stack.pop()
        examined += 1
        if examined > 100000 or depth > 100:
            raise ValueError("PDF object structure exceeds fixed limits")
        if isinstance(obj, pikepdf.Object) and obj.is_indirect:
            if obj.objgen in visited:
                continue
            visited.add(obj.objgen)
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            if any(str(key) in forbidden for key in obj.keys()):
                raise ValueError("active or embedded PDF content is forbidden")
            if str(obj.get("/S", "")) in forbidden or str(obj.get("/Type", "")) == "/EmbeddedFile":
                raise ValueError("active or embedded PDF content is forbidden")
            if "/OpenAction" in obj:
                if obj.objgen != pdf.Root.objgen:
                    raise ValueError("OpenAction is only allowed on the PDF catalog")
                validate_open_destination(obj["/OpenAction"], page_ids)
            stack.extend((value, depth + 1) for value in obj.values())
        elif isinstance(obj, pikepdf.Array):
            stack.extend((value, depth + 1) for value in obj)


def validate_open_destination(value: object, page_ids: set[tuple[int, int]]) -> None:
    # Never accept an action dictionary, named destination, page number,
    # external reference, or malformed array as an automatic open action.
    if not isinstance(value, pikepdf.Array) or len(value) < 2:
        raise ValueError("OpenAction must be an explicit local page destination")
    page = value[0]
    if not isinstance(page, pikepdf.Dictionary) or not page.is_indirect or page.objgen not in page_ids:
        raise ValueError("OpenAction destination must reference an existing local page")
    kind = str(value[1])
    lengths = {"/Fit": 2, "/FitB": 2, "/FitH": 3, "/FitBH": 3,
               "/FitV": 3, "/FitBV": 3, "/XYZ": 5, "/FitR": 6}
    if not isinstance(value[1], pikepdf.Name) or lengths.get(kind) != len(value):
        raise ValueError("OpenAction destination has an invalid view mode or arity")
    for coordinate in list(value)[2:]:
        if coordinate is None and kind != "/FitR":
            continue
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float, Decimal)) or not math.isfinite(float(coordinate)):
            raise ValueError("OpenAction coordinates must be finite numbers")
    if kind == "/XYZ" and value[4] is not None and value[4] < 0:
        raise ValueError("OpenAction zoom must not be negative")
    if kind == "/FitR" and (value[2] >= value[4] or value[3] >= value[5]):
        raise ValueError("OpenAction rectangle must have positive area")


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
    structure_verified = False
    try:
        with pikepdf.open(source, password=password) as pdf:
            page_count = len(pdf.pages)
            if page_count < 1 or page_count > MAX_PAGES:
                raise ValueError("PDF page count is outside the fixed limit")
            validate_structure(pdf)
            structure_verified = True
            encrypted = bool(pdf.is_encrypted)
    except pikepdf.PasswordError:
        if not allow_locked or password:
            raise
        page_count = None
        encrypted = True
    return {
        "status": "valid" if structure_verified else "locked",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "pages": page_count,
        "encrypted": encrypted,
        "profile": "statement-v1",
        "active_content": False if structure_verified else None,
        "structure_verified": structure_verified,
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
        chunks: list[str] = []
        size = 0
        # Match finance_tracker.statements.extract_pdf_text exactly. Stream
        # reading order can separate issuer period labels from their dates.
        with pdfplumber.open(source, password=password or None) as reader:
            for page in reader.pages:
                text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                size += len(text.encode("utf-8"))
                if size > MAX_EXTRACTED_TEXT_BYTES:
                    raise ValueError("extracted PDF text exceeds fixed limit")
                chunks.append(text)
                page.close()
        metadata = {**metadata, "status": "profiled", "extracted_text": "\n\n".join(chunks),
                    "extraction_engine": "pdfplumber", "extraction_version": pdfplumber.__version__,
                    "extraction_x_tolerance": 2, "extraction_y_tolerance": 3}
    if args.operation == "unlock":
        if not args.output:
            raise ValueError("unlock requires the fixed output slot")
        with pikepdf.open(source, password=password) as pdf:
            pdf.save(args.output, encryption=False, linearize=False)
        unlocked = Path(args.output).read_bytes()
        inspect_pdf(Path(args.output), "", allow_locked=False)
        metadata = {**metadata, "status": "unlocked", "output_sha256": hashlib.sha256(unlocked).hexdigest(), "output_bytes": len(unlocked)}
    result_path.write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"pdf operation failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2)
