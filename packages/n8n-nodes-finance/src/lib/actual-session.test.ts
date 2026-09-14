import assert from 'node:assert/strict';
import { stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { ActualSession, preflightOutbox } from './actual-session';
import type { ActualApi } from './actual-session';
import { assertActualMutationMode } from './contracts';

const future = () => new Date(Date.now() + 60_000).toISOString();
const envelope = () => ({
  schema_version: 1, outbox_id: 'outbox-1', state: 'PREPARED', account_id: 'account-1',
  execution_context: { trigger: 'SCHEDULE', manual: false, mcp: false },
  writer_lease: { resource_key: 'actual:sync', lease_id: 'lease-1', fencing_token: 1, expires_at: future() },
  transactions: [{ imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant' }],
});

function fakeApi(overrides: Partial<ActualApi> = {}): ActualApi & { calls: string[]; dataDirs: string[] } {
  const calls: string[] = [];
  const dataDirs: string[] = [];
  let loaded = false;
  const requireLoaded = () => { if (!loaded) throw new Error('budget not loaded'); };
  return {
    calls, dataDirs,
    async init(config) { loaded = false; dataDirs.push(config.dataDir); calls.push('init'); }, async downloadBudget() { loaded = true; calls.push('download'); },
    async sync() { requireLoaded(); calls.push('sync'); }, async shutdown() { calls.push('shutdown'); },
    async getServerVersion() { requireLoaded(); return { version: 'test' }; },
    async getAccounts() { requireLoaded(); return [{ id: 'account-1', name: 'Card', closed: false }]; },
    async getAccountBalance() { requireLoaded(); return -1000; }, async getCategories() { requireLoaded(); return [{ id: 'cat-1', name: 'Shopping' }]; },
    async getTransactions() { requireLoaded(); return [{ id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant', category: undefined, notes: undefined, cleared: false }]; },
    async importTransactions(_account, _rows, options) { requireLoaded(); calls.push(`import:${String(options.reimportDeleted)}`); return { errors: [] }; },
    ...overrides,
  };
}

const credential = { serverUrl: 'http://actual:5006', password: 'secret', syncId: 'sync', mutationEnabled: true };
const session = (api: ActualApi) => new ActualSession(api, path.join(tmpdir(), 'finance-actual-node-tests'));

test('prepared outbox rejects manual, MCP, duplicate, impossible dates, string booleans and expired inputs', () => {
  assert.throws(() => preflightOutbox({ ...envelope(), execution_context: { trigger: 'SCHEDULE', manual: true, mcp: false } }), /forbidden/);
  assert.throws(() => preflightOutbox({ ...envelope(), transactions: [...envelope().transactions, ...envelope().transactions] }), /duplicate imported_id/);
  assert.throws(() => preflightOutbox({ ...envelope(), transactions: [{ ...envelope().transactions[0], date: '2026-02-30' }] }), /YYYY-MM-DD/);
  assert.throws(() => preflightOutbox({ ...envelope(), transactions: [{ ...envelope().transactions[0], cleared: 'false' }] }), /cleared must be boolean/);
  assert.throws(() => preflightOutbox({ ...envelope(), writer_lease: { ...envelope().writer_lease, expires_at: '2020-01-01T00:00:00Z' } }), /expired/);
});

test('import performs one mutation sync and exact readback, then shuts down', async () => {
  const api = fakeApi();
  const result = await session(api).import(credential, envelope());
  assert.equal(result.status, 'ACTUAL_OBSERVED');
  assert.equal(result.imported_ids_verified, true);
  assert.equal(result.expected_sha256, result.observed_sha256);
  assert.deepEqual(api.calls, ['init', 'download', 'import:false', 'sync', 'shutdown']);
});

test('read-only operations never sync the Actual cache', async () => {
  const api = fakeApi();
  await session(api).read(credential, { shape: 'categories' });
  await session(api).verify(credential, { account_id: 'account-1', expected_transactions: envelope().transactions, start_date: '2026-08-01', end_date: '2026-08-01' });
  assert.equal(api.calls.includes('sync'), false);
});

test('each session uses a fresh isolated directory and removes it', async () => {
  const api = fakeApi();
  const root = path.join(tmpdir(), `finance-actual-fresh-${Date.now()}`);
  await new ActualSession(api, root).read(credential, { shape: 'categories' });
  await new ActualSession(api, root).read(credential, { shape: 'categories' });
  assert.equal(api.dataDirs.length, 2);
  assert.notEqual(api.dataDirs[0], api.dataDirs[1]);
  for (const directory of api.dataDirs) await assert.rejects(stat(directory));
});

test('returned Actual errors fail closed and still shut down', async () => {
  const api = fakeApi({ async importTransactions() { return { errors: [{ message: 'bad row' }] }; } });
  await assert.rejects(session(api).import(credential, envelope()), /bad row/);
  assert.equal(api.calls.at(-1), 'shutdown');
});

test('post-issuance sync failure is fail closed without retry or release', async () => {
  const api = fakeApi({
    async sync() {
      api.calls.push('sync');
      throw new Error('sync boundary lost');
    },
  });
  await assert.rejects(session(api).import(credential, envelope()), /sync boundary lost/);
  assert.deepEqual(api.calls, ['init', 'download', 'import:false', 'sync', 'shutdown']);
});

test('mutation requires the complete workflow lease tuple', async () => {
  const api = fakeApi();
  await assert.rejects(session(api).import(credential, { ...envelope(), writer_lease: { lease_id: 'lease-1', fencing_token: 1, expires_at: future() } }), /resource_key/);
  assert.deepEqual(api.calls, []);
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

test('doctor and account reads expose safe account health without provider IDs', async () => {
  const balanceIds: string[] = [];
  const api = fakeApi({
    async getServerVersion() { return { version: 'test' }; },
    async getAccounts() { return [{ id: 'account-1', name: 'Card', closed: false, offbudget: true }]; },
    async getAccountBalance(id) { balanceIds.push(id); return -1000; },
  });
  const expected = {
    status: 'ok', server: { version: 'test' }, counts: { accounts: 1, categories: 1 },
    accounts: [{ name: 'Card', closed: false, offbudget: true, balance: -1000 }],
  };

  const doctor = await session(api).doctor(credential);
  const accounts = await session(api).read(credential, { shape: 'accounts' });

  assert.deepEqual(doctor, expected);
  assert.deepEqual(accounts, expected);
  assert.deepEqual(balanceIds, ['account-1', 'account-1']);
  const serialized = JSON.stringify({ doctor, accounts });
  assert.equal(serialized.includes('account-1'), false);
  assert.equal(serialized.includes('sync'), false);
});

test('manual, MCP-like, chat, agent, and evaluation modes cannot mutate', () => {
  for (const mode of ['manual', 'webhook', 'chat', 'agent', 'evaluation', 'cli', 'error']) {
    assert.throws(() => assertActualMutationMode(mode), /forbidden/);
  }
  for (const mode of ['trigger', 'integrated', 'retry']) assert.doesNotThrow(() => assertActualMutationMode(mode));
});
