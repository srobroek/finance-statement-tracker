from __future__ import annotations

import json
import importlib.util
import os
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
        shim = (RUNNER / "n8n-cli-redacted-microsoft-oauth-refresh-proof.cjs").read_text(encoding="utf-8")
        for marker in (
            "captureRawIRunInMemory",
            "EXECUTIONS_DATA_SAVE_ON_SUCCESS",
            "EXECUTIONS_DATA_SAVE_ON_ERROR",
            "EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS",
            "fs.writeSync(1",
            "payload = null",
        ):
            self.assertIn(marker, shim)
        self.assertNotIn("writeFile", shim)

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
            "--rawOutput",
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
            "execution_rows_zero_verified",
            "data_table_digest_restored",
            'raw_irun_persisted":False',
            'finance_data_table_writes":False',
            'baseline_digest_restored":True',
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
        ):
            self.assertNotIn(forbidden, runner)

    def test_all_runner_sources_are_syntactically_valid(self) -> None:
        subprocess.run(["bash", "-n", str(RUNNER / "run-transient-microsoft-oauth-refresh-proof.sh")], check=True)
        for source in RUNNER.glob("*.py"):
            subprocess.run([sys.executable, "-m", "py_compile", str(source)], check=True)
        for source in RUNNER.glob("*.cjs"):
            subprocess.run(["node", "--check", str(source)], check=True)


if __name__ == "__main__":
    unittest.main()
