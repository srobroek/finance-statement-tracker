import assert from 'node:assert/strict';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { ActualApi, ActualSession, preflightOutbox } from './actual-session';
import { assertActualMutationMode } from './contracts';

const future = () => new Date(Date.now() + 60_000).toISOString();
const envelope = () => ({
  schema_version: 1, outbox_id: 'outbox-1', state: 'PREPARED', account_id: 'account-1',
  execution_context: { trigger: 'SCHEDULE', manual: false, mcp: false },
  writer_lease: { lease_id: 'lease-1', fencing_token: 1, expires_at: future() },
  transactions: [{ imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant' }],
});

function fakeApi(overrides: Partial<ActualApi> = {}, initialTransactions: Array<Record<string, unknown>> = [{ id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant', category: undefined, notes: undefined, cleared: false }]): ActualApi & { calls: string[] } {
  const calls: string[] = [];
  const transactions = initialTransactions.map(row => ({ ...row }));
  let balance = transactions.reduce((sum, row) => sum + Number(row.amount ?? 0), 0);
  return {
    calls,
    async init() { calls.push('init'); }, async downloadBudget() { calls.push('download'); },
    async sync() { calls.push('sync'); }, async shutdown() { calls.push('shutdown'); },
    async getServerVersion() { return { version: 'test' }; },
    async getAccounts() { return [{ id: 'account-1', name: 'Card', closed: false }]; },
    async getAccountBalance() { return balance; }, async getCategories() { return [{ id: 'cat-1', name: 'Shopping' }]; },
    async getTransactions() { return transactions; },
    async importTransactions(_account, rows, options) {
      calls.push(`import:${String(options.reimportDeleted)}`);
      const added = rows.map((row, index) => {
        const id = `added-${transactions.length + index + 1}`;
        transactions.push({ ...row, id });
        balance += Number(row.amount);
        return id;
      });
      return { errors: [], added, updated: [], updatedPreview: [] };
    },
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

test('import forces reimportDeleted false, syncs before and after, and shuts down', async () => {
  const api = fakeApi({}, []);
  const result = await session(api).import(credential, envelope());
  assert.equal(result.status, 'ACTUAL_OBSERVED');
  assert.deepEqual(api.calls, ['init', 'download', 'sync', 'import:false', 'sync', 'sync', 'shutdown']);
});

test('Actual cache separates server origins sharing a sync ID', async () => {
  const dataDirectories: string[] = [];
  const api = fakeApi({
    async init(config) { dataDirectories.push(config.dataDir); },
  });
  await session(api).doctor(credential);
  await session(api).doctor({ ...credential, serverUrl: 'https://another-actual.example' });
  assert.equal(dataDirectories.length, 2);
  assert.notEqual(dataDirectories[0], dataDirectories[1]);
});

test('import revalidates the lease immediately before Actual mutation', async () => {
  const originalNow = Date.now;
  let now = originalNow();
  Date.now = () => now;
  let importCalled = false;
  try {
    const expiresAt = new Date(now + 500).toISOString();
    const api = fakeApi({
      async sync() { now += 1000; },
      async importTransactions() {
        importCalled = true;
        return { errors: [], added: [], updated: [], updatedPreview: [] };
      },
    }, []);
    await assert.rejects(
      session(api).import(credential, { ...envelope(), writer_lease: { lease_id: 'lease-1', fencing_token: 1, expires_at: expiresAt } }),
      /expired/,
    );
    assert.equal(importCalled, false);
  } finally {
    Date.now = originalNow;
  }
});

test('replay skips existing IDs so manual split and transfer state cannot change', async () => {
  const api = fakeApi();
  const result = await session(api).import(credential, envelope());
  assert.equal(result.status, 'ACTUAL_OBSERVED');
  assert.deepEqual(result.already_observed, ['statement:one']);
  assert.equal(api.calls.includes('import:false'), false);
});

test('fuzzy match reports updated ID without counting a new balance delta', async () => {
  let imported = false;
  const importedId = 'statement:adcb_v1:fuzzy';
  const manualRow = {
    id: 'manual-1', account: 'account-1', imported_id: undefined,
    date: '2026-08-01', amount: -1000, imported_payee: 'Imported merchant',
    payee: 'Existing Payee', category: 'manual-category', notes: 'manual note', cleared: true,
  };
  const api = fakeApi({
    async getTransactions() {
      return imported ? [{ ...manualRow, imported_id: importedId }] : [manualRow];
    },
    async importTransactions() {
      imported = true;
      return { errors: [], added: [], updated: ['manual-1'], updatedPreview: [] };
    },
  }, []);
  const historical = {
    ...envelope(),
    historical_import: true,
    historical_source: 'ADCB_CASHBACK',
    historical_account_id: 'account-1',
    card_code: 'ADCB_CASHBACK',
    transactions: [{
      imported_id: importedId, date: '2026-08-01', amount: -1000,
      imported_payee: 'Imported merchant', cleared: true,
    }],
  };
  const result = await session(api).import(credential, historical);
  assert.deepEqual(result.added_imported_ids, []);
  assert.deepEqual(result.reconciled_imported_ids, [importedId]);
  assert.equal(result.applied_delta, 0);
  const verified = await session(api).verify(credential, {
    account_id: 'account-1', expected_transactions: historical.transactions,
    start_date: '2026-08-01', end_date: '2026-08-01',
    preserve_manual_fields_for_ids: [importedId],
  });
  assert.equal(verified.status, 'VERIFIED');
});

test('import fails closed when Actual balance does not match added IDs', async () => {
  const api = fakeApi({ async getAccountBalance() { return -1000; } }, []);
  await assert.rejects(session(api).import(credential, envelope()), /balance delta mismatch/);
});

test('import rejects mutation IDs outside the submitted transaction set', async () => {
  const api = fakeApi({
    async importTransactions() {
      return { errors: [], added: ['unexpected-internal-id'], updated: [], updatedPreview: [] };
    },
  }, []);
  await assert.rejects(session(api).import(credential, envelope()), /unexpected mutation IDs/);
});

test('returned Actual errors fail closed and still shut down', async () => {
  const api = fakeApi({
    async getTransactions() { return []; },
    async importTransactions() { return { errors: [{ message: 'bad row' }] }; },
  });
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

test('closed ADCB history requires an explicit, source-bound cleared import', async () => {
  const historical = {
    ...envelope(),
    historical_import: true,
    historical_source: 'ADCB_CASHBACK',
    historical_account_id: 'account-1',
    card_code: 'ADCB_CASHBACK',
    transactions: [{
      imported_id: 'statement:adcb_v1:0123456789abcdef01234567',
      date: '2026-08-01', amount: -1000, imported_payee: 'Merchant', cleared: true,
    }],
  };
  const closedApi = fakeApi({
    async getAccounts() { return [{ id: 'account-1', name: 'Configured closed card', closed: true, offbudget: false }]; },
  }, []);

  assert.equal((await session(fakeApi()).preflight(credential, historical)).status, 'PREFLIGHT_OK');

  const preflight = await session(closedApi).preflight(credential, historical);
  assert.equal(preflight.status, 'PREFLIGHT_OK');
  const imported = await session(closedApi).import(credential, historical);
  assert.equal(imported.status, 'ACTUAL_OBSERVED');

  await assert.rejects(
    session(closedApi).preflight(credential, envelope()),
    /explicit ADCB historical_import is required/,
  );
  await assert.rejects(
    session(closedApi).preflight(credential, { ...historical, historical_source: 'OTHER' }),
    /requires the ADCB_CASHBACK source/,
  );
  await assert.rejects(
    session(closedApi).preflight(credential, { ...historical, historical_account_id: 'other-account' }),
    /historical account binding does not match account_id/,
  );
  await assert.rejects(
    session(closedApi).preflight(credential, { ...historical, card_code: 'OTHER_CARD' }),
    /configured ADCB_CASHBACK card mapping/,
  );
  await assert.rejects(
    session(closedApi).preflight(credential, { ...historical, transactions: [{ ...historical.transactions[0], imported_id: 'browser:adcb:row' }] }),
    /statement:adcb_v1: imported IDs/,
  );
  await assert.rejects(
    session(closedApi).preflight(credential, { ...historical, transactions: [{ ...historical.transactions[0], cleared: false }] }),
    /rows must be cleared/,
  );
});

test('preflight rejects immutable replay drift before Actual mutation', async () => {
  const drift = fakeApi({
    async getTransactions() {
      return [{
        id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-02',
        amount: -999, imported_payee: 'Merchant', category: 'manual-category',
        notes: 'manual note', cleared: true,
      }];
    },
    });
  await assert.rejects(session(drift).preflight(credential, envelope()), /immutable field drift.*date/);

  const missingSourcePayee = fakeApi({
    async getTransactions() {
      return [{
        id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01',
        amount: -1000, imported_payee: null,
      }];
    },
  });
  await assert.rejects(session(missingSourcePayee).preflight(credential, envelope()), /immutable field drift.*imported_payee/);

  const manual = fakeApi({
    async getTransactions() {
      return [{
        id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01',
        amount: -1000, imported_payee: 'Merchant', category: 'manual-category',
        notes: 'manual note', cleared: true,
      }];
    },
  });
  assert.equal((await session(manual).preflight(credential, envelope())).status, 'PREFLIGHT_OK');
  const verification = {
    account_id: 'account-1', expected_transactions: envelope().transactions,
    start_date: '2026-08-01', end_date: '2026-08-01', preserve_manual_fields_for_ids: ['statement:one'],
  };
  assert.equal((await session(manual).verify(credential, verification)).status, 'VERIFIED');
  await assert.rejects(session(manual).verify(credential, { ...verification, preserve_manual_fields_for_ids: [] }), /field mismatch/);
  await assert.rejects(session(manual).verify(credential, { ...verification, preserve_manual_fields_for_ids: ['unknown'] }), /unexpected imported ID/);
});

test('preflight rejects duplicate imported IDs already present in Actual', async () => {
  const duplicate = fakeApi({
    async getTransactions() { return [
      { id: 'tx-1', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant' },
      { id: 'tx-2', account: 'account-1', imported_id: 'statement:one', date: '2026-08-01', amount: -1000, imported_payee: 'Merchant' },
    ]; },
  });
  await assert.rejects(session(duplicate).preflight(credential, envelope()), /duplicate imported IDs/);
});

test('new imported rows still verify mutable fields when existing rows are preserved', async () => {
  const first = envelope().transactions[0];
  const second = { imported_id: 'statement:new', date: '2026-08-02', amount: -2000, imported_payee: 'New merchant', category: 'cat-1', notes: 'expected', cleared: false };
  const verification = {
    account_id: 'account-1', expected_transactions: [first, second],
    start_date: '2026-08-01', end_date: '2026-08-02', preserve_manual_fields_for_ids: [first.imported_id],
  };
  const changedNew = fakeApi({
    async getTransactions() { return [
      { id: 'tx-1', account: 'account-1', imported_id: first.imported_id, date: first.date, amount: first.amount, imported_payee: first.imported_payee, category: 'manual-category', notes: 'manual note', cleared: true },
      { id: 'tx-2', account: 'account-1', imported_id: second.imported_id, date: second.date, amount: second.amount, imported_payee: second.imported_payee, category: 'other-category', notes: second.notes, cleared: second.cleared },
    ]; },
  });
  await assert.rejects(session(changedNew).verify(credential, verification), /field mismatch: statement:new/);
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
