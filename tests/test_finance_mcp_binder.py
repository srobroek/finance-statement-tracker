from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BINDER_PATH = ROOT / "deploy" / "finance-runtime" / "bind-finance-mcp-facade.py"
WORKFLOW = ROOT / "integrations" / "n8n" / "workflows" / "15-finance-mcp-facade.json"


def load_binder():
    spec = importlib.util.spec_from_file_location("finance_mcp_binder", BINDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def executable(path: Path, source: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(source), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class FinanceMcpBinderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binder = load_binder()

    def test_uuid5_identity_is_stable_and_internal(self):
        first = self.binder.deterministic_credential_id()
        second = self.binder.deterministic_credential_id()
        self.assertEqual(first, second)
        self.assertNotIn(first, json.dumps({"status": "VERIFIED", "ids": "REDACTED"}))

    def test_export_is_one_encrypted_bearer_shape(self):
        export = self.binder.credential_export("unit-test-secret")
        self.assertEqual(len(export), 1)
        self.assertEqual(export[0]["type"], "httpBearerAuth")
        self.assertEqual(export[0]["name"], "Finance MCP Facade Bearer")
        self.assertEqual(export[0]["data"], {"token": "unit-test-secret"})

    def test_bound_workflow_changes_only_placeholder(self):
        credential_id = self.binder.deterministic_credential_id()
        original = json.loads(WORKFLOW.read_text())
        bound = self.binder.bound_workflow(WORKFLOW, credential_id)
        original["nodes"][0]["credentials"]["httpBearerAuth"]["id"] = credential_id
        self.assertEqual(json.dumps(bound, sort_keys=True), json.dumps(original, sort_keys=True))
        self.assertFalse(bound["active"])
        self.assertIsNone(bound.get("activeVersionId"))

    def test_private_file_writer_uses_mode_0600_and_exclusive_create(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "credential.json"
            self.binder._write_private(target, b"redacted-test-payload")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                self.binder._write_private(target, b"second-payload")

    def test_secret_is_required_from_runtime_environment(self):
        old = os.environ.pop(self.binder.ENVIRONMENT_NAME, None)
        try:
            with self.assertRaises(self.binder.ContractError):
                self.binder._secret(os.environ.get(self.binder.ENVIRONMENT_NAME))
        finally:
            if old is not None:
                os.environ[self.binder.ENVIRONMENT_NAME] = old

    def test_fake_n8n_state_machine_proves_noop_replay_and_readbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binder_root = root / "binder"
            binder_root.mkdir(mode=0o700)
            state = root / "state.json"
            imports = root / "imports.log"
            fake_findmnt = executable(root / "findmnt", """\nprint('tmpfs')\n""")
            fake_umount = executable(root / "umount", """
                import os
                raise SystemExit(1 if os.environ.get('FAIL_UMOUNT') == 'true' else 0)
                """)
            fake_n8n = executable(
                root / "n8n",
                """
                import json, os, pathlib, sys
                state_path = pathlib.Path(os.environ['STATE'])
                state = json.loads(state_path.read_text()) if state_path.exists() else {'credential': False, 'workflow': False, 'owner': False, 'binding': False}
                if sys.argv[1:] == ['--version']:
                    print('2.36.2')
                    raise SystemExit(0)
                if sys.argv[1] not in ('import:credentials', 'import:workflow'):
                    raise SystemExit(2)
                with pathlib.Path(os.environ['IMPORTS']).open('a', encoding='utf-8') as stream:
                    stream.write(' '.join(sys.argv[1:]) + '\\n')
                if sys.argv[1] == 'import:credentials': state['credential'] = True
                if sys.argv[1] == 'import:workflow': state['workflow'] = True; state['owner'] = True
                state_path.write_text(json.dumps(state))
                raise SystemExit(0)
                """,
            )
            metadata_reader = executable(
                root / "metadata-reader",
                """
                import json, os, pathlib
                state_path = pathlib.Path(os.environ['STATE'])
                state = json.loads(state_path.read_text()) if state_path.exists() else {'credential': False, 'workflow': False, 'owner': False, 'binding': False}
                present = state['credential'] and state['workflow'] and state['owner']
                payload = {
                    'name': 'Finance MCP Facade Bearer' if present else None,
                    'type': 'httpBearerAuth' if present else None,
                    'projectId': os.environ['FINANCE_MCP_METADATA_PROJECT'] if present else None,
                    'ownerRole': 'credential:owner' if present else None,
                    'workflowPath': 'finance-operations-v1' if present else None,
                    'credentialPresent': present,
                    'workflowPresent': present,
                    'active': False,
                    'activeVersionId': None,
                    'published': False,
                    'ciphertextPlaintextEqual': False if present else False,
                    'decryptUseVerified': state['binding'] if present else False,
                    'secretValueRecorded': False,
                    'idsRecorded': False,
                    'credentialId': 'REDACTED' if present else None,
                    'workflowId': 'REDACTED' if present else None,
                    'counts': {'credentials': 1 if present else 0, 'owners': 1 if present else 0, 'workflows': 1 if present else 0, 'webhooks': 0, 'executions': 0},
                }
                pathlib.Path(os.environ['FINANCE_MCP_METADATA_OUTPUT']).write_text(json.dumps(payload))
                """,
            )
            challenge = executable(
                root / "challenge",
                """
                import json, pathlib, os
                state_path = pathlib.Path(os.environ['STATE'])
                state = json.loads(state_path.read_text())
                state['binding'] = True
                state_path.write_text(json.dumps(state))
                pathlib.Path(os.environ['FINANCE_MCP_CHALLENGE_OUTPUT']).write_text(json.dumps({'authenticatedRequest': True, 'decryptUseVerified': True, 'secretValueRecorded': False}))
                """,
            )
            path_env = f"{root}:{os.environ['PATH']}"
            environment = {
                **os.environ,
                self.binder.ENVIRONMENT_NAME: "unit-test-runtime-value",
                "FINANCE_MCP_SIMULATED": "true",
                "FINANCE_MCP_BINDER_MOUNT": str(binder_root),
                "IMPORTS": str(imports),
                "STATE": str(state),
                "FINANCE_MCP_METADATA_READER": str(metadata_reader),
                "FINANCE_MCP_DECRYPT_USE_CHALLENGE": str(challenge),
                "N8N_FINANCE_PROJECT_ID": "finance-project",
                "PATH": path_env,
            }
            first_output = io.StringIO()
            with patch.dict(os.environ, environment, clear=True):
                with redirect_stdout(first_output):
                    first = self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(fake_n8n), "--binder-root", str(binder_root)])
            self.assertEqual(first, 0)
            first_receipt = json.loads(first_output.getvalue())
            self.assertEqual(first_receipt["status"], "SIMULATED")
            self.assertFalse(first_receipt["runtimeEvidence"])
            self.assertEqual(first_receipt["scope"], "W15_SPEC_ONLY")
            first_imports = imports.read_text().splitlines()
            self.assertEqual(len(first_imports), 2)
            self.assertTrue(all("--projectId=finance-project" in line for line in first_imports))
            self.assertTrue(any("--activeState=false" in line for line in first_imports))
            self.assertFalse(any("unit-test-runtime-value" in line for line in first_imports))
            second_output = io.StringIO()
            with patch.dict(os.environ, environment, clear=True):
                with redirect_stdout(second_output):
                    second = self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(fake_n8n), "--binder-root", str(binder_root)])
            self.assertEqual(second, 0)
            second_receipt = json.loads(second_output.getvalue())
            self.assertEqual(second_receipt["status"], "SIMULATED")
            self.assertFalse(second_receipt["runtimeEvidence"])
            self.assertEqual(second_receipt["scope"], "W15_SPEC_ONLY")
            self.assertEqual(len(imports.read_text().splitlines()), 2)
            self.assertEqual(json.loads(state.read_text()), {"credential": True, "workflow": True, "owner": True, "binding": True})
            self.assertEqual(list(binder_root.iterdir()), [])
            self.assertTrue(fake_findmnt.exists() and fake_umount.exists())

            failed_unmount_output = io.StringIO()
            with patch.dict(os.environ, {**environment, "FAIL_UMOUNT": "true"}, clear=True):
                with redirect_stdout(failed_unmount_output):
                    with self.assertRaises(self.binder.ContractError):
                        self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(fake_n8n), "--binder-root", str(binder_root)])
            self.assertEqual(failed_unmount_output.getvalue(), "")

    def test_fake_state_machine_cleans_zero_and_recreates_after_challenge_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binder_root = root / "binder"
            binder_root.mkdir(mode=0o700)
            state = root / "state.json"
            imports = root / "imports.log"
            challenge_count = root / "challenge-count"
            readback_states = root / "readback-states.log"
            fake_findmnt = executable(root / "findmnt", """\nprint('tmpfs')\n""")
            executable(root / "umount", """\nraise SystemExit(0)\n""")
            executable(root / "n8n", """
                import json, os, pathlib, sys
                state_path = pathlib.Path(os.environ['STATE'])
                state = json.loads(state_path.read_text()) if state_path.exists() else {'credential': False, 'workflow': False, 'owner': False, 'binding': False}
                if sys.argv[1:] == ['--version']:
                    print('2.36.2'); raise SystemExit(0)
                with pathlib.Path(os.environ['IMPORTS']).open('a') as stream:
                    stream.write(' '.join(sys.argv[1:]) + '\\n')
                if sys.argv[1] == 'import:credentials': state['credential'] = True
                if sys.argv[1] == 'import:workflow': state['workflow'] = True; state['owner'] = True
                state_path.write_text(json.dumps(state))
                """)
            executable(root / "metadata-reader", """
                import json, os, pathlib
                state_path = pathlib.Path(os.environ['STATE'])
                state = json.loads(state_path.read_text()) if state_path.exists() else {'credential': False, 'workflow': False, 'owner': False, 'binding': False}
                present = state['credential'] and state['workflow'] and state['owner']
                with pathlib.Path(os.environ['READBACK_STATES']).open('a') as stream:
                    stream.write(json.dumps(state) + '\\n')
                payload = {'name': 'Finance MCP Facade Bearer' if present else None, 'type': 'httpBearerAuth' if present else None, 'projectId': os.environ['FINANCE_MCP_METADATA_PROJECT'] if present else None, 'ownerRole': 'credential:owner' if present else None, 'workflowPath': 'finance-operations-v1' if present else None, 'credentialPresent': present, 'workflowPresent': present, 'active': False, 'activeVersionId': None, 'published': False, 'ciphertextPlaintextEqual': False, 'decryptUseVerified': state['binding'] if present else False, 'secretValueRecorded': False, 'idsRecorded': False, 'credentialId': 'REDACTED' if present else None, 'workflowId': 'REDACTED' if present else None, 'counts': {'credentials': 1 if present else 0, 'owners': 1 if present else 0, 'workflows': 1 if present else 0, 'webhooks': 0, 'executions': 0}}
                pathlib.Path(os.environ['FINANCE_MCP_METADATA_OUTPUT']).write_text(json.dumps(payload))
                """)
            executable(root / "challenge", """
                import json, os, pathlib
                state_path = pathlib.Path(os.environ['STATE'])
                count = int(pathlib.Path(os.environ['CHALLENGE_COUNT']).read_text() or '0') if pathlib.Path(os.environ['CHALLENGE_COUNT']).exists() else 0
                pathlib.Path(os.environ['CHALLENGE_COUNT']).write_text(str(count + 1))
                if count == 0:
                    pathlib.Path(os.environ['FINANCE_MCP_CHALLENGE_OUTPUT']).write_text(json.dumps({'authenticatedRequest': False, 'decryptUseVerified': False, 'secretValueRecorded': False}))
                    raise SystemExit(7)
                state = json.loads(state_path.read_text())
                state['binding'] = True
                state_path.write_text(json.dumps(state))
                pathlib.Path(os.environ['FINANCE_MCP_CHALLENGE_OUTPUT']).write_text(json.dumps({'authenticatedRequest': True, 'decryptUseVerified': True, 'secretValueRecorded': False}))
                """)
            executable(root / "cleanup", """
                import json, os, pathlib
                if os.environ.get('LIE_CLEANUP') != 'true':
                    pathlib.Path(os.environ['STATE']).write_text(json.dumps({'credential': False, 'workflow': False, 'owner': False, 'binding': False}))
                pathlib.Path(os.environ['CLEANUP_MARKER']).write_text(os.environ['FINANCE_MCP_CLEANUP_ACK'])
                pathlib.Path(os.environ['FINANCE_MCP_CLEANUP_OUTPUT']).write_text(json.dumps({'cleanupVerified': True, 'counts': {'credentials': 0, 'owners': 0, 'workflows': 0, 'webhooks': 0, 'executions': 0}, 'idsRecorded': False, 'secretValueRecorded': False}))
                """)
            environment = {
                self.binder.ENVIRONMENT_NAME: "unit-test-runtime-value",
                "FINANCE_MCP_SIMULATED": "true",
                "FINANCE_MCP_BINDER_MOUNT": str(binder_root),
                "IMPORTS": str(imports),
                "STATE": str(state),
                "CHALLENGE_COUNT": str(challenge_count),
                "READBACK_STATES": str(readback_states),
                "CLEANUP_MARKER": str(root / "cleanup.marker"),
                "FINANCE_MCP_METADATA_READER": str(root / "metadata-reader"),
                "FINANCE_MCP_DECRYPT_USE_CHALLENGE": str(root / "challenge"),
                "FINANCE_MCP_DISPOSABLE_CLEANUP": str(root / "cleanup"),
                "N8N_FINANCE_PROJECT_ID": "finance-project",
                "PATH": f"{root}:{os.environ['PATH']}",
            }
            output = io.StringIO()
            with patch.dict(os.environ, environment, clear=True):
                with redirect_stdout(output):
                    result = self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(root / "n8n"), "--binder-root", str(binder_root)])
            self.assertEqual(result, 0)
            receipt = json.loads(output.getvalue())
            self.assertEqual(receipt["status"], "SIMULATED")
            self.assertFalse(receipt["runtimeEvidence"])
            self.assertEqual(receipt["scope"], "W15_SPEC_ONLY")
            self.assertEqual(len(imports.read_text().splitlines()), 4)
            self.assertEqual((root / "cleanup.marker").read_text(), "REMOVE_W15_FINANCE_MCP_ONLY")
            self.assertEqual(json.loads(state.read_text()), {"credential": True, "workflow": True, "owner": True, "binding": True})
            self.assertEqual(
                [json.loads(line) for line in readback_states.read_text().splitlines()],
                [
                    {"credential": False, "workflow": False, "owner": False, "binding": False},
                    {"credential": True, "workflow": True, "owner": True, "binding": False},
                    {"credential": False, "workflow": False, "owner": False, "binding": False},
                    {"credential": True, "workflow": True, "owner": True, "binding": False},
                    {"credential": True, "workflow": True, "owner": True, "binding": True},
                ],
            )
            self.assertEqual(list(binder_root.iterdir()), [])
            self.assertTrue(fake_findmnt.exists())

            state.write_text(json.dumps({"credential": False, "workflow": False, "owner": False, "binding": False}))
            imports.unlink()
            challenge_count.unlink()
            readback_states.unlink()
            lying_environment = {**environment, "LIE_CLEANUP": "true"}
            failed_output = io.StringIO()
            with patch.dict(os.environ, lying_environment, clear=True):
                with redirect_stdout(failed_output):
                    with self.assertRaises(self.binder.ContractError):
                        self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(root / "n8n"), "--binder-root", str(binder_root)])
            self.assertEqual(failed_output.getvalue(), "")
            self.assertEqual(json.loads(state.read_text()), {"credential": True, "workflow": True, "owner": True, "binding": False})

    def test_partial_import_and_readback_failures_cleans_and_verifies_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binder_root = root / "binder"
            binder_root.mkdir(mode=0o700)
            state = root / "state.json"
            imports = root / "imports.log"
            readback_count = root / "readback-count"
            readback_states = root / "readback-states.log"
            executable(root / "findmnt", """
                print('tmpfs')
                """)
            executable(root / "umount", """
                raise SystemExit(0)
                """)
            executable(root / "n8n", """
                import json, os, pathlib, sys
                if sys.argv[1:] == ['--version']:
                    print('2.36.2'); raise SystemExit(0)
                state_path = pathlib.Path(os.environ['STATE'])
                state = json.loads(state_path.read_text()) if state_path.exists() else {'credential': False, 'workflow': False, 'owner': False, 'binding': False}
                with pathlib.Path(os.environ['IMPORTS']).open('a') as stream:
                    stream.write(' '.join(sys.argv[1:]) + '\\n')
                if sys.argv[1] == 'import:credentials':
                    state['credential'] = True
                if sys.argv[1] == 'import:workflow':
                    state['workflow'] = True
                    state['owner'] = True
                if sys.argv[1] == os.environ['FAIL_STAGE']:
                    state_path.write_text(json.dumps(state))
                    raise SystemExit(9)
                state_path.write_text(json.dumps(state))
                """)
            executable(root / "metadata-reader", """
                import json, os, pathlib
                state_path = pathlib.Path(os.environ['STATE'])
                state = json.loads(state_path.read_text()) if state_path.exists() else {'credential': False, 'workflow': False, 'owner': False, 'binding': False}
                count_path = pathlib.Path(os.environ['READBACK_COUNT'])
                count = int(count_path.read_text() or '0') if count_path.exists() else 0
                count_path.write_text(str(count + 1))
                with pathlib.Path(os.environ['READBACK_STATES']).open('a') as stream:
                    stream.write(json.dumps(state) + '\\n')
                if os.environ.get('MALFORMED_READBACK') == 'true' and count == 1:
                    pathlib.Path(os.environ['FINANCE_MCP_METADATA_OUTPUT']).write_text('{')
                    raise SystemExit(0)
                present = state['credential'] and state['workflow'] and state['owner']
                payload = {'name': 'Finance MCP Facade Bearer' if present else None, 'type': 'httpBearerAuth' if present else None, 'projectId': os.environ['FINANCE_MCP_METADATA_PROJECT'] if present else None, 'ownerRole': 'credential:owner' if present else None, 'workflowPath': 'finance-operations-v1' if present else None, 'credentialPresent': present, 'workflowPresent': present, 'active': False, 'activeVersionId': None, 'published': False, 'ciphertextPlaintextEqual': False if present else False, 'decryptUseVerified': state['binding'] if present else False, 'secretValueRecorded': False, 'idsRecorded': False, 'credentialId': 'REDACTED' if present else None, 'workflowId': 'REDACTED' if present else None, 'counts': {'credentials': 1 if present else 0, 'owners': 1 if present else 0, 'workflows': 1 if present else 0, 'webhooks': 0, 'executions': 0}}
                pathlib.Path(os.environ['FINANCE_MCP_METADATA_OUTPUT']).write_text(json.dumps(payload))
                """)
            executable(root / "challenge", """
                import os, pathlib
                if os.environ.get('INVALID_CHALLENGE') == 'true':
                    pathlib.Path(os.environ['FINANCE_MCP_CHALLENGE_OUTPUT']).write_bytes(b'\\xff')
                    raise SystemExit(0)
                raise SystemExit(2)
                """)
            executable(root / "cleanup", """
                import json, os, pathlib
                pathlib.Path(os.environ['STATE']).write_text(json.dumps({'credential': False, 'workflow': False, 'owner': False, 'binding': False}))
                pathlib.Path(os.environ['CLEANUP_MARKER']).write_text(os.environ['FINANCE_MCP_CLEANUP_ACK'])
                pathlib.Path(os.environ['FINANCE_MCP_CLEANUP_OUTPUT']).write_text(json.dumps({'cleanupVerified': True, 'counts': {'credentials': 0, 'owners': 0, 'workflows': 0, 'webhooks': 0, 'executions': 0}, 'idsRecorded': False, 'secretValueRecorded': False}))
                """)
            environment = {
                self.binder.ENVIRONMENT_NAME: "unit-test-runtime-value",
                "FINANCE_MCP_BINDER_MOUNT": str(binder_root),
                "STATE": str(state),
                "IMPORTS": str(imports),
                "READBACK_COUNT": str(readback_count),
                "READBACK_STATES": str(readback_states),
                "FAIL_STAGE": "import:workflow",
                "CLEANUP_MARKER": str(root / "cleanup.marker"),
                "FINANCE_MCP_METADATA_READER": str(root / "metadata-reader"),
                "FINANCE_MCP_DECRYPT_USE_CHALLENGE": str(root / "challenge"),
                "FINANCE_MCP_DISPOSABLE_CLEANUP": str(root / "cleanup"),
                "N8N_FINANCE_PROJECT_ID": "finance-project",
                "PATH": f"{root}:{os.environ['PATH']}",
            }
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(self.binder.ContractError):
                    self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(root / "n8n"), "--binder-root", str(binder_root)])
            self.assertEqual(len(imports.read_text().splitlines()), 2)
            self.assertEqual(json.loads(state.read_text()), {"credential": False, "workflow": False, "owner": False, "binding": False})

            state.write_text(json.dumps({"credential": False, "workflow": False, "owner": False, "binding": False}))
            imports.unlink()
            readback_count.unlink()
            readback_states.unlink()
            invalid_challenge_environment = {**environment, "FAIL_STAGE": "", "INVALID_CHALLENGE": "true"}
            invalid_challenge_output = io.StringIO()
            with patch.dict(os.environ, invalid_challenge_environment, clear=True):
                with redirect_stdout(invalid_challenge_output):
                    with self.assertRaises(self.binder.ContractError):
                        self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(root / "n8n"), "--binder-root", str(binder_root)])
            self.assertEqual(len(imports.read_text().splitlines()), 4)
            self.assertEqual((root / "cleanup.marker").read_text(), "REMOVE_W15_FINANCE_MCP_ONLY")
            self.assertEqual(readback_count.read_text(), "5")
            self.assertEqual(
                [json.loads(line) for line in readback_states.read_text().splitlines()],
                [
                    {"credential": False, "workflow": False, "owner": False, "binding": False},
                    {"credential": True, "workflow": True, "owner": True, "binding": False},
                    {"credential": False, "workflow": False, "owner": False, "binding": False},
                    {"credential": True, "workflow": True, "owner": True, "binding": False},
                    {"credential": False, "workflow": False, "owner": False, "binding": False},
                ],
            )
            self.assertEqual(json.loads(state.read_text()), {"credential": False, "workflow": False, "owner": False, "binding": False})
            self.assertEqual(invalid_challenge_output.getvalue(), "")

            state.write_text(json.dumps({"credential": False, "workflow": False, "owner": False, "binding": False}))
            imports.unlink()
            readback_count.unlink()
            credential_environment = {**environment, "FAIL_STAGE": "import:credentials"}
            with patch.dict(os.environ, credential_environment, clear=True):
                with self.assertRaises(self.binder.ContractError):
                    self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(root / "n8n"), "--binder-root", str(binder_root)])
            self.assertEqual(imports.read_text().splitlines(), ["import:credentials --input=" + str(binder_root / "finance-mcp-facade-credential.json") + " --projectId=finance-project"])
            self.assertEqual(json.loads(state.read_text()), {"credential": False, "workflow": False, "owner": False, "binding": False})

            state.write_text(json.dumps({"credential": False, "workflow": False, "owner": False, "binding": False}))
            imports.unlink()
            readback_count.unlink()
            malformed_environment = {**environment, "FAIL_STAGE": "", "MALFORMED_READBACK": "true"}
            with patch.dict(os.environ, malformed_environment, clear=True):
                with self.assertRaises(self.binder.ContractError):
                    self.binder.main(["--workflow", str(WORKFLOW), "--n8n", str(root / "n8n"), "--binder-root", str(binder_root)])
            self.assertEqual(len(imports.read_text().splitlines()), 2)
            self.assertEqual(json.loads(state.read_text()), {"credential": False, "workflow": False, "owner": False, "binding": False})

    def test_wrong_secret_and_newline_fail_closed(self):
        for value in ("", "secret\nvalue"):
            with self.assertRaises(self.binder.ContractError):
                self.binder._secret(value)


if __name__ == "__main__":
    unittest.main()
