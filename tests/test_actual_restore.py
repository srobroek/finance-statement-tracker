from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy/actual-poc/restore-actual-disposable.py"
SCHEMA = ROOT / "config/actual-restore-receipt.schema.json"


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
) -> Path:
    return executable(
        root,
        "runtime",
        f"""
import pathlib
import sys
root = pathlib.Path({str(root)!r})
command = sys.argv[1] if len(sys.argv) > 1 else ''
if command == 'network':
    action = sys.argv[2] if len(sys.argv) > 2 else ''
    name = sys.argv[-1]
    marker = root / (name + '.network')
    if action == 'inspect':
        if {inspect_failure!r} or {network_inspect_failure!r} or ({cleanup_inspect_failure!r} and marker.exists()):
            print('runtime transport failure', file=sys.stderr)
            raise SystemExit(1)
        if {network_collision!r} and 'restore-net-1-' in name:
            raise SystemExit(0)
        if marker.exists():
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
        print(name)
        raise SystemExit(0)
    if action == 'rm':
        marker.unlink(missing_ok=True)
        raise SystemExit(0)
    raise SystemExit(1)
name = sys.argv[sys.argv.index('--name') + 1] if '--name' in sys.argv else (sys.argv[2] if len(sys.argv) > 2 else '')
if command == 'rm' and len(sys.argv) > 3 and sys.argv[2] == '-f':
    name = sys.argv[3]
marker = root / (name + '.container')
if command == 'version':
    raise SystemExit(0)
if command == 'image' and len(sys.argv) > 3 and sys.argv[2] == 'inspect':
    print('a' * 64)
    raise SystemExit(0)
if command == 'inspect':
    if {inspect_failure!r} or ({cleanup_inspect_failure!r} and marker.exists()):
        print('runtime transport failure', file=sys.stderr)
        raise SystemExit(1)
    if {collision!r} and 'restore-1-' in name:
        raise SystemExit(0)
    if marker.exists():
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
    print(name)
    raise SystemExit(0)
if command == 'restart':
    if {restart_failure!r}:
        print('restart failed', file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)
if command == 'rm':
    marker.unlink(missing_ok=True)
    raise SystemExit(0)
raise SystemExit(1)
""",
    )


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
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        receipt = root / "receipt.json"
        result = subprocess.run(
            [
                str(SCRIPT),
                "--backup-root", str(root),
                "--receipt", str(receipt),
                "--runtime", str(runtime),
                "--expected-readback", str(expected or expected_fixture(root)),
                "--readback-command", str(probe_path or probe(root)),
                "--http-probe-command", str(health_path or executable(root, "health", "raise SystemExit(0)")),
                "--temp-root", str(root),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, json.loads(receipt.read_text(encoding="utf-8"))

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
            self.assertEqual(process.returncode, 143)
            self.assertEqual(payload["error"]["code"], "signal_interrupted")
            self.assertTrue(payload["cleanup_verified"])
            self.assertTrue(payload["cleanup"]["outer_temp_root_removed"])
            self.assertFalse(list(root.glob("*.container")))
            self.assertFalse(list(root.glob("*.network")))
            self.assertFalse(list(root.glob("finance-actual-restore.*")))

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
            self.assertNotIn("--network none", run_args)
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
