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

    def test_lean_views_keep_buckets_channels_capacity_and_check_status(self):
        script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('apps/cashback-control/web/app.js', 'utf8');
class Element {
 constructor(tag) {this.tag=tag;this.children=[];this.dataset={};this.style={};this.attributes={};this.text='';this.classList={toggle(){}};}
 set textContent(v){this.text=String(v)} get textContent(){return this.text+this.children.map(c=>c.textContent??String(c)).join(' ')}
 append(...v){this.children.push(...v)} replaceChildren(...v){this.children=v} setAttribute(k,v){this.attributes[k]=v} addEventListener(){}
}
const roots=new Map(['#cards','#recommendations','#as-of'].map(k=>[k,new Element('div')]));
const context=vm.createContext({document:{querySelector:k=>roots.get(k),createElement:t=>new Element(t),createElementNS:(ns,t)=>new Element(t),createTextNode:t=>t},Intl});
vm.runInContext(source.slice(0, source.lastIndexOf('\nsetupScreenViews();')),context);
context.configureDisplay({currency:'AED',cards:[{card:'SC',short_name:'SC Platinum'}]});
context.renderCards([{name:'RAK World',total_spend_aed:'2450.50',safety_target_aed:'10300',tier:'BASE',pace:{status:'UNDER'},buckets:[{code:'RAK_GROCERY',spend_aed:'2450.50',spend_cap_aed:'3000'}]},{name:'EI Amazon',reward_eligibility_verified:false,position_mode:'SPEND',buckets:[{code:'EI_AMAZON',spend_aed:'0',spend_cap_aed:null}]}]);
const cards=roots.get('#cards');
assert.equal(cards.children[0].children[0].tag,'details');
assert.equal(cards.children[0].children[1].tag,'div','buckets remain outside collapsed card details');
assert.match(cards.textContent,/549[.,]50/);assert.match(cards.textContent,/grocery/);assert.doesNotMatch(cards.textContent,/RAK_GROCERY/);
assert.match(cards.textContent,/7,849\.50 to AED\s10,300\.00 target/);assert.doesNotMatch(cards.textContent,/under/i);
assert.equal(context.bucketLabel('SC_FILLER'),'Other spend');assert.equal(context.typeLabel({label:'Ewallet'}),'E-wallet');
assert.match(cards.textContent,/Reward eligibility unknown/);assert.match(cards.textContent,/Limit unknown/);assert.doesNotMatch(cards.textContent,/No cap/);
context.renderCards([{card:'EI_AMAZON',name:'EI Amazon',buckets:[]}]);assert.equal(cards.children.length,0);
context.renderRecommendations([{label:'Groceries',active:true,ranked_cards:[{card:'EI_AMAZON'},{card:'SC',bucket:'SC_WALLET',bucket_spend_aed:'1950.50',bucket_cap_aed:'2000',payment_channel:'APPLE_PAY_POS',target_rate_percent:'10',current_tier_rate_percent:'3',target_tier:'TOP',tier_before:'LOW',tier_remaining_aed:'1000.50',bucket_remaining_aed:'49.50'}]}]);
const routes=roots.get('#recommendations');assert.match(routes.textContent,/Apple Pay/);assert.match(routes.textContent,/49[.,]50/);assert.equal(routes.children[0].tag,'details');
assert.match(routes.children[0].children[0].textContent,/wallet bucket/);assert.match(routes.children[0].children[0].textContent,/1,950\.50/);assert.doesNotMatch(routes.textContent,/EI Amazon|EI_AMAZON/);
assert.doesNotMatch(routes.textContent,/est\.|cycle value|Purchase amount/i);
context.renderRecommendations([{code:'PHYSICAL'},{code:'FILLER'},{code:'ONLINE',label:'Online',ranked_cards:[{card:'SC',payment_channel:'ONLINE'}]},{code:'APPLE_PAY',label:'Apple Pay',ranked_cards:[{card:'SC',payment_channel:'APPLE_PAY_POS'}]}]);
assert.equal(routes.children.length,2);assert.equal(routes.children[0].children[0].textContent.match(/Online/g).length,1);assert.equal(routes.children[1].children[0].textContent.match(/Apple Pay/g).length,1);assert.match(routes.children[0].children[1].textContent,/Online/);
context.renderStatus({is_stale:false,last_successful_check_at:'2026-09-05T08:05:00Z',last_event_at:'2026-01-01T00:00:00Z'});assert.match(roots.get('#as-of').textContent,/Checked/);
context.renderStatus({is_stale:true,last_successful_check_at:'2026-09-04T08:05:00Z'});assert.match(roots.get('#as-of').textContent,/Overdue/);
const html=fs.readFileSync('apps/cashback-control/web/index.html','utf8');assert.doesNotMatch(html,/routing-amount|reward-disclosure|feed-warning|eyebrow/);
'''
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
