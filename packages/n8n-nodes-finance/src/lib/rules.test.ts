import assert from 'node:assert/strict';
import test from 'node:test';
import { applyNonRepresentableRules, assertProtectedFieldsUnchanged, normalizeTransaction, validateNonRepresentableRule } from './rules';
import { parseStatement } from './statements';

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
  assert.equal(after.merchant_raw, before.merchant_raw);
  assert.deepEqual(after.tags, ['#a', '#b']);
  assertProtectedFieldsUnchanged(before, after);
});

test('merchant matching normalizes whitespace without changing raw evidence', () => {
  const merchantRaw = '  AmZn   UAE ';
  const after = applyNonRepresentableRules({ merchant_raw: merchantRaw, amount_aed: '10.00' }, [{ ...rule, match: { any: [{ all: [{ field: 'merchant_raw', operator: 'contains', value: 'amzn uae' }] }] } }]);
  assert.equal(after.merchant_raw, merchantRaw);
  assert.equal(after.vendor, 'Amazon');
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

test('parser CREDIT reaches conditioned rules before deterministic finalization', () => {
  const statement = parseStatement(`Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
13 JUL 12 JUL MERCHANT CREDIT 3.55CR
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,966.84 100.00 25/08/26 3.55 0.00 3.55`, 'emirates_islamic_v1');
  const creditRule = {
    ...rule,
    stage: 'TRANSACTION_NORMALIZATION',
    match: { any: [{ all: [{ field: 'transaction_type', operator: 'equals', value: 'CREDIT' }] }] },
    actions: [{ action: 'set', field: 'vendor', value: 'Credit rule matched' }],
  };
  const after = applyNonRepresentableRules({ ...statement.transactions[0], merchant_raw: statement.transactions[0].description }, [creditRule]);
  assert.equal(after.vendor, 'Credit rule matched');
  assert.equal(after.transaction_type, 'REFUND');
  assert.equal(after.transaction_type_locked, true);
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
