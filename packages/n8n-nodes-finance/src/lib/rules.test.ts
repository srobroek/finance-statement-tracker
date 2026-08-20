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
  assert.throws(() => validateNonRepresentableRule({ ...rule, actions: [{ action: 'add_tag', value: '#shared' }] }), /unsupported n8n action/);
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
