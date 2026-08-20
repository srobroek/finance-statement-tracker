from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "deploy" / "finance-runtime" / "run-finance-mcp-disposable-proof.sh"


def executable(path: Path, source: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(source), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def build_state_machine(
    root: Path,
    *,
    fail_activation: bool = False,
    slow_activation: bool = False,
    fail_cleanup: bool = False,
):
    actions = root / "actions.log"
    state = root / "n8n-state.json"
    mutation = executable(
        root / "mutation-gate",
        f"""
        import json, os, pathlib, time
        action = os.environ['FINANCE_MCP_ACTION']
        state_path = pathlib.Path(r'{state}')
        state = json.loads(state_path.read_text()) if state_path.exists() else {{
            'active': False, 'published': False, 'webhook': False, 'rows': False
        }}
        with pathlib.Path(r'{actions}').open('a') as stream:
            stream.write(action + '\\n')
        if action == 'activate' and {fail_activation!r}:
            state['active'] = True
            state_path.write_text(json.dumps(state))
            pathlib.Path(r'{root / 'partial.state'}').write_text('partial')
            raise SystemExit(9)
        if action == 'activate' and {slow_activation!r}:
            state['active'] = True
            state_path.write_text(json.dumps(state))
            pathlib.Path(r'{root / 'activate.started'}').write_text('started')
            time.sleep(30)
        if action == 'activate':
            state['active'] = True
            state['webhook'] = True
            state['rows'] = True
        elif action == 'publish': state['published'] = True
        elif action == 'readback-active':
            if not (state['active'] and state['published']): raise SystemExit(20)
        elif action == 'deactivate': state['active'] = False
        elif action == 'unpublish': state['published'] = False
        elif action == 'remove-webhook': state['webhook'] = False
        elif action == 'remove-disposable-rows': state['rows'] = False
        elif action == 'readback-clean':
            if {fail_cleanup!r}: raise SystemExit(21)
            if any(state.values()): raise SystemExit(22)
            pathlib.Path(os.environ['FINANCE_MCP_OUTPUT']).write_text(json.dumps({{
                'status': 'CLEAN', 'scope': 'W15_DISPOSABLE_ONLY',
                'counts': {{'credentials': 0, 'owners': 0, 'workflows': 0, 'webhooks': 0, 'executions': 0}},
                'idsRecorded': False, 'secretValueRecorded': False,
            }}))
        state_path.write_text(json.dumps(state))
        if action != 'readback-clean':
            pathlib.Path(os.environ['FINANCE_MCP_OUTPUT']).write_text(json.dumps({{'status': 'SIMULATED', 'runtimeEvidence': False, 'scope': 'W15_SPEC_ONLY'}}))
        """,
    )
    probe = executable(
        root / "probe-gate",
        """
        import os
        raise SystemExit(0 if os.environ['FINANCE_MCP_PROBE_CASE'] == 'positive' else 1)
        """,
    )
    return actions, mutation, probe


def proof_environment(root: Path, mutation: Path, probe: Path, proof_tmp: Path) -> dict[str, str]:
    return {
        "FINANCE_N8N_MCP_DISPOSABLE_ACK": "ACTIVATE_W15_ONLY",
        "FINANCE_MCP_BINDER_VERIFIED": "VERIFIED",
        "FINANCE_MCP_RUNTIME_SCOPE": "disposable",
        "FINANCE_MCP_WORKFLOW_SCOPE": "W15",
        "FINANCE_MCP_N8N_MUTATION_GATE": str(mutation),
        "FINANCE_MCP_PROBE_GATE": str(probe),
        "FINANCE_MCP_PROOF_TMPDIR": str(proof_tmp),
        "FINANCE_MCP_SIMULATED": "true",
        "N8N_PUBLIC_API_DISABLED": "true",
    }


class FinanceMcpDisposableProofTests(unittest.TestCase):
    def test_success_state_machine_proves_scope_probes_and_teardown(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            root = Path(directory)
            proof_tmp = root / "proof-tmp"
            proof_tmp.mkdir(mode=0o700)
            actions, mutation, probe = build_state_machine(root)
            result = subprocess.run(
                [str(PROOF)],
                env={**os.environ, **proof_environment(root, mutation, probe, proof_tmp)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"status":"SIMULATED"', result.stdout)
            self.assertIn('"scope":"W15_SPEC_ONLY"', result.stdout)
            self.assertNotIn('"runtimeEvidence":true', result.stdout)
            self.assertNotIn('"status":"VERIFIED"', result.stdout)
            self.assertEqual(
                actions.read_text().splitlines(),
                ["activate", "publish", "readback-active", "deactivate", "unpublish", "remove-webhook", "remove-disposable-rows", "readback-clean"],
            )
            self.assertEqual(list(proof_tmp.iterdir()), [])
            self.assertEqual(json.loads((root / "n8n-state.json").read_text()), {"active": False, "published": False, "webhook": False, "rows": False})

    def test_partial_activation_runs_idempotent_cleanup(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            root = Path(directory)
            proof_tmp = root / "proof-tmp"
            proof_tmp.mkdir(mode=0o700)
            actions, mutation, probe = build_state_machine(root, fail_activation=True)
            result = subprocess.run(
                [str(PROOF)],
                env={**os.environ, **proof_environment(root, mutation, probe, proof_tmp)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            observed = actions.read_text().splitlines()
            self.assertEqual(observed[0], "activate")
            self.assertEqual(observed[1:], ["deactivate", "unpublish", "remove-webhook", "remove-disposable-rows", "readback-clean"])
            self.assertEqual(json.loads((root / "n8n-state.json").read_text()), {"active": False, "published": False, "webhook": False, "rows": False})

    def test_signal_during_activation_runs_cleanup_trap(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            root = Path(directory)
            proof_tmp = root / "proof-tmp"
            proof_tmp.mkdir(mode=0o700)
            actions, mutation, probe = build_state_machine(root, slow_activation=True)
            process = subprocess.Popen(
                [str(PROOF)],
                env={**os.environ, **proof_environment(root, mutation, probe, proof_tmp)},
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            started = root / "activate.started"
            for _ in range(50):
                if started.exists():
                    break
                time.sleep(0.02)
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=10)
            observed = actions.read_text().splitlines()
            self.assertEqual(observed[0], "activate")
            self.assertEqual(observed[1:], ["deactivate", "unpublish", "remove-webhook", "remove-disposable-rows", "readback-clean"])
            self.assertEqual(json.loads((root / "n8n-state.json").read_text()), {"active": False, "published": False, "webhook": False, "rows": False})

    def test_cleanup_failure_emits_no_success_receipt(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            root = Path(directory)
            proof_tmp = root / "proof-tmp"
            proof_tmp.mkdir(mode=0o700)
            actions, mutation, probe = build_state_machine(root, fail_cleanup=True)
            result = subprocess.run(
                [str(PROOF)],
                env={**os.environ, **proof_environment(root, mutation, probe, proof_tmp)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("SIMULATED", result.stdout)
            self.assertNotIn("VERIFIED", result.stdout)
            self.assertEqual(json.loads((root / "n8n-state.json").read_text()), {"active": False, "published": False, "webhook": False, "rows": False})

    def test_workflow_export_remains_inactive_before_proof(self):
        workflow = json.loads((ROOT / "integrations/n8n/workflows/15-finance-mcp-facade.json").read_text())
        self.assertFalse(workflow["active"])
        self.assertIsNone(workflow.get("activeVersionId"))
        self.assertFalse(workflow["meta"]["instanceMcpRequired"])


if __name__ == "__main__":
    unittest.main()
