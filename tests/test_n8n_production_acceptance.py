import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('acceptance', ROOT / 'integrations/n8n/setup-workflows/runner/generate-production-acceptance-workflow.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
AT = datetime(2026, 9, 5, 16, 0, tzinfo=timezone.utc)


class ProductionAcceptanceTests(unittest.TestCase):
    def build(self, kind, **kwargs):
        return M.build(ROOT, kind, AT, 'a' * 40, **kwargs)

    def execute(self, code, **values):
        payload = {'code': code, 'now': int(AT.timestamp()*1000), 'mode': 'production', 'row': {}, 'index': 0, **values}
        runner = "const x=JSON.parse(process.argv[1]);const D={now:()=>x.now,parse:Date.parse};try{const out=new Function('Date','$execution','$json','$runIndex',x.code)(D,{mode:x.mode},x.row,x.index);console.log(JSON.stringify({out}));}catch(e){console.log(JSON.stringify({error:e.message}));}"
        return json.loads(subprocess.check_output(['node', '-e', runner, json.dumps(payload)], text=True))

    def test_acquisition_graphs_preserve_every_original_node_and_downstream_edge(self):
        for kind, filename in M.SOURCES.items():
            before = json.loads((ROOT/'integrations/n8n/workflows'/filename).read_text())
            after = self.build(kind)
            self.assertFalse(after['active'])
            source_nodes = {n['id']: n for n in before['nodes']}
            for node in after['nodes']:
                if node['type'] == 'n8n-nodes-base.scheduleTrigger' or node['id'] == 'acceptance-time-bound':
                    continue
                self.assertEqual(node, source_nodes[node['id']])
            trigger = next(n for n in before['nodes'] if n['type']=='n8n-nodes-base.scheduleTrigger')
            for name, edge in before['connections'].items():
                self.assertEqual(edge, after['connections']['Verify Absolute Acceptance Window' if name == trigger['name'] else name])
            self.assertFalse(any(n['type'].endswith(('manualTrigger', 'executeWorkflowTrigger', 'webhook')) for n in after['nodes']))

    def test_absolute_window_rejects_manual_early_expired_and_future_year_runs(self):
        workflow = self.build('rak')
        code = next(n['parameters']['jsCode'] for n in workflow['nodes'] if n['id']=='acceptance-time-bound')
        self.assertEqual(self.execute(code)['out'], [{'json': {}}])
        self.assertEqual(self.execute(code, mode='test')['error'], 'ACCEPTANCE_SCHEDULE_REQUIRED')
        for delta in [-1, 900000, 366*86400000]:
            self.assertEqual(self.execute(code, now=int(AT.timestamp()*1000)+delta)['error'], 'ACCEPTANCE_WINDOW_CLOSED')

    def test_maintenance_is_fixed_integrated_child_with_bounded_retry_and_complete_exit(self):
        workflow = self.build('maintenance', max_calls=3)
        call = next(n for n in workflow['nodes'] if n['type']=='n8n-nodes-base.executeWorkflow')
        self.assertEqual(call['parameters']['workflowId']['value'], M.MAINTENANCE_ID)
        self.assertTrue(call['parameters']['options']['waitForSubWorkflow'])
        self.assertEqual(call['parameters']['workflowInputs']['value'], {})
        clear = next(n for n in workflow['nodes'] if n['name']=='Clear Maintenance Caller Input')
        self.assertFalse(clear['parameters']['includeOtherFields'])
        self.assertEqual(clear['parameters']['assignments']['assignments'], [])
        code = next(n['parameters']['jsCode'] for n in workflow['nodes'] if n['name']=='Bound Maintenance Iterations')
        self.assertEqual(self.execute(code, row={'complete': True}, index=2)['out'], [{'json': {'complete': True}}])
        self.assertEqual(self.execute(code, row={'complete': False}, index=2)['error'], 'ACCEPTANCE_MAINTENANCE_CALL_BOUND')
        self.assertIn('error', self.execute(code, row={'complete': 'true'}))
        self.assertEqual(workflow['connections']['Maintenance Complete']['main'][0], [])
        self.assertEqual(workflow['connections']['Maintenance Complete']['main'][1][0]['node'], 'Clear Maintenance Caller Input')

    def test_cycle_and_schedule_constraints_fail_before_rendering(self):
        for kind in ['ei', 'wio']:
            with self.assertRaises(ValueError):
                M.build(ROOT, kind, datetime(2026, 9, 15, tzinfo=timezone.utc), 'a'*40)
        with self.assertRaises(ValueError):
            self.build('maintenance', max_calls=101)
        with self.assertRaises(ValueError):
            M.build(ROOT, 'rak', AT.replace(second=1), 'a'*40)
