import assert from 'node:assert/strict';
import test from 'node:test';
import { assertActualImportTransactions, assertIsoDate } from './contracts';

test('invalid calendar dates cannot normalize into another financial period', () => {
  for (const date of ['2026-02-29', '2026-02-30', '2026-04-31', '2026-13-01']) assert.throws(() => assertIsoDate(date, 'date'), /YYYY-MM-DD/);
  assert.equal(assertIsoDate('2024-02-29', 'date'), '2024-02-29');
});

test('cleared requires an actual boolean instead of truthiness coercion', () => {
  const row = { imported_id: 'source:1', date: '2026-08-01', amount: -100, imported_payee: 'Merchant' };
  for (const cleared of ['false', 'true', 0, 1, null]) assert.throws(() => assertActualImportTransactions([{ ...row, cleared }]), /cleared must be boolean/);
  assert.equal(assertActualImportTransactions([{ ...row, cleared: false }])[0].cleared, false);
});
