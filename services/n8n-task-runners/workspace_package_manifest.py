#!/usr/bin/env python3
"""Bind shipped first-party runner packages to the pinned workspace manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PACKAGE_PATHS = {
    "@n8n/config": "packages/@n8n/config",
    "@n8n/di": "packages/@n8n/di",
    "@n8n/errors": "packages/@n8n/errors",
    "@n8n/utils": "packages/@n8n/utils",
    "n8n-core": "packages/core",
    "n8n-workflow": "packages/workflow",
}


def _identity(package_json: Path) -> tuple[str, str]:
    package = json.loads(package_json.read_text(encoding="utf-8"))
    return package["name"], package["version"]


def build_manifest(closure: Path, workspace: Path) -> dict[str, object]:
    closure = closure.resolve(strict=True)
    workspace = workspace.resolve(strict=True)
    packages: dict[str, str] = {}
    for name, source_relative in PACKAGE_PATHS.items():
        source_identity = _identity(workspace / source_relative / "package.json")
        shipped = closure / "node_modules" / Path(*name.split("/"))
        shipped_real = shipped.resolve(strict=True)
        try:
            shipped_real.relative_to(closure)
        except ValueError as exc:
            raise ValueError(f"shipped package escapes closure: {name}") from exc
        shipped_identity = _identity(shipped_real / "package.json")
        if source_identity != shipped_identity or source_identity[0] != name:
            raise ValueError(f"workspace/shipped identity mismatch: {name}")
        packages[name] = source_identity[1]
    return {"schema_version": 1, "packages": packages}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("closure", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.closure, args.workspace)
    args.output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
