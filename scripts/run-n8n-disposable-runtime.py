#!/usr/bin/env python3
"""Execute source-derived disposable workflows in a real n8n CLI runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "integrations" / "n8n" / "disposable"
SUCCESS_FILE = "101-error-redaction.json"
FAILURE_FILE = "94-sweep-pagination-failure.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def derived_sources(workflow: dict) -> set[str]:
    sources: set[str] = set()
    source = (workflow.get("meta") or {}).get("derivedFrom")
    if isinstance(source, str):
        sources.add(source)
    for node in workflow.get("nodes", []):
        if node.get("type") != "n8n-nodes-base.executeWorkflow":
            continue
        inline = (node.get("parameters") or {}).get("workflowJson")
        if isinstance(inline, str):
            sources.update(derived_sources(json.loads(inline)))
    return sources


def validate_fixtures(fixture_root: Path) -> dict:
    manifest = json.loads((fixture_root / "fixture-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("required_acknowledgement") != "DISPOSABLE_ONLY":
        raise RuntimeError("fixture manifest is not DISPOSABLE_ONLY")
    by_file = {row["file"]: row for row in manifest.get("workflows", [])}
    for filename, source in (
        (SUCCESS_FILE, "16-operations-error-handler.json"),
        (FAILURE_FILE, "12-outlook-message-sweep.json"),
    ):
        path = fixture_root / "generated" / filename
        row = by_file.get(filename)
        if not row or sha256(path) != row.get("sha256"):
            raise RuntimeError(f"fixture digest mismatch: {filename}")
        workflow = json.loads(path.read_text(encoding="utf-8"))
        meta = workflow.get("meta") or {}
        if not meta.get("disposableOnly") or source not in derived_sources(workflow):
            raise RuntimeError(f"fixture is not source-derived and disposable-only: {filename}")
        if workflow.get("active"):
            raise RuntimeError(f"disposable fixture must be inactive: {filename}")
    return manifest


def run(command: list[str], env: dict[str, str], *, expect_success: bool) -> str:
    result = subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
        check=False,
    )
    if (result.returncode == 0) != expect_success:
        print(result.stdout)
        expectation = "success" if expect_success else "failure"
        raise RuntimeError(f"n8n command did not produce expected {expectation}: {' '.join(command)}")
    return result.stdout


def terminal_redaction_receipt(output: str) -> dict:
    marker = "Execution was successful:"
    try:
        payload_start = output.index("{", output.index(marker) + len(marker))
        execution = json.loads(output[payload_start:])
        runs = execution["data"]["resultData"]["runData"]
        terminal = runs["Read Back Verified Failure Receipt"][-1]["data"]["main"][0][0]["json"]
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("n8n output omitted the terminal redaction receipt") from error
    if not isinstance(terminal, dict):
        raise RuntimeError("n8n terminal redaction receipt is malformed")
    return terminal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument(
        "--runtime-fixture-root",
        type=Path,
        help="Fixture path visible to n8n (for example, the container mount path)",
    )
    parser.add_argument("--n8n", default=os.environ.get("N8N_EXECUTABLE", "n8n"))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    fixture_root = args.fixture_root.resolve()
    manifest = validate_fixtures(fixture_root)
    if args.validate_only:
        print("disposable n8n runtime fixtures validated")
        return 0
    if os.environ.get("DISPOSABLE_ONLY_ACK") != manifest["required_acknowledgement"]:
        raise RuntimeError("set DISPOSABLE_ONLY_ACK=DISPOSABLE_ONLY to execute fixtures")

    runtime_root = (args.runtime_fixture_root or fixture_root).resolve()
    executable = str(Path(args.n8n).resolve()) if os.sep in args.n8n else args.n8n
    with tempfile.TemporaryDirectory(prefix="n8n-disposable-") as state_dir:
        env = {
            **os.environ,
            "N8N_USER_FOLDER": state_dir,
            "N8N_DIAGNOSTICS_ENABLED": "false",
            "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
            "N8N_RUNNERS_ENABLED": "false",
        }
        for filename in (SUCCESS_FILE, FAILURE_FILE):
            run(
                [executable, "import:workflow", "--input", str(runtime_root / "generated" / filename)],
                env,
                expect_success=True,
            )

        success_id = next(
            row["id"] for row in manifest["workflows"] if row["file"] == SUCCESS_FILE
        )
        receipts = [
            terminal_redaction_receipt(
                run([executable, "execute", "--id", success_id], env, expect_success=True)
            )
            for _ in range(2)
        ]
        forbidden = manifest["scenario_contract"]["error_redaction"]["forbidden_readback"]
        for index, receipt in enumerate(receipts, start=1):
            rendered = json.dumps(receipt, sort_keys=True)
            if any(secret in rendered for secret in forbidden):
                raise RuntimeError(f"redaction fixture replay {index} exposed forbidden data")
            if (
                receipt.get("error_message_redacted")
                != "password:[REDACTED] token:[REDACTED] card:[REDACTED]"
                or receipt.get("terminal_receipt_sink") != "n8n_execution_history"
                or receipt.get("readback_verified") is not True
            ):
                raise RuntimeError(f"redaction fixture replay {index} failed terminal assertions")
        stable_fields = (
            "execution_id", "workflow_id", "workflow_code", "provider_code",
            "error_class", "error_message_redacted", "terminal_receipt_sink",
            "readback_verified",
        )
        if any(receipts[0].get(field) != receipts[1].get(field) for field in stable_fields):
            raise RuntimeError("redaction fixture replay changed stable terminal fields")

        failure_id = next(
            row["id"] for row in manifest["workflows"] if row["file"] == FAILURE_FILE
        )
        failure = run([executable, "execute", "--id", failure_id], env, expect_success=False)
        if "FIXTURE_PAGE_2_FAILURE" not in failure:
            raise RuntimeError("pagination failure did not reach the source-derived failure branch")

    print("n8n 2.36.2 disposable runtime: replay and failure routes verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
