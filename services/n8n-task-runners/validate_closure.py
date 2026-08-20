#!/usr/bin/env python3
"""Validate and fingerprint a self-contained JavaScript runner closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path


SCHEMA_VERSION = 1
MANIFEST_NAME = "finance-closure-manifest.json"


class ClosureError(ValueError):
    """Raised when the deploy closure is not portable or contains unsafe nodes."""


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_and_fingerprint(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ClosureError(f"closure root is not a directory: {root}")

    digest = hashlib.sha256()
    file_count = 0
    symlink_count = 0
    total_bytes = 0

    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs.sort()
        files.sort()
        current_path = Path(current)
        for name in [*dirs, *files]:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative == MANIFEST_NAME:
                continue

            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                symlink_count += 1
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ClosureError(f"dangling or cyclic symlink: {relative}") from exc
                if not _within(target, root):
                    raise ClosureError(f"external symlink: {relative} -> {os.readlink(path)}")
                record = f"L\0{relative}\0{os.readlink(path)}\n".encode()
                digest.update(record)
                continue

            if stat.S_ISDIR(mode):
                digest.update(f"D\0{relative}\n".encode())
                continue

            if not stat.S_ISREG(mode):
                raise ClosureError(f"unsupported filesystem node: {relative}")

            file_count += 1
            size = path.stat().st_size
            total_bytes += size
            file_digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    file_digest.update(chunk)
            digest.update(
                f"F\0{relative}\0{stat.S_IMODE(mode):04o}\0{size}\0{file_digest.hexdigest()}\n".encode()
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm": "sha256-path-mode-content-v1",
        "files": file_count,
        "symlinks": symlink_count,
        "bytes": total_bytes,
        "closure_sha256": digest.hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("closure", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    manifest = validate_and_fingerprint(args.closure)
    manifest_path = args.manifest or args.closure / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
