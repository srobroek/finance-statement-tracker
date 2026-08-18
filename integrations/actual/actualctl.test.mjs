import assert from "node:assert/strict";
import test from "node:test";

import {
  assertCommitEnabled,
  partitionCrossSourceStatementDuplicates,
  repairTransactions,
  selectRetiredRuleIds,
  selectStageMigrationRuleIds,
  validateTransactionRepairPlan,
} from "./actualctl.mjs";


test("Actual commit requires the explicit production write gate", () => {
  assert.doesNotThrow(() => assertCommitEnabled(false, {}));
  assert.doesNotThrow(() => assertCommitEnabled(true, { ALLOW_ACTUAL_WRITES: "true" }));
  assert.throws(
    () => assertCommitEnabled(true, {}),
    /Actual commits are disabled/,
  );
});

test("native classification rules migrate from pre to Actual default without touching unrelated rules", () => {
  const payload = {
    conditionsOp: "and",
    conditions: [{ field: "payee", op: "is", value: "amazon" }],
    actions: [{ field: "category", op: "set", value: "online" }],
  };
  const legacy = { id: "legacy", stage: "pre", ...payload };
  const unrelated = { id: "unrelated", stage: "pre", ...payload, actions: [{ field: "category", op: "set", value: "other" }] };
  const desired = { stage: null, ...payload };

  assert.deepEqual(
    selectStageMigrationRuleIds([legacy, unrelated], [desired], [{ from: "pre", to: "default" }]),
    ["legacy"],
  );
});

test("Actual write gate is case insensitive but rejects other values", () => {
  assert.doesNotThrow(() => assertCommitEnabled(true, { ALLOW_ACTUAL_WRITES: "TRUE" }));
  assert.throws(
    () => assertCommitEnabled(true, { ALLOW_ACTUAL_WRITES: "1" }),
    /Actual commits are disabled/,
  );
});

test("unique statement rows already captured by a browser export are suppressed", () => {
  const incoming = [{
    date: "2026-06-21",
    amount: -24965,
    imported_payee: "KIBSONS INTERNATIONAL HTTPS WWW K",
    imported_id: "statement:adcb_v1:new",
  }];
  const existing = [{
    date: "2026-06-21",
    amount: -24965,
    imported_payee: "Kibsons International - https://www.k",
    imported_id: "browser:adcb-personal-internet-banking:existing",
  }];

  const result = partitionCrossSourceStatementDuplicates(incoming, existing);

  assert.equal(result.records.length, 0);
  assert.equal(result.suppressed.length, 1);
  assert.equal(result.suppressed[0].matched_existing_id, existing[0].imported_id);
});

test("cross-source suppression remains conservative for repeated or committed rows", () => {
  const repeated = [
    {
      date: "2026-06-21",
      amount: -1000,
      imported_payee: "COFFEE SHOP",
      imported_id: "statement:adcb_v1:first",
    },
    {
      date: "2026-06-21",
      amount: -1000,
      imported_payee: "COFFEE SHOP",
      imported_id: "statement:adcb_v1:second",
    },
  ];
  const existing = [
    {
      date: "2026-06-21",
      amount: -1000,
      imported_payee: "COFFEE SHOP",
      imported_id: "browser:adcb-personal-internet-banking:existing",
    },
    {
      date: "2026-06-22",
      amount: -2000,
      imported_payee: "SHOP",
      imported_id: "statement:adcb_v1:committed",
    },
  ];
  const incoming = [
    ...repeated,
    {
      date: "2026-06-22",
      amount: -2000,
      imported_payee: "SHOP",
      imported_id: "statement:adcb_v1:committed",
    },
  ];

  const result = partitionCrossSourceStatementDuplicates(incoming, existing);

  assert.equal(result.records.length, 3);
  assert.equal(result.suppressed.length, 0);
});

test("retired Actual rules are selected only by their full semantic signature", () => {
  const oldRule = {
    id: "old-grocery",
    stage: "pre",
    conditionsOp: "and",
    conditions: [{ field: "payee", op: "oneOf", value: ["carrefour", "spinneys"] }],
    actions: [
      { field: "category", op: "set", value: "groceries" },
      { op: "append-notes", value: " #grocery" },
    ],
  };
  const unrelated = {
    ...oldRule,
    id: "new-grocery",
    actions: [
      { field: "category", op: "set", value: "groceries" },
      { op: "append-notes", value: " #grocery #shared" },
    ],
  };

  assert.deepEqual(
    selectRetiredRuleIds([oldRule, unrelated], [{ ...oldRule, id: undefined }]),
    ["old-grocery"],
  );
});

const repairPlan = () => ({
  schema_version: "actual-transaction-repair-v1",
  reason: "Correct a proven source-direction sign mismatch",
  repairs: [{
    imported_id: "browser:fab:test-row",
    account: "FAB Current",
    date: "2026-08-01",
    expected_current_amount: -12500,
    corrected_amount: 12500,
  }],
});

test("transaction repair plans require exact sign reversals and unique imported IDs", () => {
  assert.equal(validateTransactionRepairPlan(repairPlan()).length, 1);
  const invalidSign = repairPlan();
  invalidSign.repairs[0].corrected_amount = 12000;
  assert.throws(() => validateTransactionRepairPlan(invalidSign), /exact sign reversal/);
  const duplicate = repairPlan();
  duplicate.repairs.push({ ...duplicate.repairs[0] });
  assert.throws(() => validateTransactionRepairPlan(duplicate), /Duplicate imported_id/);
});

test("transaction repair validates old state, applies once, and is idempotent", async () => {
  const rows = [{
    id: "actual-row-1",
    imported_id: "browser:fab:test-row",
    date: "2026-08-01",
    amount: -12500,
  }];
  let syncCount = 0;
  const api = {
    getAccounts: async () => [{ id: "account-1", name: "FAB Current" }],
    getTransactions: async () => rows,
    updateTransaction: async (id, fields) => {
      assert.equal(id, "actual-row-1");
      rows[0] = { ...rows[0], ...fields };
    },
    sync: async () => { syncCount += 1; },
  };

  const planned = await repairTransactions(repairPlan(), false, api);
  assert.equal(planned.pending.length, 1);
  assert.equal(rows[0].amount, -12500);

  const applied = await repairTransactions(repairPlan(), true, api);
  assert.equal(applied.repaired.length, 1);
  assert.equal(applied.verification.length, 1);
  assert.equal(rows[0].amount, 12500);
  assert.equal(syncCount, 1);

  const replay = await repairTransactions(repairPlan(), true, api);
  assert.equal(replay.repaired.length, 0);
  assert.equal(replay.already_corrected.length, 1);
  assert.equal(syncCount, 1);
});

test("transaction repair refuses amount drift", async () => {
  const api = {
    getAccounts: async () => [{ id: "account-1", name: "FAB Current" }],
    getTransactions: async () => [{
      id: "actual-row-1",
      imported_id: "browser:fab:test-row",
      date: "2026-08-01",
      amount: -12499,
    }],
  };
  await assert.rejects(() => repairTransactions(repairPlan(), false, api), /amount drifted/);
});
