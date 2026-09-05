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

    def test_feed_warning_is_shared_and_clears_after_successful_ingest(self):
        shell = (ROOT / "apps/cashback-control/web/index.html").read_text()
        warning = next(line for line in shell.splitlines() if 'id="feed-warning"' in line)
        self.assertNotIn("data-app-screen", warning)
        script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('apps/cashback-control/web/app.js', 'utf8');
const roots = new Map([['#as-of', {}], ['#feed-warning', {}]]);
const context = vm.createContext({document: {querySelector: key => roots.get(key)}});
vm.runInContext(source.slice(source.indexOf('function renderStatus('), source.indexOf('function renderRewardDisclosure(')), context);
const warning = roots.get('#feed-warning');
context.renderStatus({is_stale: true, last_successful_ingest_at: '2026-08-20T12:00:00Z'});
assert.equal(warning.hidden, false);
assert.match(warning.textContent, /Routing and bucket balances may be incomplete/);
context.renderStatus({is_stale: false, last_successful_ingest_at: '2026-09-05T12:00:00Z'});
assert.equal(warning.hidden, true);
assert.equal(warning.textContent, '');
context.renderStatus({});
assert.equal(warning.hidden, false);
assert.match(warning.textContent, /Feed not checked/);
assert.doesNotMatch(source.slice(source.indexOf('function renderAttention('), source.indexOf('async function setAlertAcknowledgement(')), /feed:stale/);
'''
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_routing_views_keep_inactive_profiles_and_disclose_conditional_targets(self):
        script = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('apps/cashback-control/web/app.js', 'utf8');
const routingViews = source.slice(0, source.indexOf('function setupRoutingViews('));

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.style = {};
    this._text = '';
    this._classes = new Set();
    this.classList = {
      toggle: (name, force) => {
        const enabled = force === undefined ? !this._classes.has(name) : Boolean(force);
        if (enabled) this._classes.add(name); else this._classes.delete(name);
        this.className = [...this._classes].join(' ');
        return enabled;
      },
    };
  }
  set textContent(value) { this._text = String(value); }
  get textContent() {
    return this._text + this.children.map(child => child?.textContent ?? String(child)).join('');
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
}

const roots = new Map([
  ['#recommendations', new Element('div')],
  ['#decision-tree', new Element('div')],
]);
const document = {
  title: '',
  querySelector: selector => roots.get(selector),
  createElement: tagName => new Element(tagName),
  createTextNode: text => String(text),
};
const context = vm.createContext({ document, console });
vm.runInContext(routingViews, context);
context.configureDisplay({
  currency: 'AED',
  cards: [
    {card: 'ACTIVE_CARD', name: 'Active Card', short_name: 'Active'},
    {card: 'STALE_CARD', name: 'Stale Card', short_name: 'Stale'},
  ],
});

const candidate = (card, overrides = {}) => ({
  order: 1,
  status: 'PREFERRED',
  card,
  bucket: 'RAK_GROCERY',
  payment_channel: 'ONLINE',
  purpose: 'REWARD',
  position_mode: 'CAPPED',
  tier_before: 'TIER_0',
  tier_after: 'TIER_1',
  target_tier: 'TIER_1',
  target_rate_percent: '10',
  current_tier_rate_percent: '0',
  estimated_net_value_aed: '10',
  estimated_net_return_percent: '10',
  card_spend_aed: '0',
  tier_threshold_aed: '100',
  tier_remaining_aed: '100',
  bucket_spend_aed: '0',
  bucket_cap_aed: '1000',
  bucket_remaining_aed: '1000',
  ...overrides,
});
const active = {
  code: 'AMAZON', label: 'Amazon', active: true, currency: 'AED', methods: ['ONLINE'],
  ranked_cards: [candidate('ACTIVE_CARD', {
    estimate_basis: 'CONDITIONAL_TARGET_TIER',
    conditional_target_rate_percent: '10',
    conditional_target_reward_aed: '10',
  })],
};
const inactive = {
  code: 'GROCERY', label: 'Grocery', active: false, currency: 'AED', methods: ['ONLINE'],
  ranked_cards: [candidate('STALE_CARD')],
};

context.renderRecommendations([active, inactive]);
const listText = roots.get('#recommendations').textContent;
assert.match(listText, /Amazon/);
assert.match(listText, /Grocery/);
assert.match(listText, /No eligible route/);
assert.doesNotMatch(listText, /Stale Card/);

context.renderDecisionTree([active, inactive]);
const tree = roots.get('#decision-tree');
const selector = tree.children[0].children[1];
assert.deepEqual(selector.children.map(option => option.textContent), ['Amazon', 'Grocery']);
selector.value = 'GROCERY';
selector.listeners.change();
const inactiveGraphText = tree.children[1].textContent;
assert.match(inactiveGraphText, /No eligible card route/);
assert.doesNotMatch(inactiveGraphText, /Stale Card/);

context.renderDecisionTree([active]);
const conditionalText = roots.get('#decision-tree').children[1].textContent;
assert.match(conditionalText, /10% if tier requirements are met/);
assert.match(conditionalText, /current tier 0%/);
assert.doesNotMatch(conditionalText, /est\.|cycle value|unlocks/i);
assert.match(context.compactReason(active), /100.*more qualifying spend needed/);
// A hypothetical purchase crossing a tier must not masquerade as earned return.
const crossing = candidate('ACTIVE_CARD', {
  estimate_basis: 'CURRENT_TIER', estimated_net_value_aed: '500',
  estimated_net_return_percent: '500', tier_before: 'TIER_0', tier_after: 'TIER_1',
});
assert.match(context.candidateValueLabel(crossing), /if tier requirements are met/);
assert.doesNotMatch(context.candidateValueLabel(crossing), /500/);
assert.equal(context.candidateValueLabel(candidate('ACTIVE_CARD', {
  tier_before: 'TIER_1', current_tier_rate_percent: '3', target_rate_percent: '3',
  estimated_net_return_percent: '500',
})), '3% cashback');
assert.doesNotMatch(context.candidateValueLabel(candidate('ACTIVE_CARD', {
  current_tier_rate_percent: '10',
})), /current tier/);
assert.match(context.routeHeading({}, {card: 'ACTIVE_CARD', payment_channel: 'APPLE_PAY_POS'}, true), /Apple Pay/);
assert.match(context.routeHeading({}, {card: 'ACTIVE_CARD', payment_channel: 'ONLINE'}), /Online/);
assert.match(context.exactMoney('49.50'), /49[.,]50/);
assert.doesNotMatch(context.exactMoney('1050'), /1[.,]1k/);
assert.match(context.exactMoney('1050'), /1.?050/);
const foreign = context.candidateValueLabel(candidate('ACTIVE_CARD', {
  tier_before: 'TIER_1', target_rate_percent: '3', current_tier_rate_percent: '3',
  configured_fx_fee_percent: '2.99',
}));
assert.match(foreign, /Gross 3% cashback/);
assert.match(foreign, /configured FX fee 2[.,]99%/);
assert.doesNotMatch(foreign, /net|AED|cycle value/);
assert.doesNotMatch(context.candidateValueLabel(candidate('ACTIVE_CARD', {configured_fx_fee_percent: '0'})), /fee/);
assert.equal(context.candidateValueLabel({}), 'Rate unavailable');
assert.equal(context.candidateValueLabel({ target_rate_percent: null }), 'Rate unavailable');
assert.match(context.candidateValueLabel(candidate('ACTIVE_CARD', {
  purpose: 'THRESHOLD_FILLER', target_rate_percent: '0',
})), /No direct cashback/);
''';
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
