"""Exercise asynchronous alert controls without a browser or provider calls."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node is required for UI behaviour tests")
class CashbackUiRecoveryTests(unittest.TestCase):
    def test_acknowledgement_failure_recovers_after_event_dispatch(self):
        script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('apps/cashback-control/web/app.js', 'utf8');
const attention = source.slice(source.indexOf('function renderAttention('), source.indexOf('async function setAlertAcknowledgement('));
async function scenario(writeFails) {
  const inputs = [];
  const element = () => ({append() {}, replaceChildren() {}, addEventListener(name, fn) { this.listener = fn; }});
  const createNode = tag => { const node = element(); if (tag === 'input') inputs.push(node); return node; };
  let refreshes = 0;
  const context = vm.createContext({
    document: {querySelector: element, createElement: element, createTextNode: text => text},
    createNode,
    setAlertAcknowledgement: async () => { if (writeFails) throw new Error('Write rejected'); },
    refreshDashboard: async () => { refreshes++; throw new Error('Refresh failed'); },
  });
  vm.runInContext(attention, context);
  context.renderAttention({alerts: [{key: 'alert:1', title: 'Test', detail: 'Synthetic'}]});
  const control = inputs[0];
  control.checked = true;
  const event = {currentTarget: control};
  const pending = control.listener(event);
  // Browsers clear currentTarget once synchronous event dispatch returns.
  event.currentTarget = null;
  await pending;
  assert.equal(control.disabled, false);
  assert.equal(control.checked, !writeFails, 'only a failed write may revert persisted acknowledgement');
  assert.equal(refreshes, writeFails ? 0 : 1);
  assert.ok(control.title);
}
(async () => { await scenario(true); await scenario(false); })().catch(error => {console.error(error); process.exitCode = 1;});
'''
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
