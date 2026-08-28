import assert from 'node:assert/strict';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { ActualApi, ActualSession, preflightOutbox } from './actual-session';
import { assertActualImportTransactions, assertActualMutationMode } from './contracts';

const future = () => new Date(Date.now() + 60_000).toISOString();
const envelope = () => ({
  schema_version: 1, outbox_id: 'outbox-1', state: 'PREPARED', account_id: 'account-1',
  execution_context: { trigger: 'SCHEDULE', manual: false, mcp: false },
  writer_lease: { lease_id: 'lease-1', fencing_token: 1, expires_at: future() },
  transactions: [{ imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant' }],
});

function fakeApi(overrides: Partial<ActualApi> = {}): ActualApi & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async init() { calls.push('init'); }, async downloadBudget() { calls.push('download'); },
    async sync() { calls.push('sync'); }, async shutdown() { calls.push('shutdown'); },
    async getServerVersion() { return { version: 'test' }; },
    async getAccounts() { return [{ id: 'account-1', name: 'Card', closed: false }]; },
    async getAccountBalance() { return -1000; }, async getCategories() { return [{ id: 'cat-1', name: 'Shopping' }]; },
    async getTransactions() { return [{ id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant', category: undefined, notes: undefined, cleared: false }]; },
    async importTransactions(_account, _rows, options) { calls.push(`import:${String(options.reimportDeleted)}`); return { errors: [] }; },
    ...overrides,
  };
}

const credential = { serverUrl: 'http://actual:5006', password: 'secret', syncId: 'sync', mutationEnabled: true };
const session = (api: ActualApi) => new ActualSession(api, path.join(tmpdir(), 'finance-actual-node-tests'));

test('prepared outbox rejects manual, MCP, duplicate and expired inputs', () => {
  assert.throws(() => preflightOutbox({ ...envelope(), execution_context: { trigger: 'SCHEDULE', manual: true, mcp: false } }), /forbidden/);
  assert.throws(() => preflightOutbox({ ...envelope(), transactions: [...envelope().transactions, ...envelope().transactions] }), /duplicate imported_id/);
  assert.throws(() => preflightOutbox({ ...envelope(), writer_lease: { lease_id: 'x', fencing_token: 1, expires_at: '2020-01-01T00:00:00Z' } }), /expired/);
});

test('transaction clearing accepts only booleans', () => {
  const transaction = envelope().transactions[0]!;
  for (const cleared of [false, true]) {
    assert.equal(assertActualImportTransactions([{ ...transaction, cleared }])[0]?.cleared, cleared);
  }
  for (const cleared of ['false', 0, 1, null]) {
    assert.throws(
      () => assertActualImportTransactions([{ ...transaction, cleared }]),
      /cleared must be a boolean/,
    );
  }
});

test('import forces reimportDeleted false, syncs before and after, and shuts down', async () => {
  const api = fakeApi();
  const result = await session(api).import(credential, envelope());
  assert.equal(result.status, 'ACTUAL_OBSERVED');
  assert.deepEqual(api.calls, ['init', 'download', 'sync', 'import:false', 'sync', 'sync', 'shutdown']);
});

test('returned Actual errors fail closed and still shut down', async () => {
  const api = fakeApi({ async importTransactions() { return { errors: [{ message: 'bad row' }] }; } });
  await assert.rejects(session(api).import(credential, envelope()), /bad row/);
  assert.equal(api.calls.at(-1), 'shutdown');
});

test('verify rejects missing and duplicate imported IDs', async () => {
  const missing = fakeApi({ async getTransactions() { return []; } });
  const verification = { account_id: 'account-1', expected_transactions: envelope().transactions, start_date: '2026-08-01', end_date: '2026-08-01' };
  await assert.rejects(session(missing).verify(credential, verification), /missing=statement:one/);
  const duplicate = fakeApi({ async getTransactions() { return [
    { id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant' },
    { id: 'tx-2', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant' },
  ]; } });
  await assert.rejects(session(duplicate).verify(credential, verification), /duplicate=statement:one/);
});

test('verify compares exact economic fields, hash, and optional balance', async () => {
  const api = fakeApi();
  const verification = { account_id: 'account-1', expected_transactions: envelope().transactions, start_date: '2026-08-01', end_date: '2026-08-01', expected_account_balance: -1000 };
  const result = await session(api).verify(credential, verification);
  assert.equal(result.status, 'VERIFIED');
  assert.equal(result.expected_sha256, result.observed_sha256);
  assert.equal(result.account_balance, -1000);
  const mismatched = fakeApi({ async getTransactions() { return [{ id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -999, imported_payee: 'Merchant', cleared: false }]; } });
  await assert.rejects(session(mismatched).verify(credential, verification), /field mismatch/);
  const wrongShape = fakeApi({ async getTransactions() { return [{ id: 'tx-1', account: 'wrong-account', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Changed', notes: '#changed', cleared: true }]; } });
  await assert.rejects(session(wrongShape).verify(credential, verification), /field mismatch/);
  const base = { id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant', category: undefined, notes: undefined, cleared: false };
  for (const changed of [
    { ...base, account: 'wrong-account' }, { ...base, date: '2026-08-02' }, { ...base, amount: -999 },
    { ...base, imported_payee: 'Changed' }, { ...base, category: 'cat-1' }, { ...base, notes: '#changed' }, { ...base, cleared: true },
  ]) {
    const changedApi = fakeApi({ async getTransactions() { return [changed]; } });
    await assert.rejects(session(changedApi).verify(credential, verification), /field mismatch/);
  }
  const nonInteger = fakeApi({ async getTransactions() { return [{ ...base, amount: -10.5 }]; } });
  await assert.rejects(session(nonInteger).verify(credential, verification), /integer minor units/);
  await assert.rejects(session(api).verify(credential, { ...verification, expected_account_balance: 0 }), /balance mismatch/);
});

test('mutation-disabled credential cannot import', async () => {
  await assert.rejects(session(fakeApi()).import({ ...credential, mutationEnabled: false }, envelope()), /disabled/);
});

test('manual, MCP-like, chat, agent, and evaluation modes cannot mutate', () => {
  for (const mode of ['manual', 'webhook', 'chat', 'agent', 'evaluation', 'cli', 'error']) {
    assert.throws(() => assertActualMutationMode(mode), /forbidden/);
  }
  for (const mode of ['trigger', 'integrated', 'retry']) assert.doesNotThrow(() => assertActualMutationMode(mode));
});
