from __future__ import annotations

import datetime as dt
import json
import importlib.util
import os
import copy
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "integrations" / "n8n" / "setup-workflows"
RUNNER = SETUP / "runner"
SOURCE = SETUP / "23-microsoft-oauth-refresh-proof.json"
SOURCE_COMMIT = "f2f8d772bb3f397278d4aa5ded8c741a71d73466"
SOURCE_SHA = "2e26bd188468cf007562d3f4f47670aeb3661fbd7a8e86053a62da2cc845d940"
DATA_TABLE_OUTPUT_FIXTURE = ROOT / "tests" / "fixtures" / "n8n-2.36.2-data-table-digest-output.json"
PSQL_GATE_FIXTURE = ROOT / "tests" / "fixtures" / "psql-wf23-exact-commit-gate.sql"
PSQL_GATE_RESULTS = ROOT / "tests" / "fixtures" / "psql-wf23-exact-commit-gate-results.json"


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
    def test_runtime_binder_is_exact_and_never_records_identifiers(self) -> None:
        self.assertEqual(
            subprocess.check_output(["git", "show", f"{SOURCE_COMMIT}:integrations/n8n/setup-workflows/23-microsoft-oauth-refresh-proof.json"], cwd=ROOT),
            SOURCE.read_bytes(),
        )
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

        optional_status = irun(terminal_result())
        del optional_status["data"]["resultData"]["runData"]["Emit Redacted OAuth Proof Receipt"][0]["executionStatus"]
        completed = self.run_irun_validator(optional_status)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        explicit_error = irun(terminal_result())
        terminal_run = explicit_error["data"]["resultData"]["runData"]["Emit Redacted OAuth Proof Receipt"][0]
        del terminal_run["executionStatus"]
        terminal_run["error"] = {"message": "SECRET_PROVIDER_VALUE"}
        completed = self.run_irun_validator(explicit_error)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "WF23_TERMINAL_RUN_INVALID")
        self.assertNotIn("SECRET_PROVIDER_VALUE", completed.stdout + completed.stderr)
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
            "WF23_DEDICATED_INTERNAL_TASK_RUNNER_BOUNDARY_REQUIRED",
            "process.env.N8N_RUNNERS_MODE !== 'internal'",
            "process.env.N8N_RUNNERS_BROKER_PORT !== '15679'",
            "process.env.N8N_RUNNERS_BROKER_LISTEN_ADDRESS !== '127.0.0.1'",
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

    def test_production_shim_rejects_inherited_runner_broker_before_loading_n8n(self) -> None:
        shim = RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"
        env = os.environ.copy()
        env.update({
            "FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK": "EXECUTE_WF23_REDACTED_ONLY",
            "EXECUTIONS_DATA_SAVE_ON_SUCCESS": "none",
            "EXECUTIONS_DATA_SAVE_ON_ERROR": "none",
            "EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS": "false",
            "N8N_RUNNERS_MODE": "internal",
            "N8N_RUNNERS_BROKER_PORT": "5679",
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
        self.assertIn("WF23_DEDICATED_INTERNAL_TASK_RUNNER_BOUNDARY_REQUIRED", completed.stderr)
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

    def test_data_table_digest_is_read_only_in_memory_and_bounded(self) -> None:
        source = (RUNNER / "n8n-cli-finance-data-table-digest.cjs").read_text(encoding="utf-8")
        for marker in (
            "DataTableService",
            "getManyAndCount",
            "getColumns",
            "getManyRowsAndCount",
            "createHash('sha256')",
            "page.count > 100000",
            "row_values_recorded: false",
            "writes_performed: false",
        ):
            self.assertIn(marker, source)
        for forbidden in ("insertRows", "updateRows", "deleteRows", "createDataTable", "drop"):
            self.assertNotIn(forbidden, source)

    def test_transport_parser_accepts_exact_retained_n8n_2362_framing(self) -> None:
        parser = load_module("wf23_transport_parser", RUNNER / "parse_n8n_redacted_wrapper_output.py")
        fixture = json.loads(DATA_TABLE_OUTPUT_FIXTURE.read_text(encoding="utf-8"))["raw_stdout"]
        self.assertEqual(parser.parse_data_table(fixture), "0" * 64)

        with self.assertRaisesRegex(ValueError, "UNEXPECTED_N8N_WRAPPER_OUTPUT"):
            parser.parse_data_table("unreviewed warning\n" + fixture)
        receipt = next(line for line in fixture.splitlines() if line.startswith("finance data table digest verified:"))
        with self.assertRaisesRegex(ValueError, "EXACT_ONE_REDACTED_RECEIPT_REQUIRED"):
            parser.parse_data_table(fixture + receipt + "\n")
        with self.assertRaisesRegex(ValueError, "DATA_TABLE_DIGEST_RECEIPT_CONTRACT_MISMATCH"):
            parser.parse_data_table(fixture.replace('"finance_tables":15', '"finance_tables":14'))

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

    def test_stranded_remediation_is_ack_gated_exact_and_transactional(self) -> None:
        runner_path = RUNNER / "run-remediate-stranded-wf23.sh"
        sql_path = RUNNER / "remediate-stranded-wf23.sql"
        process_proof_path = RUNNER / "prove-wf23-orphan-inactive.sh"
        runner = runner_path.read_text(encoding="utf-8")
        sql = sql_path.read_text(encoding="utf-8")
        process_proof = process_proof_path.read_text(encoding="utf-8")

        for marker in (
            "FINANCE_WF23_STRANDED_REMEDIATION_MODE",
            "REHEARSE_EXACT_WF23_TRANSACTION_AND_ROLL_BACK",
            "REMOVE_EXACT_WF23_AND_ONE_SOFT_DELETED_ORPHAN",
            'incident_finance_commit="8149f42f2694c200ca9fae37875c4dba4e727978"',
            'required_orchestrator_commit="2c3286ae3c63a80b86ade945f19d419bf562874b"',
            'execution_id="15"',
            '"22|0|0"',
            '"21|0|0"',
            '"$(tag_edge_count)" == "66"',
            '"$(tag_edge_count)" == "63"',
            "corpus_digest_without_wf23",
            "bad_project_tag_set_count",
            "credential_digest_before",
            "data_table_digest_before",
            "workflow_corpus_digest",
            "wf23_execution_data_count",
            "wf23_orphan_signature_count",
            "validate-wf23-rehearsal-receipt.py",
            "WF23_REHEARSAL_RECEIPT_VERIFIED",
            "sql_sha256",
            "post_state_unchanged",
            "canonicalize-wf23-source.py",
            "expected_workflow_b64",
            "expected_history_b64",
            "expected_version_id",
            '"${data_table_digest_after}" == "${data_table_digest_before}"',
            "PROVE_WF23_EXECUTOR_ABSENT_READ_ONLY",
            "docker ps -aq",
            "ORPHANED_SOFT_DELETED_EXECUTION",
            '"provider_calls": False',
            '"credential_corpus_unchanged": True',
        ):
            self.assertIn(marker, runner)
        for forbidden in (
            "execute_probe",
            "microsoft-oauth-metadata-readback",
            "activate:workflow",
            "publish:workflow",
            "DELETE FROM credentials_entity",
            "UPDATE credentials_entity",
        ):
            self.assertNotIn(forbidden, runner + sql)

        self.assertLess(runner.index("observed_inactivity="), runner.index("data_table_digest_before="))
        self.assertLess(runner.index("data_table_digest_before="), runner.index("< \"${sql_file}\""))
        self.assertLess(runner.index("< \"${sql_file}\""), runner.index("data_table_digest_after="))
        self.assertLess(runner.index("receipt_validation="), runner.index("< \"${sql_file}\""))
        self.assertEqual(runner.count('< "${sql_file}"'), 1)
        self.assertIn('-v commit_authorized="${commit_authorized}"', runner)
        self.assertIn('readonly commit_authorized="off"', runner)
        self.assertIn('readonly commit_authorized="on"', runner)
        self.assertIn('docker pause "${n8n_container}"', runner)
        self.assertIn('docker unpause "${n8n_container}"', runner)
        self.assertIn("Stranded boundary changed before locked transaction", runner)
        self.assertLess(runner.index("receipt_validation="), runner.index('docker pause "${n8n_container}"'))
        self.assertLess(runner.index('docker pause "${n8n_container}"'), runner.index('< "${sql_file}"'))

        for marker in (
            "BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE",
            "pg_advisory_xact_lock",
            "LOCK TABLE workflow_entity, execution_entity, execution_data, workflow_history",
            "e.id = scope.execution_id",
            'w."isArchived" = false',
            "WF23_REMEDIATION_CANONICAL_WORKFLOW_MISMATCH",
            "WF23_REMEDIATION_CANONICAL_HISTORY_MISMATCH",
            "WF23_REMEDIATION_BINDER_ANNOTATION_MISMATCH",
            "binding->'configured' = 'true'::jsonb",
            "binding->'action_required' = 'false'::jsonb",
            "NOT (binding ? 'credential_id')",
            "'versionId', h.\"versionId\"",
            "'workflowId', h.\"workflowId\"",
            "'authors', h.authors",
            "jsonb_array_elements(w.nodes::jsonb) WITH ORDINALITY",
            "jsonb_array_elements(h.nodes::jsonb) WITH ORDINALITY",
            "e.status = 'running'",
            "e.finished = false",
            'e."waitTill" IS NULL',
            'e."stoppedAt" IS NULL',
            'e."deletedAt" IS NOT NULL',
            'e."storedAt" = \'db\'',
            "WF23_REMEDIATION_UNEXPECTED_WORKFLOW_REFERENCE",
            "WF23_REMEDIATION_UNEXPECTED_EXECUTION_REFERENCE",
            "CREATE TEMP TABLE wf23_backup_execution_data ON COMMIT DROP",
            "DELETE FROM execution_entity WHERE id = target_execution",
            "DELETE FROM workflow_history WHERE \"workflowId\" = target_workflow",
            "DELETE FROM workflows_tags WHERE \"workflowId\" = target_workflow",
            "DELETE FROM shared_workflow WHERE \"workflowId\" = target_workflow",
            "DELETE FROM workflow_entity WHERE id = target_workflow",
            "WF23_REMEDIATION_DELETE_READBACK_MISMATCH",
            "\\if :{?commit_authorized}",
            "SELECT :'commit_authorized' = 'on' AS exact_commit_authorized \\gset",
            "ROLLBACK;",
            "COMMIT;",
        ):
            self.assertIn(marker, sql)
        self.assertEqual(sql.count("BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE"), 1)
        self.assertEqual(sql.count("DELETE FROM execution_entity WHERE id = target_execution"), 1)
        self.assertEqual(sql.count("ROLLBACK;"), 1)
        self.assertEqual(sql.count("COMMIT;"), 1)
        gate = PSQL_GATE_FIXTURE.read_text(encoding="utf-8").strip()
        self.assertTrue(sql.rstrip().endswith(gate))
        self.assertLess(sql.index("WF23_REMEDIATION_DELETE_READBACK_MISMATCH"), sql.index("\\if :{?commit_authorized}"))
        self.assertLess(sql.index("$remediation_preflight$;"), sql.index("DELETE FROM execution_entity"))
        self.assertLess(
            sql.index("SELECT w.meta::jsonb->'credentialBindings' INTO runtime_bindings"),
            sql.index("'meta', jsonb_set"),
        )
        self.assertLess(sql.index("CREATE TEMP TABLE wf23_backup_execution_data"), sql.index("DELETE FROM execution_entity"))

        for marker in (
            "WF23_PROCESS_LOCAL_ACTIVE_EXECUTIONS_ABSENCE",
            "stdin_node_processes\":0",
            "n8n_initialized\":false",
            "database_initialized\":false",
            "workflow_loaded\":false",
            "provider_calls\":false",
            "/proc/[0-9]*/cmdline",
        ):
            self.assertIn(marker, process_proof)
        self.assertNotIn("cat \"${cmdline}\"", process_proof)

        missing_ack = subprocess.run(["sh", str(process_proof_path)], text=True, capture_output=True)
        self.assertEqual(missing_ack.returncode, 2)
        env = os.environ.copy()
        env["FINANCE_WF23_ORPHAN_PROCESS_PROOF_ACK"] = "PROVE_WF23_EXECUTOR_ABSENT_READ_ONLY"
        completed = subprocess.run(["sh", str(process_proof_path)], env=env, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        receipt = json.loads(completed.stdout.split(":", 1)[1])
        self.assertEqual(receipt["scope"], "WF23_PROCESS_LOCAL_ACTIVE_EXECUTIONS_ABSENCE")
        self.assertFalse(receipt["provider_calls"])
        self.assertFalse(receipt["secret_values_recorded"])

    def test_real_psql_gate_fixture_covers_absent_malformed_false_and_true(self) -> None:
        harness = (ROOT / "tests" / "fixtures" / "psql-wf23-exact-commit-gate-harness.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("BEGIN;", harness)
        self.assertIn("INSERT INTO wf23_commit_gate_probe VALUES (1);", harness)
        self.assertIn("\\ir psql-wf23-exact-commit-gate.sql", harness)
        self.assertIn("SELECT count(*) FROM wf23_commit_gate_probe;", harness)
        results = json.loads(PSQL_GATE_RESULTS.read_text(encoding="utf-8"))
        self.assertEqual(results["schema_version"], 1)
        self.assertRegex(results["psql_version"], r"^16\.14$")
        by_name = {case["name"]: case for case in results["cases"]}
        self.assertEqual(set(by_name), {"absent", "malformed", "false", "true"})
        for name in ("absent", "malformed", "false"):
            self.assertEqual(by_name[name]["outcome"], "ROLLBACK")
            self.assertEqual(by_name[name]["persisted_rows"], 0)
        self.assertEqual(by_name["true"]["value"], "on")
        self.assertEqual(by_name["true"]["outcome"], "COMMIT")
        self.assertEqual(by_name["true"]["persisted_rows"], 1)
        self.assertEqual(by_name["false"]["value"], "off")
        self.assertIsNone(by_name["absent"]["value"])

    def test_real_psql_gate_against_ephemeral_postgres_when_configured(self) -> None:
        psql = os.environ.get("WF23_TEST_PSQL")
        dsn = os.environ.get("WF23_TEST_POSTGRES_DSN")
        if not psql or not dsn:
            self.skipTest("set WF23_TEST_PSQL and WF23_TEST_POSTGRES_DSN for real psql integration")
        results = json.loads(PSQL_GATE_RESULTS.read_text(encoding="utf-8"))
        version = subprocess.run([psql, "--version"], text=True, capture_output=True, check=True)
        self.assertIn(f" {results['psql_version']} ", f" {version.stdout.strip()} ")
        for case in results["cases"]:
            command = [psql, dsn, "-XqAt", "-v", "ON_ERROR_STOP=1"]
            if case["value"] is not None:
                command.extend(["-v", f"commit_authorized={case['value']}"])
            command.extend(["-f", str(ROOT / "tests" / "fixtures" / "psql-wf23-exact-commit-gate-harness.sql")])
            completed = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), str(case["persisted_rows"]))

    def test_wf23_rehearsal_receipt_is_recent_exact_and_fail_closed(self) -> None:
        validator = load_module(
            "wf23_rehearsal_receipt_validator", RUNNER / "validate-wf23-rehearsal-receipt.py"
        )
        now = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)
        hashes = {
            "source": "1" * 64,
            "sql": "2" * 64,
            "workflow": "3" * 64,
            "credential": "4" * 64,
            "data_table": "5" * 64,
        }
        receipt = {
            "schema_version": 1,
            "status": "VERIFIED",
            "scope": "WF23_POSTGRESQL_ROLLBACK_REHEARSAL",
            "recorded_at_utc": "2026-08-20T11:50:00+00:00",
            "commits": {"finance": "a" * 40, "orchestrator": "b" * 40},
            "workflow_source_sha256": hashes["source"],
            "sql_sha256": hashes["sql"],
            "live_pre_state": {
                "project_id": "gT5rxq26L0PoNUWX",
                "workflow_id": "10000000-0000-4000-8000-000000000023",
                "execution_id": 15,
                "project_state": "22|0|0",
                "folder_placements": 22,
                "tag_edges": 66,
                "bad_tag_sets": 0,
                "setup_ids": 1,
                "wf23_workflows": 1,
                "wf23_executions": 1,
                "wf23_execution_data_rows": 1,
                "wf23_histories": 1,
                "workflow_corpus_sha256": hashes["workflow"],
                "credential_corpus_sha256": hashes["credential"],
                "finance_data_table_sha256": hashes["data_table"],
                "orphan_signature": "ORPHANED_SOFT_DELETED_EXECUTION",
            },
            "transaction_outcome": "ROLLED_BACK",
            "production_sql_body_completed": True,
            "post_state_unchanged": True,
            "services_healthy": True,
            "provider_calls": False,
            "secret_values_recorded": False,
        }

        def validate(value: dict, *, observed_now: dt.datetime = now) -> None:
            validator.validate_receipt(
                value,
                finance_commit="a" * 40,
                orchestrator_commit="b" * 40,
                source_sha256=hashes["source"],
                sql_sha256=hashes["sql"],
                workflow_corpus_sha256=hashes["workflow"],
                credential_corpus_sha256=hashes["credential"],
                data_table_sha256=hashes["data_table"],
                now=observed_now,
            )

        validate(receipt)
        corruptions = {
            "wrong SQL": lambda value: value.update(sql_sha256="6" * 64),
            "live drift": lambda value: value["live_pre_state"].update(tag_edges=67),
            "not rolled back": lambda value: value.update(transaction_outcome="COMMITTED"),
            "false postcondition": lambda value: value.update(post_state_unchanged=False),
            "provider call": lambda value: value.update(provider_calls=True),
            "bool count": lambda value: value["live_pre_state"].update(wf23_executions=True),
            "bool schema": lambda value: value.update(schema_version=True),
        }
        for label, corrupt in corruptions.items():
            with self.subTest(label=label):
                invalid = copy.deepcopy(receipt)
                corrupt(invalid)
                with self.assertRaises(ValueError):
                    validate(invalid)
        with self.assertRaisesRegex(ValueError, "TIMESTAMP_NOT_RECENT"):
            validate(receipt, observed_now=now + dt.timedelta(minutes=16))

        validator_path = RUNNER / "validate-wf23-rehearsal-receipt.py"
        with tempfile.TemporaryDirectory() as temporary:
            duplicate = Path(temporary) / "duplicate.json"
            duplicate.write_text(
                '{"status":"BOGUS","status":"VERIFIED","token":"SECRET_ACCESS_TOKENZ"}',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(validator_path), str(duplicate), *("a" * 40 for _ in range(2)), *("1" * 64 for _ in range(5))],
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr.strip(), "WF23_REHEARSAL_RECEIPT_REJECTED")
        self.assertNotIn("SECRET_ACCESS_TOKENZ", completed.stdout + completed.stderr)

    def test_wf23_canonical_projection_rejects_every_reviewed_surface_drift(self) -> None:
        canonicalizer = load_module(
            "wf23_source_canonicalizer", RUNNER / "canonicalize-wf23-source.py"
        )
        source = json.loads(SOURCE.read_text(encoding="utf-8"))
        expected_workflow = canonicalizer.workflow_projection(source)
        version_id = "12345678-1234-4123-8123-123456789abc"
        expected_history = canonicalizer.history_projection(source, version_id)
        self.assertEqual(len(expected_workflow["nodes"]), 7)
        self.assertEqual(
            set(expected_workflow),
            {
                "id", "name", "description", "active", "nodes", "connections", "settings", "pinData", "meta",
                "nodeGroups", "isArchived", "staticData", "sourceWorkflowId", "triggerCount", "activeVersionId",
                "parentFolderId",
            },
        )

        bound = copy.deepcopy(source)
        for node in bound["nodes"]:
            if node["type"] == "n8n-nodes-base.microsoftOutlook":
                node["credentials"]["microsoftOutlookOAuth2Api"]["id"] = "runtimeOutlookCredential"
            elif node["type"] == "n8n-nodes-base.microsoftOneDrive":
                node["credentials"]["microsoftOneDriveOAuth2Api"]["id"] = "runtimeOneDriveCredential"
        for binding in bound["meta"]["credentialBindings"]:
            binding.update(configured=True, action_required=False)

        def normalize_runtime(value: dict) -> dict:
            normalized = copy.deepcopy(value)
            for node in normalized["nodes"]:
                if node["type"] == "n8n-nodes-base.microsoftOutlook":
                    node["credentials"]["microsoftOutlookOAuth2Api"]["id"] = "BIND_OUTLOOK"
                elif node["type"] == "n8n-nodes-base.microsoftOneDrive":
                    node["credentials"]["microsoftOneDriveOAuth2Api"]["id"] = "BIND_ONEDRIVE"
            bindings = normalized["meta"]["credentialBindings"]
            if len(bindings) != 2:
                raise ValueError("BINDER_ANNOTATION_MISMATCH")
            for binding in bindings:
                if (
                    binding.get("configured") is not True
                    or binding.get("action_required") is not False
                    or "credential_id" in binding
                ):
                    raise ValueError("BINDER_ANNOTATION_MISMATCH")
                binding.pop("configured")
                binding.pop("action_required")
            return canonicalizer.workflow_projection(normalized)

        self.assertEqual(normalize_runtime(bound), expected_workflow)
        for label, mutate in {
            "extra node": lambda value: value["nodes"].append({"id": "evil", "type": "n8n-nodes-base.noOp"}),
            "extra connection": lambda value: value["connections"].update({"Unexpected": {"main": []}}),
            "extra meta": lambda value: value["meta"].update({"unexpected": True}),
            "extra setting": lambda value: value["settings"].update({"unexpected": True}),
        }.items():
            with self.subTest(label=label):
                drifted = copy.deepcopy(bound)
                mutate(drifted)
                self.assertNotEqual(normalize_runtime(drifted), expected_workflow)

        for label, mutate in {
            "wrong configured value": lambda value: value["meta"]["credentialBindings"][0].update(configured=False),
            "missing action annotation": lambda value: value["meta"]["credentialBindings"][0].pop("action_required"),
            "credential id retained": lambda value: value["meta"]["credentialBindings"][0].update(credential_id="forbidden"),
        }.items():
            with self.subTest(label=label):
                invalid = copy.deepcopy(bound)
                mutate(invalid)
                with self.assertRaisesRegex(ValueError, "BINDER_ANNOTATION_MISMATCH"):
                    normalize_runtime(invalid)

        history_from_source = {
            **expected_history,
            "nodes": copy.deepcopy(bound["nodes"]),
        }
        for node in history_from_source["nodes"]:
            if node["type"] == "n8n-nodes-base.microsoftOutlook":
                node["credentials"]["microsoftOutlookOAuth2Api"]["id"] = "BIND_OUTLOOK"
            elif node["type"] == "n8n-nodes-base.microsoftOneDrive":
                node["credentials"]["microsoftOneDriveOAuth2Api"]["id"] = "BIND_ONEDRIVE"
        self.assertEqual(history_from_source, expected_history)

    def test_wf23_history_projection_matches_n8n_2362_import_semantics(self) -> None:
        canonicalizer = load_module(
            "wf23_import_semantics_canonicalizer", RUNNER / "canonicalize-wf23-source.py"
        )
        source = json.loads(SOURCE.read_text(encoding="utf-8"))
        version_id = "12345678-1234-4123-8123-123456789abc"
        history = canonicalizer.history_projection(source, version_id)
        self.assertNotIn("versionMetadata", source)
        self.assertEqual(history["versionId"], version_id)
        self.assertEqual(history["workflowId"], source["id"])
        self.assertIsNone(history["name"])
        self.assertNotEqual(history["name"], source["name"])
        self.assertIsNone(history["description"])
        self.assertEqual(history["authors"], "import")
        self.assertFalse(history["autosaved"])
        self.assertEqual(history["nodes"], source["nodes"])
        self.assertEqual(history["connections"], source["connections"])
        self.assertEqual(history["nodeGroups"], source["nodeGroups"])

        with_version_metadata = copy.deepcopy(source)
        with_version_metadata["versionMetadata"] = {"name": "fabricated"}
        with self.assertRaisesRegex(ValueError, "WF23_SOURCE_VERSION_METADATA_UNEXPECTED"):
            canonicalizer.history_projection(with_version_metadata, version_id)

    def test_transaction_rollback_model_rejects_all_predelete_and_delete_faults(self) -> None:
        workflow_id = "10000000-0000-4000-8000-000000000023"
        execution_id = 15
        canonicalizer = load_module(
            "wf23_transaction_model_canonicalizer", RUNNER / "canonicalize-wf23-source.py"
        )
        source = json.loads(SOURCE.read_text(encoding="utf-8"))
        expected = canonicalizer.canonical_json(canonicalizer.workflow_projection(source))

        def create_database() -> sqlite3.Connection:
            connection = sqlite3.connect(":memory:")
            connection.executescript(
                """
                CREATE TABLE workflow (id TEXT PRIMARY KEY, canonical TEXT NOT NULL);
                CREATE TABLE history (workflow_id TEXT PRIMARY KEY);
                CREATE TABLE execution (id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL, status TEXT NOT NULL, finished INTEGER NOT NULL, deleted INTEGER NOT NULL);
                CREATE TABLE share (workflow_id TEXT PRIMARY KEY);
                CREATE TABLE tag_edge (workflow_id TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(workflow_id, tag));
                CREATE TABLE dependency_ref (workflow_id TEXT NOT NULL);
                """
            )
            connection.execute("INSERT INTO workflow VALUES (?,?)", (workflow_id, expected))
            connection.execute("INSERT INTO history VALUES (?)", (workflow_id,))
            connection.execute("INSERT INTO execution VALUES (?,?,?,?,?)", (execution_id, workflow_id, "running", 0, 1))
            connection.execute("INSERT INTO share VALUES (?)", (workflow_id,))
            connection.executemany(
                "INSERT INTO tag_edge VALUES (?,?)",
                [(workflow_id, tag) for tag in ("finance", "inactive", "setup-required")],
            )
            connection.commit()
            return connection

        def snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
            return {
                table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall())
                for table in ("workflow", "history", "execution", "share", "tag_edge", "dependency_ref")
            }

        def remediate(connection: sqlite3.Connection, *, inject_delete_count_fault: bool = False) -> None:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT canonical FROM workflow WHERE id=?", (workflow_id,)).fetchone()
                if row != (expected,):
                    raise RuntimeError("CANONICAL_MISMATCH")
                if connection.execute("SELECT count(*) FROM history WHERE workflow_id=?", (workflow_id,)).fetchone() != (1,):
                    raise RuntimeError("HISTORY_MISMATCH")
                if connection.execute(
                    "SELECT count(*) FROM execution WHERE id=? AND workflow_id=? AND status='running' AND finished=0 AND deleted=1",
                    (execution_id, workflow_id),
                ).fetchone() != (1,):
                    raise RuntimeError("EXECUTION_MISMATCH")
                tags = connection.execute("SELECT tag FROM tag_edge WHERE workflow_id=? ORDER BY tag", (workflow_id,)).fetchall()
                if tags != [("finance",), ("inactive",), ("setup-required",)]:
                    raise RuntimeError("TAG_MISMATCH")
                if connection.execute("SELECT count(*) FROM dependency_ref WHERE workflow_id=?", (workflow_id,)).fetchone() != (0,):
                    raise RuntimeError("UNEXPECTED_REFERENCE")

                if inject_delete_count_fault:
                    connection.execute("DELETE FROM history WHERE workflow_id=?", (workflow_id,))
                for table, expected_count in (
                    ("execution", 1), ("history", 1), ("tag_edge", 3), ("share", 1), ("workflow", 1)
                ):
                    column = "id" if table == "workflow" else "workflow_id"
                    value = workflow_id
                    cursor = connection.execute(f"DELETE FROM {table} WHERE {column}=?", (value,))
                    if cursor.rowcount != expected_count:
                        raise RuntimeError("DELETE_COUNT_MISMATCH")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        exact = create_database()
        remediate(exact)
        self.assertEqual(snapshot(exact), {table: [] for table in snapshot(exact)})

        for label, corrupt in {
            "source drift": lambda connection: connection.execute("UPDATE workflow SET canonical='{}'"),
            "unexpected reference": lambda connection: connection.execute("INSERT INTO dependency_ref VALUES (?)", (workflow_id,)),
            "extra tag": lambda connection: connection.execute(
                "INSERT INTO tag_edge VALUES (?,?)", (workflow_id, "unexpected")
            ),
        }.items():
            with self.subTest(label=label):
                connection = create_database()
                corrupt(connection)
                connection.commit()
                before = snapshot(connection)
                with self.assertRaises(RuntimeError):
                    remediate(connection)
                self.assertEqual(snapshot(connection), before)

        delete_fault = create_database()
        before = snapshot(delete_fault)
        with self.assertRaisesRegex(RuntimeError, "DELETE_COUNT_MISMATCH"):
            remediate(delete_fault, inject_delete_count_fault=True)
        self.assertEqual(snapshot(delete_fault), before)

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
            "docker compose restart n8n",
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
            'internal_runner_broker_port="15679"',
            "internal_runner_port_preflight",
            "WF23 dedicated internal task-runner broker port unavailable",
            'N8N_RUNNERS_BROKER_PORT="${internal_runner_broker_port}"',
            "N8N_RUNNERS_BROKER_LISTEN_ADDRESS=127.0.0.1",
            'server.listen(port,"127.0.0.1"',
            '"${n8n_container}" node - < "${runner_dir}/n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs"',
            "/dev/shm/",
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
        ):
            self.assertNotIn(forbidden, runner)
        transport_position = runner.index("direct_transport_probe ||")
        runner_port_position = runner.index("internal_runner_port_preflight ||")
        self.assertLess(transport_position, runner_port_position)
        self.assertLess(runner_port_position, runner.index('data_table_digest_before="$(data_table_digest)"'))
        self.assertLess(runner_port_position, runner.index('metadata_before="$(read_metadata)"'))
        self.assertLess(runner_port_position, runner.index('failure_stage="workflow_import"'))
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
