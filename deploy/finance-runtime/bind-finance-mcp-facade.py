#!/usr/bin/env python3
"""Bind the finance MCP bearer through the disposable n8n CLI boundary."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


ITEM_PATH = "FinanceRuntime/Finance Statement Tracker Runtime"
FIELD_LABEL = "finance_n8n_mcp_bearer"
ENVIRONMENT_NAME = "FINANCE_N8N_MCP_BEARER"
WORKFLOW_PATH = "finance-operations-v1"
CREDENTIAL_NAME = "Finance MCP Facade Bearer"
CREDENTIAL_TYPE = "httpBearerAuth"
OWNER_ROLE = "credential:owner"
PLACEHOLDER = "BIND_FINANCE_MCP_FACADE"
OP_REFERENCE = f"op://{ITEM_PATH}/{FIELD_LABEL}"
BINDER_ROOT = Path("/run/finance-mcp-binder")
PINNED_N8N_VERSION = "2.36.2"
# The caller must use `op run --env FINANCE_N8N_MCP_BEARER=<reference>`; this
# binder accepts only the resulting child environment and never reads the item.
# This namespace is an internal identity boundary; it is not emitted in receipts.
BINDER_NAMESPACE = uuid.UUID("4d7e3a41-9e83-4bf3-bf8c-c0f99c0a8c32")


class ContractError(RuntimeError):
    """A local contract failure that must not expose credentials or IDs."""


def deterministic_credential_id(name: str = CREDENTIAL_NAME) -> str:
    return str(uuid.uuid5(BINDER_NAMESPACE, name))


def _secret(value: str | None) -> str:
    if not value or any(character in value for character in "\r\n"):
        raise ContractError("FINANCE_N8N_MCP_BEARER must be a non-empty single-line value")
    return value


def credential_export(secret: str, credential_id: str | None = None) -> list[dict[str, Any]]:
    """Build the n8n import shape while keeping the value in the tmpfs file only."""

    return [
        {
            "id": credential_id or deterministic_credential_id(),
            "name": CREDENTIAL_NAME,
            "type": CREDENTIAL_TYPE,
            "data": {"token": _secret(secret)},
        }
    ]


def _replace_placeholder(value: Any, credential_id: str) -> tuple[Any, int]:
    if isinstance(value, str):
        return (credential_id if value == PLACEHOLDER else value), int(value == PLACEHOLDER)
    if isinstance(value, list):
        replaced: list[Any] = []
        count = 0
        for item in value:
            item_replaced, item_count = _replace_placeholder(item, credential_id)
            replaced.append(item_replaced)
            count += item_count
        return replaced, count
    if isinstance(value, dict):
        replaced_dict: dict[str, Any] = {}
        count = 0
        for key, item in value.items():
            item_replaced, item_count = _replace_placeholder(item, credential_id)
            replaced_dict[key] = item_replaced
            count += item_count
        return replaced_dict, count
    return value, 0


def bound_workflow(source: Path, credential_id: str) -> dict[str, Any]:
    if not source.is_file() or source.is_symlink():
        raise ContractError("W15 workflow source must be a regular file")
    workflow = json.loads(source.read_text(encoding="utf-8"))
    trigger = next(
        (node for node in workflow.get("nodes", []) if node.get("name") == "Finance MCP Server Trigger"),
        None,
    )
    if (
        workflow.get("active") is not False
        or workflow.get("activeVersionId") is not None
        or not trigger
        or trigger.get("parameters", {}).get("path") != WORKFLOW_PATH
        or trigger.get("credentials", {}).get(CREDENTIAL_TYPE, {}).get("id") != PLACEHOLDER
    ):
        raise ContractError("W15 inactive path or credential placeholder contract mismatch")
    bound = copy.deepcopy(workflow)
    bound_trigger = next(node for node in bound["nodes"] if node.get("name") == "Finance MCP Server Trigger")
    bound_trigger["credentials"][CREDENTIAL_TYPE]["id"] = credential_id
    if sum(
        node.get("credentials", {}).get(CREDENTIAL_TYPE, {}).get("id") == credential_id
        for node in bound["nodes"]
    ) != 1:
        raise ContractError("W15 credential placeholder count mismatch")
    return bound


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        descriptor = -1
    finally:
        if descriptor != -1:
            os.close(descriptor)
    os.chmod(path, 0o600)


def _command_from_env(name: str) -> list[str] | None:
    value = os.environ.get(name, "").strip()
    return shlex.split(value) if value else None


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    if environment is None:
        environment = os.environ.copy()
        environment.pop(ENVIRONMENT_NAME, None)
    try:
        completed = subprocess.run(
            command,
            check=False,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ContractError(f"command unavailable: {command[0]}") from exc
    if completed.returncode:
        raise ContractError(f"command failed: {command[0]}")


def _readback_metadata(path: Path, expected_project: str, credential_id: str) -> None:
    """Check only non-secret metadata returned by an operator-supplied reader."""

    if not path.is_file():
        raise ContractError("n8n metadata readback is missing")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("name") != CREDENTIAL_NAME or document.get("type") != CREDENTIAL_TYPE:
        raise ContractError("n8n credential metadata mismatch")
    if document.get("projectId") != expected_project or document.get("ownerRole") != OWNER_ROLE:
        raise ContractError("n8n credential owner metadata mismatch")
    if document.get("credentialPresent") is not True or document.get("secretValueRecorded") is not False:
        raise ContractError("n8n credential metadata is not redacted")
    if document.get("credentialId") not in (None, "REDACTED", credential_id):
        raise ContractError("unexpected credential identifier in metadata")


def _run_optional_metadata_reader(project_id: str, credential_id: str, root: Path) -> None:
    command = _command_from_env("FINANCE_MCP_METADATA_READER")
    readback_path = os.environ.get("FINANCE_MCP_METADATA_READBACK")
    if not command and not readback_path:
        return
    if command:
        readback_path = str(root / "metadata.json")
        environment = os.environ.copy()
        environment.pop(ENVIRONMENT_NAME, None)
        environment.update(
            {
                "FINANCE_MCP_METADATA_PROJECT": project_id,
                "FINANCE_MCP_METADATA_OUTPUT": readback_path,
                "FINANCE_MCP_METADATA_CREDENTIAL": "REDACTED",
            }
        )
        _run(command, environment=environment)
    _readback_metadata(Path(readback_path), project_id, credential_id)


def _run_decrypt_use_challenge(secret: str, root: Path) -> None:
    command = _command_from_env("FINANCE_MCP_DECRYPT_USE_CHALLENGE")
    if not command:
        return
    environment = os.environ.copy()
    environment.pop(ENVIRONMENT_NAME, None)
    environment.update({ENVIRONMENT_NAME: secret, "FINANCE_MCP_CHALLENGE_OUTPUT": str(root / "challenge.json")})
    _run(command, environment=environment)


def _cleanup_disposable(root: Path) -> None:
    command = _command_from_env("FINANCE_MCP_DISPOSABLE_CLEANUP")
    if not command:
        raise ContractError("decrypt/use failure requires an authorized disposable cleanup command")
    environment = os.environ.copy()
    environment.pop(ENVIRONMENT_NAME, None)
    environment.update(
        {
            "FINANCE_MCP_CLEANUP_ACK": "REMOVE_W15_FINANCE_MCP_ONLY",
            "FINANCE_MCP_CLEANUP_OUTPUT": str(root / "cleanup.json"),
        }
    )
    _run(command, environment=environment)


def _unmount_if_requested() -> None:
    mount = os.environ.get("FINANCE_MCP_BINDER_MOUNT", "")
    if mount != str(BINDER_ROOT):
        return
    umount = shutil.which("umount")
    if umount:
        subprocess.run([umount, str(BINDER_ROOT)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, default=None)
    parser.add_argument("--project-id", default=os.environ.get("N8N_FINANCE_PROJECT_ID", ""))
    parser.add_argument("--n8n", default=os.environ.get("N8N_CLI", "n8n"))
    parser.add_argument("--binder-root", type=Path, default=Path(os.environ.get("FINANCE_MCP_BINDER_ROOT", BINDER_ROOT)))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    secret = _secret(os.environ.get(ENVIRONMENT_NAME))
    if not args.project_id or any(character in args.project_id for character in "\r\n"):
        raise ContractError("N8N_FINANCE_PROJECT_ID is required")
    source = args.workflow or Path(__file__).resolve().parents[2] / "integrations/n8n/workflows/15-finance-mcp-facade.json"
    if os.environ.get("N8N_EXPECTED_VERSION", PINNED_N8N_VERSION) != PINNED_N8N_VERSION:
        raise ContractError(f"n8n CLI must be pinned to {PINNED_N8N_VERSION}")
    credential_id = deterministic_credential_id()
    root = args.binder_root
    credential_file = root / "finance-mcp-facade-credential.json"
    workflow_file = root / "15-finance-mcp-facade-bound.json"
    try:
        _write_private(credential_file, json.dumps(credential_export(secret, credential_id), separators=(",", ":")).encode())
        workflow = bound_workflow(source, credential_id)
        _write_private(workflow_file, (json.dumps(workflow, separators=(",", ":")) + "\n").encode())
        _run([args.n8n, "import:credentials", f"--input={credential_file}", f"--projectId={args.project_id}"])
        _run([args.n8n, "import:workflow", f"--input={workflow_file}", f"--projectId={args.project_id}", "--activeState=false"])
        _run_optional_metadata_reader(args.project_id, credential_id, root)
        try:
            _run_decrypt_use_challenge(secret, root)
        except ContractError:
            _cleanup_disposable(root)
            _run([args.n8n, "import:credentials", f"--input={credential_file}", f"--projectId={args.project_id}"])
            _run([args.n8n, "import:workflow", f"--input={workflow_file}", f"--projectId={args.project_id}", "--activeState=false"])
            _run_optional_metadata_reader(args.project_id, credential_id, root)
        print(json.dumps({"status": "VERIFIED", "credential_present": True, "owner": True, "active": False, "published": False, "values": "REDACTED", "ids": "REDACTED"}, separators=(",", ":")))
        return 0
    finally:
        os.environ.pop(ENVIRONMENT_NAME, None)
        for path in (credential_file, workflow_file, root / "metadata.json", root / "challenge.json", root / "cleanup.json"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        _unmount_if_requested()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
