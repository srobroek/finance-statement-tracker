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
# The caller must use `op run --env-file=<mode-0600-template>`; this
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
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ContractError("Binder root must be an existing directory")
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


def _require_tmpfs(root: Path) -> None:
    if not root.is_dir() or root.is_symlink() or root.stat().st_mode & 0o777 != 0o700:
        raise ContractError("Binder root must be a mode-0700 directory")
    if os.environ.get("FINANCE_MCP_BINDER_MOUNT") != str(root):
        raise ContractError("Binder root ownership marker is required")
    findmnt = shutil.which("findmnt")
    if not findmnt:
        raise ContractError("findmnt is required to verify the binder tmpfs")
    result = subprocess.run(
        [findmnt, "--noheadings", "--output", "FSTYPE", "--target", str(root)],
        check=False,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != ENVIRONMENT_NAME},
    )
    if result.returncode or result.stdout.strip() != "tmpfs":
        raise ContractError("Binder root must be a tmpfs mount")


def _require_pinned_n8n(n8n: str) -> None:
    environment = os.environ.copy()
    environment.pop(ENVIRONMENT_NAME, None)
    try:
        result = subprocess.run(
            [n8n, "--version"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as exc:
        raise ContractError("Pinned n8n CLI is unavailable") from exc
    if result.returncode or result.stdout.strip() != PINNED_N8N_VERSION:
        raise ContractError(f"n8n CLI version must be exactly {PINNED_N8N_VERSION}")


def _readback_metadata(
    path: Path,
    expected_project: str,
    credential_id: str,
    *,
    require_decrypt_use: bool = True,
) -> bool:
    """Check the redacted preflight/readback state and return presence."""
    try:
        if not path.is_file():
            raise ContractError("n8n metadata readback is missing")
        document = json.loads(path.read_text(encoding="utf-8"))
    except ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("n8n metadata readback is invalid") from exc
    if not isinstance(document, dict):
        raise ContractError("n8n metadata readback is not an object")
    if document.get("name") not in (None, CREDENTIAL_NAME) or document.get("type") not in (None, CREDENTIAL_TYPE):
        raise ContractError("n8n credential metadata mismatch")
    if document.get("projectId") not in (None, expected_project) or document.get("ownerRole") not in (None, OWNER_ROLE):
        raise ContractError("n8n credential owner metadata mismatch")
    if document.get("workflowPath") not in (None, WORKFLOW_PATH):
        raise ContractError("n8n workflow path metadata mismatch")
    if document.get("secretValueRecorded") is not False or document.get("idsRecorded") is not False:
        raise ContractError("n8n credential metadata is not redacted")
    if document.get("credentialId") not in (None, "REDACTED") or document.get("workflowId") not in (None, "REDACTED"):
        raise ContractError("unexpected credential identifier in metadata")
    counts = document.get("counts")
    expected_empty = {"credentials": 0, "owners": 0, "workflows": 0, "webhooks": 0, "executions": 0}
    expected_present = {"credentials": 1, "owners": 1, "workflows": 1, "webhooks": 0, "executions": 0}
    present = document.get("credentialPresent") is True or document.get("workflowPresent") is True
    if not isinstance(counts, dict) or counts != (expected_present if present else expected_empty):
        raise ContractError("n8n metadata counts are not an exact clean boundary")
    if document.get("credentialPresent") is not present or document.get("workflowPresent") is not present:
        raise ContractError("n8n credential/workflow presence is inconsistent")
    if document.get("active") is not False or document.get("activeVersionId") is not None or document.get("published") is not False:
        raise ContractError("n8n workflow is not inactive and unpublished")
    if not present and document.get("decryptUseVerified") is not False:
        raise ContractError("n8n empty readback did not prove an unbound credential")
    if present:
        if document.get("name") != CREDENTIAL_NAME or document.get("type") != CREDENTIAL_TYPE or document.get("projectId") != expected_project or document.get("ownerRole") != OWNER_ROLE:
            raise ContractError("n8n credential owner metadata is incomplete")
        if document.get("ciphertextPlaintextEqual") is not False:
            raise ContractError("n8n encrypted decrypt/use evidence is missing")
        if document.get("decryptUseVerified") is not require_decrypt_use:
            raise ContractError("n8n decrypt/use challenge state is invalid")
    return present


def _run_metadata_reader(
    project_id: str,
    credential_id: str,
    root: Path,
    *,
    require_decrypt_use: bool = True,
) -> bool:
    command = _command_from_env("FINANCE_MCP_METADATA_READER")
    if not command:
        raise ContractError("Mandatory n8n metadata readback command is required")
    readback_path = root / "metadata.json"
    environment = os.environ.copy()
    environment.pop(ENVIRONMENT_NAME, None)
    environment.update(
        {
            "FINANCE_MCP_METADATA_PROJECT": project_id,
            "FINANCE_MCP_METADATA_OUTPUT": str(readback_path),
            "FINANCE_MCP_METADATA_CREDENTIAL": "REDACTED",
        }
    )
    _run(command, environment=environment)
    return _readback_metadata(
        readback_path,
        project_id,
        credential_id,
        require_decrypt_use=require_decrypt_use,
    )


def _run_decrypt_use_challenge(secret: str, root: Path) -> None:
    command = _command_from_env("FINANCE_MCP_DECRYPT_USE_CHALLENGE")
    if not command:
        raise ContractError("Mandatory n8n decrypt/use challenge command is required")
    environment = os.environ.copy()
    environment.pop(ENVIRONMENT_NAME, None)
    environment.update({ENVIRONMENT_NAME: secret, "FINANCE_MCP_CHALLENGE_OUTPUT": str(root / "challenge.json")})
    _run(command, environment=environment)
    try:
        challenge = json.loads((root / "challenge.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ContractError("n8n decrypt/use challenge evidence is missing") from exc
    if challenge != {"authenticatedRequest": True, "decryptUseVerified": True, "secretValueRecorded": False}:
        raise ContractError("n8n decrypt/use challenge evidence is invalid")


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
    try:
        cleanup = json.loads((root / "cleanup.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("n8n disposable cleanup evidence is missing or invalid") from exc
    if cleanup != {
        "cleanupVerified": True,
        "counts": {"credentials": 0, "owners": 0, "workflows": 0, "webhooks": 0, "executions": 0},
        "idsRecorded": False,
        "secretValueRecorded": False,
    }:
        raise ContractError("n8n disposable cleanup did not prove a zero boundary")


def _cleanup_and_verify_zero(project_id: str, credential_id: str, root: Path) -> None:
    """Require an independent metadata readback after the cleanup gate."""

    _cleanup_disposable(root)
    if _run_metadata_reader(project_id, credential_id, root, require_decrypt_use=False):
        raise ContractError("n8n cleanup readback still contains finance MCP rows")


def _unmount_if_requested(root: Path) -> None:
    mount = os.environ.get("FINANCE_MCP_BINDER_MOUNT", "")
    if not mount:
        return
    if mount != str(root):
        raise ContractError("Binder unmount target does not match the verified root")
    umount = shutil.which("umount")
    if not umount:
        raise ContractError("umount is required to remove the binder tmpfs")
    if subprocess.run([umount, mount], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ContractError("binder tmpfs removal failed")


def _receipt(*, idempotent_no_op: bool, simulated: bool) -> dict[str, Any]:
    if simulated:
        return {
            "status": "SIMULATED",
            "runtimeEvidence": False,
            "scope": "W15_SPEC_ONLY",
            "idempotentNoOp": idempotent_no_op,
            "values": "REDACTED",
            "ids": "REDACTED",
        }
    return {
        "status": "VERIFIED",
        "runtimeEvidence": True,
        "idempotentNoOp": idempotent_no_op,
        "values": "REDACTED",
        "ids": "REDACTED",
    }


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
    _require_tmpfs(args.binder_root)
    _require_pinned_n8n(args.n8n)
    credential_id = deterministic_credential_id()
    root = args.binder_root
    credential_file = root / "finance-mcp-facade-credential.json"
    workflow_file = root / "15-finance-mcp-facade-bound.json"
    simulated = os.environ.get("FINANCE_MCP_SIMULATED") == "true"
    cleanup_armed = False
    cleanup_attempted = False
    receipt: dict[str, Any] | None = None

    def cleanup_after_failure() -> None:
        nonlocal cleanup_attempted
        if not cleanup_armed or cleanup_attempted:
            return
        cleanup_attempted = True
        _cleanup_and_verify_zero(args.project_id, credential_id, root)

    try:
        present = _run_metadata_reader(args.project_id, credential_id, root)
        if present:
            # Existing exact state is a clean idempotent no-op; never re-import it.
            _run_decrypt_use_challenge(secret, root)
            if not _run_metadata_reader(args.project_id, credential_id, root):
                raise ContractError("n8n decrypt/use readback lost the bound rows")
            receipt = _receipt(idempotent_no_op=True, simulated=simulated)
        else:
            _write_private(credential_file, json.dumps(credential_export(secret, credential_id), separators=(",", ":")).encode())
            workflow = bound_workflow(source, credential_id)
            _write_private(workflow_file, (json.dumps(workflow, separators=(",", ":")) + "\n").encode())
            # Arm the authorized cleanup before the first external mutation. A
            # failed CLI call may have applied only part of its requested change.
            cleanup_armed = True
            _run([args.n8n, "import:credentials", f"--input={credential_file}", f"--projectId={args.project_id}"])
            _run([args.n8n, "import:workflow", f"--input={workflow_file}", f"--projectId={args.project_id}", "--activeState=false"])
            if not _run_metadata_reader(args.project_id, credential_id, root, require_decrypt_use=False):
                raise ContractError("n8n import readback did not prove credential and workflow ownership")
            try:
                _run_decrypt_use_challenge(secret, root)
            except ContractError:
                cleanup_after_failure()
                cleanup_attempted = False
                _run([args.n8n, "import:credentials", f"--input={credential_file}", f"--projectId={args.project_id}"])
                _run([args.n8n, "import:workflow", f"--input={workflow_file}", f"--projectId={args.project_id}", "--activeState=false"])
                if not _run_metadata_reader(args.project_id, credential_id, root, require_decrypt_use=False):
                    raise ContractError("n8n recreation readback did not prove credential and workflow ownership")
                _run_decrypt_use_challenge(secret, root)
            if not _run_metadata_reader(args.project_id, credential_id, root):
                raise ContractError("n8n decrypt/use readback lost the bound rows")
            receipt = _receipt(idempotent_no_op=False, simulated=simulated)
    except ContractError:
        cleanup_after_failure()
        raise
    finally:
        os.environ.pop(ENVIRONMENT_NAME, None)
        for path in (credential_file, workflow_file, root / "metadata.json", root / "challenge.json", root / "cleanup.json"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        _unmount_if_requested(root)

    if receipt is None:
        raise ContractError("finance MCP binder did not produce a verified receipt")
    print(json.dumps(receipt, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
