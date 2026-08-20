#!/usr/bin/env python3
"""Relink pnpm legacy-deploy workspace links to copies already in its store."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


RELINK_MANIFEST = "finance-closure-relinks.json"


class RelinkError(ValueError):
    """Raised when an external workspace link cannot be repaired unambiguously."""


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _package_metadata(package_root: Path) -> tuple[str, str]:
    try:
        package = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
        name = package["name"]
        version = package["version"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise RelinkError(f"invalid package metadata: {package_root}") from exc
    if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
        raise RelinkError(f"invalid package identity: {package_root}")
    return name, version


def _find_workspace_package(target: Path, workspace: Path) -> tuple[Path, Path]:
    candidate = target if target.is_dir() else target.parent
    while _within(candidate, workspace):
        if (candidate / "package.json").is_file():
            return candidate, target.relative_to(candidate)
        if candidate == workspace:
            break
        candidate = candidate.parent
    raise RelinkError(f"external link target is not a workspace package: {target.relative_to(workspace)}")


def _store_candidates(store: Path, name: str, version: str) -> list[Path]:
    parts = name.split("/")
    pattern = f"*/node_modules/{'/'.join(parts)}/package.json"
    matches: list[Path] = []
    for package_json in sorted(store.glob(pattern)):
        package_root = package_json.parent
        if package_root.is_symlink():
            continue
        try:
            candidate_name, candidate_version = _package_metadata(package_root)
        except RelinkError:
            continue
        if (candidate_name, candidate_version) == (name, version):
            matches.append(package_root)
    return matches


def relink_external_workspace_links(closure: Path, workspace: Path) -> list[dict[str, str]]:
    closure = closure.resolve(strict=True)
    workspace = workspace.resolve(strict=True)
    store = closure / "node_modules" / ".pnpm"
    if not store.is_dir():
        raise RelinkError("closure has no pnpm virtual store")

    links: list[Path] = []
    for current, dirs, files in os.walk(closure, topdown=True, followlinks=False):
        dirs.sort()
        files.sort()
        current_path = Path(current)
        links.extend(path for path in (current_path / name for name in [*dirs, *files]) if path.is_symlink())

    changes: list[dict[str, str]] = []
    for link in sorted(links):
        relative_link = link.relative_to(closure)
        try:
            target = link.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RelinkError(f"dangling or cyclic symlink: {relative_link}") from exc
        if _within(target, closure):
            continue
        if not _within(target, workspace):
            raise RelinkError(f"external link escapes pinned workspace: {relative_link}")

        package_root, suffix = _find_workspace_package(target, workspace)
        name, version = _package_metadata(package_root)
        workspace_relative = package_root.relative_to(workspace)
        encoded = "+".join(workspace_relative.parts)
        candidates = [
            candidate
            for candidate in _store_candidates(store, name, version)
            if f"@file+{encoded}" in candidate.relative_to(store).parts[0]
        ]
        if len(candidates) != 1:
            raise RelinkError(
                f"expected one in-closure copy for {name}@{version} ({relative_link}), found {len(candidates)}"
            )
        replacement = candidates[0] / suffix
        if not replacement.exists():
            raise RelinkError(f"in-closure target is missing for {relative_link}: {replacement.relative_to(closure)}")

        relative_target = os.path.relpath(replacement, link.parent)
        link.unlink()
        link.symlink_to(relative_target, target_is_directory=replacement.is_dir())
        changes.append(
            {
                "link": relative_link.as_posix(),
                "package": name,
                "version": version,
                "target": replacement.relative_to(closure).as_posix(),
            }
        )
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("closure", type=Path)
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()

    changes = relink_external_workspace_links(args.closure, args.workspace)
    manifest = {"schema_version": 1, "relinked": len(changes), "links": changes}
    output = args.closure / RELINK_MANIFEST
    output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"relinked": len(changes)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
