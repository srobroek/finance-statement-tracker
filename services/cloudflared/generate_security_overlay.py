#!/usr/bin/env python3
"""Build the reviewed Cloudflared Go security overlay reproducibly."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile


ROOT_FILES = ("go.mod", "go.sum", "vendor/modules.txt")
MODULE_ROOTS = (
    "vendor/google.golang.org/grpc",
    "vendor/google.golang.org/genproto/googleapis/api",
    "vendor/google.golang.org/genproto/googleapis/rpc",
)


def selected_files(source: Path) -> list[Path]:
    files = [source / relative for relative in ROOT_FILES]
    for relative in MODULE_ROOTS:
        files.extend(path for path in (source / relative).rglob("*") if path.is_file())
    result = sorted(files, key=lambda path: path.relative_to(source).as_posix())
    if not result or any(not path.is_file() for path in result):
        raise SystemExit("security overlay source is incomplete")
    return result


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    archive.addfile(info, io.BytesIO(data))


def build(source: Path, output: Path) -> None:
    source = source.resolve()
    files = selected_files(source)
    file_hashes = {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    manifest = json.dumps(
        {"schema_version": 1, "files": file_hashes},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                add_bytes(archive, "overlay-manifest.json", manifest)
                for path in files:
                    relative = path.relative_to(source).as_posix()
                    if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
                        raise SystemExit(f"unsafe overlay path: {relative}")
                    add_bytes(archive, relative, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)


if __name__ == "__main__":
    main()
