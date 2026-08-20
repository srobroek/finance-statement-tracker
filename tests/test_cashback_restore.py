from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from finance_tracker.cashback_events import CashbackEventStore
from finance_tracker.web_push import WebPushStore

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy/actual-poc/restore-cashback-disposable.sh"
SCHEMA = ROOT / "config/cashback-restore-receipt.schema.json"


def sqlite_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (1, 'cashback')")


def backup_fixture(root: Path, *, real_cashback: bool = False) -> Path:
    source = root / "source"
    sqlite_database(source / "actual-data/server-files/account.sqlite")
    sqlite_database(source / "actual-data/user-files/budget.sqlite")
    cashback_database = source / "cashback-data/cashback-events.sqlite3"
    if real_cashback:
        CashbackEventStore(cashback_database)
        WebPushStore(cashback_database)
    else:
        sqlite_database(cashback_database)
    configuration = source / "configuration"
    configuration.mkdir(parents=True)
    (configuration / "actual-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (configuration / "cashback-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (configuration / "profile.json").write_text('{"schema_version": 1}\n', encoding="utf-8")

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


def real_backup_fixture(root: Path) -> Path:
    return backup_fixture(root, real_cashback=True)


def fake_runtime(root: Path, *, inspect_fault: bool) -> Path:
    runtime = root / ("fake-runtime-fault" if inspect_fault else "fake-runtime")
    mode = "fault" if inspect_fault else "success"
    runtime.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        f"root = pathlib.Path({str(root)!r})\n"
        f"mode = {mode!r}\n"
        "command = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "marker = root / 'retained-marker'\n"
        "if command == 'version':\n"
        "    raise SystemExit(0)\n"
        "if command == 'image' and len(sys.argv) > 2 and sys.argv[2] == 'inspect':\n"
        "    print('sha256:' + 'a' * 64)\n"
        "    raise SystemExit(0)\n"
        "if command == 'run':\n"
        "    marker.write_text('container-retained', encoding='utf-8')\n"
        "    raise SystemExit(0)\n"
        "if command in {'exec', 'restart'}:\n"
        "    raise SystemExit(0)\n"
        "if command == 'inspect':\n"
        "    if mode == 'fault':\n"
        "        print('runtime transport failure', file=sys.stderr)\n"
        "        raise SystemExit(1)\n"
        "    if marker.exists():\n"
        "        raise SystemExit(0)\n"
        "    print('Error: no such container', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "if command == 'rm':\n"
        "    (root / 'rm-called').write_text('called', encoding='utf-8')\n"
        "    marker.unlink(missing_ok=True)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    runtime.chmod(0o700)
    return runtime


def real_startup_runtime(root: Path) -> Path:
    """Create a disposable runtime shim that starts the actual Cashback server."""
    runtime = root / "real-startup-runtime"
    runtime.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import signal\n"
        "import socket\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        f"root = Path({str(root)!r})\n"
        f"source_root = Path({str(ROOT)!r})\n"
        "state_path = root / 'runtime-state.json'\n"
        "digest = 'sha256:' + 'b' * 64\n"
        "def read_state():\n"
        "    if not state_path.exists():\n"
        "        return {}\n"
        "    return json.loads(state_path.read_text(encoding='utf-8'))\n"
        "def write_state(state):\n"
        "    state_path.write_text(json.dumps(state, sort_keys=True), encoding='utf-8')\n"
        "def alive(pid):\n"
        "    try:\n"
        "        os.kill(int(pid), 0)\n"
        "    except (OSError, ValueError):\n"
        "        return False\n"
        "    return True\n"
        "def stop(item):\n"
        "    pid = item.get('pid')\n"
        "    if not pid or not alive(pid):\n"
        "        return\n"
        "    os.kill(int(pid), signal.SIGTERM)\n"
        "    deadline = time.monotonic() + 5\n"
        "    while alive(pid) and time.monotonic() < deadline:\n"
        "        time.sleep(0.05)\n"
        "    if alive(pid):\n"
        "        os.kill(int(pid), signal.SIGKILL)\n"
        "def start(name, data_dir, token):\n"
        "    with socket.socket() as sock:\n"
        "        sock.bind(('127.0.0.1', 0))\n"
        "        port = sock.getsockname()[1]\n"
        "    env = os.environ.copy()\n"
        "    env.update({\n"
        "        'CASHBACK_HOST': '127.0.0.1',\n"
        "        'CASHBACK_PUBLIC_URL': 'http://127.0.0.1:' + str(port),\n"
        "        'CASHBACK_PORT': str(port),\n"
        "        'CASHBACK_REFRESH_SECONDS': '0',\n"
        "        'CASHBACK_DB_PATH': str(Path(data_dir) / 'cashback-events.sqlite3'),\n"
        "        'CASHBACK_DASHBOARD_PATH': str(Path(data_dir) / 'cashback-dashboard.json'),\n"
        "        'CASHBACK_INGEST_TOKEN': token,\n"
        "        'PYTHONPATH': str(source_root) + os.pathsep + env.get('PYTHONPATH', ''),\n"
        "    })\n"
        "    log = (root / (name + '.log')).open('ab')\n"
        "    process = subprocess.Popen([sys.executable, str(source_root / 'apps/cashback-control/server.py')], cwd=source_root, env=env, stdout=log, stderr=subprocess.STDOUT)\n"
        "    log.close()\n"
        "    return {'pid': process.pid, 'data_dir': str(data_dir), 'token': token, 'port': port}\n"
        "args = sys.argv[1:]\n"
        "command = args[0] if args else ''\n"
        "if command == 'version':\n"
        "    raise SystemExit(0)\n"
        "if command == 'image' and len(args) > 2 and args[1] == 'inspect':\n"
        "    print(digest)\n"
        "    raise SystemExit(0)\n"
        "state = read_state()\n"
        "if command == 'run':\n"
        "    name = args[args.index('--name') + 1]\n"
        "    data_dir = Path(args[args.index('-v') + 1].split(':', 1)[0])\n"
        "    token = next(value.split('=', 1)[1] for value in args if value.startswith('CASHBACK_INGEST_TOKEN='))\n"
        "    state[name] = start(name, data_dir, token)\n"
        "    write_state(state)\n"
        "    raise SystemExit(0)\n"
        "if command == 'inspect':\n"
        "    name = args[1]\n"
        "    if name in state and alive(state[name]['pid']):\n"
        "        raise SystemExit(0)\n"
        "    print('Error: no such container', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "if command == 'exec':\n"
        "    name = args[1]\n"
        "    item = state[name]\n"
        "    env = os.environ.copy()\n"
        "    env['CASHBACK_INGEST_TOKEN'] = item['token']\n"
        "    env['PYTHONPATH'] = str(source_root) + os.pathsep + env.get('PYTHONPATH', '')\n"
        "    probe = \"import probe_health; probe_health.HEALTH_URL = 'http://127.0.0.1:\" + str(item['port']) + \"/api/health'; raise SystemExit(0 if probe_health.probe() else 1)\"\n"
        "    raise SystemExit(subprocess.run([sys.executable, '-c', probe], cwd=source_root / 'apps/cashback-control', env=env).returncode)\n"
        "if command == 'restart':\n"
        "    name = args[1]\n"
        "    item = state[name]\n"
        "    stop(item)\n"
        "    state[name] = start(name, item['data_dir'], item['token'])\n"
        "    write_state(state)\n"
        "    raise SystemExit(0)\n"
        "if command == 'rm':\n"
        "    name = args[-1]\n"
        "    item = state.pop(name, None)\n"
        "    if item is not None:\n"
        "        stop(item)\n"
        "    write_state(state)\n"
        "    (root / 'rm-called').write_text('called', encoding='utf-8')\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    runtime.chmod(0o700)
    return runtime


class CashbackRestoreContractTests(unittest.TestCase):
    def test_script_uses_authoritative_contract_and_exact_disposable_cleanup(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        for required in (
            'python3 "${VERIFY_SCRIPT}"',
            "sha256sum -c SHA256SUMS",
            "--pull=never",
            "--network none",
            "apps/cashback-control/probe_health.py",
            '"${RUNTIME}" restart',
            '"${RUNTIME}" rm -f',
            '"${RUNTIME}" inspect',
            "CASHBACK_INGEST_TOKEN",
            'table in {"push_subscriptions", "push_deliveries"}',
            'str(row[0]) != "routing-map"',
            "image inspect --format '{{.Id}}'",
            '"secret_values_recorded": False',
        ):
            self.assertIn(required, source)
        self.assertNotIn("docker compose", source)
        self.assertNotIn("finance-runtime/.env", source)

    def test_blocked_runtime_writes_truthful_redacted_mode_0600_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            receipt = root / "artifacts" / "cb5-receipt.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(root / "missing-runtime"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            errors = sorted(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(payload), key=str)
            self.assertEqual(errors, [])
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["error"]["code"], "container_runtime_unavailable")
            self.assertTrue(payload["backup"]["verified"])
            self.assertFalse(payload["runtime"]["verified"])
            self.assertFalse(payload["secret_values_recorded"])
            self.assertFalse(payload["production_mutated"])
            self.assertFalse(payload["retained_mutated"])
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("CASHBACK_INGEST_TOKEN", receipt.read_text(encoding="utf-8"))

    def test_checksum_failure_is_not_reported_as_a_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = backup_fixture(root)
            (backup / "finance-data.tar.gz").write_bytes(b"changed")
            receipt = root / "failure.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(root / "missing-runtime"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "backup_verification_failed")
            self.assertFalse(payload["backup"]["verified"])
            self.assertFalse(payload["runtime"]["verified"])

    def test_runtime_inspect_fault_fails_closed_and_never_claims_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            runtime = fake_runtime(root, inspect_fault=True)
            receipt = root / "fault.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(runtime),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertTrue((root / "retained-marker").is_file())
            self.assertFalse((root / "rm-called").exists())
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "cleanup_not_verified")
            self.assertFalse(payload["cleanup_verified"])
            self.assertFalse(payload["runs"][0]["cleanup_verified"])

    def test_receipt_write_failure_is_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            runtime = fake_runtime(root, inspect_fault=False)
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    "/dev/null/cb5-receipt.json",
                    "--runtime",
                    str(runtime),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("restore_receipt_write_failed", result.stderr)
            self.assertTrue((root / "rm-called").is_file())
            self.assertFalse((root / "retained-marker").exists())

    def test_happy_path_runs_twice_and_cleans_each_exact_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_fixture(root)
            runtime = fake_runtime(root, inspect_fault=False)
            receipt = root / "passed.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(runtime),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(payload)), [])
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["requested_runs"], 2)
            self.assertEqual(len(payload["runs"]), 2)
            self.assertEqual({run["status"] for run in payload["runs"]}, {"passed"})
            self.assertEqual(len({run["sidecar_id"] for run in payload["runs"]}), 2)
            for run in payload["runs"]:
                self.assertEqual(run["pre_state_sha256"], run["post_state_sha256"])
                self.assertTrue(run["exact_state_match"])
                self.assertTrue(run["health_authorized"])
                self.assertTrue(run["restart_verified"])
                self.assertTrue(run["cleanup_verified"])
            self.assertTrue((root / "rm-called").is_file())
            self.assertFalse((root / "retained-marker").exists())
            self.assertTrue(payload["cleanup_verified"])

    def test_real_startup_runs_twice_and_preserves_logical_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_backup_fixture(root)
            runtime = real_startup_runtime(root)
            receipt = root / "real-startup.json"
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--backup-root",
                    str(root),
                    "--receipt",
                    str(receipt),
                    "--runtime",
                    str(runtime),
                    "--image",
                    "cashback:real-startup-test",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["runtime"]["image_digest"], "sha256:" + "b" * 64)
            self.assertEqual(
                payload["source_provenance"]["script_sha256"],
                hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            )
            self.assertEqual(len(payload["runs"]), 2)
            self.assertEqual({run["status"] for run in payload["runs"]}, {"passed"})
            self.assertEqual(len({run["sidecar_id"] for run in payload["runs"]}), 2)
            for run in payload["runs"]:
                self.assertEqual(run["pre_state_sha256"], run["post_state_sha256"])
                self.assertTrue(run["exact_state_match"])
                self.assertTrue(run["health_authorized"])
                self.assertTrue(run["restart_verified"])
                self.assertTrue(run["cleanup_verified"])
            logs = "".join(path.read_text(encoding="utf-8") for path in root.glob("finance-*.log"))
            self.assertGreaterEqual(logs.count('"event": "service_started"'), 4)
            self.assertTrue((root / "rm-called").is_file())
            self.assertEqual(json.loads((root / "runtime-state.json").read_text()), {})
            self.assertEqual(list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(payload)), [])

    def test_repeat_requires_two_and_passed_schema_requires_all_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / "repeat-one.json"
            result = subprocess.run(
                [str(SCRIPT), "--repeat", "1", "--receipt", str(receipt)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 64)
            self.assertFalse(receipt.exists())

        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        run = {
            "run_index": 1,
            "sidecar_id": "sidecar-1",
            "pre_state_sha256": "a" * 64,
            "post_state_sha256": "a" * 64,
            "exact_state_match": True,
            "health_authorized": True,
            "restart_verified": True,
            "cleanup_verified": True,
            "status": "passed",
        }
        receipt = {
            "schema_version": 1,
            "status": "passed",
            "mode": "disposable",
            "redacted": True,
            "started_at": "2026-08-20T12:00:00Z",
            "completed_at": "2026-08-20T12:00:01Z",
            "backup": {
                "name": "20260820T120000Z",
                "archive_sha256": "b" * 64,
                "archive_bytes": 1,
                "verified": True,
            },
            "runtime": {
                "engine": "podman",
                "image": "cashback:test",
                "image_digest": "sha256:" + "a" * 64,
                "available": True,
                "verified": True,
            },
            "source_provenance": {
                "commit": "c" * 40,
                "script_sha256": "d" * 64,
            },
            "requested_runs": 2,
            "runs": [run, {**run, "run_index": 2, "sidecar_id": "sidecar-2"}],
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
        receipt["runs"][0]["restart_verified"] = False
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(receipt)))


if __name__ == "__main__":
    unittest.main()
