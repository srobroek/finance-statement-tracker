from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy/actual-poc/restore-actual-disposable.py"
SCHEMA = ROOT / "config/actual-restore-receipt.schema.json"
READBACK_SCHEMA = ROOT / "schemas/actual-restore-readback-v1.schema.json"


def _namespace_probe_error() -> OSError | None:
    """Return the local runner's namespace-inspection limitation, if any."""
    try:
        os.stat(f"/proc/{os.getpid()}/ns/net")
    except OSError as exc:
        return exc
    return None


def _sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (1, 'actual')")
        connection.commit()
    finally:
        connection.close()


def backup_fixture(root: Path) -> Path:
    source = root / "source"
    _sqlite(source / "actual-data/server-files/account.sqlite")
    _sqlite(source / "actual-data/user-files/budget.sqlite")
    _sqlite(source / "cashback-data/cashback-events.sqlite3")
    configuration = source / "configuration"
    configuration.mkdir(parents=True)
    (configuration / "actual-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (configuration / "cashback-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    backup = root / "20260820T120000Z"
    backup.mkdir()
    archive = backup / "finance-data.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(source.rglob("*")):
            bundle.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (backup / "SHA256SUMS").write_text(f"{digest}  finance-data.tar.gz\n", encoding="ascii")
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 4,
                "created_at": backup.name,
                "includes": ["actual-data", "cashback-data", "configuration"],
                "secrets_included": False,
                "excluded_data": [
                    "cashback-data/cashback-events.sqlite3:push_deliveries",
                    "cashback-data/cashback-events.sqlite3:push_state",
                    "cashback-data/cashback-events.sqlite3:push_subscriptions",
                ],
                "excluded_paths": ["cashback-data/pre-deploy-*.sqlite3*"],
            }
        ),
        encoding="utf-8",
    )
    return backup


def expected_fixture(root: Path) -> Path:
    path = root / "expected.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "accounts": [{"name": "Current", "balance_minor": 100, "closed": False, "offbudget": False}],
                "representative_transactions": [{
                    "account_name": "Current",
                    "amount_minor": -10,
                    "date": "2026-08-01",
                    "imported_id": "statement:fixture:1",
                    "payee": "Merchant",
                }],
            }
        ),
        encoding="utf-8",
    )
    return path


def executable(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.write_text(f"#!/usr/bin/env python3\n{body}\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def fake_runtime(
    root: Path,
    *,
    backend: str = "docker",
    collision: bool = False,
    network_collision: bool = False,
    inspect_failure: bool = False,
    cleanup_inspect_failure: bool = False,
    network_inspect_failure: bool = False,
    network_failure: bool = False,
    container_missing_message: str | None = None,
    network_missing_message: str | None = None,
    container_missing_stdout: str = "",
    network_missing_stdout: str = "",
    start_failure: bool = False,
    restart_failure: bool = False,
    network_internal: bool = True,
    namespace_identity: str = "owned",
    replacement_race: str | None = None,
    network_label_mode: str = "valid",
    container_label_mode: str = "valid",
) -> Path:
    return executable(
        root,
        "runtime",
        f"""
import pathlib
import os
import sys
root = pathlib.Path({str(root)!r})
command = sys.argv[1] if len(sys.argv) > 1 else ''
if command == 'version':
    if {backend!r} == 'Emulate Docker CLI using Podman':
        print('Emulate Docker CLI using podman. Create /etc/containers/nodocker to quiet msg.')
    else:
        print({backend!r} + ' version 1.0')
    raise SystemExit(0)
if command == 'network':
    action = sys.argv[2] if len(sys.argv) > 2 else ''
    name = sys.argv[-1]
    if {replacement_race!r} == 'network' and action == 'inspect' and '--format' in sys.argv and not (root / 'network-replacement-done').exists() and ('Internal' in sys.argv[sys.argv.index('--format') + 1] or 'Labels' in sys.argv[sys.argv.index('--format') + 1]):
        target_id = name
        for identity_path in root.glob('*.network-id'):
            if identity_path.read_text(encoding='ascii') == target_id:
                name = identity_path.name.removesuffix('.network-id')
                identity_path.write_text('d' * 64, encoding='ascii')
                (root / 'network-replacement-done').write_text('1', encoding='ascii')
                print('d' * 64 + '|' + name + '|foreign-owner|foreign-run|true')
                raise SystemExit(0)
    for identity_path in root.glob('*.network-id'):
        if identity_path.read_text(encoding='ascii') == name:
            name = identity_path.name.removesuffix('.network-id')
            break
    marker = root / (name + '.network')
    if action == 'inspect':
        if '--format' in sys.argv:
            (root / 'network-inspect-format').write_text(sys.argv[sys.argv.index('--format') + 1], encoding='ascii')
        if {inspect_failure!r} or {network_inspect_failure!r} or ({cleanup_inspect_failure!r} and list(root.glob('*.container'))):
            print('runtime transport failure', file=sys.stderr)
            raise SystemExit(1)
        if {network_collision!r} and 'restore-net-1-' in name:
            raise SystemExit(0)
        if marker.exists():
            if '--format' in sys.argv:
                format_string = sys.argv[sys.argv.index('--format') + 1]
                if format_string in ('{{{{.Id}}}}|{{{{.Name}}}}', '{{{{.ID}}}}|{{{{.Name}}}}'):
                    print((root / (name + '.network-id')).read_text(encoding='ascii') + '|' + name)
                if format_string == '{{{{.Id}}}}|{{{{.Name}}}}|{{{{.Internal}}}}':
                    print((root / (name + '.network-id')).read_text(encoding='ascii') + '|' + name + '|' + ('true' if {network_internal!r} else 'false'))
                if 'index .Labels' in format_string:
                    owner_label = 'actual-restore' if {network_label_mode!r} == 'valid' else ('' if {network_label_mode!r} == 'missing' else 'foreign-owner')
                    run_label = name if {network_label_mode!r} == 'valid' else ('' if {network_label_mode!r} == 'missing' else 'foreign-run')
                    if 'Internal' in format_string:
                        print((root / (name + '.network-id')).read_text(encoding='ascii') + '|' + name + '|' + owner_label + '|' + run_label + '|' + ('true' if {network_internal!r} else 'false'))
                    else:
                        print((root / (name + '.network-id')).read_text(encoding='ascii') + '|' + name + '|' + owner_label + '|' + run_label)
            raise SystemExit(0)
        missing_message = {network_missing_message!r}
        if missing_message is None:
            missing_message = f'Error: network {{name}} not found'
        else:
            missing_message = missing_message.format(name=name, name_upper=name.upper())
        missing_stdout = {network_missing_stdout!r}.format(name=name, name_upper=name.upper())
        if missing_stdout:
            print(missing_stdout, end='')
        print(missing_message, file=sys.stderr)
        raise SystemExit(1)
    if action == 'create':
        if {network_failure!r}:
            print('network create failed', file=sys.stderr)
            raise SystemExit(1)
        marker.write_text('internal', encoding='utf-8')
        network_id = 'c' * 64
        (root / (name + '.network-id')).write_text(network_id, encoding='ascii')
        create_output = name if {backend!r} == 'podman' else network_id
        (root / 'network-create-stdout').write_text(create_output, encoding='ascii')
        print(create_output)
        raise SystemExit(0)
    if action == 'rm':
        marker.unlink(missing_ok=True)
        (root / (name + '.network-id')).unlink(missing_ok=True)
        raise SystemExit(0)
    raise SystemExit(1)
name = sys.argv[sys.argv.index('--name') + 1] if '--name' in sys.argv else (sys.argv[2] if len(sys.argv) > 2 else '')
if command == 'inspect' and '--format' in sys.argv:
    name = sys.argv[-1]
    for identity_path in root.glob('*.container-id'):
        if identity_path.read_text(encoding='ascii') == name:
            name = identity_path.name.removesuffix('.container-id')
            break
if command == 'rm' and len(sys.argv) > 3 and sys.argv[2] == '-f':
    name = sys.argv[3]
    for identity_path in root.glob('*.container-id'):
        if identity_path.read_text(encoding='ascii') == name:
            name = identity_path.name.removesuffix('.container-id')
            break
if command == 'restart':
    for identity_path in root.glob('*.container-id'):
        if identity_path.read_text(encoding='ascii') == name:
            name = identity_path.name.removesuffix('.container-id')
            break
marker = root / (name + '.container')
if command == 'image' and len(sys.argv) > 3 and sys.argv[2] == 'inspect':
    print('a' * 64)
    raise SystemExit(0)
if command == 'inspect':
    if {inspect_failure!r} or ({cleanup_inspect_failure!r} and marker.exists() and (('--format' not in sys.argv) or 'State.Pid' not in sys.argv[sys.argv.index('--format') + 1])):
        print('runtime transport failure', file=sys.stderr)
        raise SystemExit(1)
    if {collision!r} and 'restore-1-' in name:
        raise SystemExit(0)
    if marker.exists():
        if '--format' in sys.argv:
            format_string = sys.argv[sys.argv.index('--format') + 1]
            if format_string == '{{{{.Id}}}}|{{{{.Name}}}}':
                print((root / (name + '.container-id')).read_text(encoding='ascii') + '|' + name)
            if format_string == '{{{{.Id}}}}|{{{{.Name}}}}|{{{{.State.Pid}}}}':
                identity_name = name if {namespace_identity!r} == 'owned' else 'foreign-sidecar'
                identity_id = (root / (name + '.container-id')).read_text(encoding='ascii') if {namespace_identity!r} != 'invalid-id' else 'not-an-id'
                identity_pid = str(os.getppid()) if {namespace_identity!r} != 'invalid-pid' else 'not-a-pid'
                print(identity_id + '|' + identity_name + '|' + identity_pid)
            if 'index .Config.Labels' in format_string:
                identity_name = name if ('State.Pid' not in format_string or {namespace_identity!r} == 'owned') else 'foreign-sidecar'
                identity_id = (root / (name + '.container-id')).read_text(encoding='ascii') if ('State.Pid' not in format_string or {namespace_identity!r} != 'invalid-id') else 'not-an-id'
                identity_pid = str(os.getppid()) if {namespace_identity!r} != 'invalid-pid' else 'not-a-pid'
                owner_label = 'actual-restore' if {container_label_mode!r} == 'valid' else ('' if {container_label_mode!r} == 'missing' else 'foreign-owner')
                run_label = next((path.name.removesuffix('.network-id') for path in root.glob('*.network-id')), '') if {container_label_mode!r} == 'valid' else ('' if {container_label_mode!r} == 'missing' else 'foreign-run')
                if 'State.Pid' in format_string:
                    print(identity_id + '|' + identity_name + '|' + identity_pid + '|' + owner_label + '|' + run_label)
                else:
                    print(identity_id + '|' + identity_name + '|' + owner_label + '|' + run_label)
        raise SystemExit(0)
    if {container_missing_stdout!r}:
        print({container_missing_stdout!r})
    missing_message = {container_missing_message!r}
    if missing_message is None:
        missing_message = f'Error: no container with name or ID "{{name}}" found: no such container'
    else:
        missing_message = missing_message.format(name=name, name_upper=name.upper())
    print(missing_message, file=sys.stderr)
    raise SystemExit(1)
if command == 'run':
    (root / 'run-args').write_text(' '.join(sys.argv), encoding='utf-8')
    if {start_failure!r}:
        print('start failed', file=sys.stderr)
        raise SystemExit(1)
    marker.write_text('present', encoding='utf-8')
    container_id = 'a' * 64
    (root / (name + '.container-id')).write_text(container_id, encoding='ascii')
    (root / (name + '.container-label-mode')).write_text({container_label_mode!r}, encoding='ascii')
    print(container_id)
    raise SystemExit(0)
if command == 'restart':
    if {replacement_race!r} == 'container' and not (root / 'container-replacement-done').exists():
        for identity_path in root.glob('*.container-id'):
            if identity_path.name.removesuffix('.container-id') == name:
                identity_path.write_text('d' * 64, encoding='ascii')
                (root / 'container-replacement-done').write_text('1', encoding='ascii')
                break
    if {restart_failure!r}:
        print('restart failed', file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)
if command == 'rm':
    marker.unlink(missing_ok=True)
    (root / (name + '.container-id')).unlink(missing_ok=True)
    raise SystemExit(0)
raise SystemExit(1)
""",
    )


def namespace_tool(root: Path) -> Path:
    return executable(
        root,
        "namespace-tool",
        """
import pathlib
import subprocess
import sys
root = pathlib.Path(__file__).parent
if '--target' not in sys.argv or '--net' not in sys.argv or '--' not in sys.argv:
    raise SystemExit(1)
target = sys.argv[sys.argv.index('--target') + 1]
if not pathlib.Path('/proc', target, 'ns', 'net').exists():
    raise SystemExit(1)
(root / 'namespace-args').write_text(' '.join(sys.argv), encoding='utf-8')
command = sys.argv[sys.argv.index('--') + 1:]
child = subprocess.Popen(command)
(root / 'namespace-child.pid').write_text(str(child.pid), encoding='ascii')
raise SystemExit(child.wait())
""",
    )


def process_is_live(pid_path: Path) -> bool:
    try:
        pid = int(pid_path.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()[2]
    except (OSError, IndexError):
        return False
    return state != "Z"


def probe(root: Path) -> Path:
    return executable(
        root,
        "probe",
        """
import json
payload = {
  'accounts': [{'name': 'Current', 'balance_minor': 100, 'closed': False, 'offbudget': False}],
  'representative_transactions': [{'account_name': 'Current', 'amount_minor': -10, 'date': '2026-08-01', 'imported_id': 'statement:fixture:1', 'payee': 'Merchant'}]
}
print(json.dumps({'api': payload, 'ui': payload}))
""",
    )


class ActualRestoreTests(unittest.TestCase):
    def run_drill(
        self,
        root: Path,
        runtime: Path,
        *,
        expected: Path | None = None,
        probe_path: Path | None = None,
        health_path: Path | None = None,
        health_attempts: int | None = None,
        readback_timeout: float | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        receipt = root / "receipt.json"
        namespace = namespace_tool(root)
        command = [
            str(SCRIPT),
            "--backup-root", str(root),
            "--receipt", str(receipt),
            "--runtime", str(runtime),
            "--namespace-tool", str(namespace),
            "--expected-readback", str(expected or expected_fixture(root)),
            "--readback-command", str(probe_path or probe(root)),
            "--http-probe-command", str(health_path or executable(root, "health", "raise SystemExit(0)")),
            "--temp-root", str(root),
        ]
        if health_attempts is not None:
            command.extend(["--health-attempts", str(health_attempts)])
        if readback_timeout is not None:
            command.extend(["--readback-timeout", str(readback_timeout)])
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        if payload.get("error", {}).get("code") == "runtime_namespace_identity_failed":
            limitation = _namespace_probe_error()
            if limitation is not None:
                required = os.environ.get("CI", "").lower() == "true" or os.environ.get(
                    "FINANCE_ACTUAL_RESTORE_REQUIRE_NAMESPACE", ""
                ).lower() == "true"
                if required:
                    self.fail(f"namespace identity probe unavailable in required runner: {limitation}")
                self.skipTest(f"namespace identity probe unavailable in this runner: {limitation}")
        return result, payload

    def test_source_contract_has_exact_cleanup_and_signal_guards(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        for required in (
            "verify-backup.py",
            "--pull",
            "--network",
            "--internal",
            '"network", "inspect"',
            "--name",
            '"restart"',
            '"rm", "-f"',
            "SIGINT",
            "SIGTERM",
            "disposable_resource_collision",
            "ui_api_parity",
            "ACTUAL_RESTORE_READBACK_PATH",
            "ACTUAL_RESTORE_READBACK_CONTAINER_PATH",
            "ACTUAL_RESTORE_CHECKPOINT_PATH",
            "ACTUAL_RESTORE_CHECKPOINT_CONTAINER_PATH",
            "failure_checkpoint_payload",
            "readback_probe_output_empty",
            "probe_failure_detail",
            "MAX_READBACK_DIAGNOSTIC",
            "SAFE_DIAGNOSTIC_LABEL",
            "stderr_sha256",
            '"production_mutated": False',
            '"secret_values_recorded": False',
            '"network_cleanup_verified": False',
            '"outer_temp_root_removed": False',
            '"retained_paths": []',
        ):
            self.assertIn(required, source)
        self.assertNotIn("docker compose", source)
        self.assertNotIn('"--network", "none"', source)
        self.assertNotIn("ignore_errors", source)

    def test_hash_mismatch_is_failed_and_not_a_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = backup_fixture(root)
            (backup / "finance-data.tar.gz").write_bytes(b"tampered")
            result, receipt = self.run_drill(root, fake_runtime(root))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "backup_verification_failed")
            self.assertFalse(receipt["backup"]["verified"])
            self.assertEqual(receipt["runs"], [])

    def test_collision_is_refused_without_removing_owned_or_preexisting_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, collision=True))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "disposable_resource_collision")
            self.assertTrue(receipt["cleanup_verified"])

    def test_network_collision_is_refused_without_cleanup_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, network_collision=True))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "disposable_resource_collision")
            self.assertTrue(receipt["cleanup_verified"])

    def test_unknown_runtime_inspect_fails_closed_without_cleanup_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, inspect_failure=True))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_docker_no_such_object_messages_are_classified_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    container_missing_message='Error: No such object: "{name}"',
                    network_missing_message="Error: network {name}: unable to find network with name or ID {name}: network not found",
                    container_missing_stdout="[]",
                ),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["status"], "passed")
            self.assertEqual(len(receipt["runs"]), 2)

    def test_docker_and_podman_aliases_report_the_detected_backend(self) -> None:
        for backend, expected_backend in (("docker", "docker"), ("podman", "podman"), ("Emulate Docker CLI using Podman", "podman")):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                backup_fixture(root)
                result, receipt = self.run_drill(root, fake_runtime(root, backend=backend))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(receipt["runtime"]["engine"], expected_backend)
                inspect_format = (root / "network-inspect-format").read_text(encoding="ascii")
                self.assertIn("{{.ID}}" if expected_backend == "podman" else "{{.Id}}", inspect_format)

    def test_podman_name_shaped_network_create_output_resolves_immutable_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, backend="podman"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["status"], "passed")
            self.assertTrue((root / "network-create-stdout").read_text(encoding="ascii").startswith("finance-actual-restore-net-"))

    def test_ambiguous_runtime_text_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, backend="runc ... Docker compatibility"))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "unsupported_container_runtime")

    def test_bare_podman_alias_prose_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, backend="maintenance note: emulate docker cli using podman"))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "unsupported_container_runtime")

    def test_network_labels_are_required_and_bound_to_requested_name(self) -> None:
        for label_mode in ("missing", "wrong"):
            with self.subTest(label_mode=label_mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                backup_fixture(root)
                result, receipt = self.run_drill(root, fake_runtime(root, network_label_mode=label_mode))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(receipt["error"]["code"], "runtime_network_identity_changed")
                self.assertFalse(receipt["cleanup_verified"])
                self.assertTrue(list(root.glob("*.network")))

    def test_container_labels_are_required_and_bound_to_network(self) -> None:
        for label_mode in ("missing", "wrong"):
            with self.subTest(label_mode=label_mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                backup_fixture(root)
                result, receipt = self.run_drill(root, fake_runtime(root, container_label_mode=label_mode))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(receipt["error"]["code"], "runtime_namespace_identity_failed")
                self.assertFalse(receipt["cleanup_verified"])
                self.assertTrue(list(root.glob("*.container")))

    def test_unsupported_runtime_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, backend="runc"))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "unsupported_container_runtime")
            self.assertFalse(receipt["runtime"]["verified"])

    def test_unidentified_docker_basename_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            runtime = fake_runtime(root, backend="unknown")
            docker_alias = root / "docker"
            runtime.rename(docker_alias)
            result, receipt = self.run_drill(root, docker_alias)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "unsupported_container_runtime")

    def test_non_internal_network_fails_closed_without_starting_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, network_internal=False))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "network_not_internal")
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_foreign_sidecar_namespace_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, namespace_identity="foreign"))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_namespace_identity_failed")
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_invalid_sidecar_namespace_pid_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, namespace_identity="invalid-pid"))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_namespace_identity_failed")
            self.assertTrue(receipt["cleanup_verified"])

    def test_container_replacement_race_is_not_removed_as_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, replacement_race="container"))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_namespace_identity_failed")
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(list(root.glob("*.container")))
            self.assertEqual((next(root.glob("*.container-id"))).read_text(encoding="ascii"), "d" * 64)

    def test_network_replacement_race_is_not_removed_as_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, replacement_race="network"))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_network_identity_changed")
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(list(root.glob("*.network")))
            self.assertEqual((next(root.glob("*.network-id"))).read_text(encoding="ascii"), "d" * 64)

    def test_namespace_probe_timeout_fails_closed_and_cleans_owned_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            slow_health = executable(root, "slow-health", "import time; time.sleep(10)")
            started = time.monotonic()
            result, receipt = self.run_drill(root, fake_runtime(root), health_path=slow_health, health_attempts=1)
            self.assertLess(time.monotonic() - started, 8)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "ui_http_probe_failed")
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))
            self.assertFalse(process_is_live(root / "namespace-child.pid"))

    def test_readback_probe_timeout_fails_closed_and_reaps_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            slow_probe = executable(root, "slow-probe", "import time; time.sleep(10)")
            started = time.monotonic()
            result, receipt = self.run_drill(root, fake_runtime(root), probe_path=slow_probe, readback_timeout=5)
            self.assertLess(time.monotonic() - started, 8)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "readback_probe_failed")
            self.assertIn("exit_code=124", receipt["error"]["detail"])
            self.assertIn("command timed out", receipt["error"]["detail"])
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))
            self.assertFalse(process_is_live(root / "namespace-child.pid"))

    def test_readback_path_is_bound_to_the_receipt_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            path_probe = executable(
                root,
                "path-probe",
                f"""
import json
import os
from pathlib import Path

run_index = os.environ["ACTUAL_RESTORE_RUN_INDEX"]
expected_host = Path({str(root)!r}) / f"readback-{{run_index}}.json"
actual_host = Path(os.environ["ACTUAL_RESTORE_READBACK_PATH"])
if actual_host != expected_host:
    print(f"wrong-path token=path-sentinel expected={{expected_host}} actual={{actual_host}}", file=__import__("sys").stderr)
    raise SystemExit(41)
if os.environ["ACTUAL_RESTORE_READBACK_CONTAINER_PATH"] != f"/data/readback-{{run_index}}.json":
    raise SystemExit(42)
actual_host.write_text("bound", encoding="ascii")
payload = {{
  "accounts": [{{"name": "Current", "balance_minor": 100, "closed": False, "offbudget": False}}],
  "representative_transactions": [{{"account_name": "Current", "amount_minor": -10, "date": "2026-08-01", "imported_id": "statement:fixture:1", "payee": "Merchant"}}]
}}
print(json.dumps({{"api": payload, "ui": payload}}))
""",
            )
            result, receipt = self.run_drill(root, fake_runtime(root), probe_path=path_probe)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["status"], "passed")
            self.assertEqual((root / "readback-1.json").read_text(encoding="ascii"), "bound")
            self.assertEqual((root / "readback-2.json").read_text(encoding="ascii"), "bound")

    def test_readback_empty_stdout_is_failed_with_redacted_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            empty_probe = executable(
                root,
                "empty-probe",
                "print('browser token=top-secret-sentinel', file=__import__('sys').stderr)",
            )
            result, receipt = self.run_drill(root, fake_runtime(root), probe_path=empty_probe)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "readback_probe_output_empty")
            detail = receipt["error"]["detail"]
            self.assertIn("exit_code=0", detail)
            self.assertIn("stdout_bytes=0", detail)
            self.assertIn("browser token=<redacted>", detail)
            self.assertNotIn("top-secret-sentinel", detail)
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))
            self.assertFalse(list(root.glob("finance-actual-restore.*")))

    def test_readback_failure_retains_sanitized_mode0600_phase_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            failing_probe = executable(
                root,
                "phase-failing-probe",
                """
import json
import os
import sys
from pathlib import Path

checkpoint = Path(os.environ["ACTUAL_RESTORE_CHECKPOINT_PATH"])
checkpoint.parent.mkdir(parents=True, exist_ok=True)
checkpoint.write_text(json.dumps({"label": "probe_failure", "secret": "checkpoint-secret-sentinel"}), encoding="utf-8")
print("playwright failure token=stderr-secret-sentinel", file=sys.stderr)
raise SystemExit(23)
""",
            )
            result, receipt = self.run_drill(root, fake_runtime(root), probe_path=failing_probe)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "readback_probe_failed")
            retained = [Path(path) for path in receipt["cleanup"]["retained_paths"]]
            checkpoint = root / "phase-checkpoint-1.json"
            self.assertIn(checkpoint, retained)
            self.assertTrue(checkpoint.is_file())
            self.assertEqual(checkpoint.stat().st_mode & 0o777, 0o600)
            phase = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(phase["schema_version"], 1)
            self.assertEqual(phase["status"], "failed")
            self.assertEqual(phase["run_index"], 1)
            self.assertEqual(phase["phase"], "readback")
            self.assertEqual(phase["code"], "readback_probe_failed")
            self.assertEqual(phase["exit_code"], 23)
            self.assertIn("playwright", phase["diagnostic"])
            self.assertNotIn("checkpoint-secret-sentinel", checkpoint.read_text(encoding="utf-8"))
            self.assertNotIn("stderr-secret-sentinel", checkpoint.read_text(encoding="utf-8"))
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(receipt["cleanup"]["outer_temp_root_removed"])

    def test_readback_failure_retains_phase_checkpoint_when_cleanup_is_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            failing_probe = executable(
                root,
                "phase-failing-probe",
                """
import json
import os
import sys
from pathlib import Path

checkpoint = Path(os.environ["ACTUAL_RESTORE_CHECKPOINT_PATH"])
checkpoint.parent.mkdir(parents=True, exist_ok=True)
checkpoint.write_text(json.dumps({"secret": "cleanup-secret-sentinel"}), encoding="utf-8")
print("playwright failure token=cleanup-stderr-secret-sentinel", file=sys.stderr)
raise SystemExit(23)
""",
            )
            result, receipt = self.run_drill(
                root,
                fake_runtime(root, cleanup_inspect_failure=True),
                probe_path=failing_probe,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "readback_probe_failed")
            checkpoint = root / "phase-checkpoint-1.json"
            retained = [Path(path) for path in receipt["cleanup"]["retained_paths"]]
            self.assertIn(checkpoint, retained)
            self.assertEqual(checkpoint.stat().st_mode & 0o777, 0o600)
            phase_text = checkpoint.read_text(encoding="utf-8")
            phase = json.loads(phase_text)
            self.assertEqual(phase["phase"], "readback")
            self.assertEqual(phase["exit_code"], 23)
            self.assertNotIn("cleanup-secret-sentinel", phase_text)
            self.assertNotIn("cleanup-stderr-secret-sentinel", phase_text)
            self.assertFalse(receipt["cleanup_verified"])
            self.assertFalse(receipt["cleanup"]["outer_temp_root_removed"])
            self.assertTrue(Path(receipt["cleanup"]["outer_temp_root"]).is_dir())
            self.assertTrue(list(root.glob("*.container")))
            self.assertTrue(list(root.glob("*.network")))

    def test_readback_schema_accepts_expected_and_probe_payloads(self) -> None:
        schema = json.loads(READBACK_SCHEMA.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        expected = {
            "schema_version": 1,
            "accounts": [{"name": "Current", "balance_minor": 100, "closed": False, "offbudget": False}],
            "representative_transactions": [{
                "account_name": "Current",
                "amount_minor": -10,
                "date": "2026-08-01",
                "imported_id": "statement:fixture:1",
                "payee": None,
            }],
        }
        self.assertEqual(list(validator.iter_errors(expected)), [])
        probe_payload = {key: value for key, value in expected.items() if key != "schema_version"}
        self.assertEqual(list(validator.iter_errors({"api": probe_payload, "ui": probe_payload})), [])
        probe_payload["accounts"][0]["balance_minor"] = "100"
        self.assertTrue(list(validator.iter_errors({"api": probe_payload, "ui": probe_payload})))

    def test_readback_digests_use_one_order_for_permuted_and_grouped_transactions(self) -> None:
        module_spec = importlib.util.spec_from_file_location("restore_actual_disposable_ordering", SCRIPT)
        self.assertIsNotNone(module_spec)
        self.assertIsNotNone(module_spec.loader)
        restore_module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = restore_module
        module_spec.loader.exec_module(restore_module)
        accounts = [
            {"name": "B", "balance_minor": 20, "closed": False, "offbudget": False},
            {"name": "A", "balance_minor": 10, "closed": False, "offbudget": False},
        ]
        transactions = [
            {"account_name": "B", "amount_minor": 10, "date": "2026-08-02", "imported_id": "b-2", "payee": None},
            {"account_name": "A", "amount_minor": 20, "date": "2026-08-01", "imported_id": "a-1", "payee": "Alpha"},
            {"account_name": "B", "amount_minor": -5, "date": "2026-08-01", "imported_id": "b-1", "payee": "Beta"},
        ]
        expected = {"schema_version": 1, "accounts": accounts, "representative_transactions": transactions}
        probe = {
            "api": {"accounts": [accounts[1], accounts[0]], "representative_transactions": [transactions[1], transactions[0], transactions[2]]},
            "ui": {"accounts": [accounts[0], accounts[1]], "representative_transactions": [transactions[2], transactions[1], transactions[0]]},
        }
        first = restore_module.validate_readback(probe, expected)
        probe["api"]["representative_transactions"] = [transactions[2], transactions[0], transactions[1]]
        probe["ui"]["representative_transactions"] = [transactions[0], transactions[1], transactions[2]]
        second = restore_module.validate_readback(probe, expected)
        self.assertEqual(first["api_readback_sha256"], second["api_readback_sha256"])
        self.assertEqual(first["ui_readback_sha256"], second["ui_readback_sha256"])
        self.assertEqual(first["api_readback_sha256"], first["ui_readback_sha256"])

    def test_readback_schema_and_runtime_reject_equivalent_malformed_payloads(self) -> None:
        module_spec = importlib.util.spec_from_file_location("restore_actual_disposable", SCRIPT)
        self.assertIsNotNone(module_spec)
        self.assertIsNotNone(module_spec.loader)
        restore_module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = restore_module
        module_spec.loader.exec_module(restore_module)
        expected_base = {
            "schema_version": 1,
            "accounts": [{"name": "Current", "balance_minor": 100, "closed": False, "offbudget": False}],
            "representative_transactions": [{
                "account_name": "Current",
                "amount_minor": -10,
                "date": "2026-08-01",
                "imported_id": "statement:fixture:1",
                "payee": None,
            }],
        }
        probe_base = {
            "api": copy.deepcopy({key: value for key, value in expected_base.items() if key != "schema_version"}),
            "ui": copy.deepcopy({key: value for key, value in expected_base.items() if key != "schema_version"}),
        }
        schema = json.loads(READBACK_SCHEMA.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        self.assertEqual(list(validator.iter_errors(expected_base)), [])
        self.assertEqual(list(validator.iter_errors(probe_base)), [])
        self.assertIsInstance(restore_module.validate_readback(probe_base, expected_base), dict)
        cases = (
            ("expected missing", "expected", lambda payload: payload.pop("accounts")),
            ("expected malformed", "expected", lambda payload: payload["accounts"][0].update(balance_minor="100")),
            ("expected additional", "expected", lambda payload: payload.update(unexpected=True)),
            ("api missing", "api", lambda payload: payload.pop("accounts")),
            ("api malformed", "api", lambda payload: payload["accounts"][0].update(closed="false")),
            ("api nullable non-payee", "api", lambda payload: payload["accounts"][0].update(name=None)),
            ("api additional", "api", lambda payload: payload.update(unexpected=True)),
            ("ui missing", "ui", lambda payload: payload.pop("accounts")),
            ("ui malformed", "ui", lambda payload: payload["accounts"][0].update(offbudget="false")),
            ("ui additional", "ui", lambda payload: payload.update(unexpected=True)),
        )
        for label, target, mutate in cases:
            with self.subTest(label=label):
                expected = copy.deepcopy(expected_base)
                probe = copy.deepcopy(probe_base)
                if target == "expected":
                    mutate(expected)
                    schema_payload = expected
                else:
                    mutate(probe[target])
                    schema_payload = probe
                self.assertTrue(list(validator.iter_errors(schema_payload)))
                with self.assertRaises(restore_module.DrillError):
                    if target == "expected":
                        restore_module.validate_readback_contract(expected, label="expected", include_schema_version=True)
                    else:
                        restore_module.validate_readback(probe, expected)

    def test_expected_readback_wrong_types_fail_before_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            expected = expected_fixture(root)
            payload = json.loads(expected.read_text(encoding="utf-8"))
            payload["accounts"][0]["balance_minor"] = "100"
            expected.write_text(json.dumps(payload), encoding="utf-8")
            result, receipt = self.run_drill(root, fake_runtime(root), expected=expected)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "readback_contract_invalid")
            self.assertEqual(receipt["error"]["stage"], "readback")
            self.assertIn("expected.accounts.balance_minor has invalid type", receipt["error"]["detail"])
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_api_and_ui_readback_wrong_types_fail_before_normalization(self) -> None:
        for interface in ("api", "ui"):
            with self.subTest(interface=interface), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                backup_fixture(root)
                malformed_probe = executable(
                    root,
                    f"malformed-{interface}-probe",
                    f"""
import copy
import json
payload = {{
  'accounts': [{{'name': 'Current', 'balance_minor': 100, 'closed': False, 'offbudget': False}}],
  'representative_transactions': [{{'account_name': 'Current', 'amount_minor': -10, 'date': '2026-08-01', 'imported_id': 'statement:fixture:1', 'payee': 'Merchant'}}]
}}
payloads = {{'api': copy.deepcopy(payload), 'ui': copy.deepcopy(payload)}}
payloads['{interface}']['accounts'][0]['balance_minor'] = '100'
print(json.dumps(payloads))
""",
                )
                result, receipt = self.run_drill(root, fake_runtime(root), probe_path=malformed_probe)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(receipt["error"]["code"], "readback_contract_invalid")
                self.assertEqual(receipt["error"]["stage"], "readback")
                self.assertIn(f"{interface}.accounts.balance_minor has invalid type", receipt["error"]["detail"])
                self.assertIn("exit_code=0", receipt["error"]["detail"])
                self.assertTrue(receipt["cleanup_verified"])
                self.assertFalse(list(root.glob("*.container")))
                self.assertFalse(list(root.glob("*.network")))

    def test_readback_exit_and_stderr_are_persisted_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            failing_probe = executable(
                root,
                "failing-probe",
                """
import sys
print(
    'playwright failure ACTUAL_PASSWORD=actual-password-sentinel '
    '{\"password\":\"quoted-json-sentinel\",\"access_token\":\"access-token-sentinel\"} '
    'Authorization: Basic basic-auth-sentinel',
    file=sys.stderr,
)
raise SystemExit(9)
""",
            )
            result, receipt = self.run_drill(root, fake_runtime(root), probe_path=failing_probe)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "readback_probe_failed")
            detail = receipt["error"]["detail"]
            self.assertIn("exit_code=9", detail)
            for redacted in (
                "ACTUAL_PASSWORD=<redacted>",
                '"password":"<redacted>"',
                '"access_token":"<redacted>"',
                "Authorization: Basic <redacted>",
            ):
                self.assertIn(redacted, detail)
            for sentinel in (
                "actual-password-sentinel",
                "quoted-json-sentinel",
                "access-token-sentinel",
                "basic-auth-sentinel",
            ):
                self.assertNotIn(sentinel, receipt["error"]["detail"])
                self.assertNotIn(sentinel, (root / "receipt.json").read_text(encoding="utf-8"))
            self.assertFalse(receipt["secret_values_recorded"])
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_unlabeled_diagnostic_is_hashed_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            secret_values = (
                "Current checking account 987654 AED OTP 731942 "
                "statement-id stmt-20260821-opaque"
            )
            unlabeled_probe = executable(
                root,
                "unlabeled-secret-probe",
                f"print({secret_values!r}, file=__import__('sys').stderr); raise SystemExit(17)",
            )
            result, receipt = self.run_drill(root, fake_runtime(root), probe_path=unlabeled_probe)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "readback_probe_failed")
            detail = receipt["error"]["detail"]
            self.assertIn("exit_code=17", detail)
            self.assertRegex(detail, r"stderr=(?:<redacted> )?stderr_sha256=[0-9a-f]{64}")
            for secret in secret_values.split():
                self.assertNotIn(secret, detail)
                self.assertNotIn(secret, (root / "receipt.json").read_text(encoding="utf-8"))
            self.assertFalse(receipt["secret_values_recorded"])
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_readback_wrong_path_failure_is_truthful_and_cleans_exact_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            wrong_path_probe = executable(
                root,
                "wrong-path-probe",
                """
import os
import sys
from pathlib import Path

expected = Path(os.environ["ACTUAL_RESTORE_DATA_DIR"]).parent / "readback-1.json"
actual = Path(os.environ["ACTUAL_RESTORE_READBACK_PATH"])
if actual != expected:
    print(f"wrong-path token=path-sentinel expected={expected} actual={actual}", file=sys.stderr)
    raise SystemExit(41)
raise SystemExit(42)
""",
            )
            result, receipt = self.run_drill(root, fake_runtime(root), probe_path=wrong_path_probe)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "readback_probe_failed")
            detail = receipt["error"]["detail"]
            self.assertIn("exit_code=41", detail)
            self.assertIn("wrong-path token=<redacted>", detail)
            self.assertNotIn("path-sentinel", detail)
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(receipt["cleanup"]["outer_temp_root_removed"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))
            self.assertFalse(list(root.glob("finance-actual-restore.*")))

    def test_rootful_docker_network_stdout_prefix_is_bound_to_requested_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    network_missing_stdout="[]",
                    network_missing_message='Error: No such object: "{name}"',
                ),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["status"], "passed")
            self.assertEqual(len(receipt["runs"]), 2)

    def test_rootful_docker_detailed_network_stdout_prefix_is_bound_to_requested_id(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    network_missing_stdout="[]",
                    network_missing_message=(
                        "Error: network {name}: unable to find network with name or ID "
                        "{name}: network not found"
                    ),
                ),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["status"], "passed")
            self.assertEqual(len(receipt["runs"]), 2)

    def test_podman_absent_container_and_network_messages_are_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    container_missing_message='Error: no container with name or ID "{name}" found: no such container',
                    network_missing_message="Error: network {name} not found",
                ),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["status"], "passed")

    def test_unknown_network_inspect_fails_closed_without_cleanup_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, network_inspect_failure=True))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_network_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_docker_absent_container_with_embedded_diagnostic_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(root, container_missing_message="Error: No such object: {name}\npermission denied"),
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_docker_absent_network_with_embedded_diagnostic_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    network_missing_stdout="[]",
                    network_missing_message="Error: No such object: {name}\npermission denied",
                ),
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_network_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_podman_absent_container_with_wrong_object_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(root, container_missing_message='Error: no container with name or ID "other-container" found: no such container'),
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_docker_network_absent_with_mismatched_repeated_object_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    network_missing_stdout="[]",
                    network_missing_message="Error: network {name}: unable to find network with name or ID other-network: network not found",
                ),
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_network_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_docker_container_case_only_wrong_object_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    container_missing_message='Error: NO SUCH OBJECT: "{name_upper}"',
                    container_missing_stdout="[]",
                ),
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_docker_network_both_case_only_wrong_object_ids_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    network_missing_stdout="[]",
                    network_missing_message="Error: NETWORK {name_upper}: unable to find network with name or ID {name_upper}: NETWORK NOT FOUND",
                ),
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_network_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_docker_network_mixed_second_case_only_wrong_object_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(
                root,
                fake_runtime(
                    root,
                    network_missing_stdout="[]",
                    network_missing_message="Error: network {name}: unable to find network with name or ID {name_upper}: network not found",
                ),
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "runtime_network_inspect_failed")
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_unknown_cleanup_inspect_retains_owned_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, cleanup_inspect_failure=True))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "cleanup_not_verified")
            self.assertFalse(receipt["cleanup_verified"])
            self.assertFalse(receipt["cleanup"]["outer_temp_root_removed"])
            retained_root = Path(receipt["cleanup"]["retained_paths"][0])
            self.assertEqual(retained_root, Path(receipt["cleanup"]["outer_temp_root"]))
            self.assertTrue(retained_root.is_dir())
            self.assertTrue(list(root.glob("*.container")))
            self.assertTrue(list(root.glob("*.network")))
            self.assertTrue(list(root.glob("finance-actual-restore.*/data-1")))

    def test_partial_start_failure_cleans_the_exact_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, start_failure=True))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "sidecar_start_failed")
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(receipt["cleanup"]["outer_temp_root_removed"])
            self.assertFalse(list(root.glob("finance-actual-restore.*")))

    def test_network_create_failure_cleans_the_exact_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, network_failure=True))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "disposable_network_create_failed")
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(receipt["cleanup"]["outer_temp_root_removed"])
            self.assertFalse(list(root.glob("finance-actual-restore.*")))

    def test_restart_failure_cleans_and_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root, restart_failure=True))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["error"]["code"], "sidecar_restart_failed")
            self.assertTrue(receipt["cleanup_verified"])

    def test_outer_temp_root_removal_failure_is_nonzero_and_reports_exact_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            breaking_probe = executable(
                root,
                "breaking-probe",
                """
import json
import os
import pathlib
import shutil

payload = {
    'accounts': [{'name': 'Current', 'balance_minor': 100, 'closed': False, 'offbudget': False}],
    'representative_transactions': [{'account_name': 'Current', 'amount_minor': -10, 'date': '2026-08-01', 'imported_id': 'statement:fixture:1', 'payee': 'Merchant'}],
}
if os.environ.get('ACTUAL_RESTORE_RUN_INDEX') == '2' and not (pathlib.Path(os.environ['ACTUAL_RESTORE_DATA_DIR']).parent.parent / 'break-once').exists():
    data_dir = pathlib.Path(os.environ['ACTUAL_RESTORE_DATA_DIR'])
    outer_root = data_dir.parent
    (outer_root.parent / 'break-once').write_text('1', encoding='ascii')
    shutil.rmtree(outer_root)
    outer_root.symlink_to(outer_root.parent / 'missing-target')
print(json.dumps({'api': payload, 'ui': payload}))
""",
            )
            result, receipt = self.run_drill(root, fake_runtime(root), probe_path=breaking_probe)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["error"]["code"], "outer_temp_cleanup_failed")
            self.assertFalse(receipt["cleanup_verified"])
            self.assertFalse(receipt["cleanup"]["outer_temp_root_removed"])
            retained_root = Path(receipt["cleanup"]["retained_paths"][0])
            self.assertEqual(retained_root, Path(receipt["cleanup"]["outer_temp_root"]))
            self.assertTrue(os.path.lexists(retained_root))
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))

    def test_sigterm_cleans_the_current_sidecar_and_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            runtime = fake_runtime(root)
            namespace = namespace_tool(root)
            expected = expected_fixture(root)
            probe_path = probe(root)
            slow_health = executable(root, "slow-health", "import time; time.sleep(10)")
            receipt = root / "signal.json"
            process = subprocess.Popen(
                [
                    str(SCRIPT),
                    "--backup-root", str(root),
                    "--receipt", str(receipt),
                    "--runtime", str(runtime),
                    "--namespace-tool", str(namespace),
                    "--expected-readback", str(expected),
                    "--readback-command", str(probe_path),
                    "--http-probe-command", str(slow_health),
                    "--health-attempts", "60",
                    "--temp-root", str(root),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(0.5)
            process.terminate()
            process.wait(timeout=10)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            if payload.get("error", {}).get("code") == "runtime_namespace_identity_failed":
                limitation = _namespace_probe_error()
                if limitation is not None:
                    required = os.environ.get("CI", "").lower() == "true" or os.environ.get(
                        "FINANCE_ACTUAL_RESTORE_REQUIRE_NAMESPACE", ""
                    ).lower() == "true"
                    if required:
                        self.fail(f"namespace identity probe unavailable in required runner: {limitation}")
                    self.skipTest(f"namespace identity probe unavailable in this runner: {limitation}")
            self.assertEqual(process.returncode, 143)
            self.assertEqual(payload["error"]["code"], "signal_interrupted")
            self.assertTrue(payload["cleanup_verified"])
            self.assertTrue(payload["cleanup"]["outer_temp_root_removed"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))
            self.assertFalse(list(root.glob("finance-actual-restore.*")))
            self.assertFalse(process_is_live(root / "namespace-child.pid"))

    def test_happy_path_proves_two_runs_restart_and_ui_api_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            result, receipt = self.run_drill(root, fake_runtime(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["status"], "passed")
            self.assertEqual(len(receipt["runs"]), 2)
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(receipt["cleanup"]["outer_temp_root_removed"])
            for run in receipt["runs"]:
                self.assertTrue(run["restart_verified"])
                self.assertTrue(run["repeat_state_match"])
                self.assertTrue(run["ui_api_parity"])
                self.assertTrue(run["cleanup_verified"])
                self.assertTrue(run["network_internal"])
                self.assertTrue(run["network_cleanup_verified"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))
            self.assertFalse(list(root.glob("finance-actual-restore.*")))
            run_args = (root / "run-args").read_text(encoding="utf-8")
            self.assertIn("--network finance-actual-restore-net-", run_args)
            self.assertNotIn(" -p ", f" {run_args} ")
            namespace_args = (root / "namespace-args").read_text(encoding="utf-8")
            self.assertIn("--target ", namespace_args)
            self.assertIn(" --net ", f" {namespace_args} ")
            errors = list(Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).iter_errors(receipt))
            self.assertEqual(errors, [])

    def test_loopback_health_probe_requires_the_internal_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            health = executable(
                root,
                "network-health",
                f"""
import os
import pathlib
import sys
args = (pathlib.Path({str(root)!r}) / 'run-args').read_text(encoding='utf-8')
tokens = args.split()
network = next((tokens[index + 1] for index, value in enumerate(tokens[:-1]) if value == '--network'), '')
if not network.startswith('finance-actual-restore-net-'):
    raise SystemExit(1)
if not (pathlib.Path({str(root)!r}) / (network + '.network')).exists():
    raise SystemExit(1)
if not os.environ.get('ACTUAL_RESTORE_URL', '').startswith('http://127.0.0.1:'):
    raise SystemExit(1)
""",
            )
            result, receipt = self.run_drill(root, fake_runtime(root), health_path=health)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt["status"], "passed")

    def test_receipt_schema_requires_two_runs_for_passed_proof(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        run = {
            "run_index": 1,
            "sidecar_id": "sidecar-1",
            "network_name": "network-1",
            "network_internal": True,
            "data_sha256": "a" * 64,
            "api_readback_sha256": "b" * 64,
            "ui_readback_sha256": "b" * 64,
            "account_count": 1,
            "representative_transaction_count": 1,
            "ui_api_parity": True,
            "restart_verified": True,
            "repeat_state_match": True,
            "cleanup_verified": True,
            "network_cleanup_verified": True,
            "status": "passed",
        }
        receipt = {
            "schema_version": 1,
            "status": "passed",
            "mode": "disposable",
            "redacted": True,
            "started_at": "2026-08-20T12:00:00Z",
            "completed_at": "2026-08-20T12:00:01Z",
            "backup": {"name": "20260820T120000Z", "archive_sha256": "c" * 64, "archive_bytes": 1, "verified": True},
            "runtime": {"engine": "podman", "image": "actual:test", "image_digest": "sha256:" + "d" * 64, "available": True, "verified": True},
            "source_provenance": {"commit": "e" * 40, "script_sha256": "f" * 64},
            "requested_runs": 2,
            "runs": [run, {**run, "run_index": 2, "sidecar_id": "sidecar-2"}],
            "cleanup": {"outer_temp_root": "/tmp/finance-actual-restore.fixture", "outer_temp_root_removed": True, "retained_paths": []},
            "cleanup_verified": True,
            "production_mutated": False,
            "retained_mutated": False,
            "secret_values_recorded": False,
            "error": None,
        }
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(receipt)), [])
        receipt["requested_runs"] = 1
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(receipt)))
        receipt["requested_runs"] = 2
        receipt["runs"] = [run]
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(receipt)))
        receipt["runs"] = [run, {**run, "run_index": 2, "sidecar_id": "sidecar-2"}]
        receipt["runs"][0]["network_cleanup_verified"] = False
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(receipt)))
        receipt["runs"][0]["network_cleanup_verified"] = True
        receipt["error"] = {"code": "unexpected", "stage": "test"}
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(receipt)))


if __name__ == "__main__":
    unittest.main()
