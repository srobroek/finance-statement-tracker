import { createHash } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import * as actualApi from '@actual-app/api';
import { ACTUAL_DATA_DIR, ActualCredential, ActualImportTransaction, JsonObject, PreparedActualOutbox, assertActualImportTransactions, assertIsoDate, assertObject, assertPreparedOutbox, requiredString } from './contracts';

type ActualReturnedTransaction = Awaited<ReturnType<typeof actualApi.getTransactions>>[number];

// Historical ADCB statements may need to be replayed into an account that is
// already closed. Keep that exception narrow: a caller must opt in from the
// trusted statement workflow, identify the source, and provide only cleared
// rows from the verified ADCB adapter. Other closed accounts remain blocked.
const ADCB_HISTORICAL_SOURCE = 'ADCB_CASHBACK' as const;
const ADCB_HISTORICAL_ID_PREFIX = 'statement:adcb_v1:' as const;
const ACCOUNT_HISTORY_START = '1900-01-01' as const;
const ACCOUNT_HISTORY_END = '2100-12-31' as const;

export interface ActualApi {
  init(config: { dataDir: string; serverURL: string; password: string; verbose: false }): Promise<unknown>;
  downloadBudget(syncId: string, options?: { password?: string }): Promise<void>;
  sync(): Promise<void>;
  shutdown(): Promise<void>;
  getServerVersion(): Promise<unknown>;
  getAccounts(): Promise<Array<Record<string, unknown>>>;
  getAccountBalance(id: string, cutoff?: Date): Promise<number>;
  getCategories(): Promise<Array<Record<string, unknown>>>;
  getPayees(): Promise<Array<Record<string, unknown>>>;
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
  /**
   * Existing imported IDs whose mutable Actual fields are user-owned. This
   * list is captured by preflight and passed to verify after import. New IDs
   * are always checked against the expected category/notes/cleared fields.
   */
  preserve_manual_fields_for_ids?: string[];
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
  let preserveManualFieldsForIds: string[] | undefined;
  if (value.preserve_manual_fields_for_ids !== undefined) {
    if (!Array.isArray(value.preserve_manual_fields_for_ids) || value.preserve_manual_fields_for_ids.length > expectedTransactions.length) {
      throw new Error('verification.preserve_manual_fields_for_ids must be an array of expected imported IDs');
    }
    const expectedIds = new Set(expectedTransactions.map(row => row.imported_id));
    const ids = value.preserve_manual_fields_for_ids.map((id, index) => requiredString(id, `verification.preserve_manual_fields_for_ids[${index}]`, 256));
    if (new Set(ids).size !== ids.length) throw new Error('verification.preserve_manual_fields_for_ids contains duplicates');
    if (ids.some(id => !expectedIds.has(id))) throw new Error('verification.preserve_manual_fields_for_ids contains an unexpected imported ID');
    preserveManualFieldsForIds = ids;
  }
  return {
    account_id: accountId,
    expected_transactions: expectedTransactions,
    start_date: start,
    end_date: end,
    ...(value.expected_account_balance === undefined ? {} : { expected_account_balance: Number(value.expected_account_balance) }),
    ...(preserveManualFieldsForIds === undefined ? {} : { preserve_manual_fields_for_ids: preserveManualFieldsForIds }),
  };
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

function assertHistoricalOutboxAllowed(
  outbox: PreparedActualOutbox,
  account: Record<string, unknown>,
): void {
  const optedIn = outbox.historical_import === true;
  if (!optedIn && outbox.historical_source !== undefined) {
    throw new Error('outbox.historical_source requires historical_import');
  }
  if (optedIn && outbox.historical_source !== ADCB_HISTORICAL_SOURCE) {
    throw new Error('historical_import requires the ADCB_CASHBACK source');
  }

  if (account.closed === true && !optedIn) {
    throw new Error('outbox account does not exist or is closed; explicit ADCB historical_import is required');
  }
  if (!optedIn) return;

  if (outbox.card_code !== ADCB_HISTORICAL_SOURCE) {
    throw new Error('historical_import requires the configured ADCB_CASHBACK card mapping');
  }
  if (outbox.historical_account_id !== outbox.account_id) {
    throw new Error('historical_import account binding does not match account_id');
  }
  if (account.offbudget === true) {
    throw new Error('historical_import cannot target an off-budget account');
  }
  if (outbox.transactions.some(row => !row.imported_id.startsWith(ADCB_HISTORICAL_ID_PREFIX))) {
    throw new Error('historical_import rows must use statement:adcb_v1: imported IDs');
  }
  if (outbox.transactions.some(row => row.cleared !== true)) {
    throw new Error('historical_import rows must be cleared statement rows');
  }
}

/**
 * Actual's import reconciler deliberately keeps an existing row's amount and
 * date.  A changed statement row would therefore appear to import cleanly
 * while retaining stale economics. Reject that conflict before mutation;
 * category, notes, payee, cleared, and transfer links remain user-owned.
 */
function assertExistingImmutableFacts(
  outbox: PreparedActualOutbox,
  existing: ActualReturnedTransaction[],
): void {
  const expected = new Map(outbox.transactions.map(row => [row.imported_id, row]));
  for (const row of existing) {
    const importedId = String(row.imported_id ?? '');
    const incoming = expected.get(importedId);
    if (!incoming) continue;
    if (String(row.account ?? '') !== outbox.account_id) {
      throw new Error(`Actual immutable field drift for ${importedId}: account`);
    }
    if (String(row.date ?? '') !== incoming.date) {
      throw new Error(`Actual immutable field drift for ${importedId}: date`);
    }
    if (!Number.isSafeInteger(row.amount) || Number(row.amount) !== incoming.amount) {
      throw new Error(`Actual immutable field drift for ${importedId}: amount`);
    }
    if (String(row.imported_payee ?? '') !== incoming.imported_payee) {
      throw new Error(`Actual immutable field drift for ${importedId}: imported_payee`);
    }
  }
}

async function existingTransactionsForOutbox(
  api: ActualApi,
  outbox: PreparedActualOutbox,
): Promise<Awaited<ReturnType<typeof readTransactionsByImportedIds>>> {
  // Query the bounded account history instead of only the incoming statement
  // period. Actual's reconciler keeps an existing row's date, so a stale row
  // whose date moved outside the statement window would otherwise be missed
  // and silently retain old economics.
  return readTransactionsByImportedIds(api, {
    account_id: outbox.account_id,
    imported_ids: outbox.transactions.map(row => row.imported_id),
    start_date: ACCOUNT_HISTORY_START,
    end_date: ACCOUNT_HISTORY_END,
  });
}

function mutationIds(result: Record<string, unknown>, field: 'added' | 'updated'): string[] {
  const value = result[field];
  if (!Array.isArray(value)) throw new Error(`Actual import result is missing ${field} IDs`);
  const ids = value.map((id, index) => requiredString(id, `Actual import ${field}[${index}]`, 128));
  if (new Set(ids).size !== ids.length) throw new Error(`Actual import result contains duplicate ${field} IDs`);
  return ids;
}

export class ActualSession {
  constructor(
    private readonly api: ActualApi = actualApi as unknown as ActualApi,
    private readonly dataRoot: string = ACTUAL_DATA_DIR,
  ) {}

  async run<T>(credentialValue: ActualCredential, operation: (api: ActualApi, credential: ActualCredential) => Promise<T>): Promise<T> {
    const credential = validateCredential(credentialValue);
    return serialized(async () => {
      // A sync ID is only unique within an Actual server. Include the
      // canonical server origin so a cloned/migrated budget cannot reuse a
      // different server's local cache and encryption metadata.
      const cacheKey = `${credential.serverUrl}\n${credential.syncId}`;
      const directory = path.join(this.dataRoot, createHash('sha256').update(cacheKey).digest('hex').slice(0, 16));
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
    if (request.shape === 'categories') return this.run(credential, async (api, auth) => ({ shape: 'categories', actual_file_id: auth.syncId, rows: await api.getCategories(), payees: await api.getPayees() }));
    const verified = importedIdReadRequest(request);
    return this.run(credential, async api => ({ shape: 'transactionsByImportedIds', ...(await readTransactionsByImportedIds(api, verified)) }));
  }

  async preflight(credential: ActualCredential, input: unknown): Promise<JsonObject> {
    const outbox = assertPreparedOutbox(input);
    return this.run(credential, async api => {
      const accounts = await api.getAccounts();
      const account = accounts.find(row => String(row.id) === outbox.account_id);
      if (!account) throw new Error('outbox account does not exist or is closed');
      assertHistoricalOutboxAllowed(outbox, account);
      const categoryIds = new Set((await api.getCategories()).map(row => String(row.id)));
      const unknownCategories = [...new Set(outbox.transactions.map(row => row.category).filter((id): id is string => Boolean(id) && !categoryIds.has(id!)))];
      if (unknownCategories.length) throw new Error(`outbox contains unknown category IDs: ${unknownCategories.join(', ')}`);
      const payeeIds = new Set((await api.getPayees()).map(row => String(row.id)));
      const unknownPayees = outbox.transactions.filter(row => row.payee && !payeeIds.has(row.payee));
      if (unknownPayees.length) throw new Error('outbox contains unknown payee IDs');
      const existing = await existingTransactionsForOutbox(api, outbox);
      if (existing.duplicate_ids.length) throw new Error(`Actual contains duplicate imported IDs: ${existing.duplicate_ids.join(', ')}`);
      assertExistingImmutableFacts(outbox, existing.rows);
      return { status: 'PREFLIGHT_OK', outbox_id: outbox.outbox_id, account_id: outbox.account_id, transaction_count: outbox.transactions.length, already_observed: existing.found_ids };
    });
  }

  async import(credential: ActualCredential, input: unknown): Promise<JsonObject> {
    const outbox = assertPreparedOutbox(input);
    if (credential.mutationEnabled !== true) throw new Error('Actual mutation credential is disabled');
    return this.run(credential, async api => {
      const accounts = await api.getAccounts();
      const account = accounts.find(row => String(row.id) === outbox.account_id);
      if (!account) throw new Error('outbox account does not exist or is closed');
      assertHistoricalOutboxAllowed(outbox, account);
      const categoryIds = new Set((await api.getCategories()).map(row => String(row.id)));
      const unknownCategories = [...new Set(outbox.transactions.map(row => row.category).filter((id): id is string => Boolean(id) && !categoryIds.has(id!)))];
      if (unknownCategories.length) throw new Error(`outbox contains unknown category IDs: ${unknownCategories.join(', ')}`);
      const payeeIds = new Set((await api.getPayees()).map(row => String(row.id)));
      const unknownPayees = outbox.transactions.filter(row => row.payee && !payeeIds.has(row.payee));
      if (unknownPayees.length) throw new Error('outbox contains unknown payee IDs');
      const existing = await existingTransactionsForOutbox(api, outbox);
      if (existing.duplicate_ids.length) throw new Error(`Actual contains duplicate imported IDs: ${existing.duplicate_ids.join(', ')}`);
      assertExistingImmutableFacts(outbox, existing.rows);
      const balanceBefore = await api.getAccountBalance(outbox.account_id);
      // Do not send replayed IDs back through Actual's reconciler. It normally
      // preserves payee/category/notes, but can still propagate a changed
      // cleared value to split children; skipping existing IDs protects
      // manual splits, transfer links, and reconciliation state completely.
      const existingIds = new Set(existing.found_ids);
      const newRows = outbox.transactions
        .filter(row => !existingIds.has(row.imported_id))
        .map(row => ({ account: outbox.account_id, ...row }));
      let result: Record<string, unknown> & { errors?: Array<{ message: string }> };
      if (newRows.length) {
        // The outbox was validated before entering the serialized session, but
        // download/sync/account reads can consume the lease. Revalidate at the
        // mutation boundary so an expired runner can never write to Actual.
        assertPreparedOutbox(outbox);
        result = await api.importTransactions(outbox.account_id, newRows, { defaultCleared: false, reimportDeleted: false });
      } else {
        result = { errors: [], added: [], updated: [], updatedPreview: [], skippedExisting: existing.found_ids };
      }
      const errors = Array.isArray(result.errors) ? result.errors : [];
      if (errors.length) throw new Error(`Actual import returned errors: ${errors.map(error => error.message).join('; ')}`);
      await api.sync();
      const balanceAfter = await api.getAccountBalance(outbox.account_id);
      if (!Number.isSafeInteger(balanceBefore) || !Number.isSafeInteger(balanceAfter)) {
        throw new Error('Actual account balance must be integer minor units');
      }

      // Actual can fuzzy-match a new imported ID onto an existing manual row.
      // In that case the API reports `updated`, and the account balance must
      // not move even though this request contained a new ID. Read the exact
      // IDs back and derive the delta from the IDs Actual says it added;
      // never assume every submitted row changed the balance.
      const addedIds = mutationIds(result, 'added');
      const updatedIds = mutationIds(result, 'updated');
      const addedIdSet = new Set(addedIds);
      const updatedIdSet = new Set(updatedIds);
      if (addedIds.some(id => updatedIdSet.has(id))) {
        throw new Error('Actual import result ID appears in both added and updated');
      }
      const postImport = await existingTransactionsForOutbox(api, outbox);
      if (postImport.duplicate_ids.length) throw new Error(`Actual contains duplicate imported IDs after import: ${postImport.duplicate_ids.join(', ')}`);
      assertExistingImmutableFacts(outbox, postImport.rows);
      const postByImportedId = new Map(postImport.rows.map(row => [String(row.imported_id), row]));
      const postRows = await api.getTransactions(outbox.account_id, ACCOUNT_HISTORY_START, ACCOUNT_HISTORY_END);
      const postById = new Map(postRows.map(row => [String(row.id ?? ''), row]));
      const submittedInternalIds = new Set<string>();
      for (const row of newRows) {
        const observed = postByImportedId.get(String(row.imported_id));
        if (observed?.id !== undefined) submittedInternalIds.add(String(observed.id));
      }
      const unexpectedAdded = addedIds.filter(id => !submittedInternalIds.has(id));
      const unexpectedUpdated = updatedIds.filter(id => {
        if (submittedInternalIds.has(id)) return false;
        const child = postById.get(id);
        return String(child?.parent_id ?? '') === '' || !submittedInternalIds.has(String(child?.parent_id));
      });
      if (unexpectedAdded.length || unexpectedUpdated.length) {
        throw new Error(`Actual import result contains unexpected mutation IDs: ${[...unexpectedAdded, ...unexpectedUpdated].join(', ')}`);
      }
      const addedImportedIds: string[] = [];
      const reconciledImportedIds: string[] = [];
      const addedAmounts: number[] = [];
      for (const row of newRows) {
        const importedId = String(row.imported_id);
        const observed = postByImportedId.get(importedId);
        const internalId = String(observed?.id ?? '');
        if (!observed || !internalId) throw new Error(`Actual import did not materialize ${importedId}`);
        if (addedIdSet.has(internalId)) {
          if (!Number.isSafeInteger(observed.amount)) throw new Error(`Actual added transaction amount is not integer: ${importedId}`);
          addedImportedIds.push(importedId);
          addedAmounts.push(Number(observed.amount));
        } else if (updatedIdSet.has(internalId)) {
          reconciledImportedIds.push(importedId);
        } else {
          throw new Error(`Actual import result did not classify ${importedId} as added or updated`);
        }
      }
      const appliedDelta = addedAmounts.reduce((sum, amount) => sum + amount, 0);
      const expectedBalanceAfter = balanceBefore + appliedDelta;
      if (!Number.isSafeInteger(expectedBalanceAfter) || balanceAfter !== expectedBalanceAfter) {
        throw new Error(`Actual account balance delta mismatch: before=${balanceBefore} applied=${appliedDelta} after=${balanceAfter}`);
      }
      return {
        status: 'ACTUAL_OBSERVED', outbox_id: outbox.outbox_id,
        writer_lease: outbox.writer_lease, imported_ids: outbox.transactions.map(row => row.imported_id), already_observed: existing.found_ids,
        balance_before: balanceBefore, balance_after: balanceAfter,
        expected_balance_after: expectedBalanceAfter, applied_delta: appliedDelta,
        added_imported_ids: addedImportedIds.sort(), reconciled_imported_ids: reconciledImportedIds.sort(),
        actual_result: result,
      };
    });
  }

  async verify(credential: ActualCredential, input: unknown): Promise<JsonObject> {
    const request = verifyRequest(input);
    return this.run(credential, async api => {
      const ids = request.expected_transactions.map(row => row.imported_id);
      // Receipt period and closing-balance cutoff remain issuer facts. The ID
      // lookup must also cover explicitly projected source dates (for example
      // Wio's prior-period printed dates with no separate posting-date column).
      const projectedDates = request.expected_transactions.map(row => row.date);
      const readStart = [request.start_date, ...projectedDates].sort()[0];
      const readEnd = [request.end_date, ...projectedDates].sort().at(-1)!;
      const result = await readTransactionsByImportedIds(api, { ...request, start_date: readStart, end_date: readEnd, imported_ids: ids });
      if (result.missing_ids.length || result.duplicate_ids.length) throw new Error(`Actual verification failed; missing=${result.missing_ids.join(',')} duplicate=${result.duplicate_ids.join(',')}`);
      const preserveManualFieldsForIds = new Set(request.preserve_manual_fields_for_ids ?? []);
      const expected = request.expected_transactions.map(row => canonicalExpectedRow(request.account_id, row, preserveManualFieldsForIds.has(row.imported_id))).sort(byImportedId);
      const expectedById = new Map(request.expected_transactions.map(row => [row.imported_id, row]));
      const observed = result.rows.map(row => canonicalObservedRow(row, preserveManualFieldsForIds.has(String(row.imported_id ?? '')), expectedById.get(String(row.imported_id))?.payee !== undefined)).sort(byImportedId);
      const mismatches = expected.flatMap((row, index) => JSON.stringify(row) === JSON.stringify(observed[index]) ? [] : [{ imported_id: row.imported_id, expected: row, observed: observed[index] ?? null }]);
      if (mismatches.length) throw new Error(`Actual verification field mismatch: ${mismatches.map(row => row.imported_id).join(',')}`);
      // Statement reconciliation is against the inclusive statement date, even
      // when newer transactions already exist in the account.
      const cutoff = request.expected_account_balance === undefined ? undefined : new Date(request.end_date + 'T12:00:00');
      const accountBalance = await api.getAccountBalance(request.account_id, cutoff);
      if (request.expected_account_balance !== undefined && accountBalance !== request.expected_account_balance) throw new Error(`Actual account balance mismatch: expected=${request.expected_account_balance} observed=${accountBalance}`);
      return {
        status: 'VERIFIED', ...result, mismatches: [], account_balance: accountBalance,
        expected_sha256: canonicalHash(expected), observed_sha256: canonicalHash(observed),
        transaction_count: expected.length, amount_sum: expected.reduce((sum, row) => sum + row.amount, 0),
        preserve_manual_fields_for_ids: [...preserveManualFieldsForIds].sort(),
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

type EconomicRow = { account_id: string; imported_id: string; date: string; amount: number; imported_payee: string; category?: string | null; notes?: string | null; cleared?: boolean; payee?: string | null };
function canonicalExpectedRow(accountId: string, row: ActualImportTransaction, preserveManualFields = false): EconomicRow {
  const result: EconomicRow = {
    account_id: accountId,
    imported_id: String(row.imported_id ?? ''),
    date: String(row.date ?? ''),
    amount: Number(row.amount),
    imported_payee: String(row.imported_payee ?? ''),
  };
  if (!preserveManualFields) {
    result.category = row.category === undefined || row.category === null || row.category === '' ? null : String(row.category);
    result.notes = row.notes === undefined || row.notes === null || row.notes === '' ? null : String(row.notes);
    result.cleared = row.cleared === true;
    if (row.payee !== undefined) result.payee = row.payee;
  }
  return result;
}
function canonicalObservedRow(row: ActualReturnedTransaction, preserveManualFields = false, verifyPayee = false): EconomicRow {
  if (!Number.isSafeInteger(row.amount)) throw new Error('Actual returned transaction amount must be integer minor units');
  const result: EconomicRow = {
    account_id: requiredString(row.account, 'Actual returned transaction account', 128),
    imported_id: requiredString(row.imported_id, 'Actual returned imported_id', 256),
    date: assertIsoDate(row.date, 'Actual returned date'),
    amount: Number(row.amount),
    imported_payee: requiredString(row.imported_payee, 'Actual returned imported_payee', 512),
  };
  if (!preserveManualFields) {
    result.category = row.category === undefined || row.category === null || row.category === '' ? null : String(row.category);
    result.notes = row.notes === undefined || row.notes === null || row.notes === '' ? null : String(row.notes);
    result.cleared = row.cleared === true;
    if (verifyPayee) result.payee = row.payee == null ? null : String(row.payee);
  }
  return result;
}
const byImportedId = (left: EconomicRow, right: EconomicRow) => left.imported_id.localeCompare(right.imported_id);
function canonicalHash(rows: EconomicRow[]): string {
  return createHash('sha256').update(JSON.stringify(rows)).digest('hex');
}

export function preflightOutbox(input: unknown): PreparedActualOutbox {
  return assertPreparedOutbox(input);
}
