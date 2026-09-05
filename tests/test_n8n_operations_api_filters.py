"""Regress the live HTTP400 caused by a literal all-status API filter."""
import json
import unittest
from pathlib import Path
from integrations.n8n.refactor_workflow_ui import ensure_operations_execution_filters

ROOT = Path(__file__).resolve().parents[1]


class OperationsApiFilterTests(unittest.TestCase):
    def test_both_health_queries_include_failures_without_invalid_status(self):
        workflow = json.loads((ROOT / 'integrations/n8n/workflows/10-finance-operations-status.json').read_text())
        nodes = [node for node in workflow['nodes'] if node['type'] == 'n8n-nodes-base.n8n']
        self.assertEqual(len(nodes), 2)
        for node in nodes:
            self.assertNotIn('status', node['parameters']['filters'])
            self.assertEqual(node['parameters']['operation'], 'getAll')

    def test_renderer_repairs_all_but_preserves_real_filters_idempotently(self):
        nodes = [{'type': 'n8n-nodes-base.n8n', 'parameters': {
            'resource': 'execution', 'operation': 'getAll',
            'filters': {'status': status, 'workflowId': 'fixture'}}}
            for status in ['all', 'error', 'success', 'waiting']]
        workflows = [{'nodes': nodes}]
        ensure_operations_execution_filters(workflows)
        self.assertEqual(nodes[0]['parameters']['filters'], {'workflowId': 'fixture'})
        self.assertEqual([n['parameters']['filters']['status'] for n in nodes[1:]], ['error', 'success', 'waiting'])
        before = json.dumps(workflows)
        ensure_operations_execution_filters(workflows)
        self.assertEqual(json.dumps(workflows), before)
