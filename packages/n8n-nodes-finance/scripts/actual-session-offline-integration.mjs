#!/usr/bin/env node

/*
 * Offline integration proof for ActualSession.
 *
 * This deliberately uses the real @actual-app/api database and reconciler,
 * while injecting an API wrapper whose lifecycle methods are no-ops. That
 * keeps the session under test in charge of validation and reconciliation
 * without attempting to download, sync, or upload a production budget.
 *
 * Run after the package has been built:
 *   npm run build && node scripts/actual-session-offline-integration.mjs
 */

import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const actual = require('@actual-app/api');
const { ActualSession } = require('../dist/lib/actual-session.js');

const HISTORY_START = '1900-01-01';
const HISTORY_END = '2100-12-31';
const credential = {
  // The injected wrapper never uses this endpoint. The origin is still valid
  // so ActualSession exercises its normal credential validation.
  serverUrl: 'http://actual.invalid',
  password: 'offline-test-only',
  syncId: 'offline-actual-session-integration',
  mutationEnabled: true,
};

let outboxNumber = 0;

function outbox(accountId, transactions, extra = {}) {
  outboxNumber += 1;
  return {
    schema_version: 1,
    outbox_id: `offline-session:${outboxNumber}`,
    state: 'PREPARED',
    account_id: accountId,
    execution_context: { trigger: 'SCHEDULE', manual: false, mcp: false },
    writer_lease: {
      lease_id: 'offline-session-test',
      fencing_token: 1,
      expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    },
    transactions,
    ...extra,
  };
}

async function rowsFor(accountId) {
  return actual.getTransactions(accountId, HISTORY_START, HISTORY_END);
}

async function rowFor(accountId, importedId) {
  const rows = await rowsFor(accountId);
  return rows.find(row => row.imported_id === importedId);
}

function splitManualProjection(row) {
  return {
    category: row.category,
    notes: row.notes,
    cleared: row.cleared,
    reconciled: row.reconciled,
    is_parent: row.is_parent,
    children: (row.subtransactions ?? [])
      .map(child => ({
        amount: child.amount,
        notes: child.notes,
        category: child.category,
        cleared: child.cleared,
        reconciled: child.reconciled,
      }))
      .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))),
  };
}

// Actual 26.8.1 exposes some mutators through a fire-and-forget handler. A
// macrotask lets its queued database messages settle before authoritative
// readback or shutdown.
async function settleActual() {
  await new Promise(resolve => setImmediate(resolve));
}

function actualSessionApi() {
  return {
    async init() {},
    async downloadBudget() {},
    async sync() {},
    async shutdown() {},
    getServerVersion: () => actual.getServerVersion(),
    getAccounts: () => actual.getAccounts(),
    getAccountBalance: accountId => actual.getAccountBalance(accountId),
    getCategories: () => actual.getCategories(),
    getTransactions: (...args) => actual.getTransactions(...args),
    async importTransactions(...args) {
      const result = await actual.importTransactions(...args);
      await settleActual();
      return result;
    },
  };
}

async function createAccount(name) {
  return actual.createAccount({
    name,
    type: 'checking',
    offbudget: false,
    closed: false,
  }, 0);
}

async function main() {
  const tempRoot = await mkdtemp(path.join(tmpdir(), 'finance-actual-session-offline-'));
  const dataDir = path.join(tempRoot, 'data');
  await mkdir(dataDir);
  let actualInitialized = false;

  try {
    // runImport creates a fresh local budget with avoidUpload=true. No server
    // URL or credential is supplied to this Actual API instance.
    await actual.init({ dataDir });
    actualInitialized = true;
    await actual.runImport('ActualSession offline integration', async () => {});

    const session = new ActualSession(actualSessionApi(), tempRoot);
    const groupId = await actual.createCategoryGroup({ name: 'Offline integration' });
    const categoryId = await actual.createCategory({ name: 'Manual category', group_id: groupId });

    // New posted rows must be materialized and change the account balance by
    // exactly the amount that Actual reports as added.
    const newAccount = await createAccount('Offline new posted row');
    const newRow = {
      imported_id: 'offline:posted:new-1',
      date: '2026-08-01',
      amount: -1000,
      imported_payee: 'Posted merchant',
      cleared: false,
    };
    const newResult = await session.import(credential, outbox(newAccount, [newRow]));
    assert.deepEqual(newResult.added_imported_ids, [newRow.imported_id]);
    assert.equal(newResult.reconciled_imported_ids.length, 0);
    assert.equal(await actual.getAccountBalance(newAccount), -1000);
    assert.equal((await rowFor(newAccount, newRow.imported_id)).amount, -1000);

    // A user can edit the imported row into a split. Replaying the same
    // imported ID must skip it entirely, preserving notes, cleared state, and
    // every split child even when the incoming mutable fields differ.
    const newActualRow = await rowFor(newAccount, newRow.imported_id);
    assert.ok(newActualRow?.id, 'new posted row should have an Actual ID');
    await actual.updateTransaction(newActualRow.id, {
      notes: '#manual | preserve this note',
      cleared: true,
      subtransactions: [
        { amount: -600, notes: 'groceries', category: categoryId },
        { amount: -400, notes: 'service fee', category: categoryId },
      ],
    });
    await settleActual();
    const manualBeforeReplay = await rowFor(newAccount, newRow.imported_id);
    assert.equal(manualBeforeReplay.notes, '#manual | preserve this note');
    assert.equal(manualBeforeReplay.cleared, true);
    assert.equal(manualBeforeReplay.is_parent, true);
    assert.deepEqual(
      manualBeforeReplay.subtransactions
        .map(child => ({ amount: child.amount, notes: child.notes, category: child.category }))
        .sort((left, right) => left.notes.localeCompare(right.notes)),
      [
        { amount: -600, notes: 'groceries', category: categoryId },
        { amount: -400, notes: 'service fee', category: categoryId },
      ].sort((left, right) => left.notes.localeCompare(right.notes)),
    );
    const replayResult = await session.import(credential, outbox(newAccount, [{
      ...newRow,
      notes: 'provider replay must not overwrite manual note',
      cleared: false,
    }]));
    assert.deepEqual(replayResult.already_observed, [newRow.imported_id]);
    assert.equal(replayResult.actual_result.skippedExisting.length, 1);
    const manualAfterReplay = await rowFor(newAccount, newRow.imported_id);
    assert.equal(manualAfterReplay.notes, manualBeforeReplay.notes);
    assert.equal(manualAfterReplay.cleared, true);
    assert.deepEqual(
      manualAfterReplay.subtransactions
        .map(child => ({ amount: child.amount, notes: child.notes, category: child.category }))
        .sort((left, right) => left.notes.localeCompare(right.notes)),
      manualBeforeReplay.subtransactions
        .map(child => ({ amount: child.amount, notes: child.notes, category: child.category }))
        .sort((left, right) => left.notes.localeCompare(right.notes)),
    );

    // Actual's reconciler should match a new import ID to an existing manual
    // row with the same account, date, amount, and payee, so the balance must
    // remain unchanged and no duplicate row may be created.
    const matchingAccount = await createAccount('Offline manual matching');
    await actual.addTransactions(matchingAccount, [{
      date: '2026-08-10',
      amount: -2345,
      imported_payee: 'Manual merchant',
      notes: '#manual | entered in Actual',
      cleared: true,
    }], false, false);
    await settleActual();
    const matchingBalance = await actual.getAccountBalance(matchingAccount);
    const matchingRow = (await rowsFor(matchingAccount))[0];
    const matchedId = 'offline:posted:matched-manual-1';
    const matchingResult = await session.import(credential, outbox(matchingAccount, [{
      imported_id: matchedId,
      date: '2026-08-10',
      amount: -2345,
      imported_payee: 'Manual merchant',
      cleared: false,
    }]));
    assert.deepEqual(matchingResult.added_imported_ids, []);
    assert.deepEqual(matchingResult.reconciled_imported_ids, [matchedId]);
    assert.equal(matchingResult.applied_delta, 0);
    assert.equal(await actual.getAccountBalance(matchingAccount), matchingBalance);
    const matchingRowsAfter = await rowsFor(matchingAccount);
    assert.equal(matchingRowsAfter.length, 1);
    assert.equal(matchingRowsAfter[0].id, matchingRow.id);
    assert.equal(matchingRowsAfter[0].imported_id, matchedId);
    assert.equal(matchingRowsAfter[0].notes, '#manual | entered in Actual');
    assert.equal(matchingRowsAfter[0].cleared, true);

    // A manually created split has no imported_id, so it takes Actual's
    // fuzzy-match path when a statement supplies a new ID. Capture every
    // mutable parent/child field and require the reconciler to preserve it.
    const splitMatchingAccount = await createAccount('Offline manual split matching');
    await actual.addTransactions(splitMatchingAccount, [{
      date: '2026-08-11',
      amount: -2345,
      imported_payee: 'Split merchant',
      notes: '#manual split parent',
      cleared: false,
    }], false, false);
    await settleActual();
    const splitParent = (await rowsFor(splitMatchingAccount))[0];
    await actual.updateTransaction(splitParent.id, {
      notes: '#manual split parent',
      cleared: true,
      subtransactions: [
        { amount: -1200, notes: 'split child one', category: categoryId },
        { amount: -1145, notes: 'split child two', category: categoryId },
      ],
    });
    await settleActual();
    const splitBeforeMatch = await rowFor(splitMatchingAccount, null);
    const splitManualBefore = splitManualProjection(splitBeforeMatch);
    assert.equal(splitManualBefore.is_parent, true);
    assert.equal(splitManualBefore.children.length, 2);
    const splitMatchedId = 'offline:posted:matched-split-1';
    const splitMatchResult = await session.import(credential, outbox(splitMatchingAccount, [{
      imported_id: splitMatchedId,
      date: '2026-08-11',
      amount: -2345,
      imported_payee: 'Split merchant',
      cleared: false,
    }]));
    assert.deepEqual(splitMatchResult.added_imported_ids, []);
    assert.deepEqual(splitMatchResult.reconciled_imported_ids, [splitMatchedId]);
    assert.equal(splitMatchResult.applied_delta, 0);
    const splitAfterMatch = await rowFor(splitMatchingAccount, splitMatchedId);
    assert.deepEqual(splitManualProjection(splitAfterMatch), splitManualBefore);
    assert.equal(await actual.getAccountBalance(splitMatchingAccount), -2345);

    // A later row must not hide an older ADCB statement row. The historical
    // exception is explicit and source/account bound, as it is in production.
    const historicalAccount = await createAccount('Offline historical ADCB');
    const laterRow = {
      imported_id: 'offline:posted:later-1',
      date: '2026-08-15',
      amount: -777,
      imported_payee: 'Later merchant',
      cleared: false,
    };
    await session.import(credential, outbox(historicalAccount, [laterRow]));
    const balanceBeforeHistorical = await actual.getAccountBalance(historicalAccount);
    const historicalRow = {
      imported_id: 'statement:adcb_v1:offline-old-1',
      date: '2026-07-01',
      amount: -222,
      imported_payee: 'Old ADCB statement merchant',
      cleared: true,
    };
    const historicalResult = await session.import(credential, outbox(historicalAccount, [historicalRow], {
      card_code: 'ADCB_CASHBACK',
      historical_import: true,
      historical_source: 'ADCB_CASHBACK',
      historical_account_id: historicalAccount,
    }));
    assert.deepEqual(historicalResult.added_imported_ids, [historicalRow.imported_id]);
    assert.equal(await actual.getAccountBalance(historicalAccount), balanceBeforeHistorical - 222);
    const historicalRows = await rowsFor(historicalAccount);
    assert.equal(historicalRows.length, 2);
    assert.ok(historicalRows.some(row => row.imported_id === laterRow.imported_id && row.date === laterRow.date));
    assert.ok(historicalRows.some(row => row.imported_id === historicalRow.imported_id && row.date === historicalRow.date));

    // Actual intentionally refuses to re-add a deleted imported ID when
    // reimportDeleted=false. ActualSession therefore fails closed after its
    // authoritative readback; this assertion proves the deleted row is not
    // resurrected and records the current safe behavior for the owner to
    // decide whether a graceful no-op result is preferable later.
    const deletedAccount = await createAccount('Offline deleted row');
    const deletedRow = {
      imported_id: 'offline:posted:deleted-1',
      date: '2026-08-20',
      amount: -3456,
      imported_payee: 'Deleted merchant',
      cleared: false,
    };
    await session.import(credential, outbox(deletedAccount, [deletedRow]));
    const deletedActualRow = await rowFor(deletedAccount, deletedRow.imported_id);
    await actual.deleteTransaction(deletedActualRow.id);
    await settleActual();
    assert.equal((await rowsFor(deletedAccount)).some(row => row.imported_id === deletedRow.imported_id), false);
    assert.equal(await actual.getAccountBalance(deletedAccount), 0);
    await assert.rejects(
      session.import(credential, outbox(deletedAccount, [deletedRow])),
      /Actual import did not materialize offline:posted:deleted-1/,
    );
    assert.equal((await rowsFor(deletedAccount)).some(row => row.imported_id === deletedRow.imported_id), false);
    assert.equal(await actual.getAccountBalance(deletedAccount), 0);

    console.log(JSON.stringify({
      status: 'PASS',
      checks: [
        'new posted row imported with exact balance delta',
        'replay skipped and preserved manual fields and split children',
        'new import ID matched manual row with zero balance delta',
        'new import ID matched manual split without changing parent or child fields',
        'historical ADCB row imported alongside later existing row',
        'deleted row was not resurrected (session failed closed)',
      ],
    }, null, 2));
  } finally {
    if (actualInitialized) await actual.shutdown();
    const resolvedRoot = path.resolve(tempRoot);
    const safePrefix = path.resolve(tmpdir()) + path.sep;
    if (!resolvedRoot.startsWith(safePrefix) || !path.basename(resolvedRoot).startsWith('finance-actual-session-offline-')) {
      throw new Error(`Refusing to remove unexpected offline integration directory: ${resolvedRoot}`);
    }
    await rm(resolvedRoot, { recursive: true, force: true });
  }
}

main().catch(error => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
