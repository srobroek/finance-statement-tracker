#!/usr/bin/env python3
"""Publish external finance workflow inputs without building runtime images."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# Conservative build-input boundaries: changing a copied parser/config/dependency
# requires the matching image release. Workflow exports and renderers are absent.
RUNTIME_INPUTS = {
    "n8n": ["packages/n8n-nodes-finance", "config/ai-policies.json", "integrations/n8n/contracts/ai-proposal-v1.schema.json", "integrations/n8n/generated/ai-policy-contracts.seed.json", "integrations/n8n/generated/n8n-runtime-rules.json", ".github/workflows/phase1-finance-artifacts.yml", ".dockerignore"],
    "task_runners": ["services/n8n-task-runners", "finance_tracker", "config", "docs", "tests/fixtures", "pyproject.toml", "requirements.txt", ".github/workflows/phase1-finance-artifacts.yml", ".dockerignore"],
    "pdf_utility": ["services/pdf-utility", ".github/workflows/phase1-finance-artifacts.yml"],
}


def git_tree_digest(root: Path, commit: str, paths: list[str]) -> str:
    raw = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "-z", "--full-tree", commit, "--", *paths])
    if not raw:
        raise ValueError("Runtime input tree is empty")
    return hashlib.sha256(raw).hexdigest()


def stage(root: Path, destination: Path, source_commit: str) -> dict:
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if source_commit != actual:
        raise ValueError("Release must use the exact checked-out source commit")
    if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        raise ValueError("Release source has tracked modifications")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Release destination must be empty")
    spec = importlib.util.spec_from_file_location("application_adapter", root / "integrations/n8n/application-interface/adapter.py")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    adapter.stage_application(root, destination, source_commit)
    files = {str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(destination.rglob("*")) if path.is_file()}
    release = {"schema_version": 1, "application_id": "finance-statement-tracker", "source_commit": source_commit,
               "manifest": "application-manifest.json", "files": files,
               "runtime_inputs": {role: {"paths": paths, "tree_sha256": git_tree_digest(root, source_commit, paths)}
                                  for role, paths in RUNTIME_INPUTS.items()}}
    # base-image reference includes a registry port/version and sha256 suffix.
    reference = (root / "packages/n8n-nodes-finance/base-image.txt").read_text().strip()
    release["n8n_version"] = reference.split("@", 1)[0].rsplit(":", 1)[1]
    (destination / "workflow-release.json").write_text(json.dumps(release, indent=2) + "\n")
    return release


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    release = stage(args.source_root.resolve(), args.destination.resolve(), args.source_commit)
    print(json.dumps({"status": "STAGED", "source_commit": release["source_commit"], "files": len(release["files"]), "runtime_images_built": False}))
