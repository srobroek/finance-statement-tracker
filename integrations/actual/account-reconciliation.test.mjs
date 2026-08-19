import assert from "node:assert/strict";
import test from "node:test";
import { reconcileAccounts, validateAccountReconciliationPlan } from "./account-reconciliation.mjs";

function row(overrides = {}) {
  return {
    provider_account_id: "fab:current:2001",
    name: "FAB Current 2001",
    aliases: [],
    type: "checking",
    offbudget: false,
    close_after: false,
    target_balance_minor: 22501145,
    as_of: "2026-08-19",
    source_evidence_id: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    adjustment_imported_id: "reconcile:fab:current:2001:2026-08-19",
    adjustment_notes: "Reconciliation balance adjustment | Source balance as of 2026-08-19 | Evidence sha256:aaaa",
    expected_before: { exists: false },
    ...overrides,
  };
}

function fakeApi(seed = []) {
  const accounts = seed.map(account => ({ ...account }));
  const transactions = new Map();
  let next = 1;
  return {
    accounts,
    transactions,
    synced: 0,
    async getAccounts() { return accounts.map(account => ({ ...account })); },
    async getAccountBalance(id) { return accounts.find(account => account.id === id).balance; },
    async createAccount(fields, balance) {
      accounts.push({ id: `new-${next++}`, ...fields, balance });
    },
    async getTransactions(id) { return [...(transactions.get(id) ?? [])]; },
    async addTransactions(id, rows) {
      const account = accounts.find(candidate => candidate.id === id);
      for (const transaction of rows) account.balance += transaction.amount;
      transactions.set(id, [...(transactions.get(id) ?? []), ...rows]);
    },
    async closeAccount(id) { accounts.find(account => account.id === id).closed = true; },
    async sync() { this.synced += 1; },
  };
}

test("plan validates visible notes and exact expected state", () => {
  assert.doesNotThrow(() => validateAccountReconciliationPlan({
    schema_version: 1,
    mode: "ACTUAL_NATIVE_RECONCILIATION",
    accounts: [row()],
  }));
  assert.throws(() => validateAccountReconciliationPlan({
    schema_version: 1,
    mode: "ACTUAL_NATIVE_RECONCILIATION",
    accounts: [row({ adjustment_notes: "hidden plug" })],
  }), /visibly named/);
});

test("dry run is read only and reports the exact adjustment", async () => {
  const api = fakeApi([{ id: "fab-id", name: "FAB Current 2001", balance: 10, closed: false, offbudget: false }]);
  const plan = {
    schema_version: 1,
    mode: "ACTUAL_NATIVE_RECONCILIATION",
    accounts: [row({ expected_before: { exists: true, account_id: "fab-id", balance_minor: 10, closed: false, offbudget: false } })],
  };
  const result = await reconcileAccounts(api, plan, false);
  assert.equal(result.status, "planned");
  assert.equal(result.actions[0].amount_minor, 22501135);
  assert.equal(api.accounts[0].balance, 10);
  assert.equal(api.synced, 0);
});

test("apply creates missing accounts, adjusts exact balances, closes only requested account", async () => {
  const api = fakeApi([
    { id: "adcb-id", name: "ADCB historical", balance: -7480620, closed: false, offbudget: false },
    { id: "untouched-id", name: "Untouched", balance: 123, closed: false, offbudget: false },
  ]);
  const plan = {
    schema_version: 1,
    mode: "ACTUAL_NATIVE_RECONCILIATION",
    accounts: [
      row(),
      row({
        provider_account_id: "adcb:credit:8833-6838",
        name: "ADCB historical",
        target_balance_minor: 0,
        close_after: true,
        source_evidence_id: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        adjustment_imported_id: "reconcile:adcb:credit:8833-6838:2026-08-19",
        expected_before: { exists: true, account_id: "adcb-id", balance_minor: -7480620, closed: false, offbudget: false },
      }),
    ],
  };
  const result = await reconcileAccounts(api, plan, true);
  assert.equal(result.status, "applied");
  assert.equal(api.accounts.find(account => account.name === "FAB Current 2001").balance, 22501145);
  assert.equal(api.accounts.find(account => account.id === "adcb-id").balance, 0);
  assert.equal(api.accounts.find(account => account.id === "adcb-id").closed, true);
  assert.deepEqual(api.accounts.find(account => account.id === "untouched-id"), {
    id: "untouched-id", name: "Untouched", balance: 123, closed: false, offbudget: false,
  });
  assert.equal(api.synced, 1);
});

test("apply aborts on exact-state or identity drift", async () => {
  const api = fakeApi([{ id: "wrong", name: "FAB Current 2001", balance: 11, closed: false, offbudget: false }]);
  const plan = {
    schema_version: 1,
    mode: "ACTUAL_NATIVE_RECONCILIATION",
    accounts: [row({ expected_before: { exists: true, account_id: "fab-id", balance_minor: 10, closed: false, offbudget: false } })],
  };
  await assert.rejects(() => reconcileAccounts(api, plan, true), /account_id drifted/);
});
