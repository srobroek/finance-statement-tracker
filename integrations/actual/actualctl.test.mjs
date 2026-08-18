import assert from "node:assert/strict";
import test from "node:test";

import {
  assertCommitEnabled,
  enrichTransactions,
  exportDashboardDocument,
  partitionCrossSourceStatementDuplicates,
  repairTransactions,
  resolvePortableReferences,
  selectRetiredRuleIds,
  selectStageMigrationRuleIds,
  validateTransactionRepairPlan,
  validateTransactionEnrichmentPlan,
} from "./actualctl.mjs";

test("portable dashboard references resolve from names to Actual ids", () => {
  const refs = {
    category: new Map([["groceries", { id: "category-1" }]]),
  };
  const document = {
    widgets: [{ meta: { conditions: [{ value: { ref: "category", name: "Groceries" } }] } }],
  };
  assert.equal(
    resolvePortableReferences(document, refs).widgets[0].meta.conditions[0].value,
    "category-1",
  );
  assert.throws(
    () => resolvePortableReferences({ ref: "category", name: "Missing" }, refs),
    /Unknown category reference/,
  );
});

test("dashboard export embeds custom report metadata and keeps visual order", () => {
  const page = { id: "page-1", name: "Overview" };
  const widgets = [
    {
      id: "second",
      dashboard_page_id: "page-1",
      type: "custom-report",
      x: 3,
      y: 1,
      width: 6,
      height: 4,
      meta: JSON.stringify({ id: "report-1" }),
      tombstone: false,
    },
    {
      id: "first",
      dashboard_page_id: "page-1",
      type: "summary-card",
      x: 0,
      y: 0,
      width: 3,
      height: 2,
      meta: { name: "Spend" },
      tombstone: false,
    },
    {
      id: "deleted",
      dashboard_page_id: "page-1",
      type: "summary-card",
      x: 0,
      y: 9,
      width: 3,
      height: 2,
      meta: {},
      tombstone: true,
    },
  ];
  const reports = [{ id: "report-1", name: "Shared spend", graphType: "table" }];

  const exported = exportDashboardDocument(page, widgets, reports);

  assert.equal(exported.version, 1);
  assert.equal(exported.name, "Overview");
  assert.deepEqual(exported.widgets.map(widget => widget.type), ["summary-card", "custom-report"]);
  assert.equal(exported.widgets[1].meta.name, "Shared spend");
});


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

const enrichmentPlan = () => ({
  schema_version: "actual-transaction-enrichment-v1",
  expected_server_version: "26.8.1",
  reason: "Backfill explicit rental property evidence",
  changes: [{
    imported_id: "statement:adcb:test-row",
    account: "ADCB Credit Card",
    date: "2026-07-18",
    expected_current_amount: -20790,
    expected_current_notes: "source:statement | #home | #utility",
    add_note_tokens: ["#rental", "#rental:lt713"],
    remove_note_tokens: ["#home"],
  }],
});

test("transaction enrichment validates colon tags and balanced splits", () => {
  assert.equal(validateTransactionEnrichmentPlan(enrichmentPlan()).length, 1);
  const split = enrichmentPlan();
  split.changes[0].expected_current_amount = -162457;
  split.changes[0].split = [
    { amount: -99984, notes: "#utility | #home", category_name: "Electricity & Water" },
    { amount: -62473, notes: "#utility | #rental | #rental:lt713", category_name: "Electricity & Water" },
  ];
  assert.equal(validateTransactionEnrichmentPlan(split).length, 1);
  split.changes[0].split[1].amount = -62472;
  assert.throws(
    () => validateTransactionEnrichmentPlan(split),
    /must sum to the parent amount/,
  );
});

test("transaction enrichment plans, applies, verifies, and replays idempotently", async () => {
  let rows = [{
    id: "actual-row-1",
    imported_id: "statement:adcb:test-row",
    date: "2026-07-18",
    amount: -20790,
    notes: "source:statement | #home | #utility",
    is_parent: false,
  }];
  let syncCount = 0;
  const api = {
    getServerVersion: async () => "26.8.1",
    getAccounts: async () => [{ id: "account-1", name: "ADCB Credit Card" }],
    getCategories: async () => [{ id: "category-1", name: "Electricity & Water" }],
    getTransactions: async () => rows,
    updateTransaction: async (id, fields) => {
      assert.equal(id, "actual-row-1");
      rows = [{ ...rows[0], ...fields }];
    },
    sync: async () => { syncCount += 1; },
  };

  const planned = await enrichTransactions(enrichmentPlan(), false, api);
  assert.equal(planned.pending.length, 1);
  assert.equal(rows[0].notes, "source:statement | #home | #utility");

  const applied = await enrichTransactions(enrichmentPlan(), true, api);
  assert.equal(applied.verification.length, 1);
  assert.equal(rows[0].notes, "source:statement | #utility | #rental | #rental:lt713");
  assert.equal(syncCount, 1);

  const replay = await enrichTransactions(enrichmentPlan(), true, api);
  assert.equal(replay.enriched.length, 0);
  assert.equal(replay.already_applied.length, 1);
  assert.equal(syncCount, 1);
});

test("transaction enrichment refuses note drift and server version drift", async () => {
  const base = {
    getServerVersion: async () => "26.8.1",
    getAccounts: async () => [{ id: "account-1", name: "ADCB Credit Card" }],
    getCategories: async () => [],
    getTransactions: async () => [{
      id: "actual-row-1",
      imported_id: "statement:adcb:test-row",
      date: "2026-07-18",
      amount: -20790,
      notes: "manually changed",
    }],
  };
  await assert.rejects(() => enrichTransactions(enrichmentPlan(), false, base), /notes or split state drifted/);
  await assert.rejects(
    () => enrichTransactions(enrichmentPlan(), false, { ...base, getServerVersion: async () => "26.9.0" }),
    /server version drifted/,
  );
});

test("transaction enrichment accepts the production server-version response shape", async () => {
  const plan = {
    schema_version: "actual-transaction-enrichment-v1",
    expected_server_version: "26.8.1",
    reason: "test",
    changes: [{
      imported_id: "statement:test:one",
      account: "Card",
      date: "2026-08-18",
      expected_current_amount: -100,
      expected_current_notes: "#utility",
      add_note_tokens: ["#rental"],
    }],
  };
  const api = {
    getServerVersion: async () => ({ version: "26.8.1" }),
    getAccounts: async () => [{ id: "account-1", name: "Card" }],
    getCategories: async () => [],
    getTransactions: async () => [{
      id: "transaction-1",
      imported_id: "statement:test:one",
      date: "2026-08-18",
      amount: -100,
      notes: "#utility",
    }],
  };
  const result = await enrichTransactions(plan, false, api, { syncRemote: false });
  assert.equal(result.status, "planned");
  assert.equal(result.pending.length, 1);
});
