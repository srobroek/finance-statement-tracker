#!/usr/bin/env python3
"""Restore Actual data into isolated containers and prove redacted readback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
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
NOT_FOUND = (
    "no such container",
    "no container with name or id",
    "container not found",
    "no such network",
    "network not found",
)


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


def run_checked(argv: list[str], *, capture: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=capture, text=True, env=env)


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

def runtime_call(runtime: list[str], *args: str) -> subprocess.CompletedProcess[str]:
    return run_checked([*runtime, *args])


def inspect_state(runtime: list[str], sidecar: str) -> str:
    result = runtime_call(runtime, "inspect", sidecar)
    if result.returncode == 0:
        return "present"
    message = f"{result.stdout}\n{result.stderr}".casefold()
    if any(marker in message for marker in NOT_FOUND):
        return "absent"
    raise DrillError("runtime_inspect_failed", "cleanup")


def inspect_network_state(runtime: list[str], network: str) -> str:
    result = runtime_call(runtime, "network", "inspect", network)
    if result.returncode == 0:
        return "present"
    message = f"{result.stdout}\n{result.stderr}".casefold()
    if any(marker in message for marker in NOT_FOUND):
        return "absent"
    raise DrillError("runtime_network_inspect_failed", "cleanup")


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


def probe_readback(command: list[str], expected: dict[str, Any], *, run_index: int, url: str, data_dir: Path) -> dict[str, Any]:
    environment = os.environ.copy()
    environment.update({
        "ACTUAL_RESTORE_URL": url,
        "ACTUAL_RESTORE_DATA_DIR": str(data_dir),
        "ACTUAL_RESTORE_RUN_INDEX": str(run_index),
    })
    result = run_checked(command, env=environment)
    if result.returncode != 0:
        raise DrillError("readback_probe_failed", "readback")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DrillError("readback_probe_output_invalid", "readback") from exc
    return validate_readback(payload, expected)


def probe_http(url: str, command: list[str] | None) -> None:
    if command:
        result = run_checked(command, env={**os.environ, "ACTUAL_RESTORE_URL": url})
        if result.returncode != 0:
            raise DrillError("ui_http_probe_failed", "health")
        return
    try:
        with urllib.request.urlopen(f"{url}/", timeout=5) as response:
            if response.status != 200:
                raise DrillError("ui_http_probe_failed", "health", f"HTTP {response.status}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DrillError("ui_http_probe_failed", "health", str(exc)) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", default=os.environ.get("FINANCE_BACKUP_ROOT", "/opt/backups/finance-actual-poc"))
    parser.add_argument("--backup-path")
    parser.add_argument("--receipt", default=os.environ.get("FINANCE_ACTUAL_RESTORE_RECEIPT"))
    parser.add_argument("--image", default=os.environ.get("FINANCE_ACTUAL_RESTORE_IMAGE", DEFAULT_IMAGE))
    parser.add_argument("--runtime", default=os.environ.get("FINANCE_CONTAINER_RUNTIME"))
    parser.add_argument("--repeat", type=int, default=int(os.environ.get("FINANCE_ACTUAL_RESTORE_RUNS", "2")))
    parser.add_argument("--health-attempts", type=int, default=int(os.environ.get("FINANCE_ACTUAL_RESTORE_HEALTH_ATTEMPTS", "60")))
    parser.add_argument("--temp-root", default=os.environ.get("FINANCE_ACTUAL_RESTORE_TEMP_ROOT", "/tmp"))
    parser.add_argument("--port", type=int, default=0)
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
    current_sidecar: str | None = None
    current_network: str | None = None
    current_data: Path | None = None
    temp_root_for_cleanup: Path | None = None

    def cleanup() -> bool:
        nonlocal current_sidecar, current_network, current_data
        ok = True
        sidecar_state = "absent"
        if current_runtime and current_sidecar:
            try:
                sidecar_state = inspect_state(current_runtime, current_sidecar)
            except DrillError:
                sidecar_state = "unknown"
                ok = False
            if sidecar_state == "present":
                removed = runtime_call(current_runtime, "rm", "-f", current_sidecar)
                if removed.returncode != 0:
                    ok = False
                else:
                    try:
                        sidecar_state = inspect_state(current_runtime, current_sidecar)
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
            try:
                network_state = inspect_network_state(current_runtime, current_network)
            except DrillError:
                network_state = "unknown"
                ok = False
            if network_state == "present" and sidecar_state == "absent":
                removed = runtime_call(current_runtime, "network", "rm", current_network)
                if removed.returncode != 0:
                    ok = False
                else:
                    try:
                        network_state = inspect_network_state(current_runtime, current_network)
                    except DrillError:
                        network_state = "unknown"
                        ok = False
                    if network_state != "absent":
                        ok = False
            elif network_state != "absent":
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
            current_sidecar, current_network, current_data = None, None, None
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
        result["runtime"]["engine"] = Path(current_runtime[0]).name
        version = runtime_call(current_runtime, "version")
        if version.returncode != 0:
            raise DrillError("container_runtime_unavailable", "runtime")
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
        for run_index in range(1, args.repeat + 1):
            sidecar = f"finance-actual-restore-{run_index}-{os.getpid()}"
            data_dir = temp_root / f"data-{run_index}"
            network = f"finance-actual-restore-net-{run_index}-{os.getpid()}"
            if inspect_state(current_runtime, sidecar) != "absent" or data_dir.exists():
                raise DrillError("disposable_resource_collision", "preflight", sidecar)
            if inspect_network_state(current_runtime, network) != "absent":
                raise DrillError("disposable_resource_collision", "preflight", network)
            current_sidecar, current_data = sidecar, data_dir
            current_network = network
            safe_extract_actual(archive, data_dir)
            created_network = runtime_call(
                current_runtime,
                "network",
                "create",
                "--internal",
                "--label",
                "finance.restore.owner=actual-restore",
                "--label",
                f"finance.restore.run={network}",
                network,
            )
            if created_network.returncode != 0:
                raise DrillError("disposable_network_create_failed", "network")
            data_sha = digest({str(path.relative_to(data_dir)): sha256(path) for path in sorted(data_dir.rglob("*")) if path.is_file()})
            port = args.port or (5006 + run_index + (os.getpid() % 1000))
            url = f"http://127.0.0.1:{port}"
            started_container = runtime_call(
                current_runtime, "run", "-d", "--pull=never", "--network", network, "--name", sidecar,
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-p", f"127.0.0.1:{port}:5006", "-v", f"{data_dir}:/data", args.image,
            )
            if started_container.returncode != 0:
                raise DrillError("sidecar_start_failed", "start")
            health_error: DrillError | None = None
            for _attempt in range(args.health_attempts):
                try:
                    probe_http(url, http_command)
                    break
                except DrillError as error:
                    health_error = error
                    time.sleep(1)
            else:
                raise health_error or DrillError("ui_http_probe_failed", "health")
            first_readback = probe_readback(readback_command, expected, run_index=run_index, url=url, data_dir=data_dir)
            restarted = runtime_call(current_runtime, "restart", sidecar)
            if restarted.returncode != 0:
                raise DrillError("sidecar_restart_failed", "restart")
            for _attempt in range(args.health_attempts):
                try:
                    probe_http(url, http_command)
                    break
                except DrillError as error:
                    health_error = error
                    time.sleep(1)
            else:
                raise health_error or DrillError("ui_http_probe_failed", "restart_health")
            second_readback = probe_readback(readback_command, expected, run_index=run_index, url=url, data_dir=data_dir)
            repeat_match = first_readback == second_readback
            if not repeat_match:
                raise DrillError("restart_readback_mismatch", "restart")
            run = {
                "run_index": run_index,
                "sidecar_id": sidecar,
                "network_name": network,
                "network_internal": True,
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
