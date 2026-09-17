#!/usr/bin/env python3
"""Verify that a finance backup is safe, complete, and restorable.

The verifier never touches live data. It validates the archive checksum and
manifest, extracts regular files into a temporary directory without following
links, opens the authoritative SQLite databases read-only, and parses the JSON
state that would be needed after a restore.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


STAMP_PATTERN = re.compile(r"^20\d{6}T\d{6}Z$")
SHA256_PATTERN = re.compile(r"^(?P<digest>[0-9a-f]{64})\s+\*?(?P<name>[^\r\n]+)$")
REQUIRED_ARCHIVE_PATHS = (
    "actual-data/server-files/account.sqlite",
    "cashback-data/cashback-events.sqlite3",
    "configuration/actual-compose.yaml",
    "configuration/cashback-compose.yaml",
)
EXCLUDED_PUSH_DATA = {
    "cashback-data/cashback-events.sqlite3:push_subscriptions",
    "cashback-data/cashback-events.sqlite3:push_deliveries",
    "cashback-data/cashback-events.sqlite3:push_state",
}
EXCLUDED_CASHBACK_PATHS = {"cashback-data/pre-deploy-*.sqlite3*"}
PRIOR_V4_EXCLUDED_CASHBACK_PATHS = {
    "cashback-data/pre-deploy-*.sqlite3",
    "cashback-data/pre-deploy-*.sqlite3-wal",
    "cashback-data/pre-deploy-*.sqlite3-shm",
}
HISTORICAL_CASHBACK_MEMBER = re.compile(r"pre-deploy-[^/]+\.sqlite3(?:-[^/]*)?")
CURRENT_MANIFEST_SCHEMA = 4
LEGACY_MANIFEST_SCHEMA = 3


class VerificationError(RuntimeError):
    """Raised when a backup cannot safely be restored."""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_backup(backup_root: Path, requested: Path | None) -> Path:
    root = backup_root.resolve(strict=True)
    if requested is None:
        candidates = sorted(
            (path for path in root.iterdir() if path.is_dir() and STAMP_PATTERN.fullmatch(path.name)),
            reverse=True,
        )
        if not candidates:
            raise VerificationError("no timestamped backup exists")
        backup = candidates[0].resolve(strict=True)
    else:
        backup = requested.resolve(strict=True)
    if not _inside(backup, root) or backup.parent != root:
        raise VerificationError("backup path is outside the configured backup root")
    if not STAMP_PATTERN.fullmatch(backup.name):
        raise VerificationError("backup directory name is not a UTC timestamp")
    return backup


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid JSON: {path}") from exc


def _verify_manifest(backup: Path) -> dict[str, Any]:
    manifest = _load_json(backup / "manifest.json")
    if not isinstance(manifest, dict):
        raise VerificationError("backup manifest must be an object")
    schema_version = manifest.get("schema_version")
    if schema_version not in {CURRENT_MANIFEST_SCHEMA, LEGACY_MANIFEST_SCHEMA}:
        raise VerificationError("unsupported backup manifest schema")
    if manifest.get("secrets_included") is not False:
        raise VerificationError("backup manifest does not assert secret exclusion")
    includes = manifest.get("includes")
    if not isinstance(includes, list) or set(includes) != {
        "actual-data",
        "cashback-data",
        "configuration",
    }:
        raise VerificationError("backup manifest has incomplete scope")
    excluded_data = manifest.get("excluded_data")
    if not isinstance(excluded_data, list) or set(excluded_data) != EXCLUDED_PUSH_DATA:
        if schema_version == LEGACY_MANIFEST_SCHEMA:
            raise VerificationError("legacy v3 backup requires push-state classification")
        raise VerificationError("backup manifest does not classify excluded push state")
    excluded_paths = manifest.get("excluded_paths")
    if schema_version == CURRENT_MANIFEST_SCHEMA and (
        not isinstance(excluded_paths, list)
        or set(excluded_paths)
        not in (EXCLUDED_CASHBACK_PATHS, PRIOR_V4_EXCLUDED_CASHBACK_PATHS)
    ):
        raise VerificationError("backup manifest does not classify excluded historical snapshots")
    return manifest


def _verify_checksums(backup: Path) -> tuple[str, int]:
    checksum_path = backup / "SHA256SUMS"
    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise VerificationError("missing or unreadable SHA256SUMS") from exc
    if len(lines) != 1:
        raise VerificationError("SHA256SUMS must contain exactly one archive entry")
    match = SHA256_PATTERN.fullmatch(lines[0])
    if match is None or match.group("name") != "finance-data.tar.gz":
        raise VerificationError("SHA256SUMS does not identify the finance archive")
    archive = backup / match.group("name")
    digest = hashlib.sha256()
    size = 0
    try:
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise VerificationError("finance archive is unreadable") from exc
    actual = digest.hexdigest()
    if actual != match.group("digest"):
        raise VerificationError("finance archive checksum mismatch")
    return actual, size


def _safe_member_path(name: str) -> Path:
    normalized = name[2:] if name.startswith("./") else name
    posix = PurePosixPath(normalized)
    if not normalized or posix.is_absolute() or ".." in posix.parts:
        raise VerificationError(f"unsafe archive member: {name}")
    return Path(*posix.parts)


def _extract_regular_files(archive: Path, destination: Path) -> int:
    extracted = 0
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            for member in bundle:
                relative = _safe_member_path(member.name)
                if (
                    relative.parts[:1] == ("cashback-data",)
                    and HISTORICAL_CASHBACK_MEMBER.fullmatch(relative.name)
                ):
                    raise VerificationError(
                        f"archive contains excluded historical cashback snapshot: {member.name}"
                    )
                target = destination / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise VerificationError(f"archive contains a link or special file: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise VerificationError(f"archive member cannot be read: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                extracted += 1
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError("finance archive cannot be extracted") from exc
    return extracted


def _sqlite_integrity(path: Path) -> None:
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise VerificationError(f"SQLite database cannot be opened: {path}") from exc
    if rows != [("ok",)]:
        raise VerificationError(f"SQLite integrity check failed: {path}")


def _verify_excluded_push_data(path: Path) -> None:
    """Ensure ephemeral push credentials and delivery metadata were scrubbed."""
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            for table in ("push_subscriptions", "push_deliveries", "push_state"):
                if table in tables and connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]:
                    raise VerificationError(f"backup contains excluded push state: {table}")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise VerificationError(f"SQLite backup cannot inspect push state: {path}") from exc


def _verify_extracted(root: Path) -> tuple[list[str], int]:
    for relative in REQUIRED_ARCHIVE_PATHS:
        if not (root / relative).is_file():
            raise VerificationError(f"required backup path is missing: {relative}")

    user_databases = sorted((root / "actual-data/user-files").glob("*.sqlite"))
    if not user_databases:
        raise VerificationError("Actual budget database is missing")
    databases = sorted(
        {
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix in {".sqlite", ".sqlite3"}
        }
    )
    for database in databases:
        _sqlite_integrity(database)
        _verify_excluded_push_data(database)

    json_paths = sorted((root / "configuration").glob("*.json"))
    dashboard = root / "cashback-data/cashback-dashboard.json"
    if dashboard.is_file():
        json_paths.append(dashboard)
    for path in json_paths:
        _load_json(path)

    secret_paths = [path for path in root.rglob("*") if path.is_file() and path.name == ".env"]
    if secret_paths:
        raise VerificationError("backup unexpectedly contains an .env file")
    return [str(path.relative_to(root)).replace("\\", "/") for path in databases], len(json_paths)


def verify_backup(backup_root: Path, backup_path: Path | None, work_root: Path | None) -> dict[str, Any]:
    backup = _resolve_backup(backup_root, backup_path)
    manifest = _verify_manifest(backup)
    digest, archive_bytes = _verify_checksums(backup)
    temporary_parent = None if work_root is None else str(work_root.resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix="finance-restore-verify-", dir=temporary_parent) as temporary:
        extracted_root = Path(temporary)
        extracted_files = _extract_regular_files(backup / "finance-data.tar.gz", extracted_root)
        databases, json_documents = _verify_extracted(extracted_root)
    return {
        "status": "ok",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "backup": backup.name,
        "created_at": manifest.get("created_at"),
        "archive_sha256": digest,
        "archive_bytes": archive_bytes,
        "extracted_files": extracted_files,
        "sqlite_databases": databases,
        "json_documents": json_documents,
        "excluded_data": manifest["excluded_data"],
        "excluded_paths": manifest.get("excluded_paths", []),
    }


def _write_receipt(backup: Path, result: dict[str, Any]) -> None:
    temporary = backup / ".verification.json.tmp"
    final = backup / "verification.json"
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(final)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", type=Path, default=Path("/opt/backups/finance-actual"))
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--write-receipt", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        result = verify_backup(arguments.backup_root, arguments.backup_path, arguments.work_root)
        if arguments.write_receipt:
            backup = _resolve_backup(arguments.backup_root, arguments.backup_path)
            _write_receipt(backup, result)
    except (OSError, VerificationError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
