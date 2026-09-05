import assert from 'node:assert/strict';
import test from 'node:test';
import { applyNonRepresentableRules, assertProtectedFieldsUnchanged, normalizeTransaction, validateNonRepresentableRule } from './rules';

const rule = {
  schema_version: 1,
  rule_id: 'normalize-vendor',
  execution_owner: 'N8N_ONLY',
  actual_representable: false,
  stage: 'VENDOR_NORMALIZATION',
  priority: 10,
  match: { any: [{ all: [{ field: 'merchant_raw', operator: 'contains', value: 'AMZN' }] }] },
  actions: [{ action: 'set', field: 'vendor', value: 'Amazon' }],
  stop_on_match: true,
};

test('normalization is deterministic and preserves source semantics', () => {
  const before = { merchant_raw: '  AMZN   UAE ', amount_aed: '100.00', source_direction: 'DEBIT', tags: ['#b', '#a', '#a'] };
  const after = normalizeTransaction(before);
  assert.equal(after.merchant_raw, 'AMZN UAE');
  assert.deepEqual(after.tags, ['#a', '#b']);
  assertProtectedFieldsUnchanged(before, after);
});

test('N8N_ONLY rules match any group and all conditions', () => {
  const after = applyNonRepresentableRules({ merchant_raw: 'AMZN UAE', amount_aed: '10.00' }, [rule]);
  assert.equal(after.vendor, 'Amazon');
});

test('Actual-representable and protected-field rules are rejected', () => {
  assert.throws(() => validateNonRepresentableRule({ ...rule, actual_representable: true }), /N8N_ONLY/);
  assert.throws(() => validateNonRepresentableRule({ ...rule, actions: [{ action: 'set', field: 'amount_aed', value: '0.00' }] }), /not mutable/);
});

test('locked source facts are readable in conditions but never mutable', () => {
  const sourceRule = {
    ...rule,
    match: { any: [{ all: [
      { field: 'source_direction', operator: 'equals', value: 'CREDIT' },
      { field: 'amount_aed', operator: 'gt', value: 0 },
      { field: 'transaction_type', operator: 'equals', value: 'REFUND' },
    ] }] },
    actions: [{ action: 'request_evidence' }],
  };
  const after = applyNonRepresentableRules({ source_direction: 'CREDIT', amount_aed: 10, transaction_type: 'REFUND' }, [sourceRule]);
  assert.equal(after.evidence_status, 'REQUESTED');
  assert.throws(() => validateNonRepresentableRule({ ...rule, actions: [{ action: 'set', field: 'transaction_type', value: 'PURCHASE' }] }), /not mutable/);
  assert.throws(() => validateNonRepresentableRule({ ...rule, match: { any: [{ all: [{ field: 'amount_aed', operator: 'regex', value: '.*' }] }] } }), /text operator/);
});

test('transaction topic is set only during normalization then locked', () => {
  const topicRule = {
    ...rule,
    stage: 'TRANSACTION_NORMALIZATION',
    match: { any: [{ all: [{ field: 'merchant_raw', operator: 'contains', value: 'PAYMENT RECEIVED' }] }] },
    actions: [{ action: 'set', field: 'transaction_type', value: 'PAYMENT' }],
  };
  const payment = applyNonRepresentableRules({ merchant_raw: 'CARD PAYMENT RECEIVED', source_direction: 'CREDIT', amount_aed: 100 }, [topicRule]);
  assert.equal(payment.transaction_type, 'PAYMENT');
  assert.equal(payment.transaction_type_locked, true);
  assert.equal(applyNonRepresentableRules({ merchant_raw: 'BANK TRANSFER', source_direction: 'DEBIT' }, []).transaction_type, 'TRANSFER');
  assert.equal(applyNonRepresentableRules({ merchant_raw: 'MONTHLY CASHBACK', source_direction: 'CREDIT' }, []).transaction_type, 'REWARD_CREDIT');
  assert.equal(applyNonRepresentableRules({ merchant_raw: 'MERCHANT CREDIT', source_direction: 'CREDIT' }, []).transaction_type, 'REFUND');
});

test('compatibility matrix rejects unsupported and Actual-owned constructs loudly', () => {
  assert.throws(() => validateNonRepresentableRule({ ...rule, match: { any: [{ all: [{ field: 'merchant_raw', operator: 'script', value: 'x' }] }] } }), /unsupported/);
  assert.doesNotThrow(() => validateNonRepresentableRule({ ...rule, actions: [{ action: 'add_tag', value: '#shared' }] }));
});

test('unsafe regular expressions are rejected', () => {
  const unsafe = { ...rule, match: { any: [{ all: [{ field: 'merchant_raw', operator: 'regex', value: '(a+)+(?=b)' }] }] } };
  assert.throws(() => applyNonRepresentableRules({ merchant_raw: 'aaa' }, [unsafe]), /RE2-compatible/);
  const backreference = { ...rule, match: { any: [{ all: [{ field: 'merchant_raw', operator: 'regex', value: '(a+)\\1' }] }] } };
  assert.throws(() => validateNonRepresentableRule(backreference), /RE2-compatible/);
  const catastrophic = { ...rule, match: { any: [{ all: [{ field: 'merchant_raw', operator: 'regex', value: '(a+)+$' }] }] } };
  const started = performance.now();
  assert.doesNotThrow(() => validateNonRepresentableRule(catastrophic));
  const result = applyNonRepresentableRules({ merchant_raw: `${'a'.repeat(100_000)}!` }, [catastrophic]);
  assert.equal(result.vendor, undefined);
  assert.ok(performance.now() - started < 1000, 'RE2-compatible hostile input must remain linear-time');
  assert.throws(() => validateNonRepresentableRule({ ...rule, match: { any: [{ all: [{ field: 'merchant_raw', operator: 'regex', value: '[' }] }] } }), /regular expression/);
});

test('manual locks survive normalization and every rule action', () => {
  const input = { merchant_raw: 'AMZN UAE', vendor: '  My vendor ', currency: 'aed', tags: ['#B', '#A'], review_required: false, evidence_status: 'VERIFIED', transaction_type: 'REFUND', transaction_type_locked: true, locked_fields: ['vendor', 'currency', 'tags', 'review_required', 'evidence_status'] };
  const result = applyNonRepresentableRules(input, [{ ...rule, stage: 'TRANSACTION_NORMALIZATION', actions: [
    { action: 'set', field: 'vendor', value: 'Amazon' },
    { action: 'set', field: 'transaction_type', value: 'PURCHASE' },
    { action: 'require_review' }, { action: 'request_evidence' },
  ] }]);
  assert.deepEqual(result, input);
  assert.throws(() => normalizeTransaction({ locked_fields: 'vendor' }), /locked_fields/);
});

test('missing numeric values never become zero in rule matching', () => {
  for (const amount of [undefined, null, '', ' ', false, [], 'invalid', Infinity]) {
    const result = applyNonRepresentableRules({ amount_aed: amount }, [{ ...rule, match: { any: [{ all: [{ field: 'amount_aed', operator: 'numeric_equals', value: 0 }] }] } }]);
    assert.equal(result.vendor, undefined);
  }
  for (const amount of [0, '0']) {
    assert.equal(applyNonRepresentableRules({ amount_aed: amount }, [{ ...rule, match: { any: [{ all: [{ field: 'amount_aed', operator: 'numeric_equals', value: 0 }] }] } }]).vendor, 'Amazon');
  }
  assert.throws(() => validateNonRepresentableRule({ ...rule, match: { any: [{ all: [{ field: 'amount_aed', operator: 'lt', value: null }] }] } }), /finite/);
});

test('date rules use the source calendar date and tags obey case sensitivity', () => {
  const dateRule = { ...rule, match: { any: [{ all: [{ field: 'transaction_at', operator: 'date_on', value: '2026-08-01' }] }] } };
  assert.equal(applyNonRepresentableRules({ transaction_at: '2026-08-01T23:30:00-04:00' }, [dateRule]).vendor, 'Amazon');
  assert.equal(applyNonRepresentableRules({ transaction_at: '2026-02-30' }, [dateRule]).vendor, undefined);
  const tagRule = { ...rule, match: { any: [{ all: [{ field: 'tags', operator: 'has_tag', value: '#SHARED' }] }] } };
  assert.equal(applyNonRepresentableRules({ tags: ['#shared'] }, [tagRule]).vendor, 'Amazon');
  assert.equal(applyNonRepresentableRules({ tags: ['#shared'] }, [{ ...tagRule, match: { any: [{ all: [{ ...tagRule.match.any[0].all[0], case_sensitive: true }] }] } }]).vendor, undefined);
});


test('compiled FULL_LEDGER rules all validate and classify real merchant fixtures', () => {
  const { readFileSync } = require('node:fs');
  const { resolve } = require('node:path');
  const compiled = JSON.parse(readFileSync(resolve(process.cwd(), '../../integrations/n8n/generated/n8n-runtime-rules.json'), 'utf8'));
  const rules = compiled.rules.filter((item: { rule_sets: string[] }) => item.rule_sets.includes('FULL_LEDGER'));
  assert.ok(rules.length > 100);
  for (const item of rules) assert.doesNotThrow(() => validateNonRepresentableRule(item), item.rule_id);
  const after = applyNonRepresentableRules({ merchant_raw: 'CARREFOUR', card: 'ADCB_365', currency: 'AED', amount_aed: 100, spend_aed: 100, source_direction: 'DEBIT', is_foreign: false }, rules);
  assert.equal(after.vendor, 'Carrefour');
  assert.ok(after.category);
  assert.ok(Array.isArray(after.tags));
});

test('classification tagging evidence and cashback respect sequence and stage locks', () => {
  const matched = { ...rule, match: { any: [{ all: [{ field: 'merchant_raw', operator: 'contains', value: 'AMZN' }] }] } };
  const rules = [
    { ...matched, stage: 'CLASSIFICATION', actions: [{ action: 'set_if_empty', field: 'category', value: 'Shopping' }, { action: 'set', field: 'transaction_type', value: 'FEE' }] },
    { ...matched, stage: 'TAGGING', actions: [{ action: 'remove_tag', value: 'old', sequence: 30 }, { action: 'add_tags', value: ['old', 'online'], sequence: 10 }, { action: 'add_tag', value: 'purchase', sequence: 20 }] },
    { ...matched, stage: 'EVIDENCE', actions: [{ action: 'request_evidence', value: 'RECEIPT' }] },
    { ...matched, stage: 'CASHBACK', actions: [{ action: 'set', field: 'reward_bucket', value: 'online' }] },
  ];
  const result = applyNonRepresentableRules({ merchant_raw: 'AMZN UAE', category: 'Manual', locked_fields: ['category'] }, rules);
  assert.equal(result.category, 'Manual');
  assert.equal(result.transaction_type, 'PURCHASE');
  assert.deepEqual(result.tags, ['online', 'purchase']);
  assert.equal(result.evidence_policy, 'RECEIPT');
  assert.equal(result.evidence_status, 'REQUESTED');
  assert.equal(result.reward_bucket, 'online');
  for (const field of ['evidence_policy', 'evidence_status']) {
    const locked = applyNonRepresentableRules({ merchant_raw: 'AMZN', evidence_policy: 'NONE', evidence_status: 'VERIFIED', locked_fields: [field, 'tags', 'reward_bucket'] }, rules);
    assert.equal(locked.evidence_policy, 'NONE');
    assert.equal(locked.evidence_status, 'VERIFIED');
    assert.equal(locked.tags, undefined);
    assert.equal(locked.reward_bucket, undefined);
  }
});


test('derived conditions use normalized topic and currency instead of stale supplied flags', () => {
  const transfer = { ...rule, stage: 'TRANSACTION_NORMALIZATION', actions: [{ action: 'set', field: 'transaction_type', value: 'TRANSFER' }] };
  const spend = { ...rule, stage: 'CASHBACK', match: { any: [{ all: [{ field: 'spend_aed', operator: 'gt', value: 0 }] }] }, actions: [{ action: 'set', field: 'reward_bucket', value: 'eligible' }] };
  assert.equal(applyNonRepresentableRules({ merchant_raw: 'AMZN', amount_aed: 100, spend_aed: 100 }, [transfer, spend]).reward_bucket, undefined);
  const foreign = { ...spend, match: { any: [{ all: [{ field: 'is_foreign', operator: 'is_true' }] }] } };
  assert.equal(applyNonRepresentableRules({ currency: 'USD', is_foreign: false }, [foreign]).reward_bucket, 'eligible');
  assert.equal(applyNonRepresentableRules({ currency: 'AED', is_foreign: true }, [foreign]).reward_bucket, undefined);
  const refund = { ...spend, match: { any: [{ all: [{ field: 'spend_aed', operator: 'numeric_equals', value: -100 }] }] } };
  for (const flags of [{ transaction_type: 'REFUND' }, { transaction_type: 'REVERSAL' }, { transaction_type: 'PURCHASE', is_refund: true }])
    assert.equal(applyNonRepresentableRules({ ...flags, amount_aed: 100, spend_aed: 100 }, [refund]).reward_bucket, 'eligible');
});



test('ledger projection includes Actual-owned classification without changing runtime ownership', () => {
  const { loadPackagedLedgerRules } = require('./runtime-rules');
  const { applyLedgerProjectionRules, validateLedgerProjectionRule } = require('./rules');
  const rules = loadPackagedLedgerRules();
  const owned = rules.filter((row: {execution_owner: string}) => row.execution_owner === 'ACTUAL');
  assert.equal(owned.length, 7);
  for (const rule of owned) {
    assert.equal(rule.actual_representable, true);
    assert.throws(() => validateNonRepresentableRule(rule), /N8N_ONLY/);
    assert.doesNotThrow(() => validateLedgerProjectionRule(rule));
  }
  for (const [vendor, category] of [['UNRWA', 'Charity'], ['iSTYLE', 'Electronics']]) {
    const input = {merchant_raw: vendor, vendor, amount_aed: '10.00', source_direction: 'DEBIT'};
    assert.equal(applyLedgerProjectionRules(input, rules).category, category);
    assert.equal(applyLedgerProjectionRules({...input, category: 'Manual', locked_fields: ['category']}, rules).category, 'Manual');
  }
  // Actual-owned stages must participate in stage ordering and stop_on_match,
  // not run as a late patch after tagging/reward rules already consumed category.
  const charity = owned.find((row: {rule_id: string}) => row.rule_id === 'class-unrwa-charity');
  const tagging = {...rule, stage: 'TAGGING', match: {any: [{all: [{field: 'category', operator: 'equals', value: 'Charity'}]}]}, actions: [{action: 'add_tag', value: 'charity'}]};
  assert.deepEqual(applyLedgerProjectionRules({vendor:'UNRWA'}, [tagging, charity]).tags, ['charity']);
  assert.throws(() => validateLedgerProjectionRule({...charity, actions:[{action:'set',field:'category',value:'Overwrite'}]}), /non-overwriting/);
});
