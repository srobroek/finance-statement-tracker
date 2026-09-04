#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_root}"

command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }
command -v npm >/dev/null || { echo "npm is required" >&2; exit 2; }

# Keep the local command identical to the dependency closure used by CI.
uv sync --frozen --extra statements --extra test

npm --prefix packages/n8n-nodes-finance ci --ignore-scripts
npm --prefix integrations/actual ci
node integrations/n8n/generate_browser_capture_validator.mjs --check

uv run --frozen python -m unittest discover -s tests -v
uv run --frozen python -m compileall -q finance_tracker apps scripts deploy services

uv run --frozen python - <<'PY'
import json
import os
import subprocess
from pathlib import Path

paths = [
    os.fsdecode(raw)
    for raw in subprocess.check_output(["git", "ls-files", "-z", "--", "*.json"]).split(b"\0")
    if raw
]
if not paths:
    raise SystemExit("no tracked JSON files found")
for name in paths:
    with Path(name).open(encoding="utf-8") as handle:
        json.load(handle)
print(f"validated {len(paths)} tracked JSON files")
PY

npm --prefix integrations/actual test
npm --prefix packages/n8n-nodes-finance test
node packages/n8n-nodes-finance/scripts/actual-session-offline-integration.mjs
npm --prefix integrations/actual run integration

# The PDF utility intentionally has an independent pinned runtime.  Sync it
# last so the project environment used above remains the single Python test
# environment for the rest of the command.
uv pip sync --python "${repo_root}/.venv/bin/python" services/pdf-utility/requirements.txt
(
    cd services/pdf-utility
    "${repo_root}/.venv/bin/python" -m unittest discover -s tests -v
)
