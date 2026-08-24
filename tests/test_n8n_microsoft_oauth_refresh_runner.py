from __future__ import annotations

import hashlib
import json
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "integrations" / "n8n" / "setup-workflows"
RUNNER = SETUP / "runner"
SOURCE = SETUP / "23-microsoft-oauth-refresh-proof.json"
SOURCE_COMMIT = "b3bce6e197c6603d3e8708156bed987f26ac8513"
SOURCE_SHA = "879d637a5ad71e5a35ec8a90001d33c00067e05115a3bcdd28a80a9191c7224e"
DATA_TABLE_OUTPUT_FIXTURE = ROOT / "tests" / "fixtures" / "n8n-2.36.2-data-table-digest-output.json"


def synthetic_docker_log_rows(log_path: Path) -> list[list[str]]:
    rows = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if not isinstance(row, list) or not all(isinstance(value, str) for value in row):
            raise AssertionError(f"synthetic Docker log row is not argv JSON: {row!r}")
        rows.append(row)
    if not rows:
        raise AssertionError("synthetic Docker log is empty")
    return rows


def assert_synthetic_docker_allowlist(log_path: Path) -> list[list[str]]:
    rows = synthetic_docker_log_rows(log_path)
    services = {"n8n", "task-runners"}
    inspect_formats = {
        '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}',
        "{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
        "{{.Id}}|{{.State.Running}}",
        "{{.State.Running}}",
        "{{range .Config.Env}}{{println .}}{{end}}",
    }
    for row in rows:
        operation = row[0] if row else ""
        if operation == "run":
            raise AssertionError("direct docker run is forbidden")
        if operation == "compose":
            forbidden = next((value for value in row if value in {"run", "create", "up"}), None)
            if forbidden is not None:
                raise AssertionError(f"docker compose {forbidden} is forbidden")
            if (
                len(row) != 10
                or row[1] != "--project-name"
                or row[3] != "--file"
                or row[5] != "--env-file"
                or row[7:9] != ["ps", "-q"]
                or row[9] not in services
            ):
                raise AssertionError(f"unexpected docker compose command: {row!r}")
            continue
        if operation == "inspect":
            if len(row) != 4 or row[1] != "-f" or row[2] not in inspect_formats:
                raise AssertionError(f"unexpected docker inspect command: {row!r}")
            if len(row[3]) != 64 or any(value not in "0123456789abcdef" for value in row[3]):
                raise AssertionError(f"docker inspect target is not a container ID: {row!r}")
            continue
        if operation == "exec":
            container_index = next(
                (index for index, value in enumerate(row[1:], start=1)
                 if len(value) == 64 and all(char in "0123456789abcdef" for char in value)),
                None,
            )
            if container_index is None:
                raise AssertionError(f"docker exec has no container ID: {row!r}")
            options = row[1:container_index]
            environment = set()
            index = 0
            while index < len(options):
                if options[index] == "-i":
                    index += 1
                elif options[index] == "-e" and index + 1 < len(options) and "=" in options[index + 1]:
                    variable = options[index + 1]
                    if not (
                        variable in {
                            "FINANCE_WF23_TRANSPORT_PROBE_ACK=READ_ONLY_DIRECT_EXECUTE_INSTANCE",
                            "FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY",
                            "FINANCE_MICROSOFT_OAUTH_METADATA_ACK=READ_ONLY_REDACTED",
                        }
                        or variable.startswith("N8N_FINANCE_PROJECT_ID=")
                    ):
                        raise AssertionError(f"unexpected docker exec environment: {row!r}")
                    environment.add(variable)
                    index += 2
                else:
                    raise AssertionError(f"unexpected docker exec option: {row!r}")
            command = row[container_index + 1:]
            if command[:1] == ["pg_isready"]:
                if len(command) != 5 or command[1] != "-U" or command[3] != "-d":
                    raise AssertionError(f"unexpected pg_isready command: {row!r}")
            elif command[:1] == ["psql"]:
                if len(command) != 10 or command[1:7] != ["-v", "ON_ERROR_STOP=1", "-At", "-U", "n8n", "-d"]:
                    raise AssertionError(f"unexpected psql command: {row!r}")
                if command[7] != "n8n" or command[8] != "-c" or not command[9].lstrip().lower().startswith("select "):
                    raise AssertionError(f"unexpected psql query: {row!r}")
            elif command[:2] == ["node", "-e"]:
                if len(command) != 3 or "fetch(" not in command[2] or "healthz" not in command[2]:
                    raise AssertionError(f"unexpected node control probe: {row!r}")
            elif command == ["node", "-"]:
                if environment != {"FINANCE_WF23_TRANSPORT_PROBE_ACK=READ_ONLY_DIRECT_EXECUTE_INSTANCE"}:
                    raise AssertionError(f"unexpected direct transport probe: {row!r}")
            elif command == ["node", "-", "list:workflow"]:
                acknowledgements = {
                    "FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY",
                    "FINANCE_MICROSOFT_OAUTH_METADATA_ACK=READ_ONLY_REDACTED",
                }
                if len(environment) != 2 or not environment & acknowledgements or not any(
                    value.startswith("N8N_FINANCE_PROJECT_ID=") for value in environment
                ):
                    raise AssertionError(f"unexpected n8n readback command: {row!r}")
            else:
                raise AssertionError(f"unexpected docker exec command: {row!r}")
            continue
        raise AssertionError(f"unexpected docker operation: {row!r}")
    return rows


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metadata_snapshot(observed: str, expiry: str, expired: bool, updated: str = "2026-08-20T10:00:00Z") -> dict:
    return {
        "schema_version": 1,
        "status": "VERIFIED",
        "scope": "READ_ONLY_MICROSOFT_OAUTH_METADATA",
        "observed_at_utc": observed,
        "credentials": {
            label: {
                "credential_type": credential_type,
                "credential_updated_at_utc": updated,
                "access_token_present": True,
                "refresh_token_present": True,
                "expiration_observed": True,
                "expires_at_utc": expiry,
                "expired_at_readback": expired,
            }
            for label, credential_type in {
                "outlook": "microsoftOutlookOAuth2Api",
                "onedrive": "microsoftOneDriveOAuth2Api",
            }.items()
        },
    }


def terminal_result() -> dict:
    return {
        "schema_version": "microsoft-oauth-refresh-proof-receipt-v1",
        "status": "VERIFIED",
        "execution_id": "123",
        "outlook_read_succeeded": True,
        "outlook_items_observed": 1,
        "outlook_max_messages": 1,
        "outlook_server_filter_applied": True,
        "outlook_window_start": "2026-08-13T12:00:00.000Z",
        "outlook_window_end": "2026-08-20T12:00:00.000Z",
        "onedrive_root_read_succeeded": True,
        "onedrive_root_items_observed": 4,
        "provider_writes": False,
        "message_fields_recorded": False,
        "file_fields_recorded": False,
        "credential_values_recorded": False,
        "token_values_recorded": False,
        "production_workflows_activated": False,
        "actual_writes": False,
        "cashback_writes": False,
        "verified_at": "2026-08-20T12:00:01.000Z",
    }


def irun(result: dict) -> dict:
    terminal = "Emit Redacted OAuth Proof Receipt"
    return {
        "finished": True,
        "status": "success",
        "data": {
            "resultData": {
                "lastNodeExecuted": terminal,
                "runData": {
                    terminal: [{
                        "executionStatus": "success",
                        "data": {"main": [[{"json": result}]]},
                    }],
                },
            },
        },
    }


class MicrosoftOAuthRefreshRunnerTests(unittest.TestCase):
    def make_synthetic_preflight_runtime(self):
        temporary = tempfile.TemporaryDirectory(prefix="wf23-preflight-")
        root = Path(temporary.name)
        finance_repo = root / "finance"
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(finance_repo)], check=True)
        subprocess.run(["git", "fetch", "--quiet", str(ROOT), SOURCE_COMMIT], cwd=finance_repo, check=True)
        subprocess.run(["git", "checkout", "--quiet", SOURCE_COMMIT], cwd=finance_repo, check=True)
        shutil.copytree(
            RUNNER,
            finance_repo / "integrations" / "n8n" / "setup-workflows" / "runner",
            dirs_exist_ok=True,
        )
        subprocess.run(
            ["git", "add", "integrations/n8n/setup-workflows/runner"],
            cwd=finance_repo,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "synthetic deployed runner"],
            cwd=finance_repo,
            check=True,
        )
        stack = root / "stack"
        stack.mkdir()
        compose_file = stack / "compose.yaml"
        compose_file.write_text("services: {}\n", encoding="utf-8")
        subprocess.run(["git", "init", "--quiet"], cwd=stack, check=True)
        subprocess.run(["git", "config", "user.email", "synthetic@example.test"], cwd=stack, check=True)
        subprocess.run(["git", "config", "user.name", "Synthetic Preflight"], cwd=stack, check=True)
        subprocess.run(["git", "add", "compose.yaml"], cwd=stack, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "synthetic preflight"], cwd=stack, check=True)
        stack_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=stack, text=True).strip()

        env_file = root / "runtime.env"
        env_file.write_text("DB_POSTGRESDB_USER=n8n\nDB_POSTGRESDB_DATABASE=n8n\n", encoding="utf-8")
        env_file.chmod(0o600)
        receipt = root / "prestate.json"
        receipt.write_text(json.dumps({
            "schema_version": 1,
            "purpose": "N8N_RECOVERY_PRESTATE_RECEIPT_V1",
            "postgres": {"container_id": "3" * 64},
        }) + "\n", encoding="utf-8")
        receipt.chmod(0o600)
        receipt_dir = root / "receipts"
        receipt_dir.mkdir(mode=0o700)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(r'''#!/usr/bin/env python3
import json
import os
import sys

PROJECT = "synthetic-project"
N8N = "1" * 64
TASK_RUNNERS = "2" * 64
POSTGRES = "3" * 64
IDS = {N8N, TASK_RUNNERS, POSTGRES}
SERVICES = {N8N: "n8n", TASK_RUNNERS: "task-runners"}
scenario = os.environ.get("FAKE_DOCKER_SCENARIO", "ok")
log_path = os.environ["FAKE_DOCKER_LOG"]
args = sys.argv[1:]
with open(log_path, "a", encoding="utf-8") as log:
    print(json.dumps(args), file=log)

if args[:1] == ["compose"]:
    service = args[-1]
    if service == "n8n":
        print(N8N)
    elif service == "task-runners":
        print(TASK_RUNNERS)
    else:
        sys.exit(1)
    sys.exit(0)

if args[:1] == ["inspect"]:
    fmt = args[2]
    container = args[-1]
    if "Config.Labels" in fmt:
        if container not in SERVICES:
            sys.exit(1)
        print(f"{PROJECT}|{SERVICES[container]}")
    elif "Config.Env" in fmt:
        print("DB_POSTGRESDB_USER=n8n")
        print("DB_POSTGRESDB_DATABASE=n8n")
        print("N8N_RUNNERS_MODE=external")
        print("N8N_RUNNERS_BROKER_LISTEN_ADDRESS=0.0.0.0")
        print("N8N_RUNNERS_AUTH_TOKEN=synthetic-token")
    elif ".State.Health" in fmt:
        print("true|healthy")
    elif ".Id" in fmt and container == POSTGRES:
        print(f"{'4' * 64 if scenario == 'postgres-identity-fail' else POSTGRES}|true")
    elif ".State.Running" in fmt:
        print("true")
    else:
        sys.exit(1)
    sys.exit(0)

if args[:1] != ["exec"]:
    sys.exit(1)
try:
    container_index = next(index for index, value in enumerate(args) if value in IDS)
except StopIteration:
    sys.exit(1)
container = args[container_index]
exec_env = {}
index = 1
while index < container_index:
    if args[index] == "-e":
        key, value = args[index + 1].split("=", 1)
        exec_env[key] = value
        index += 2
    else:
        index += 1
command = args[container_index + 1:]
if "pg_isready" in command:
    sys.exit(1 if scenario == "postgres-readiness-fail" else 0)
if "psql" in command:
    query = command[command.index("-c") + 1]
    if "current_database()" in query:
        print("wrong|wrong" if scenario == "postgres-identity-query-fail" else "n8n|n8n")
    elif "credentials_entity" in query:
        print("outlook-id" if "microsoftOutlookOAuth2Api" in query else "onedrive-id")
    elif "count(*)||" in query:
        print("21|0|0")
    elif "workflows_tags" in query:
        print("63")
    elif "global_folder" in query:
        print("1")
    elif "workflow_entity where id in" in query or "execution_entity" in query or "workflow_history" in query:
        print("0")
    elif "from (select" in query:
        print("synthetic-baseline")
    else:
        print("21")
    sys.exit(0)
if "node" not in command:
    sys.exit(0)
if "-e" in command:
    if container == N8N and scenario == "broker-fail":
        sys.exit(1)
    if container == TASK_RUNNERS and scenario == "task-runner-fail":
        sys.exit(1)
    sys.exit(0)
ack = exec_env.get("FINANCE_WF23_TRANSPORT_PROBE_ACK")
if ack == "READ_ONLY_DIRECT_EXECUTE_INSTANCE":
    print('WF23 direct transport probe verified:{"schema_version":1,"status":"VERIFIED","scope":"DIRECT_EXECUTE_INSTANCE_TRANSPORT","execute_instance_resolved":true,"instance_log_override_invoked":true,"workflow_loaded":false,"workflow_executed":false,"provider_calls":false,"database_initialized":false,"raw_irun_persisted":false,"provider_response_logged":false,"secret_values_recorded":false}')
elif exec_env.get("FINANCE_DATA_TABLE_DIGEST_ACK") == "READ_ONLY_IN_MEMORY":
    print("finance data table digest verified:" + json.dumps({
        "schema_version": 1, "status": "VERIFIED",
        "scope": "READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST",
        "finance_tables": 4, "total_rows": 0, "digest_sha256": "a" * 64,
        "writes_performed": False, "provider_calls": False,
        "row_values_recorded": False, "secret_values_recorded": False,
    }, separators=(",", ":")))
elif exec_env.get("FINANCE_MICROSOFT_OAUTH_METADATA_ACK") == "READ_ONLY_REDACTED":
    credential = lambda kind: {
        "credential_type": kind, "credential_updated_at_utc": "2026-08-20T00:00:00Z",
        "access_token_present": True, "refresh_token_present": True,
        "expiration_observed": True, "expires_at_utc": "2020-01-01T00:00:00Z",
        "expired_at_readback": True,
    }
    print("microsoft oauth metadata readback verified:" + json.dumps({
        "schema_version": 1, "status": "VERIFIED",
        "scope": "READ_ONLY_MICROSOFT_OAUTH_METADATA",
        "observed_at_utc": "2026-08-24T00:00:00Z",
        "credentials": {
            "outlook": credential("microsoftOutlookOAuth2Api"),
            "onedrive": credential("microsoftOneDriveOAuth2Api"),
        },
        "provider_calls": False, "database_writes": False,
        "credential_ids_recorded": False, "secret_values_recorded": False,
        "token_fingerprints_recorded": False,
    }, separators=(",", ":")))
sys.exit(0)
''', encoding="utf-8")
        docker.chmod(0o755)
        fake_id = bin_dir / "id"
        fake_id.write_text("#!/bin/sh\nif [ \"$1\" = \"-u\" ]; then printf '1000\\n'; else exec /usr/bin/id \"$@\"; fi\n", encoding="utf-8")
        fake_id.chmod(0o755)

        log_path = root / "docker.log"
        finance_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=finance_repo, text=True).strip()
        environment = os.environ.copy()
        environment.update({
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "FAKE_DOCKER_LOG": str(log_path),
            "FAKE_DOCKER_SCENARIO": "ok",
            "FINANCE_MICROSOFT_OAUTH_PROOF_ACK": "RUN_TRANSIENT_WF23_ONLY",
            "FINANCE_MICROSOFT_OAUTH_PROOF_PREFLIGHT": "true",
            "FINANCE_N8N_COMPOSE_PROJECT": "synthetic-project",
            "FINANCE_N8N_STACK_DIR": str(stack),
            "FINANCE_N8N_COMPOSE_FILE": str(compose_file),
            "FINANCE_N8N_DEPLOYMENT_ENV_FILE": str(env_file),
            "FINANCE_N8N_RECEIPT_DIR": str(receipt_dir),
            "FINANCE_N8N_RECOVERY_RECEIPT": str(receipt),
            "FINANCE_REPOSITORY_DIR": str(finance_repo),
            "FINANCE_REPOSITORY_COMMIT": finance_commit,
            "ORCHESTRATOR_REPOSITORY_COMMIT": stack_commit,
            "N8N_FINANCE_PROJECT_ID": "synthetic-project-id",
        })
        environment.pop("N8N_POSTGRES_USER", None)
        environment.pop("N8N_POSTGRES_DATABASE", None)
        return temporary, environment, log_path, receipt

    def run_synthetic_preflight(self, environment):
        return subprocess.run(
            ["bash", str(RUNNER / "run-transient-microsoft-oauth-refresh-proof.sh")],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
        )

    def test_command_level_synthetic_preflight_uses_recovered_postgres_and_fails_closed(self) -> None:
        temporary, environment, log_path, receipt = self.make_synthetic_preflight_runtime()
        try:
            completed = self.run_synthetic_preflight(environment)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            assert_synthetic_docker_allowlist(log_path)
            calls = log_path.read_text(encoding="utf-8")
            call_rows = [json.loads(line) for line in calls.splitlines()]
            self.assertTrue(any(
                row[0] == "inspect" and ".Id" in row[2] and row[-1] == "3" * 64
                for row in call_rows
            ))
            self.assertIn('"pg_isready", "-U", "n8n", "-d", "n8n"', calls)
            self.assertIn("current_database()", calls)
            self.assertIn("N8N_RUNNERS_MODE !== \\\"external\\\"", calls)
            self.assertIn("N8N_RUNNERS_BROKER_LISTEN_ADDRESS !== \\\"0.0.0.0\\\"", calls)
            self.assertIn("http://127.0.0.1:5679/healthz", calls)
            self.assertIn("http://127.0.0.1:5680/healthz", calls)
            self.assertNotIn("import:workflow", calls)
            self.assertNotIn("listen(", calls)

            for scenario, message in (
                ("postgres-identity-fail", "Recovered Postgres container identity or state mismatch"),
                ("postgres-readiness-fail", "Recovered Postgres readiness failed"),
                ("postgres-identity-query-fail", "Recovered Postgres database identity mismatch"),
                ("broker-fail", "Deployed n8n/task-runner control path unavailable"),
                ("task-runner-fail", "Deployed n8n/task-runner control path unavailable"),
            ):
                environment["FAKE_DOCKER_SCENARIO"] = scenario
                log_path.write_text("", encoding="utf-8")
                failed = self.run_synthetic_preflight(environment)
                self.assertNotEqual(failed.returncode, 0, scenario)
                self.assertIn(message, failed.stderr, scenario)
                assert_synthetic_docker_allowlist(log_path)

            receipt.write_text(json.dumps({
                "schema_version": 1,
                "purpose": "N8N_RECOVERY_PRESTATE_RECEIPT_V1",
                "postgres": {"container_id": "not-a-container"},
            }) + "\n", encoding="utf-8")
            receipt.chmod(0o600)
            environment["FAKE_DOCKER_SCENARIO"] = "ok"
            failed_receipt = self.run_synthetic_preflight(environment)
            self.assertNotEqual(failed_receipt.returncode, 0)
            self.assertIn("Deployed Postgres recovery identity unavailable", failed_receipt.stderr)
            assert_synthetic_docker_allowlist(log_path)
        finally:
            temporary.cleanup()

    def test_synthetic_docker_allowlist_rejects_mutating_commands(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf23-docker-log-") as temporary:
            log_path = Path(temporary) / "docker.log"
            forbidden_rows = [
                (["run", "--rm", "n8n"], "direct docker run is forbidden"),
                (["compose", "--project-name", "synthetic-project", "--file", "compose.yaml", "--env-file", "runtime.env", "run", "n8n"], "docker compose run is forbidden"),
                (["compose", "--project-name", "synthetic-project", "--file", "compose.yaml", "--env-file", "runtime.env", "create", "n8n"], "docker compose create is forbidden"),
                (["compose", "--project-name", "synthetic-project", "--file", "compose.yaml", "--env-file", "runtime.env", "up", "n8n"], "docker compose up is forbidden"),
            ]
            for row, message in forbidden_rows:
                with self.subTest(row=row), self.assertRaisesRegex(AssertionError, message):
                    log_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
                    assert_synthetic_docker_allowlist(log_path)

    def test_runtime_binder_is_exact_and_never_records_identifiers(self) -> None:
        self.assertEqual(
            subprocess.check_output(["git", "show", f"{SOURCE_COMMIT}:integrations/n8n/setup-workflows/23-microsoft-oauth-refresh-proof.json"], cwd=ROOT),
            SOURCE.read_bytes(),
        )
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA)
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "bound" / SOURCE.name
            env = os.environ.copy()
            env["FINANCE_OUTLOOK_CREDENTIAL_ID"] = "outlookCredential123"
            env["FINANCE_ONEDRIVE_CREDENTIAL_ID"] = "onedriveCredential123"
            completed = subprocess.run(
                [sys.executable, str(RUNNER / "bind-microsoft-oauth-refresh-proof.py"), str(SOURCE), str(destination), "--finance-commit", "a" * 40],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertNotIn("outlookCredential123", completed.stdout + completed.stderr)
            self.assertNotIn("onedriveCredential123", completed.stdout + completed.stderr)
            bound = json.loads(destination.read_text(encoding="utf-8"))
            outlook = next(node for node in bound["nodes"] if node["type"] == "n8n-nodes-base.microsoftOutlook")
            drive = next(node for node in bound["nodes"] if node["type"] == "n8n-nodes-base.microsoftOneDrive")
            self.assertEqual(outlook["credentials"]["microsoftOutlookOAuth2Api"]["id"], "outlookCredential123")
            self.assertEqual(drive["credentials"]["microsoftOneDriveOAuth2Api"]["id"], "onedriveCredential123")
            self.assertEqual(outlook["parameters"]["output"], "fields")
            self.assertEqual(outlook["parameters"]["fields"], ["id"])
            receipt = json.loads((destination.parent / "binding-receipt.json").read_text(encoding="utf-8"))
            self.assertFalse(receipt["credential_ids_recorded"])
            self.assertNotIn("outlookCredential123", json.dumps(receipt))
            self.assertNotIn("onedriveCredential123", json.dumps(receipt))

    def run_irun_validator(self, payload: dict) -> subprocess.CompletedProcess[str]:
        shim = RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"
        code = (
            f"const shim=require({json.dumps(str(shim))});"
            "try{process.stdout.write(JSON.stringify(shim.validateIRun(JSON.parse(require('node:fs').readFileSync(0,'utf8')))));}"
            "catch(error){process.stderr.write(String(error.code||'VALIDATION_FAILED'));process.exit(1)}"
        )
        return subprocess.run(["node", "-e", code], input=json.dumps(payload), text=True, capture_output=True)

    def test_in_memory_shim_accepts_only_exact_redacted_terminal_receipt(self) -> None:
        completed = self.run_irun_validator(irun(terminal_result()))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["outlook_items_observed"], 1)
        shim = (RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs").read_text(encoding="utf-8")
        for marker in (
            "captureRawIRunInMemory",
            "EXECUTIONS_DATA_SAVE_ON_SUCCESS",
            "EXECUTIONS_DATA_SAVE_ON_ERROR",
            "EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS",
            "fs.writeSync(1",
            "payload = null",
            "Container.get(Execute)",
            "n8nRequire('@n8n/backend-common')",
            "await Container.get(ModuleRegistry).loadModules()",
            "command.flags = { id: WORKFLOW_ID, rawOutput: true }",
            "await command.init()",
            "await command.run()",
            "await command.finally(sanitizedError)",
            "command.log = function captureRawIRunInMemory",
            "command.logger = new Proxy",
            "if (isDirectEntrypoint(require.main, module))",
            "const N8N_CONFIG_ENTRYPOINT = './dist/config';",
            "const originalExit = process.exit.bind(process);",
            "process.exit = () => { throw fixedError('WF23_N8N_REQUESTED_EARLY_EXIT'); };",
            "process.exit = originalExit;",
            "const WATCHDOG_TIMEOUT_MS = 120_000;",
            "watchdog.arm('CONFIG_LOAD')",
            "watchdog.arm('MODULE_LOAD')",
            "watchdog.arm('COMMAND_INIT')",
            "watchdog.arm('COMMAND_RUN')",
            "watchdog.arm('RAW_CAPTURE')",
            "watchdog.arm('FINALIZE')",
            "async function terminateOnTimeout(code)",
            "writeTerminalOnce(fixedError(code), null)",
            "WF23_DEPLOYED_TASK_RUNNER_CONTROL_PATH_REQUIRED",
            "process.env.N8N_RUNNERS_MODE !== 'external'",
            "process.env.N8N_RUNNERS_BROKER_LISTEN_ADDRESS !== '0.0.0.0'",
            "!process.env.N8N_RUNNERS_AUTH_TOKEN",
        ):
            self.assertIn(marker, shim)
        self.assertNotIn("writeFile", shim)
        self.assertNotIn("Execute.prototype", shim)
        self.assertNotIn("BaseCommand.prototype", shim)
        self.assertNotIn("bin', 'n8n", shim)
        self.assertNotIn("./dist/config.js", shim)
        self.assertNotIn("process.once('exit'", shim)
        lifecycle_markers = (
            "n8nRequire(N8N_CONFIG_ENTRYPOINT)",
            "n8nRequire('./dist/commands/execute.js')",
            "n8nRequire('@n8n/backend-common')",
            "await Container.get(ModuleRegistry).loadModules()",
            "command = Container.get(Execute)",
            "await command.init()",
            "command.log = function captureRawIRunInMemory",
            "await command.run()",
            "await command.finally(sanitizedError)",
        )
        positions = [shim.index(marker) for marker in lifecycle_markers]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(shim.rindex("watchdog.cancel()"), shim.rindex("writeTerminalOnce(terminalError, receipt)"))

    def test_production_shim_stdin_entrypoint_is_exact_and_require_import_is_inert(self) -> None:
        shim = RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"
        completed = subprocess.run(
            ["node", "-"],
            input=shim.read_text(encoding="utf-8"),
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK=EXECUTE_WF23_REDACTED_ONLY",
            completed.stderr,
        )

        extra_argument = subprocess.run(
            ["node", "-", "extra"],
            input=shim.read_text(encoding="utf-8"),
            text=True,
            capture_output=True,
        )
        self.assertEqual(extra_argument.returncode, 0, extra_argument.stderr)
        self.assertEqual(extra_argument.stdout, "")

        imported = subprocess.run(
            ["node", "-e", f"require({json.dumps(str(shim))});process.stdout.write('inert')"],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(imported.stdout, "inert")

    def test_production_shim_rejects_non_deployed_runner_path_before_loading_n8n(self) -> None:
        shim = RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"
        env = os.environ.copy()
        env.update({
            "FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK": "EXECUTE_WF23_REDACTED_ONLY",
            "EXECUTIONS_DATA_SAVE_ON_SUCCESS": "none",
            "EXECUTIONS_DATA_SAVE_ON_ERROR": "none",
            "EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS": "false",
            "N8N_RUNNERS_MODE": "internal",
            "N8N_RUNNERS_BROKER_LISTEN_ADDRESS": "SECRET_PROVIDER_VALUE",
        })
        completed = subprocess.run(
            ["node", "-"],
            input=shim.read_text(encoding="utf-8"),
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("WF23_DEPLOYED_TASK_RUNNER_CONTROL_PATH_REQUIRED", completed.stderr)
        self.assertNotIn("SECRET_PROVIDER_VALUE", completed.stdout + completed.stderr)
        self.assertNotIn("Cannot find module 'n8n/package.json'", completed.stderr)

    def test_stdin_entrypoint_gate_and_direct_lifecycle_reject_adversarial_orders(self) -> None:
        shim = RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"
        probe = RUNNER / "n8n-cli-wf23-direct-transport-probe.cjs"
        code = (
            f"const shim=require({json.dumps(str(shim))});"
            f"const probe=require({json.dumps(str(probe))});"
            "const imported={filename:'/runtime/imported.cjs'};"
            "const stdin={filename:'/runtime/[stdin]'};"
            "const direct={filename:'/runtime/direct.cjs'};"
            "const checks=["
            "shim.isDirectEntrypoint(direct,direct,['node','ignored']),"
            "shim.isDirectEntrypoint(undefined,stdin,['node','-']),"
            "!shim.isDirectEntrypoint(undefined,stdin,['node','-','extra']),"
            "!shim.isDirectEntrypoint(undefined,imported,['node','-']),"
            "probe.isDirectEntrypoint(undefined,stdin,['node','-']),"
            "!probe.isDirectEntrypoint(undefined,stdin,['node','-','extra'])];"
            "const valid=shim.directLifecycleGate();"
            "for(const stage of shim.DIRECT_LIFECYCLE_ORDER)valid(stage);"
            "let rejected=false;try{const invalid=shim.directLifecycleGate();invalid('command-loaded')}"
            "catch(error){rejected=error.code==='WF23_DIRECT_LIFECYCLE_ORDER_INVALID'}"
            "if(!checks.every(Boolean)||!rejected)process.exit(1);"
            "process.stdout.write(JSON.stringify(shim.DIRECT_LIFECYCLE_ORDER));"
        )
        completed = subprocess.run(
            ["node", "-"], input=code, text=True, capture_output=True, check=True
        )
        self.assertEqual(
            json.loads(completed.stdout),
            ["config-loaded", "command-loaded", "modules-loaded", "execute-resolved", "execute-initialized"],
        )

    def test_terminal_line_serializes_only_validated_redacted_receipt_and_safe_failure(self) -> None:
        shim = RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"
        payload = irun(terminal_result())
        payload["data"]["resultData"]["runData"]["Read One Bounded Outlook Message"] = [{
            "data": {"main": [[{"json": {"access_token": "fake-token", "subject": "fake-subject"}}]]},
        }]
        code = (
            f"const shim=require({json.dumps(str(shim))});"
            "let raw='';process.stdin.on('data',chunk=>raw+=chunk);process.stdin.on('end',()=>{"
            "const receipt=shim.validateIRun(JSON.parse(raw));"
            "process.stdout.write(shim.terminalLine(null,receipt));"
            "process.stdout.write(shim.terminalLine({code:'NOT_ALLOWLISTED',access_token:'fake-token'},null));});"
        )
        completed = subprocess.run(
            ["node", "-e", code],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(len(completed.stdout.splitlines()), 2)
        self.assertIn("transient WF23 execution verified:", completed.stdout)
        self.assertIn("WF23_REDACTED_EXECUTION_FAILED", completed.stdout)
        self.assertNotIn("fake-token", completed.stdout + completed.stderr)
        self.assertNotIn("fake-subject", completed.stdout + completed.stderr)

    def test_internal_watchdog_uses_fixed_stages_once_and_cancels_on_success(self) -> None:
        shim = RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"
        code = (
            f"const shim=require({json.dumps(str(shim))});"
            "let nextId=0;const timers=new Map();const cleared=[];"
            "const setTimer=(callback,ms)=>{const id=++nextId;timers.set(id,{callback,ms});return id};"
            "const clearTimer=(id)=>{cleared.push(id);timers.delete(id)};"
            "const stageLines=[];"
            "for(const stage of shim.WATCHDOG_STAGES){"
            "const watchdog=shim.createStageWatchdog({setTimer,clearTimer,onTimeout:(timeoutCode)=>{"
            "stageLines.push(shim.terminalLine({code:timeoutCode,access_token:'must-not-emit'},null))}});"
            "watchdog.arm(stage);const entry=timers.get(nextId);"
            "if(entry.ms!==shim.WATCHDOG_TIMEOUT_MS)process.exit(1);entry.callback();entry.callback();}"
            "const raceLines=[];const race=shim.createStageWatchdog({setTimer,clearTimer,onTimeout:(timeoutCode)=>{"
            "raceLines.push(shim.terminalLine({code:timeoutCode},null))}});"
            "race.arm('COMMAND_RUN');const stale=timers.get(nextId).callback;"
            "race.arm('RAW_CAPTURE');const current=timers.get(nextId).callback;stale();current();current();"
            "const successLines=[];const success=shim.createStageWatchdog({setTimer,clearTimer,onTimeout:(timeoutCode)=>successLines.push(timeoutCode)});"
            "success.arm('FINALIZE');const afterSuccess=timers.get(nextId).callback;"
            "if(!success.cancel())process.exit(1);afterSuccess();"
            "let invalid='';try{race.arm('access_token=secret')}catch(error){invalid=error.code}"
            "process.stdout.write(JSON.stringify({stageLines,raceLines,successLines,invalid,cleared,stages:shim.WATCHDOG_STAGES}));"
        )
        completed = subprocess.run(["node", "-"], input=code, text=True, capture_output=True, check=True)
        self.assertNotIn("must-not-emit", completed.stdout + completed.stderr)
        self.assertNotIn("access_token=secret", completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["invalid"], "WF23_WATCHDOG_STAGE_INVALID")
        self.assertEqual(len(result["stageLines"]), len(result["stages"]))
        self.assertEqual(len(result["raceLines"]), 1)
        self.assertEqual(result["successLines"], [])
        self.assertGreater(len(result["cleared"]), 0)
        for stage, line in zip(result["stages"], result["stageLines"], strict=True):
            payload = json.loads(line.split(":", 1)[1])
            self.assertEqual(payload["error_code"], f"WF23_TIMEOUT_{stage}")
            self.assertEqual(
                set(payload),
                {"schema_version", "status", "error_code", "provider_response_logged", "secret_values_recorded"},
            )
        self.assertEqual(
            json.loads(result["raceLines"][0].split(":", 1)[1])["error_code"],
            "WF23_TIMEOUT_RAW_CAPTURE",
        )

    def test_execution_output_parser_accepts_only_exact_success_and_allowlisted_timeouts(self) -> None:
        parser_path = RUNNER / "parse_wf23_execution_output.py"
        parser = load_module("wf23_execution_output", parser_path)
        success = "transient WF23 execution verified:" + json.dumps(terminal_result(), separators=(",", ":")) + "\n"
        self.assertEqual(parser.parse_success(success), terminal_result())

        timeout_codes = {
            "WF23_TIMEOUT_CONFIG_LOAD",
            "WF23_TIMEOUT_MODULE_LOAD",
            "WF23_TIMEOUT_COMMAND_INIT",
            "WF23_TIMEOUT_COMMAND_RUN",
            "WF23_TIMEOUT_RAW_CAPTURE",
            "WF23_TIMEOUT_FINALIZE",
        }
        failure_builder = load_module(
            "wf23_failure_timeout_allowlist", RUNNER / "build_microsoft_oauth_failure_receipt.py"
        )
        self.assertEqual(parser.TIMEOUT_CODES, timeout_codes)
        self.assertEqual(failure_builder.TIMEOUT_CODES, timeout_codes)
        runner_source = (RUNNER / "run-transient-microsoft-oauth-refresh-proof.sh").read_text(encoding="utf-8")
        for code in timeout_codes:
            self.assertIn(code, runner_source)
        for code in timeout_codes:
            with self.subTest(code=code):
                payload = {
                    "schema_version": 1,
                    "status": "FAILED",
                    "error_code": code,
                    "provider_response_logged": False,
                    "secret_values_recorded": False,
                }
                raw = "transient WF23 execution failed:" + json.dumps(payload, separators=(",", ":")) + "\n"
                self.assertEqual(parser.parse_timeout(raw), code)

        valid = (
            'transient WF23 execution failed:{"schema_version":1,"status":"FAILED",'
            '"error_code":"WF23_TIMEOUT_COMMAND_RUN","provider_response_logged":false,'
            '"secret_values_recorded":false}'
        )
        adversarial = {
            "duplicate override": valid.replace(
                '"error_code":"WF23_TIMEOUT_COMMAND_RUN"',
                '"error_code":"BOGUS","error_code":"WF23_TIMEOUT_COMMAND_RUN"',
            ),
            "boolean schema": valid.replace('"schema_version":1', '"schema_version":true'),
            "unknown code": valid.replace("WF23_TIMEOUT_COMMAND_RUN", "WF23_TIMEOUT_PROVIDER_SECRET"),
            "extra provider value": valid[:-1] + ',"access_token":"secret"}',
            "integer false flag": valid.replace('"provider_response_logged":false', '"provider_response_logged":0'),
            "multiple output": valid + "\n" + valid,
        }
        for name, raw in adversarial.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parser.parse_timeout(raw)

        secret = "SECRET_ACCESS_TOKENZ"
        bad_timestamp = terminal_result()
        bad_timestamp["verified_at"] = secret
        cli_adversarial = {
            "token-like timestamp": (
                "success",
                "transient WF23 execution verified:" + json.dumps(bad_timestamp, separators=(",", ":")),
            ),
            "malformed json": (
                "success",
                'transient WF23 execution verified:{"verified_at":"' + secret,
            ),
            "duplicate key": (
                "timeout",
                valid.replace(
                    '"error_code":"WF23_TIMEOUT_COMMAND_RUN"',
                    f'"error_code":"{secret}","error_code":"WF23_TIMEOUT_COMMAND_RUN"',
                ),
            ),
            "extra field": (
                "timeout",
                valid[:-1] + f',"provider_value":"{secret}"}}',
            ),
        }
        for name, (mode, raw) in cli_adversarial.items():
            with self.subTest(cli=name):
                completed = subprocess.run(
                    [sys.executable, str(parser_path), mode],
                    input=raw,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr.strip(), parser.REJECTED_DIAGNOSTIC)
                self.assertNotIn(secret, completed.stdout + completed.stderr)
                self.assertNotIn("Traceback", completed.stdout + completed.stderr)

        with self.assertRaisesRegex(ValueError, "^WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH$") as rejected:
            parser.parse_timestamp(secret)
        self.assertNotIn(secret, str(rejected.exception))

    def test_direct_transport_probe_is_no_workflow_no_provider_and_exactly_redacted(self) -> None:
        probe = RUNNER / "n8n-cli-wf23-direct-transport-probe.cjs"
        probe_text = probe.read_text(encoding="utf-8")
        for marker in (
            "READ_ONLY_DIRECT_EXECUTE_INSTANCE",
            "Container.get(Execute)",
            "command instanceof Execute",
            "command.log = () => { invoked = true; }",
            "fs.writeSync(1",
            "workflow_loaded: false",
            "workflow_executed: false",
            "provider_calls: false",
            "database_initialized: false",
            "raw_irun_persisted: false",
            "provider_response_logged: false",
            "secret_values_recorded: false",
            "if (isDirectEntrypoint(require.main, module))",
            "const N8N_CONFIG_ENTRYPOINT = './dist/config';",
            "n8nRequire(N8N_CONFIG_ENTRYPOINT)",
        ):
            self.assertIn(marker, probe_text)
        for forbidden in (
            "./dist/config.js", "command.init(", "command.run(", "WorkflowRepository",
            "WorkflowRunner", "microsoftOneDrive", "microsoftOutlook", "ModuleRegistry",
            "loadModules(",
        ):
            self.assertNotIn(forbidden, probe_text)
        code = (
            f"const probe=require({json.dumps(str(probe))});"
            "process.stdout.write(probe.PROBE_PREFIX+JSON.stringify(probe.exactProbeReceipt())+'\\n');"
        )
        completed = subprocess.run(["node", "-e", code], text=True, capture_output=True, check=True)
        lines = completed.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0].split(":", 1)[1])
        self.assertEqual(payload["status"], "VERIFIED")
        self.assertTrue(payload["execute_instance_resolved"])
        self.assertTrue(payload["instance_log_override_invoked"])
        self.assertFalse(any(payload[key] for key in (
            "workflow_loaded", "workflow_executed", "provider_calls", "database_initialized",
            "raw_irun_persisted", "provider_response_logged", "secret_values_recorded",
        )))

    def test_n8n_config_entrypoint_is_extensionless_and_resolves_directory_index(self) -> None:
        shim = RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"
        probe = RUNNER / "n8n-cli-wf23-direct-transport-probe.cjs"
        with tempfile.TemporaryDirectory() as tmp:
            package_root = Path(tmp) / "n8n"
            config_root = package_root / "dist" / "config"
            config_root.mkdir(parents=True)
            (package_root / "package.json").write_text('{"name":"n8n","version":"2.36.2"}\n', encoding="utf-8")
            (config_root / "index.js").write_text("module.exports={};\n", encoding="utf-8")
            code = (
                "const {createRequire}=require('node:module');"
                f"const shim=require({json.dumps(str(shim))});"
                f"const probe=require({json.dumps(str(probe))});"
                f"const req=createRequire({json.dumps(str(package_root / 'package.json'))});"
                "const values=[shim.N8N_CONFIG_ENTRYPOINT,probe.N8N_CONFIG_ENTRYPOINT,"
                "req.resolve(shim.N8N_CONFIG_ENTRYPOINT),req.resolve(probe.N8N_CONFIG_ENTRYPOINT)];"
                "process.stdout.write(JSON.stringify(values));"
            )
            completed = subprocess.run(["node", "-e", code], text=True, capture_output=True, check=True)
            values = json.loads(completed.stdout)
            self.assertEqual(values[:2], ["./dist/config", "./dist/config"])
            self.assertEqual(
                [Path(value).resolve() for value in values[2:]],
                [(config_root / "index.js").resolve(), (config_root / "index.js").resolve()],
            )

    def test_transport_probe_stdin_entrypoint_runs_and_requires_ack(self) -> None:
        probe = RUNNER / "n8n-cli-wf23-direct-transport-probe.cjs"
        completed = subprocess.run(
            ["node", "-"],
            input=probe.read_text(encoding="utf-8"),
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "FINANCE_WF23_TRANSPORT_PROBE_ACK=READ_ONLY_DIRECT_EXECUTE_INSTANCE",
            completed.stderr,
        )

    def test_in_memory_shim_rejects_extra_provider_fields_without_echoing_values(self) -> None:
        result = terminal_result()
        result["subject"] = "sensitive subject"
        payload = irun(result)
        payload["data"]["resultData"]["runData"]["Read One Bounded Outlook Message"] = [{
            "executionStatus": "success",
            "data": {"main": [[{"json": {"access_token": "must-not-echo", "subject": "must-not-echo"}}]]},
        }]
        completed = self.run_irun_validator(payload)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("KEYS_MISMATCH", completed.stderr)
        self.assertNotIn("must-not-echo", completed.stdout + completed.stderr)

    def test_metadata_readback_discovers_owner_credentials_and_emits_no_identifiers(self) -> None:
        source = (RUNNER / "n8n-cli-microsoft-oauth-metadata-readback.cjs").read_text(encoding="utf-8")
        for marker in (
            "CredentialsRepository",
            "SharedCredentialsRepository",
            "credential:owner",
            "decryptV2",
            "oauthTokenData",
            "n8n_expires_at",
            "credential_updated_at_utc",
            "credential_ids_recorded: false",
            "token_fingerprints_recorded: false",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("NcQo00WO7GQ3qYyA", source)
        self.assertNotIn("eSnL069pIlzjFj4B", source)
        self.assertNotIn("access_token:", source)
        self.assertNotIn("refresh_token:", source)

    def test_project_id_gate_accepts_environment_binding_and_rejects_missing_or_malformed_values(self) -> None:
        acknowledgements = {
            "n8n-cli-microsoft-oauth-metadata-readback.cjs": "FINANCE_MICROSOFT_OAUTH_METADATA_ACK=READ_ONLY_REDACTED",
            "n8n-cli-finance-data-table-digest.cjs": "FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY",
            "n8n-cli-remove-transient-microsoft-oauth-refresh-proof.cjs": "FINANCE_MICROSOFT_OAUTH_PROOF_CLEANUP_ACK=REMOVE_TRANSIENT_WF23_ONLY",
        }
        for filename, acknowledgement in acknowledgements.items():
            with self.subTest(filename=filename):
                source = (RUNNER / filename).read_text(encoding="utf-8")
                prelude = source.split("const path = require('node:path');", 1)[0]
                code = prelude + "\nprocess.stdout.write(projectId);"

                valid_env = os.environ.copy()
                valid_env[acknowledgement.split("=", 1)[0]] = acknowledgement.split("=", 1)[1]
                valid_env["N8N_FINANCE_PROJECT_ID"] = "finance-preproduction-20260824"
                valid = subprocess.run(["node", "-e", code], env=valid_env, text=True, capture_output=True)
                self.assertEqual(valid.returncode, 0, valid.stderr)
                self.assertEqual(valid.stdout, "finance-preproduction-20260824")

                missing_env = valid_env.copy()
                missing_env.pop("N8N_FINANCE_PROJECT_ID")
                missing = subprocess.run(["node", "-e", code], env=missing_env, text=True, capture_output=True)
                self.assertNotEqual(missing.returncode, 0)
                self.assertIn("N8N_FINANCE_PROJECT_ID_REQUIRED", missing.stderr)

                malformed_env = valid_env.copy()
                malformed_env["N8N_FINANCE_PROJECT_ID"] = "finance preproduction"
                malformed = subprocess.run(["node", "-e", code], env=malformed_env, text=True, capture_output=True)
                self.assertNotEqual(malformed.returncode, 0)
                self.assertIn("N8N_FINANCE_PROJECT_ID_INVALID", malformed.stderr)

    def test_runner_passes_only_the_validated_environment_project_binding(self) -> None:
        runner = (RUNNER / "run-transient-microsoft-oauth-refresh-proof.sh").read_text(encoding="utf-8")
        self.assertIn('readonly expected_project_id="${N8N_FINANCE_PROJECT_ID:-}"', runner)
        self.assertIn("N8N_FINANCE_PROJECT_ID is required", runner)
        self.assertIn("N8N_FINANCE_PROJECT_ID is invalid", runner)
        for filename in (
            "n8n-cli-microsoft-oauth-metadata-readback.cjs",
            "n8n-cli-finance-data-table-digest.cjs",
            "n8n-cli-remove-transient-microsoft-oauth-refresh-proof.cjs",
        ):
            source = (RUNNER / filename).read_text(encoding="utf-8")
            self.assertIn("/^[A-Za-z0-9_-]{8,64}$/", source)

    def test_data_table_digest_is_read_only_in_memory_and_bounded(self) -> None:
        source = (RUNNER / "n8n-cli-finance-data-table-digest.cjs").read_text(encoding="utf-8")
        for marker in (
            "DataTableService",
            "getManyAndCount",
            "getColumns",
            "getManyRowsAndCount",
            "createHash('sha256')",
            "listed.count !== CANONICAL_TABLES.size",
            "listed.data.length !== CANONICAL_TABLES.size",
            "'finance_ingestion_state'",
            "'finance_documents'",
            "'finance_actual_batches'",
            "'finance_ai_reviews'",
            "page.count > 100000",
            "row_values_recorded: false",
            "writes_performed: false",
        ):
            self.assertIn(marker, source)
        for forbidden in ("insertRows", "updateRows", "deleteRows", "createDataTable", "drop"):
            self.assertNotIn(forbidden, source)

    def test_transport_parser_accepts_exact_n8n_2362_framing(self) -> None:
        parser = load_module("wf23_transport_parser", RUNNER / "parse_n8n_redacted_wrapper_output.py")
        fixture = json.loads(DATA_TABLE_OUTPUT_FIXTURE.read_text(encoding="utf-8"))["raw_stdout"]
        self.assertEqual(parser.parse_data_table(fixture), "0" * 64)

        with self.assertRaisesRegex(ValueError, "UNEXPECTED_N8N_WRAPPER_OUTPUT"):
            parser.parse_data_table("unreviewed warning\n" + fixture)
        receipt = next(line for line in fixture.splitlines() if line.startswith("finance data table digest verified:"))
        with self.assertRaisesRegex(ValueError, "EXACT_ONE_REDACTED_RECEIPT_REQUIRED"):
            parser.parse_data_table(fixture + receipt + "\n")
        with self.assertRaisesRegex(ValueError, "DATA_TABLE_DIGEST_RECEIPT_CONTRACT_MISMATCH"):
            parser.parse_data_table(fixture.replace('"finance_tables":4', '"finance_tables":3'))

        adversarial = {
            "boolean schema version": fixture.replace('"schema_version":1', '"schema_version":true'),
            "boolean row count": fixture.replace('"total_rows":17', '"total_rows":false'),
            "duplicate status": fixture.replace('"status":"VERIFIED"', '"status":"BOGUS","status":"VERIFIED"'),
            "padded warning": fixture.replace("Postgres 16 is outside", " Postgres 16 is outside"),
        }
        for name, raw in adversarial.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parser.parse_data_table(raw)

    def test_transport_parser_accepts_noisy_redacted_oauth_metadata_only(self) -> None:
        parser = load_module("wf23_transport_oauth_parser", RUNNER / "parse_n8n_redacted_wrapper_output.py")
        value = metadata_snapshot("2026-08-20T12:00:00Z", "2026-08-20T11:00:00Z", True)
        value.update({
            "provider_calls": False,
            "database_writes": False,
            "credential_ids_recorded": False,
            "secret_values_recorded": False,
            "token_fingerprints_recorded": False,
        })
        banner = '\x1b[4m>>>> Executing external compose provider "/usr/local/bin/docker-compose". Please refer to the documentation for details. <<<<\n\x1b[0m'
        warning = "Postgres 16 is outside the supported range and receives compatibility support only. Upgrade to Postgres 17 or newer.\n"
        prefix = "microsoft oauth metadata readback verified:"
        payload = json.dumps(value, separators=(",", ":"))
        raw = banner + warning + prefix + payload + "\n"
        parsed = parser.parse_oauth_metadata(raw)
        self.assertEqual(set(parsed["credentials"]), {"outlook", "onedrive"})
        self.assertFalse(parsed["credential_ids_recorded"])

        adversarial = {
            "boolean schema version": raw.replace('"schema_version":1', '"schema_version":true'),
            "boolean extra count": raw.replace('"status":"VERIFIED"', '"total_rows":false,"status":"VERIFIED"'),
            "duplicate status": raw.replace('"status":"VERIFIED"', '"status":"BOGUS","status":"VERIFIED"'),
            "padded warning": raw.replace("Upgrade to Postgres 17 or newer.\n", "Upgrade to Postgres 17 or newer. \n"),
        }
        for name, candidate in adversarial.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parser.parse_oauth_metadata(candidate)

    def test_refresh_validator_requires_expired_to_future_transition_for_both_providers(self) -> None:
        validator = load_module("wf23_refresh_validator", RUNNER / "validate_microsoft_oauth_refresh_evidence.py")
        before = metadata_snapshot("2026-08-20T12:00:00Z", "2026-08-20T11:00:00Z", True)
        after_first = metadata_snapshot("2026-08-20T12:01:00Z", "2026-08-20T13:01:00Z", False)
        after_second = metadata_snapshot("2026-08-20T12:03:00Z", "2026-08-20T13:01:00Z", False)
        result = validator.validate_refresh([before, after_first, after_second])
        self.assertEqual(set(result), {"outlook", "onedrive"})
        for row in result.values():
            self.assertTrue(row["expired_before_first_execution"])
            self.assertTrue(row["first_execution_expiry_advanced"])
            self.assertTrue(row["first_execution_expiry_future"])
            self.assertTrue(row["post_restart_expiry_non_regression"])
            self.assertFalse(row["updated_at_used_as_refresh_proof"])
            self.assertTrue(row["refresh_proven_by_expiry_transition"])

    def test_refresh_validator_rejects_updated_at_only_and_post_restart_expiry_regression(self) -> None:
        validator = load_module("wf23_refresh_validator_rejection", RUNNER / "validate_microsoft_oauth_refresh_evidence.py")
        before = metadata_snapshot("2026-08-20T12:00:00Z", "2026-08-20T11:00:00Z", True)
        unexpired_before = metadata_snapshot("2026-08-20T12:00:00Z", "2026-08-20T13:00:00Z", False)
        with self.assertRaisesRegex(ValueError, "TOKEN_NOT_EXPIRED_BEFORE_FIRST_EXECUTION"):
            validator.validate_refresh([unexpired_before, unexpired_before, unexpired_before])

        updated_only = metadata_snapshot("2026-08-20T12:01:00Z", "2026-08-20T11:00:00Z", True, "2026-08-20T12:00:30Z")
        with self.assertRaisesRegex(ValueError, "FIRST_EXECUTION_DID_NOT_REFRESH_EXPIRED_TOKEN"):
            validator.validate_refresh([before, updated_only, updated_only])

        after_first = metadata_snapshot("2026-08-20T12:01:00Z", "2026-08-20T13:01:00Z", False)
        regressed = metadata_snapshot("2026-08-20T12:03:00Z", "2026-08-20T12:30:00Z", False)
        with self.assertRaisesRegex(ValueError, "POST_RESTART_EXPIRY_REGRESSION"):
            validator.validate_refresh([before, after_first, regressed])

    def test_failure_receipt_uses_unknown_until_postconditions_are_proven(self) -> None:
        builder = load_module("wf23_failure_receipt", RUNNER / "build_microsoft_oauth_failure_receipt.py")
        unknown = builder.build_receipt("run", "first_execution", False, False, False, False)
        self.assertEqual(unknown["status"], "FAILED_REVIEW_REQUIRED")
        self.assertIsNone(unknown["failure_code"])
        self.assertIsNone(unknown["raw_irun_persisted"])
        self.assertIsNone(unknown["finance_data_table_writes"])
        self.assertEqual(unknown["postconditions"], {
            "workflow_baseline_restored": None,
            "execution_rows_zero": None,
            "official_data_table_digest_restored": None,
        })

        clean = builder.build_receipt("run", "first_execution", True, True, True, True)
        self.assertEqual(clean["status"], "FAILED_CLEAN_BOUNDARY_RESTORED")
        self.assertFalse(clean["raw_irun_persisted"])
        self.assertFalse(clean["finance_data_table_writes"])
        self.assertIsNone(clean["failure_code"])
        timed_out = builder.build_receipt(
            "run", "first_execution", True, True, True, True, "WF23_TIMEOUT_COMMAND_RUN"
        )
        self.assertEqual(timed_out["failure_code"], "WF23_TIMEOUT_COMMAND_RUN")
        with self.assertRaisesRegex(ValueError, "INVALID_FAILURE_CODE"):
            builder.build_receipt("run", "first_execution", True, True, True, True, "provider secret")
        with self.assertRaisesRegex(ValueError, "INVALID_CLEAN_BOUNDARY_ASSERTION"):
            builder.build_receipt("run", "first_execution", True, True, False, True)

    def test_cleanup_is_exactly_scoped_to_inactive_wf23(self) -> None:
        cleanup = (RUNNER / "n8n-cli-remove-transient-microsoft-oauth-refresh-proof.cjs").read_text(encoding="utf-8")
        for marker in (
            "REMOVE_TRANSIENT_WF23_ONLY",
            "10000000-0000-4000-8000-000000000023",
            "MICROSOFT_OAUTH_REFRESH_PROOF",
            "workflow.active !== false",
            "workflow.activeVersionId !== null",
            "providerMutationScope !== 'NONE'",
            "workflowRepository.delete(workflowId)",
            "TRANSIENT_WF23_DELETE_READBACK_MISMATCH",
        ):
            self.assertIn(marker, cleanup)

    def test_runner_enforces_transient_restart_and_restoration_contract(self) -> None:
        runner = (RUNNER / "run-transient-microsoft-oauth-refresh-proof.sh").read_text(encoding="utf-8")
        for marker in (
            '"21|0|0"',
            '"22|0|0"',
            '"$(tag_edge_count)" == "63"',
            '"$(tag_edge_count)" == "66"',
            "baseline_digest_before",
            '"$(baseline_digest)" == "${baseline_digest_before}"',
            "data_table_digest_before",
            '"${observed_data_table_digest}" == "${data_table_digest_before}"',
            "EXECUTE_WF23_REDACTED_ONLY",
            "REMOVE_TRANSIENT_WF23_ONLY",
            "EXECUTIONS_DATA_SAVE_ON_SUCCESS=none",
            "EXECUTIONS_DATA_SAVE_ON_ERROR=none",
            "EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS=false",
            "wf23_execution_count",
            "wf23_history_count",
            '[[ "$(wf23_execution_count)" == "0" ]] || return 1',
            'docker restart "${n8n_container}"',
            "Non-n8n service changed during restart",
            "metadata_before",
            "metadata_after_first",
            "metadata_after_second",
            "Microsoft credential owner binding changed during proof",
            'owner_bindings_stable":True',
            'credential_ids_recorded":False',
            "validate_microsoft_oauth_refresh_evidence.py",
            "build_microsoft_oauth_failure_receipt.py",
            "parse_n8n_redacted_wrapper_output.py",
            "parse_wf23_execution_output.py",
            "execution_rows_zero_verified",
            "data_table_digest_restored",
            "retain_execution_timeout_code",
            '"${execution_failure_code}"',
            "WF23_TIMEOUT_COMMAND_RUN",
            'raw_irun_persisted":False',
            'finance_data_table_writes":False',
            'baseline_digest_restored":True',
            "FINANCE_WF23_TRANSPORT_PROBE_ACK=READ_ONLY_DIRECT_EXECUTE_INSTANCE",
            "n8n-cli-wf23-direct-transport-probe.cjs",
            "WF23 direct execution transport probe failed before metadata/provider access",
            'readonly recovery_receipt="${FINANCE_N8N_RECOVERY_RECEIPT:-}"',
            "postgres_container_from_recovery_receipt",
            'receipt.get("postgres", {}).get("container_id")',
            "Recovered Postgres readiness failed",
            "pg_isready",
            "current_database()",
            "task_runner_control_preflight",
            "N8N_RUNNERS_MODE !== \"external\"",
            "N8N_RUNNERS_BROKER_LISTEN_ADDRESS !== \"0.0.0.0\"",
            "http://127.0.0.1:5679/healthz",
            '"${task_runners_container}" node -e',
            "http://127.0.0.1:5680/healthz",
            "Canonical Global/Shared folder hierarchy required",
            'global_folder_id="f1000000-0000-4000-8000-000000000190"',
            '"${n8n_container}" node - < "${runner_dir}/n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"',
            "/dev/shm/",
            'readonly expected_project="${FINANCE_N8N_COMPOSE_PROJECT:-}"',
            'readonly compose_file_input="${FINANCE_N8N_COMPOSE_FILE:-}"',
            'readonly env_file="${FINANCE_N8N_DEPLOYMENT_ENV_FILE:-}"',
            'readonly receipt_root="${FINANCE_N8N_RECEIPT_DIR:-}"',
            "Mode-0600 deployed runtime environment required",
            "docker exec -i \"${n8n_container}\" sh -c",
            "docker exec \"${n8n_container}\" n8n import:workflow",
            'docker restart "${n8n_container}"',
            'com.docker.compose.project',
        ):
            self.assertIn(marker, runner)
        for forbidden in (
            "activate:workflow",
            "publish:workflow",
            "execution_irun",
            "irun.json",
            "provider-response",
            "N8N_API_KEY",
            "NcQo00WO7GQ3qYyA",
            "eSnL069pIlzjFj4B",
            "node - execute --id=",
            "N8N_RUNNERS_BROKER_PORT=5679",
            "N8N_RUNNERS_BROKER_PORT=15679",
            "N8N_RUNNERS_MODE=internal",
            "15679",
            "compose.disposable.yaml",
            "/opt/disposable/",
            "verify-image-lock.sh",
            "FINANCE_N8N_IMAGE_LOCK",
            "docker compose run",
            "docker compose restart n8n",
        ):
            self.assertNotIn(forbidden, runner)
        transport_position = runner.index("direct_transport_probe ||")
        runner_control_position = runner.index("task_runner_control_preflight ||")
        self.assertLess(transport_position, runner_control_position)
        self.assertLess(runner_control_position, runner.index('data_table_digest_before="$(data_table_digest)"'))
        self.assertLess(runner_control_position, runner.index('metadata_before="$(read_metadata)"'))
        self.assertLess(runner_control_position, runner.index('failure_stage="workflow_import"'))
        self.assertLess(transport_position, runner.index('data_table_digest_before="$(data_table_digest)"'))
        self.assertLess(transport_position, runner.index('metadata_before="$(read_metadata)"'))
        self.assertLess(transport_position, runner.index('failure_stage="workflow_import"'))

    def test_all_runner_sources_are_syntactically_valid(self) -> None:
        subprocess.run(["bash", "-n", str(RUNNER / "run-transient-microsoft-oauth-refresh-proof.sh")], check=True)
        for source in RUNNER.glob("*.py"):
            subprocess.run([sys.executable, "-m", "py_compile", str(source)], check=True)
        for source in RUNNER.glob("*.cjs"):
            subprocess.run(["node", "--check", str(source)], check=True)


if __name__ == "__main__":
    unittest.main()
