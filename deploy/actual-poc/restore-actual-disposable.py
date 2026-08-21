#!/usr/bin/env python3
"""Restore Actual data into isolated containers and prove redacted readback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
VERIFY_SCRIPT = SCRIPT.with_name("verify-backup.py")
DEFAULT_IMAGE = (
    "actualbudget/actual-server:26.8.1@sha256:"
    "6478d9ddfc0924479c09e6699c205e354c6f2216dfe7de3c0fb7b590d6edcdc5"
)
RESTORE_OWNER_LABEL = "finance.restore.owner"
RESTORE_RUN_LABEL = "finance.restore.run"
RESTORE_OWNER_VALUE = "actual-restore"
MAX_READBACK_DIAGNOSTIC = 2048
SENSITIVE_DIAGNOSTIC_KEY = re.compile(
    r"\b(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential)\b",
    re.IGNORECASE,
)


def is_absent_inspect_response(message: str, object_name: str, kind: str) -> bool:
    """Accept only complete, object-bound Docker or Podman absent responses."""
    escaped_name = re.escape(object_name)
    if kind == "container":
        patterns = (
            rf"\[\]\s*(?i:Error:\s+No such object:)\s+\"{escaped_name}\"",
            rf"(?i:Error:\s+no container with name or ID)\s+\"{escaped_name}\"\s+(?i:found:\s+no such container)",
        )
    elif kind == "network":
        patterns = (
            rf"\[\]\s*(?i:Error:\s+No such object:)\s+\"{escaped_name}\"",
            rf"(?i:Error:\s+No such object:)\s+\"{escaped_name}\"",
            rf"(?:\[\]\s*)?(?i:Error:\s+network)\s+{escaped_name}(?i::\s+unable to find network with name or ID)\s+{escaped_name}(?i::\s+network not found)",
            rf"(?i:Error:\s+network)\s+{escaped_name}(?i:\s+not found)",
        )
    else:
        raise ValueError(f"unsupported inspect object kind: {kind}")
    return any(re.fullmatch(pattern, message.strip()) for pattern in patterns)


class DrillError(RuntimeError):
    def __init__(self, code: str, stage: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.stage, self.detail = code, stage, detail


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def path_exists(path: Path | None) -> bool:
    return path is not None and os.path.lexists(path)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{path}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def redact_diagnostic(value: str) -> str:
    """Keep bounded child diagnostics while removing common secret assignments."""
    diagnostic = value.replace("\x00", "\\x00").strip()
    if not diagnostic:
        return ""
    diagnostic = re.sub(
        rf"({SENSITIVE_DIAGNOSTIC_KEY.pattern})(?:\s*[:=]\s*|\s+)([^\s,;]+)",
        r"\1=<redacted>",
        diagnostic,
        flags=re.IGNORECASE,
    )
    diagnostic = re.sub(r"(?i)\bBearer\s+\S+", "Bearer <redacted>", diagnostic)
    diagnostic = re.sub(r"(?i)(https?://[^\s/@:]+):[^\s/@]+@", r"\1:<redacted>@", diagnostic)
    if len(diagnostic) > MAX_READBACK_DIAGNOSTIC:
        diagnostic = diagnostic[:MAX_READBACK_DIAGNOSTIC] + "..."
    return diagnostic


def probe_failure_detail(result: subprocess.CompletedProcess[str], *, stdout_bytes: int | None = None) -> str:
    """Expose status and safe stderr without persisting child output or secrets."""
    fields = [f"exit_code={result.returncode}"]
    if stdout_bytes is not None:
        fields.append(f"stdout_bytes={stdout_bytes}")
    diagnostic = redact_diagnostic(result.stderr)
    if diagnostic:
        fields.append(f"stderr={diagnostic}")
    return "; ".join(fields)


def attach_probe_diagnostic(error: DrillError, result: subprocess.CompletedProcess[str], *, stdout_bytes: int) -> DrillError:
    detail = probe_failure_detail(result, stdout_bytes=stdout_bytes)
    error.detail = f"{error.detail}; {detail}" if error.detail else detail
    return error


_ACTIVE_PROCESSES: dict[int, subprocess.Popen[str]] = {}


def terminate_active_process_groups() -> None:
    for process in tuple(_ACTIVE_PROCESSES.values()):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_checked(
    argv: list[str],
    *,
    capture: bool = True,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    if timeout is None:
        return subprocess.run(argv, check=False, capture_output=capture, text=True, env=env)
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        env=env,
        start_new_session=True,
    )
    _ACTIVE_PROCESSES[process.pid] = process
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except SystemExit:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        if not isinstance(stdout, str):
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        if not isinstance(stderr, str):
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        completed = subprocess.CompletedProcess(argv, 124, stdout, f"{stderr}\ncommand timed out".strip())
    else:
        completed = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        _ACTIVE_PROCESSES.pop(process.pid, None)
    return completed


def runtime_command(value: str | None) -> list[str]:
    if not value:
        for candidate in ("docker", "podman"):
            resolved = shutil.which(candidate)
            if resolved:
                return [resolved]
        raise DrillError("container_runtime_unavailable", "runtime", "no docker or podman command found")
    command = shlex.split(value)
    if not command:
        raise DrillError("container_runtime_unavailable", "runtime", "empty container runtime command")
    return command


def detect_runtime_backend(runtime: list[str]) -> str:
    """Identify the engine from an unambiguous, structured version signature."""
    result = runtime_call(runtime, "version")
    if result.returncode != 0:
        raise DrillError("container_runtime_unavailable", "runtime")
    version_text = f"{result.stdout}\n{result.stderr}"
    docker = bool(
        re.search(r"(?im)^\s*(?:docker\s+version\s+v?\d+(?:\.\d+){1,3}|(?:client|server):\s+docker\s+engine\b)", version_text)
    )
    podman = bool(
        re.search(r"(?im)^\s*(?:podman\s+version\s+v?\d+(?:\.\d+){1,3}|(?:client|server):\s+podman\s+engine\b)", version_text)
    )
    podman_alias = bool(
        re.search(
            r"(?im)^\s*emulate\s+docker\s+cli\s+using\s+podman\.\s+create\s+/etc/containers/nodocker\s+to\s+quiet\s+msg\.\s*$",
            version_text,
        )
    )
    if docker and (podman or podman_alias):
        raise DrillError("unsupported_container_runtime", "runtime", "runtime identity is ambiguous")
    if podman or podman_alias:
        return "podman"
    if docker:
        return "docker"
    raise DrillError("unsupported_container_runtime", "runtime", "runtime is not Docker or Podman")


def parse_runtime_id(output: str, code: str, stage: str) -> str:
    value = output.strip()
    if not re.fullmatch(r"[0-9a-f]{12,64}", value, flags=re.IGNORECASE):
        raise DrillError(code, stage, "runtime did not return one opaque object ID")
    return value


@dataclass(frozen=True)
class NetworkNamespace:
    pid: int
    device: int
    inode: int


def namespace_stat(pid: int) -> tuple[int, int]:
    try:
        status = os.stat(f"/proc/{pid}/ns/net")
    except OSError as exc:
        raise DrillError("runtime_namespace_identity_failed", "namespace", str(exc)) from exc
    return status.st_dev, status.st_ino


def inspect_sidecar_namespace(
    runtime: list[str], sidecar: str, sidecar_id: str, network_name: str
) -> NetworkNamespace:
    result = runtime_call(
        runtime,
        "inspect",
        "--format",
        f'{{{{.Id}}}}|{{{{.Name}}}}|{{{{.State.Pid}}}}|{{{{index .Config.Labels "{RESTORE_OWNER_LABEL}"}}}}|{{{{index .Config.Labels "{RESTORE_RUN_LABEL}"}}}}',
        sidecar_id,
    )
    if result.returncode != 0:
        raise DrillError("runtime_namespace_identity_failed", "namespace")
    fields = result.stdout.strip().split("|")
    if len(fields) != 5:
        raise DrillError("runtime_namespace_identity_failed", "namespace", "unexpected inspect identity")
    container_id, name, pid_text, owner_label, run_label = fields
    if container_id.lower() != sidecar_id.lower():
        raise DrillError("runtime_namespace_identity_failed", "namespace", "invalid container identity")
    if (
        name not in {sidecar, f"/{sidecar}"}
        or owner_label != RESTORE_OWNER_VALUE
        or run_label != network_name
        or not re.fullmatch(r"[1-9][0-9]*", pid_text)
    ):
        raise DrillError("runtime_namespace_identity_failed", "namespace", "foreign sidecar identity")
    pid = int(pid_text)
    device, inode = namespace_stat(pid)
    return NetworkNamespace(pid=pid, device=device, inode=inode)


def assert_namespace_identity(namespace: NetworkNamespace) -> None:
    device, inode = namespace_stat(namespace.pid)
    if (device, inode) != (namespace.device, namespace.inode):
        raise DrillError("runtime_namespace_identity_changed", "namespace")


def namespace_command(tool: list[str], namespace: NetworkNamespace, command: list[str]) -> list[str]:
    if not tool or not command:
        raise DrillError("runtime_namespace_tool_invalid", "namespace")
    assert_namespace_identity(namespace)
    return [*tool, "--target", str(namespace.pid), "--net", "--", *command]


def runtime_call(runtime: list[str], *args: str) -> subprocess.CompletedProcess[str]:
    return run_checked([*runtime, *args])


def inspect_state(runtime: list[str], sidecar: str) -> str:
    result = runtime_call(runtime, "inspect", sidecar)
    if result.returncode == 0:
        return "present"
    message = f"{result.stdout}\n{result.stderr}"
    if is_absent_inspect_response(message, sidecar, "container"):
        return "absent"
    raise DrillError("runtime_inspect_failed", "cleanup")


def inspect_owned_state(
    runtime: list[str], object_id: str, object_name: str, kind: str, network_name: str, backend: str
) -> str:
    if kind == "container":
        format_string = (
            f'{{{{.Id}}}}|{{{{.Name}}}}|{{{{index .Config.Labels "{RESTORE_OWNER_LABEL}"}}}}|'
            f'{{{{index .Config.Labels "{RESTORE_RUN_LABEL}"}}}}'
        )
        result = runtime_call(runtime, "inspect", "--format", format_string, object_id)
        absent_code = "runtime_inspect_failed"
    else:
        id_field = ".ID" if backend == "podman" else ".Id"
        format_string = (
            f'{{{{{id_field}}}}}|{{{{.Name}}}}|{{{{index .Labels "{RESTORE_OWNER_LABEL}"}}}}|'
            f'{{{{index .Labels "{RESTORE_RUN_LABEL}"}}}}'
        )
        result = runtime_call(runtime, "network", "inspect", "--format", format_string, object_id)
        absent_code = "runtime_network_inspect_failed"
    if result.returncode == 0:
        fields = result.stdout.strip().split("|")
        expected_names = {object_name} if kind == "network" else {object_name, f"/{object_name}"}
        actual_name = fields[1] if len(fields) >= 2 else ""
        if (
            len(fields) != 4
            or fields[0].lower() != object_id.lower()
            or actual_name not in expected_names
            or fields[2] != RESTORE_OWNER_VALUE
            or fields[3] != network_name
        ):
            raise DrillError(absent_code, "cleanup", "owned resource identity changed")
        return "present"
    message = f"{result.stdout}\n{result.stderr}"
    if is_absent_inspect_response(message, object_id, kind):
        return "absent"
    raise DrillError(absent_code, "cleanup")


def inspect_network_state(runtime: list[str], network: str) -> str:
    result = runtime_call(runtime, "network", "inspect", network)
    if result.returncode == 0:
        return "present"
    message = f"{result.stdout}\n{result.stderr}"
    if is_absent_inspect_response(message, network, "network"):
        return "absent"
    raise DrillError("runtime_network_inspect_failed", "cleanup")


def inspect_network_identity(runtime: list[str], network_name: str, backend: str) -> str:
    """Resolve a created network name to an immutable, label-bound runtime ID."""
    id_field = ".ID" if backend == "podman" else ".Id"
    result = runtime_call(
        runtime,
        "network",
        "inspect",
        "--format",
        f'{{{{{id_field}}}}}|{{{{.Name}}}}|{{{{index .Labels "{RESTORE_OWNER_LABEL}"}}}}|{{{{index .Labels "{RESTORE_RUN_LABEL}"}}}}',
        network_name,
    )
    if result.returncode != 0:
        raise DrillError("disposable_network_identity_invalid", "network", "created network cannot be inspected")
    fields = result.stdout.strip().split("|")
    if (
        len(fields) != 4
        or not re.fullmatch(r"[0-9a-f]{12,64}", fields[0], flags=re.IGNORECASE)
        or fields[1] != network_name
        or fields[2] != RESTORE_OWNER_VALUE
        or fields[3] != network_name
    ):
        raise DrillError("runtime_network_identity_changed", "network", "owned network identity changed")
    return fields[0]


def inspect_network_internal(runtime: list[str], network_id: str, network_name: str, backend: str) -> bool:
    id_field = ".ID" if backend == "podman" else ".Id"
    result = runtime_call(
        runtime,
        "network",
        "inspect",
        "--format",
        f'{{{{{id_field}}}}}|{{{{.Name}}}}|{{{{index .Labels "{RESTORE_OWNER_LABEL}"}}}}|{{{{index .Labels "{RESTORE_RUN_LABEL}"}}}}|{{{{.Internal}}}}',
        network_id,
    )
    if result.returncode != 0:
        raise DrillError("runtime_network_inspect_failed", "network")
    fields = result.stdout.strip().split("|")
    if (
        len(fields) != 5
        or fields[0].lower() != network_id.lower()
        or fields[1] != network_name
        or fields[2] != RESTORE_OWNER_VALUE
        or fields[3] != network_name
    ):
        raise DrillError("runtime_network_identity_changed", "network", "owned network identity changed")
    if fields[4].lower() != "true":
        raise DrillError("network_not_internal", "network", "network is not egress-isolated")
    return True


def safe_extract_actual(archive: Path, destination: Path) -> None:
    if destination.exists():
        raise DrillError("disposable_resource_collision", "extract", "restore data directory already exists")
    destination.mkdir(mode=0o700)
    found = False
    seen: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle:
                normalized = member.name.removeprefix("./")
                relative = PurePosixPath(normalized)
                if relative.is_absolute() or ".." in relative.parts:
                    raise DrillError("unsafe_archive_member", "extract", member.name)
                if relative.parts[:1] != ("actual-data",):
                    continue
                tail = "/".join(relative.parts[1:])
                if not tail:
                    continue
                if tail in seen:
                    raise DrillError("duplicate_archive_member", "extract", tail)
                seen.add(tail)
                if member.isdir():
                    (destination / Path(*relative.parts[1:])).mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise DrillError("unsafe_archive_member", "extract", member.name)
                target = destination / Path(*relative.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise DrillError("archive_member_unreadable", "extract", member.name)
                with source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                found = True
    except (OSError, tarfile.TarError) as exc:
        raise DrillError("actual_extract_failed", "extract", str(exc)) from exc
    if not found:
        raise DrillError("actual_data_missing", "extract", "archive has no actual-data members")


def backup_verify(backup_root: Path, backup_path: Path | None, temp_root: Path) -> dict[str, Any]:
    args = [sys.executable, str(VERIFY_SCRIPT), "--backup-root", str(backup_root), "--work-root", str(temp_root)]
    if backup_path:
        args.extend(["--backup-path", str(backup_path)])
    result = run_checked(args)
    if result.returncode != 0:
        raise DrillError("backup_verification_failed", "backup")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DrillError("backup_verification_output_invalid", "backup") from exc
    return payload


def load_expected(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DrillError("readback_contract_invalid", "readback", "invalid expected readback JSON") from exc
    if payload.get("schema_version") != 1:
        raise DrillError("readback_contract_invalid", "readback", "unsupported readback schema")
    for section in ("accounts", "representative_transactions"):
        if not isinstance(payload.get(section), list):
            raise DrillError("readback_contract_invalid", "readback", f"missing {section}")
    return payload


def normalize_rows(payload: dict[str, Any], section: str) -> list[dict[str, Any]]:
    rows = payload.get(section)
    if not isinstance(rows, list):
        raise DrillError("readback_contract_invalid", "readback", f"{section} is not a list")
    canonical: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DrillError("readback_contract_invalid", "readback", f"{section} contains a non-object")
        identity = str(row.get("name" if section == "accounts" else "imported_id", ""))
        if not identity or identity in seen:
            raise DrillError("readback_contract_invalid", "readback", f"duplicate or missing {section} identity")
        seen.add(identity)
        canonical.append(dict(sorted(row.items())))
    return sorted(canonical, key=lambda row: json.dumps(row, sort_keys=True))


def validate_readback(actual_payload: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(actual_payload, dict) or not isinstance(actual_payload.get("api"), dict) or not isinstance(actual_payload.get("ui"), dict):
        raise DrillError("readback_contract_invalid", "readback", "probe must return api and ui objects")
    expected_accounts = normalize_rows(expected, "accounts")
    expected_transactions = normalize_rows(expected, "representative_transactions")
    api = actual_payload["api"]
    ui = actual_payload["ui"]
    api_accounts = normalize_rows(api, "accounts")
    ui_accounts = normalize_rows(ui, "accounts")
    api_transactions = normalize_rows(api, "representative_transactions")
    ui_transactions = normalize_rows(ui, "representative_transactions")
    if api_accounts != expected_accounts or api_transactions != expected_transactions:
        raise DrillError("api_readback_mismatch", "readback", "API readback differs from expected contract")
    if ui_accounts != expected_accounts or ui_transactions != expected_transactions:
        raise DrillError("ui_readback_mismatch", "readback", "UI readback differs from expected contract")
    if api_accounts != ui_accounts or api_transactions != ui_transactions:
        raise DrillError("ui_api_parity_failed", "readback", "UI/API readback differs")
    return {
        "api_readback_sha256": digest({"accounts": api_accounts, "representative_transactions": api_transactions}),
        "ui_readback_sha256": digest({"accounts": ui_accounts, "representative_transactions": ui_transactions}),
        "account_count": len(api_accounts),
        "representative_transaction_count": len(api_transactions),
        "ui_api_parity": True,
    }


def probe_readback(
    command: list[str],
    expected: dict[str, Any],
    *,
    run_index: int,
    url: str,
    data_dir: Path,
    namespace_tool: list[str],
    namespace: NetworkNamespace,
    timeout: float,
    readback_path: Path,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment.update({
        "ACTUAL_RESTORE_URL": url,
        "ACTUAL_RESTORE_DATA_DIR": str(data_dir),
        "ACTUAL_RESTORE_RUN_INDEX": str(run_index),
        # The wrapper may run another container. Give it both the host-owned
        # artifact path and the path visible through the sidecar's data mount.
        "ACTUAL_RESTORE_READBACK_PATH": str(readback_path),
        "ACTUAL_RESTORE_READBACK_CONTAINER_PATH": f"/data/readback-{run_index}.json",
    })
    result = run_checked(namespace_command(namespace_tool, namespace, command), env=environment, timeout=timeout)
    if result.returncode != 0:
        raise DrillError(
            "readback_probe_failed",
            "readback",
            probe_failure_detail(result),
        )
    stdout_bytes = len(result.stdout.encode("utf-8"))
    if not result.stdout.strip():
        raise DrillError(
            "readback_probe_output_empty",
            "readback",
            probe_failure_detail(result, stdout_bytes=stdout_bytes),
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DrillError(
            "readback_probe_output_invalid",
            "readback",
            probe_failure_detail(result, stdout_bytes=stdout_bytes),
        ) from exc
    try:
        return validate_readback(payload, expected)
    except DrillError as error:
        raise attach_probe_diagnostic(error, result, stdout_bytes=stdout_bytes) from error


def probe_http(
    url: str,
    command: list[str] | None,
    *,
    namespace_tool: list[str],
    namespace: NetworkNamespace,
) -> None:
    if command is None:
        command = [
            sys.executable,
            "-c",
            "import sys, urllib.request; response = urllib.request.urlopen(sys.argv[1], timeout=5); raise SystemExit(0 if response.status == 200 else 1)",
            f"{url}/",
        ]
    result = run_checked(
        namespace_command(namespace_tool, namespace, command),
        env={**os.environ, "ACTUAL_RESTORE_URL": url},
        timeout=5,
    )
    if result.returncode != 0:
        raise DrillError("ui_http_probe_failed", "health")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", default=os.environ.get("FINANCE_BACKUP_ROOT", "/opt/backups/finance-actual-poc"))
    parser.add_argument("--backup-path")
    parser.add_argument("--receipt", default=os.environ.get("FINANCE_ACTUAL_RESTORE_RECEIPT"))
    parser.add_argument("--image", default=os.environ.get("FINANCE_ACTUAL_RESTORE_IMAGE", DEFAULT_IMAGE))
    parser.add_argument("--runtime", default=os.environ.get("FINANCE_CONTAINER_RUNTIME"))
    parser.add_argument("--repeat", type=int, default=int(os.environ.get("FINANCE_ACTUAL_RESTORE_RUNS", "2")))
    parser.add_argument("--health-attempts", type=int, default=int(os.environ.get("FINANCE_ACTUAL_RESTORE_HEALTH_ATTEMPTS", "60")))
    parser.add_argument("--readback-timeout", type=float, default=float(os.environ.get("FINANCE_ACTUAL_RESTORE_READBACK_TIMEOUT", "30")))
    parser.add_argument("--temp-root", default=os.environ.get("FINANCE_ACTUAL_RESTORE_TEMP_ROOT", "/tmp"))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--namespace-tool", default=os.environ.get("FINANCE_ACTUAL_RESTORE_NAMESPACE_TOOL", "nsenter"))
    parser.add_argument("--expected-readback", required=True, help="Redacted account/balance/transaction contract JSON")
    parser.add_argument("--readback-command", required=True, help="Executable that emits the API/UI readback JSON")
    parser.add_argument("--http-probe-command", help="Optional executable used instead of urllib for UI health")
    return parser.parse_args()


def source_commit() -> str | None:
    result = run_checked(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) == 40 else None


def error_payload(error: DrillError) -> dict[str, str]:
    payload = {"code": error.code, "stage": error.stage}
    if error.detail:
        payload["detail"] = error.detail
    return payload


def main() -> int:
    args = parse_args()
    if args.repeat < 2:
        print("--repeat must be at least 2", file=sys.stderr)
        return 64
    receipt = Path(args.receipt or f"/tmp/finance-actual-restore-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json")
    readback_root = receipt.parent.resolve()
    started = now()
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "failed",
        "mode": "disposable",
        "redacted": True,
        "started_at": started,
        "completed_at": None,
        "backup": {"name": None, "archive_sha256": None, "archive_bytes": None, "verified": False},
        "runtime": {"engine": None, "image": args.image, "image_digest": None, "available": False, "verified": False},
        "source_provenance": {"commit": source_commit(), "script_sha256": sha256(SCRIPT)},
        "requested_runs": args.repeat,
        "runs": [],
        "cleanup": {
            "outer_temp_root": None,
            "outer_temp_root_removed": False,
            "retained_paths": [],
        },
        "cleanup_verified": True,
        "production_mutated": False,
        "retained_mutated": False,
        "secret_values_recorded": False,
        "error": None,
    }
    current_runtime: list[str] | None = None
    current_backend: str | None = None
    current_sidecar: str | None = None
    current_sidecar_id: str | None = None
    current_network: str | None = None
    current_network_id: str | None = None
    current_data: Path | None = None
    temp_root_for_cleanup: Path | None = None

    def cleanup() -> bool:
        nonlocal current_sidecar, current_sidecar_id, current_network, current_network_id, current_data
        ok = True
        sidecar_state = "absent"
        if current_runtime and current_sidecar:
            if current_sidecar_id:
                try:
                    sidecar_state = inspect_owned_state(
                        current_runtime, current_sidecar_id, current_sidecar, "container", current_network or "", current_backend or "docker"
                    )
                except DrillError:
                    sidecar_state = "unknown"
                    ok = False
            else:
                try:
                    sidecar_state = inspect_state(current_runtime, current_sidecar)
                except DrillError:
                    sidecar_state = "unknown"
                    ok = False
                if sidecar_state == "present":
                    sidecar_state = "unknown"
                    ok = False
            if sidecar_state == "present":
                removed = runtime_call(current_runtime, "rm", "-f", current_sidecar_id)
                if removed.returncode != 0:
                    ok = False
                else:
                    try:
                        sidecar_state = inspect_owned_state(
                            current_runtime, current_sidecar_id, current_sidecar, "container", current_network or "", current_backend or "docker"
                        )
                    except DrillError:
                        sidecar_state = "unknown"
                        ok = False
                    if sidecar_state != "absent":
                        ok = False
            elif sidecar_state != "absent":
                ok = False
        network_state = "absent"
        if current_runtime and current_network:
            if sidecar_state != "absent":
                ok = False
                network_state = "unknown"
            elif current_network_id:
                try:
                    network_state = inspect_owned_state(
                        current_runtime, current_network_id, current_network, "network", current_network, current_backend or "docker"
                    )
                except DrillError:
                    network_state = "unknown"
                    ok = False
            else:
                try:
                    network_state = inspect_network_state(current_runtime, current_network)
                except DrillError:
                    network_state = "unknown"
                    ok = False
                if network_state == "present":
                    network_state = "unknown"
                    ok = False
            if network_state == "present" and sidecar_state == "absent" and current_network_id:
                removed = runtime_call(current_runtime, "network", "rm", current_network_id)
                if removed.returncode != 0:
                    ok = False
                else:
                    try:
                        network_state = inspect_owned_state(
                            current_runtime, current_network_id, current_network, "network", current_network, current_backend or "docker"
                        )
                    except DrillError:
                        network_state = "unknown"
                        ok = False
                    if network_state != "absent":
                        ok = False
            elif network_state != "absent":
                network_state = "unknown"
                ok = False
        if current_data and path_exists(current_data):
            if sidecar_state != "absent" or network_state != "absent":
                ok = False
            else:
                try:
                    shutil.rmtree(current_data)
                except OSError:
                    ok = False
                ok = ok and not path_exists(current_data)
        if ok:
            current_sidecar, current_sidecar_id, current_network, current_network_id, current_data = None, None, None, None, None
        return ok

    def cleanup_outer_root() -> bool:
        if temp_root_for_cleanup is None:
            return True
        cleanup_receipt = result["cleanup"]
        cleanup_receipt["outer_temp_root"] = str(temp_root_for_cleanup)
        if not path_exists(temp_root_for_cleanup):
            cleanup_receipt["outer_temp_root_removed"] = True
            cleanup_receipt["retained_paths"] = []
            return True
        try:
            if temp_root_for_cleanup.is_symlink():
                raise OSError("refusing to remove symlink at owned temp root")
            shutil.rmtree(temp_root_for_cleanup)
        except OSError:
            cleanup_receipt["outer_temp_root_removed"] = False
            cleanup_receipt["retained_paths"] = [str(temp_root_for_cleanup)]
            return False
        if path_exists(temp_root_for_cleanup):
            cleanup_receipt["outer_temp_root_removed"] = False
            cleanup_receipt["retained_paths"] = [str(temp_root_for_cleanup)]
            return False
        cleanup_receipt["outer_temp_root_removed"] = True
        cleanup_receipt["retained_paths"] = []
        return True

    def handle_signal(signum: int, _frame: Any) -> None:
        terminate_active_process_groups()
        if not cleanup():
            result["cleanup_verified"] = False
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        expected = load_expected(Path(args.expected_readback))
        temp_root_for_cleanup = Path(tempfile.mkdtemp(prefix="finance-actual-restore.", dir=args.temp_root))
        temp_root = temp_root_for_cleanup
        result["cleanup"]["outer_temp_root"] = str(temp_root_for_cleanup)
        verified = backup_verify(Path(args.backup_root), Path(args.backup_path) if args.backup_path else None, temp_root)
        result["backup"].update({
            "name": verified["backup"],
            "archive_sha256": verified["archive_sha256"],
            "archive_bytes": verified["archive_bytes"],
            "verified": True,
        })
        backup_dir = Path(args.backup_root).resolve() / verified["backup"]
        archive = backup_dir / "finance-data.tar.gz"
        current_runtime = runtime_command(args.runtime)
        current_backend = detect_runtime_backend(current_runtime)
        result["runtime"]["engine"] = current_backend
        image = runtime_call(current_runtime, "image", "inspect", "--format", "{{.Id}}", args.image)
        if image.returncode != 0:
            raise DrillError("image_digest_unavailable", "runtime")
        image_id = image.stdout.strip()
        if len(image_id) == 64:
            image_id = f"sha256:{image_id}"
        if not image_id.startswith("sha256:") or len(image_id) != 71:
            raise DrillError("image_digest_malformed", "runtime")
        result["runtime"].update({"image_digest": image_id, "available": True, "verified": True})
        readback_command = shlex.split(args.readback_command)
        http_command = shlex.split(args.http_probe_command) if args.http_probe_command else None
        namespace_tool = shlex.split(args.namespace_tool)
        if not namespace_tool or not (Path(namespace_tool[0]).is_file() or shutil.which(namespace_tool[0])):
            raise DrillError("runtime_namespace_tool_unavailable", "namespace")
        for run_index in range(1, args.repeat + 1):
            sidecar = f"finance-actual-restore-{run_index}-{os.getpid()}"
            data_dir = temp_root / f"data-{run_index}"
            network = f"finance-actual-restore-net-{run_index}-{os.getpid()}"
            readback_path = readback_root / f"readback-{run_index}.json"
            if path_exists(readback_path):
                raise DrillError("disposable_resource_collision", "preflight", str(readback_path))
            if inspect_state(current_runtime, sidecar) != "absent" or data_dir.exists():
                raise DrillError("disposable_resource_collision", "preflight", sidecar)
            if inspect_network_state(current_runtime, network) != "absent":
                raise DrillError("disposable_resource_collision", "preflight", network)
            current_sidecar, current_data = sidecar, data_dir
            current_network = network
            current_sidecar_id = None
            current_network_id = None
            safe_extract_actual(archive, data_dir)
            created_network = runtime_call(
                current_runtime,
                "network",
                "create",
                "--internal",
                "--label",
                f"{RESTORE_OWNER_LABEL}={RESTORE_OWNER_VALUE}",
                "--label",
                f"{RESTORE_RUN_LABEL}={network}",
                network,
            )
            if created_network.returncode != 0:
                raise DrillError("disposable_network_create_failed", "network")
            created_output = created_network.stdout.strip()
            if created_output != network and not re.fullmatch(r"[0-9a-f]{12,64}", created_output, flags=re.IGNORECASE):
                raise DrillError("disposable_network_identity_invalid", "network", "runtime returned an unknown network identity")
            current_network_id = inspect_network_identity(current_runtime, network, current_backend)
            if re.fullmatch(r"[0-9a-f]{12,64}", created_output, flags=re.IGNORECASE) and created_output.lower() != current_network_id.lower():
                raise DrillError("runtime_network_identity_changed", "network", "network create identity changed")
            network_internal = inspect_network_internal(current_runtime, current_network_id, network, current_backend)
            data_sha = digest({str(path.relative_to(data_dir)): sha256(path) for path in sorted(data_dir.rglob("*")) if path.is_file()})
            port = args.port or 5006
            url = f"http://127.0.0.1:{port}"
            started_container = runtime_call(
                current_runtime, "run", "-d", "--pull=never", "--network", network, "--name", sidecar,
                "--label", f"{RESTORE_OWNER_LABEL}={RESTORE_OWNER_VALUE}",
                "--label", f"{RESTORE_RUN_LABEL}={network}",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{data_dir}:/data", args.image,
            )
            if started_container.returncode != 0:
                raise DrillError("sidecar_start_failed", "start")
            current_sidecar_id = parse_runtime_id(started_container.stdout, "sidecar_identity_invalid", "start")
            namespace = inspect_sidecar_namespace(current_runtime, sidecar, current_sidecar_id, network)
            health_error: DrillError | None = None
            for _attempt in range(args.health_attempts):
                try:
                    probe_http(url, http_command, namespace_tool=namespace_tool, namespace=namespace)
                    break
                except DrillError as error:
                    health_error = error
                    time.sleep(1)
            else:
                raise health_error or DrillError("ui_http_probe_failed", "health")
            first_readback = probe_readback(
                readback_command,
                expected,
                run_index=run_index,
                url=url,
                data_dir=data_dir,
                namespace_tool=namespace_tool,
                namespace=namespace,
                timeout=args.readback_timeout,
                readback_path=readback_path,
            )
            restarted = runtime_call(current_runtime, "restart", current_sidecar_id)
            if restarted.returncode != 0:
                raise DrillError("sidecar_restart_failed", "restart")
            namespace = inspect_sidecar_namespace(current_runtime, sidecar, current_sidecar_id, network)
            for _attempt in range(args.health_attempts):
                try:
                    probe_http(url, http_command, namespace_tool=namespace_tool, namespace=namespace)
                    break
                except DrillError as error:
                    health_error = error
                    time.sleep(1)
            else:
                raise health_error or DrillError("ui_http_probe_failed", "restart_health")
            second_readback = probe_readback(
                readback_command,
                expected,
                run_index=run_index,
                url=url,
                data_dir=data_dir,
                namespace_tool=namespace_tool,
                namespace=namespace,
                timeout=args.readback_timeout,
                readback_path=readback_path,
            )
            repeat_match = first_readback == second_readback
            if not repeat_match:
                raise DrillError("restart_readback_mismatch", "restart")
            run = {
                "run_index": run_index,
                "sidecar_id": sidecar,
                "network_name": network,
                "network_internal": network_internal,
                "data_sha256": data_sha,
                **first_readback,
                "restart_verified": True,
                "repeat_state_match": repeat_match,
                "cleanup_verified": False,
                "network_cleanup_verified": False,
                "status": "failed",
            }
            if not cleanup():
                result["cleanup_verified"] = False
                raise DrillError("cleanup_not_verified", "cleanup")
            run["cleanup_verified"] = True
            run["network_cleanup_verified"] = True
            run["status"] = "passed"
            result["runs"].append(run)
        result["status"] = "passed"
        return_code = 0
    except DrillError as error:
        result["status"] = "blocked" if error.code in {"container_runtime_unavailable", "image_digest_unavailable"} else "failed"
        result["error"] = error_payload(error)
        result["cleanup_verified"] = cleanup() and result["cleanup_verified"]
        return_code = 2 if result["status"] == "blocked" else 1
    except SystemExit as exit_error:
        result["status"] = "failed"
        result["error"] = {"code": "signal_interrupted", "stage": "cleanup"}
        return_code = exit_error.code if isinstance(exit_error.code, int) else 130
    finally:
        if current_sidecar or current_network or current_data:
            result["cleanup_verified"] = cleanup() and result["cleanup_verified"]
        if current_sidecar or current_network or current_data:
            result["cleanup"]["outer_temp_root_removed"] = False
            result["cleanup"]["retained_paths"] = [str(temp_root_for_cleanup)] if temp_root_for_cleanup else []
            outer_cleanup_verified = False
        else:
            outer_cleanup_verified = cleanup_outer_root()
        if not outer_cleanup_verified:
            result["cleanup_verified"] = False
            result["status"] = "failed"
            result["error"] = result["error"] or {
                "code": "outer_temp_cleanup_failed",
                "stage": "cleanup",
                "detail": str(temp_root_for_cleanup),
            }
            return_code = 1
        result["completed_at"] = now()
        try:
            write_json(receipt, result)
        except OSError as error:
            print(json.dumps({"level": "error", "event": "restore_receipt_write_failed", "detail": str(error)}), file=sys.stderr)
            return_code = 1
    print(json.dumps({"status": result["status"], "receipt": str(receipt), "error": result["error"]}, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
