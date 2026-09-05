"""Complete pre-import projection without changing native Actual rule ownership."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LedgerProjectionRulesTests(unittest.TestCase):
    def test_projection_covers_every_full_ledger_rule_once_with_original_ownership(self):
        source = json.loads((ROOT / 'config/static-rules.seed.json').read_text())
        generated = ROOT / 'integrations/n8n/generated'
        owners = [row for name in ('actual-rules.json', 'n8n-runtime-rules.json')
                  for row in json.loads((generated / name).read_text())['rules']
                  if row['rule_sets'] == ['FULL_LEDGER']]
        projection = json.loads((ROOT / 'packages/n8n-nodes-finance/src/generated/ledger-projection-rules.json').read_text())
        self.assertEqual(projection['artifact_role'], 'FULL_LEDGER_PROJECTION')
        self.assertNotIn('execution_owner', projection)
        self.assertEqual(projection['rules'], sorted(owners, key=lambda row: row['rule_id']))
        expected = {row['rule_id'] for row in source if 'FULL_LEDGER' in (row.get('rule_sets') or ['FULL_LEDGER'])}
        self.assertEqual({row['rule_id'] for row in projection['rules']}, expected)
        self.assertEqual(len(projection['rules']), len(expected))
        self.assertEqual(sum(row['execution_owner'] == 'ACTUAL' for row in owners), 7)

    def test_compiler_checks_projection_snapshot_for_drift(self):
        result = subprocess.run([sys.executable, str(ROOT / 'integrations/n8n/compile_rule_ownership.py')],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
