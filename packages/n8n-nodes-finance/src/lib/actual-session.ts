import { createHash } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import * as actualApi from '@actual-app/api';
import { ACTUAL_DATA_DIR, ActualCredential, ActualImportTransaction, JsonObject, PreparedActualOutbox, assertActualImportTransactions, assertIsoDate, assertObject, assertPreparedOutbox, requiredString } from './contracts';

type ActualReturnedTransaction = Awaited<ReturnType<typeof actualApi.getTransactions>>[number];

export interface ActualApi {
  init(config: { dataDir: string; serverURL: string; password: string; verbose: false }): Promise<unknown>;
  downloadBudget(syncId: string, options?: { password?: string }): Promise<void>;
  sync(): Promise<void>;
  shutdown(): Promise<void>;
  getServerVersion(): Promise<unknown>;
  getAccounts(): Promise<Array<Record<string, unknown>>>;
  getAccountBalance(id: string): Promise<number>;
  getCategories(): Promise<Array<Record<string, unknown>>>;
  getTransactions(accountId: string, start: string, end: string): Promise<ActualReturnedTransaction[]>;
  importTransactions(accountId: string, rows: Array<Record<string, unknown>>, options: { defaultCleared: boolean; reimportDeleted: false }): Promise<Record<string, unknown> & { errors?: Array<{ message: string }> }>;
}

export type ActualReadRequest =
  | { shape: 'accounts' }
  | { shape: 'categories' }
  | { shape: 'transactionsByImportedIds'; account_id: string; imported_ids: string[]; start_date: string; end_date: string };

export interface ActualVerificationRequest {
  account_id: string;
  expected_transactions: ActualImportTransaction[];
  start_date: string;
  end_date: string;
  expected_account_balance?: number;
}

let inProcessTail: Promise<void> = Promise.resolve();

async function serialized<T>(callback: () => Promise<T>): Promise<T> {
  const prior = inProcessTail;
  let release!: () => void;
  inProcessTail = new Promise<void>(resolve => { release = resolve; });
  await prior;
  try { return await callback(); } finally { release(); }
}

function validateCredential(value: ActualCredential): ActualCredential {
  const serverUrl = new URL(requiredString(value.serverUrl, 'Actual server URL', 2048));
  if (!['http:', 'https:'].includes(serverUrl.protocol) || serverUrl.username || serverUrl.password || serverUrl.search || serverUrl.hash) {
    throw new Error('Actual server URL must be an HTTP(S) origin without credentials, query, or fragment');
  }
  if (serverUrl.pathname !== '/' && serverUrl.pathname !== '') throw new Error('Actual server URL must not include a path');
  return {
    serverUrl: serverUrl.origin,
    password: requiredString(value.password, 'Actual password', 1024),
    syncId: requiredString(value.syncId, 'Actual sync ID', 256),
    ...(value.encryptionPassword ? { encryptionPassword: requiredString(value.encryptionPassword, 'Actual encryption password', 1024) } : {}),
    mutationEnabled: value.mutationEnabled === true,
  };
}

function verifyRequest(value: unknown): ActualVerificationRequest {
  assertObject(value, 'verification');
  const accountId = requiredString(value.account_id, 'verification.account_id', 128);
  const expectedTransactions = assertActualImportTransactions(value.expected_transactions, 'verification.expected_transactions');
  const start = assertIsoDate(value.start_date, 'verification.start_date');
  const end = assertIsoDate(value.end_date, 'verification.end_date');
  if (start > end) throw new Error('verification date range is reversed');
  if (value.expected_account_balance !== undefined && !Number.isSafeInteger(value.expected_account_balance)) throw new Error('verification.expected_account_balance must be integer minor units');
  return { account_id: accountId, expected_transactions: expectedTransactions, start_date: start, end_date: end, ...(value.expected_account_balance === undefined ? {} : { expected_account_balance: Number(value.expected_account_balance) }) };
}

function importedIdReadRequest(value: unknown): { account_id: string; imported_ids: string[]; start_date: string; end_date: string } {
  assertObject(value, 'read request');
  const accountId = requiredString(value.account_id, 'read.account_id', 128);
  if (!Array.isArray(value.imported_ids) || value.imported_ids.length === 0 || value.imported_ids.length > 5000) throw new Error('read.imported_ids must contain 1..5000 IDs');
  const importedIds = value.imported_ids.map((id, index) => requiredString(id, `read.imported_ids[${index}]`, 256));
  if (new Set(importedIds).size !== importedIds.length) throw new Error('read.imported_ids contains duplicates');
  const start = assertIsoDate(value.start_date, 'read.start_date');
  const end = assertIsoDate(value.end_date, 'read.end_date');
  if (start > end) throw new Error('read date range is reversed');
  return { account_id: accountId, imported_ids: importedIds, start_date: start, end_date: end };
}

export class ActualSession {
  constructor(
    private readonly api: ActualApi = actualApi as unknown as ActualApi,
    private readonly dataRoot: string = ACTUAL_DATA_DIR,
  ) {}

  async run<T>(credentialValue: ActualCredential, operation: (api: ActualApi, credential: ActualCredential) => Promise<T>): Promise<T> {
    const credential = validateCredential(credentialValue);
    return serialized(async () => {
      const directory = path.join(this.dataRoot, createHash('sha256').update(credential.syncId).digest('hex').slice(0, 16));
      await mkdir(directory, { recursive: true });
      let initialized = false;
      try {
        await this.api.init({ dataDir: directory, serverURL: credential.serverUrl, password: credential.password, verbose: false });
        initialized = true;
        await this.api.downloadBudget(credential.syncId, credential.encryptionPassword ? { password: credential.encryptionPassword } : undefined);
        await this.api.sync();
        const result = await operation(this.api, credential);
        await this.api.sync();
        return result;
      } finally {
        if (initialized) await this.api.shutdown();
      }
    });
  }

  async doctor(credential: ActualCredential): Promise<JsonObject> {
    return this.run(credential, async api => {
      const accounts = await api.getAccounts();
      const categories = await api.getCategories();
      const balances = await Promise.all(accounts.map(async row => ({ name: row.name, closed: Boolean(row.closed), offbudget: Boolean(row.offbudget), balance: await api.getAccountBalance(String(row.id)) })));
      return { status: 'ok', server: await api.getServerVersion(), counts: { accounts: accounts.length, categories: categories.length }, accounts: balances };
    });
  }

  async read(credential: ActualCredential, request: ActualReadRequest): Promise<JsonObject> {
    if (request.shape === 'accounts') return this.doctor(credential);
    if (request.shape === 'categories') return this.run(credential, async api => ({ shape: 'categories', rows: await api.getCategories() }));
    const verified = importedIdReadRequest(request);
    return this.run(credential, async api => ({ shape: 'transactionsByImportedIds', ...(await readTransactionsByImportedIds(api, verified)) }));
  }

  async preflight(credential: ActualCredential, input: unknown): Promise<JsonObject> {
    const outbox = assertPreparedOutbox(input);
    return this.run(credential, async api => {
      const accounts = await api.getAccounts();
      if (!accounts.some(row => row.id === outbox.account_id && row.closed !== true)) throw new Error('outbox account does not exist or is closed');
      const categoryIds = new Set((await api.getCategories()).map(row => String(row.id)));
      const unknownCategories = [...new Set(outbox.transactions.map(row => row.category).filter((id): id is string => Boolean(id) && !categoryIds.has(id!)))];
      if (unknownCategories.length) throw new Error(`outbox contains unknown category IDs: ${unknownCategories.join(', ')}`);
      const dates = outbox.transactions.map(row => row.date).sort();
      const existing = await readTransactionsByImportedIds(api, { account_id: outbox.account_id, imported_ids: outbox.transactions.map(row => row.imported_id), start_date: dates[0], end_date: dates.at(-1)! });
      return { status: 'PREFLIGHT_OK', outbox_id: outbox.outbox_id, account_id: outbox.account_id, transaction_count: outbox.transactions.length, already_observed: existing.found_ids };
    });
  }

  async import(credential: ActualCredential, input: unknown): Promise<JsonObject> {
    const outbox = assertPreparedOutbox(input);
    if (credential.mutationEnabled !== true) throw new Error('Actual mutation credential is disabled');
    return this.run(credential, async api => {
      const accounts = await api.getAccounts();
      if (!accounts.some(row => row.id === outbox.account_id && row.closed !== true)) throw new Error('outbox account does not exist or is closed');
      const categoryIds = new Set((await api.getCategories()).map(row => String(row.id)));
      const unknownCategories = [...new Set(outbox.transactions.map(row => row.category).filter((id): id is string => Boolean(id) && !categoryIds.has(id!)))];
      if (unknownCategories.length) throw new Error(`outbox contains unknown category IDs: ${unknownCategories.join(', ')}`);
      const balanceBefore = await api.getAccountBalance(outbox.account_id);
      const result = await api.importTransactions(outbox.account_id, outbox.transactions.map(row => ({ account: outbox.account_id, ...row })), { defaultCleared: false, reimportDeleted: false });
      const errors = Array.isArray(result.errors) ? result.errors : [];
      if (errors.length) throw new Error(`Actual import returned errors: ${errors.map(error => error.message).join('; ')}`);
      await api.sync();
      const balanceAfter = await api.getAccountBalance(outbox.account_id);
      return {
        status: 'ACTUAL_OBSERVED', outbox_id: outbox.outbox_id,
        writer_lease: outbox.writer_lease, imported_ids: outbox.transactions.map(row => row.imported_id),
        balance_before: balanceBefore, balance_after: balanceAfter,
        actual_result: result,
      };
    });
  }

  async verify(credential: ActualCredential, input: unknown): Promise<JsonObject> {
    const request = verifyRequest(input);
    return this.run(credential, async api => {
      const ids = request.expected_transactions.map(row => row.imported_id);
      const result = await readTransactionsByImportedIds(api, { ...request, imported_ids: ids });
      if (result.missing_ids.length || result.duplicate_ids.length) throw new Error(`Actual verification failed; missing=${result.missing_ids.join(',')} duplicate=${result.duplicate_ids.join(',')}`);
      const expected = request.expected_transactions.map(row => canonicalExpectedRow(request.account_id, row)).sort(byImportedId);
      const observed = result.rows.map(canonicalObservedRow).sort(byImportedId);
      const mismatches = expected.flatMap((row, index) => JSON.stringify(row) === JSON.stringify(observed[index]) ? [] : [{ imported_id: row.imported_id, expected: row, observed: observed[index] ?? null }]);
      if (mismatches.length) throw new Error(`Actual verification field mismatch: ${mismatches.map(row => row.imported_id).join(',')}`);
      const accountBalance = await api.getAccountBalance(request.account_id);
      if (request.expected_account_balance !== undefined && accountBalance !== request.expected_account_balance) throw new Error(`Actual account balance mismatch: expected=${request.expected_account_balance} observed=${accountBalance}`);
      return {
        status: 'VERIFIED', ...result, mismatches: [], account_balance: accountBalance,
        expected_sha256: canonicalHash(expected), observed_sha256: canonicalHash(observed),
        transaction_count: expected.length, amount_sum: expected.reduce((sum, row) => sum + row.amount, 0),
      };
    });
  }
}

type ImportedIdRead = { account_id: string; imported_ids: string[]; start_date: string; end_date: string };

async function readTransactionsByImportedIds(api: ActualApi, request: ImportedIdRead): Promise<{ rows: ActualReturnedTransaction[]; found_ids: string[]; missing_ids: string[]; duplicate_ids: string[] }> {
  const wanted = new Set(request.imported_ids);
  const rows = (await api.getTransactions(request.account_id, request.start_date, request.end_date)).filter(row => wanted.has(String(row.imported_id ?? '')));
  const counts = new Map<string, number>();
  for (const row of rows) counts.set(String(row.imported_id), (counts.get(String(row.imported_id)) ?? 0) + 1);
  const found = [...counts.keys()].sort();
  return {
    rows,
    found_ids: found,
    missing_ids: request.imported_ids.filter(id => !counts.has(id)).sort(),
    duplicate_ids: [...counts.entries()].filter(([, count]) => count > 1).map(([id]) => id).sort(),
  };
}

type EconomicRow = { account_id: string; imported_id: string; date: string; amount: number; imported_payee: string; category: string | null; notes: string | null; cleared: boolean };
function canonicalExpectedRow(accountId: string, row: ActualImportTransaction): EconomicRow {
  return {
    account_id: accountId,
    imported_id: String(row.imported_id ?? ''),
    date: String(row.date ?? ''),
    amount: Number(row.amount),
    imported_payee: String(row.imported_payee ?? ''),
    category: row.category === undefined || row.category === null || row.category === '' ? null : String(row.category),
    notes: row.notes === undefined || row.notes === null || row.notes === '' ? null : String(row.notes),
    cleared: row.cleared === true,
  };
}
function canonicalObservedRow(row: ActualReturnedTransaction): EconomicRow {
  if (!Number.isSafeInteger(row.amount)) throw new Error('Actual returned transaction amount must be integer minor units');
  return {
    account_id: requiredString(row.account, 'Actual returned transaction account', 128),
    imported_id: requiredString(row.imported_id, 'Actual returned imported_id', 256),
    date: assertIsoDate(row.date, 'Actual returned date'),
    amount: Number(row.amount),
    imported_payee: requiredString(row.imported_payee, 'Actual returned imported_payee', 512),
    category: row.category === undefined || row.category === null || row.category === '' ? null : String(row.category),
    notes: row.notes === undefined || row.notes === null || row.notes === '' ? null : String(row.notes),
    cleared: row.cleared === true,
  };
}
const byImportedId = (left: EconomicRow, right: EconomicRow) => left.imported_id.localeCompare(right.imported_id);
function canonicalHash(rows: EconomicRow[]): string {
  return createHash('sha256').update(JSON.stringify(rows)).digest('hex');
}

export function preflightOutbox(input: unknown): PreparedActualOutbox {
  return assertPreparedOutbox(input);
}
