"""Compile runtime-authoritative finance configuration fingerprints for n8n bootstrap."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "integrations" / "n8n" / "generated" / "config-versions.seed.json"
SOURCES = (
    "config/ai-policies.json",
    "config/agent-providers.json",
    "config/static-rules.seed.json",
    "config/transaction-email-sources.json",
    "config/statement-sources.json",
    "config/browser-sources.json",
    "config/cashback-programs.json",
    "config/document-processing.json",
    "config/evidence-search-policy.json",
)


def normalized_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def compile_versions() -> dict:
    rows = []
    for source in SOURCES:
        path = ROOT / source
        raw = normalized_bytes(path)
        document = json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
        declared = document.get("schema_version", document.get("version")) if isinstance(document, dict) else None
        version = str(declared) if declared is not None else f"sha256-{digest[:16]}"
        rows.append({
            "config_name": path.stem,
            "version": version,
            "source_path": source,
            "content_sha256": digest,
            "git_commit": "RUNTIME_BIND_GIT_COMMIT",
            "state": "ACTIVE",
            "readback_verified": False,
        })
    return {
        "schema_version": 1,
        "contract_status": "SPEC_ONLY",
        "rows": rows,
        "warning": "Generated hashes are Git-canonical LF fingerprints, not runtime import evidence.",
    }


def render(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    expected = render(compile_versions())
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
        return 0
    if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
        raise SystemExit(f"generated config version drift: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
