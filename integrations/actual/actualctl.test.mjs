import assert from "node:assert/strict";
import test from "node:test";

import {
  assertCommitEnabled,
  canonicalActualImportProjection,
  compareActualImportProjections,
  doctor,
  enrichTransactions,
  exportDashboardDocument,
  fetchActualTransferRows,
  findUnexpectedActualImportRows,
  partitionCrossSourceStatementDuplicates,
  repairTransactions,
  resolveSplitChildren,
  resolvePortableReferences,
  selectRetiredRuleIds,
  selectStageMigrationRuleIds,
  validateActualTransferCounterparts,
  validateTransactionRepairPlan,
  validateTransactionEnrichmentPlan,
} from "./actualctl.mjs";
import {
  reconcileAccounts,
  reconcileBootstrapResources,
  reconcileRules,
} from "./bootstrap-resources.mjs";

test("split-child resolution rejects unknown categories consistently and keeps ids stable", () => {
  const categories = new Map([
    ["electricity & water", { id: "category-electricity", name: "Electricity & Water" }],
  ]);
  const children = [{
    amount: -100,
    notes: "#utility",
    category_name: "Electricity & Water",
  }];
  assert.deepEqual(resolveSplitChildren(children, categories), [{
    amount: -100,
    notes: "#utility",
    category: "category-electricity",
  }]);
  assert.throws(
    () => resolveSplitChildren([{ ...children[0], category_name: "Missing" }], categories),
    /Unknown Actual category in split: Missing/,
  );
});

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

test("import readback compares the complete canonical economic projection", () => {
  const source = {
    imported_id: "statement:fixture:1",
    date: "2026-08-16",
    amount: -12345,
    imported_payee: "MERCHANT RAW",
    payee_name: "Merchant",
    category: "category-shopping",
    notes: "#shopping",
    reconciled: true,
    transfer_id: "transfer-peer",
  };
  const expected = canonicalActualImportProjection(source, {
    account: "account-1",
    defaultCleared: true,
  });
  const observed = canonicalActualImportProjection({
    ...source,
    account: "account-1",
    payee_name: undefined,
    payee: "payee-1",
    cleared: true,
  }, { account: "account-1", payeeName: "Merchant" });

  const result = compareActualImportProjections([expected], [observed]);
  assert.deepEqual(result.missing, []);
  assert.deepEqual(result.duplicated, []);
  assert.deepEqual(result.duplicate_expected, []);
  assert.deepEqual(result.mismatches, []);
});

test("import readback resolves API payee_id and payee-driven transfer semantics", () => {
  const payees = new Map([
    ["payee-transfer", { id: "payee-transfer", name: "Savings transfer", transfer_acct: "account-2" }],
  ]);
  const expected = canonicalActualImportProjection({
    imported_id: "statement:transfer:source",
    account: "account-1",
    date: "2026-08-16",
    amount: -500,
    imported_payee: "SAVINGS TRANSFER",
    payee: "payee-transfer",
    category: null,
    notes: "",
    cleared: true,
  }, { account: "account-1", payees, expectGeneratedTransfer: true });
  const observed = canonicalActualImportProjection({
    id: "transaction-source",
    imported_id: "statement:transfer:source",
    account: "account-1",
    date: "2026-08-16",
    amount: -500,
    imported_payee: "SAVINGS TRANSFER",
    payee_id: "payee-transfer",
    category: null,
    notes: null,
    cleared: true,
    transfer_id: "transaction-counterpart",
  }, { payees });
  assert.deepEqual(compareActualImportProjections([expected], [observed]).mismatches, []);
  assert.deepEqual(
    compareActualImportProjections([expected], [{ ...observed, transfer: { linked: false, account: "account-2" } }]).mismatches,
    [{ imported_id: "statement:transfer:source", fields: ["transfer"] }],
  );
});

test("batch readback allows rows expected by overlapping envelopes", () => {
  const rows = [
    { id: "row-a", imported_id: "a" },
    { id: "row-b", imported_id: "b" },
  ];
  assert.deepEqual(
    findUnexpectedActualImportRows(rows, {
      allowedImportedIds: new Set(["a", "b"]),
      baselineRowIds: new Set(),
    }),
    [],
  );
  assert.deepEqual(
    findUnexpectedActualImportRows(rows, {
      allowedImportedIds: new Set(["a"]),
      baselineRowIds: new Set(),
    }),
    ["b"],
  );
});

test("transfer readback requires reciprocal cross-account inverse rows", () => {
  const payees = new Map([
    ["payee-transfer", { name: "Savings transfer", transfer_acct: "account-2" }],
  ]);
  const source = {
    id: "transaction-source",
    imported_id: "statement:transfer:source",
    account: "account-1",
    date: "2026-08-16",
    amount: -500,
    payee_id: "payee-transfer",
    transfer_id: "transaction-counterpart",
  };
  const counterpart = {
    id: "transaction-counterpart",
    account: "account-2",
    date: "2026-08-16",
    amount: 500,
    transfer_id: "transaction-source",
  };
  assert.deepEqual(validateActualTransferCounterparts([source], [source, counterpart], payees), []);
  assert.deepEqual(
    validateActualTransferCounterparts([source], [source, { ...counterpart, amount: 499 }], payees),
    [{ imported_id: source.imported_id, fields: ["transfer.inverse_amount"] }],
  );
  assert.deepEqual(
    validateActualTransferCounterparts([source], [source, { ...counterpart, account: "account-1" }], payees),
    [{ imported_id: source.imported_id, fields: ["transfer.account", "transfer.payee_account"] }],
  );
});

test("transfer readback refreshes a cached sibling account over the union range", async () => {
  const source = {
    id: "transaction-source",
    imported_id: "statement:transfer:source",
    account: "account-source",
    date: "2026-08-16",
    amount: -500,
    transfer_id: "transaction-counterpart",
  };
  const counterpart = {
    id: "transaction-counterpart",
    account: "account-target",
    date: "2026-08-16",
    amount: 500,
    transfer_id: "transaction-source",
  };
  const sibling = {
    id: "transaction-sibling",
    account: "account-target",
    date: "2026-09-01",
    amount: -25,
    imported_id: "statement:target:sibling",
  };
  const calls = [];
  const api = {
    getTransactions: async (account, start, end) => {
      calls.push([account, start, end]);
      return account === "account-target" ? [counterpart, sibling] : [source];
    },
  };
  const rowsByAccount = await fetchActualTransferRows(
    api,
    [{ id: "account-source" }, { id: "account-target" }],
    "2026-08-16",
    "2026-09-01",
    new Map([
      ["account-source", [source]],
      ["account-target", [sibling]],
    ]),
  );

  assert.deepEqual(calls, [
    ["account-source", "2026-08-16", "2026-09-01"],
    ["account-target", "2026-08-16", "2026-09-01"],
  ]);
  assert.deepEqual(
    validateActualTransferCounterparts(
      [source],
      [...rowsByAccount.values()].flat(),
    ),
    [],
  );
  assert.deepEqual(
    rowsByAccount.get("account-target").map(row => row.id),
    ["transaction-sibling", "transaction-counterpart"],
  );
});

test("import readback detects drift in every economic field", () => {
  const expected = canonicalActualImportProjection({
    imported_id: "statement:fixture:1",
    account: "account-1",
    date: "2026-08-16",
    amount: -12345,
    imported_payee: "MERCHANT RAW",
    payee_name: "Merchant",
    category: "category-shopping",
    notes: "#shopping",
    cleared: true,
    reconciled: false,
    transfer_id: null,
  });
  const observed = { ...expected };
  const mutations = {
    account: "account-2",
    date: "2026-08-17",
    amount: -12346,
    imported_payee: "OTHER RAW",
    payee: "Other Merchant",
    category: "category-travel",
    notes: "#travel",
    cleared: false,
    reconciled: true,
    transfer: { linked: true, account: "account-2" },
  };
  for (const [field, value] of Object.entries(mutations)) {
    const result = compareActualImportProjections(
      [expected],
      [{ ...observed, [field]: value }],
    );
    assert.equal(result.mismatches.length, 1, `expected ${field} drift to fail`);
    assert.deepEqual(result.mismatches[0].fields, [field]);
  }
});

test("import readback detects missing and duplicate imported rows deterministically", () => {
  const expected = [
    canonicalActualImportProjection({ imported_id: "b", account: "account-1", date: "2026-08-16", amount: -2 }),
    canonicalActualImportProjection({ imported_id: "a", account: "account-1", date: "2026-08-15", amount: -1 }),
  ];
  const observed = [{ ...expected[1] }, { ...expected[1] }];
  const result = compareActualImportProjections(expected, observed);
  assert.deepEqual(result.missing, ["b"]);
  assert.deepEqual(result.duplicated, ["a"]);
  assert.deepEqual(result.expected.map(row => row.imported_id), ["a", "b"]);
});

test("import readback normalizes null optional fields and default clearing", () => {
  const expected = canonicalActualImportProjection({
    imported_id: "statement:fixture:nulls",
    account: "account-1",
    date: "2026-08-16",
    amount: 1,
    imported_payee: null,
    category: null,
    notes: null,
    reconciled: null,
    transfer_id: null,
  }, { account: "account-1", defaultCleared: true });
  const observed = canonicalActualImportProjection({
    imported_id: "statement:fixture:nulls",
    account: "account-1",
    date: "2026-08-16",
    amount: 1,
    imported_payee: undefined,
    category: undefined,
    notes: undefined,
    reconciled: undefined,
    transfer_id: "",
    cleared: true,
  });
  assert.deepEqual(compareActualImportProjections([expected], [observed]).mismatches, []);
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

test("doctor redacts sync and provider account identifiers while retaining health data", async () => {
  const syncId = "sync-secret-123";
  const providerAccountId = "account-provider-secret-456";
  const previousSyncId = process.env.ACTUAL_SYNC_ID;
  process.env.ACTUAL_SYNC_ID = syncId;
  try {
    const result = await doctor({
      getServerVersion: async () => ({ version: "26.8.1" }),
      getAccounts: async () => [{
        id: providerAccountId,
        name: "FAB Current",
        offbudget: false,
        closed: false,
      }],
      getCategories: async () => [{ id: "category-1" }],
      getCategoryGroups: async () => [{ id: "group-1" }],
      getTags: async () => [{ id: "tag-1" }],
      getRules: async () => [{ id: "rule-1" }],
      getSchedules: async () => [{ id: "schedule-1" }],
      getAccountBalance: async id => {
        assert.equal(id, providerAccountId);
        return -4200;
      },
    });

    const serialized = JSON.stringify(result);
    assert.equal(result.sync_id_present, true);
    assert.equal(result.counts.accounts, 1);
    assert.equal(result.accounts[0].id, "[REDACTED]");
    assert.equal(result.accounts[0].name, "FAB Current");
    assert.equal(result.accounts[0].balance, -4200);
    assert.equal(result.accounts[0].closed, false);
    assert.equal(result.accounts[0].offbudget, false);
    assert.ok(!serialized.includes(syncId));
    assert.ok(!serialized.includes(providerAccountId));
  } finally {
    if (previousSyncId === undefined) delete process.env.ACTUAL_SYNC_ID;
    else process.env.ACTUAL_SYNC_ID = previousSyncId;
  }
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

test("transaction enrichment uses the same split category id for preflight and readback", async () => {
  const plan = enrichmentPlan();
  plan.changes[0].expected_current_amount = -100;
  plan.changes[0].split = [
    { amount: -60, notes: "#utility", category_name: "Electricity & Water" },
    { amount: -40, notes: "#home", category_name: "Electricity & Water" },
  ];
  let rows = [{
    id: "actual-row-1",
    imported_id: "statement:adcb:test-row",
    date: "2026-07-18",
    amount: -100,
    notes: "source:statement | #home | #utility",
    is_parent: false,
  }];
  const api = {
    getServerVersion: async () => "26.8.1",
    getAccounts: async () => [{ id: "account-1", name: "ADCB Credit Card" }],
    getCategories: async () => [{ id: "category-1", name: "Electricity & Water" }],
    getTransactions: async () => rows,
    updateTransaction: async (_id, fields) => {
      rows = [{ ...rows[0], ...fields, is_parent: true }];
    },
    sync: async () => {},
  };

  const planned = await enrichTransactions(plan, false, api);
  assert.deepEqual(planned.pending[0].desired_children, [
    { amount: -60, notes: "#utility", category: "category-1" },
    { amount: -40, notes: "#home", category: "category-1" },
  ]);
  await enrichTransactions(plan, true, api);
  assert.deepEqual(rows[0].subtransactions, planned.pending[0].desired_children);
});

const postWriteSplitFixture = mutateAfterWrite => {
  const plan = enrichmentPlan();
  plan.changes[0].expected_current_amount = -100;
  plan.changes[0].split = [
    { amount: -60, notes: "#utility", category_name: "Electricity & Water" },
    { amount: -40, notes: "#home", category_name: "Electricity & Water" },
  ];
  let rows = [{
    id: "actual-row-1",
    imported_id: "statement:adcb:test-row",
    date: "2026-07-18",
    amount: -100,
    notes: "source:statement | #home | #utility",
    is_parent: false,
  }];
  const category = { id: "category-1", name: "Electricity & Water" };
  return {
    plan,
    api: {
      getServerVersion: async () => "26.8.1",
      getAccounts: async () => [{ id: "account-1", name: "ADCB Credit Card" }],
      getCategories: async () => [category],
      getTransactions: async () => rows,
      updateTransaction: async (_id, fields) => {
        rows = [{ ...rows[0], ...fields, is_parent: true }];
        mutateAfterWrite({ category, plan });
      },
      sync: async () => {},
    },
  };
};

test("transaction enrichment rejects an unknown split category during post-write readback", async () => {
  const fixture = postWriteSplitFixture(({ plan }) => {
    plan.changes[0].split[0].category_name = "Missing after write";
  });
  await assert.rejects(
    () => enrichTransactions(fixture.plan, true, fixture.api),
    /Unknown Actual category in split: Missing after write/,
  );
});

test("transaction enrichment rejects a split category identity drift during post-write readback", async () => {
  const fixture = postWriteSplitFixture(({ category }) => {
    category.id = "category-drifted";
  });
  await assert.rejects(
    () => enrichTransactions(fixture.plan, true, fixture.api),
    /Enrichment verification failed for statement:adcb:test-row/,
  );
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

test("bootstrap account phase closes only zero-balance retirees and refreshes once", async () => {
  const calls = [];
  const accounts = [
    { id: "legacy", name: "Legacy", offbudget: false, closed: false },
    { id: "current", name: "Current", offbudget: false, closed: false },
  ];
  const api = {
    createAccount: async (...args) => calls.push(["createAccount", ...args]),
    updateAccount: async (...args) => calls.push(["updateAccount", ...args]),
    getAccountBalance: async id => {
      calls.push(["getAccountBalance", id]);
      return 0;
    },
    closeAccount: async id => {
      calls.push(["closeAccount", id]);
      accounts.find(account => account.id === id).closed = true;
    },
    getAccounts: async () => {
      calls.push(["getAccounts"]);
      return accounts;
    },
  };
  const changes = [];
  const result = await reconcileAccounts({
    api,
    config: {
      accounts: [{ name: "Current", offbudget: true }],
      retired_accounts: ["Legacy"],
    },
    apply: true,
    accounts,
    changes,
  });

  assert.equal(result, accounts);
  assert.deepEqual(changes, [
    { action: "update", type: "account", name: "Current", fields: { offbudget: true } },
    { action: "close", type: "account", name: "Legacy" },
  ]);
  assert.deepEqual(calls.map(call => call[0]), [
    "updateAccount",
    "getAccountBalance",
    "closeAccount",
    "getAccounts",
  ]);
});

test("bootstrap resource phases preserve dry-run reads and one terminal sync", async () => {
  const makeApi = () => {
    const state = {
      accounts: [{ id: "account-1", name: "Current", offbudget: false, closed: false }],
      groups: [{ id: "group-1", name: "Existing" }],
      categories: [{ id: "category-1", name: "Existing", group_id: "group-1" }],
      tags: [],
      payees: [],
      rules: [],
      schedules: [],
      budget: { categoryGroups: [{ categories: [{ id: "category-1", budgeted: 0, carryover: false }] }] },
      calls: [],
      syncs: 0,
    };
    const api = {
      getAccounts: async () => { state.calls.push("getAccounts"); return state.accounts; },
      getCategoryGroups: async () => { state.calls.push("getCategoryGroups"); return state.groups; },
      getCategories: async () => { state.calls.push("getCategories"); return state.categories; },
      getTags: async () => { state.calls.push("getTags"); return state.tags; },
      getPayees: async () => { state.calls.push("getPayees"); return state.payees; },
      createAccount: async fields => {
        state.calls.push("createAccount");
        state.accounts.push({ id: "account-2", ...fields });
      },
      updateAccount: async () => state.calls.push("updateAccount"),
      getAccountBalance: async () => 0,
      closeAccount: async () => state.calls.push("closeAccount"),
      createCategoryGroup: async fields => {
        state.calls.push("createCategoryGroup");
        state.groups.push({ id: "group-2", ...fields });
        return "group-2";
      },
      createCategory: async fields => {
        state.calls.push("createCategory");
        state.categories.push({ id: "category-2", name: fields.name, group_id: fields.group_id });
      },
      updateCategory: async (...args) => state.calls.push(["updateCategory", ...args]),
      createTag: async fields => { state.calls.push("createTag"); state.tags.push(fields); },
      createPayee: async fields => { state.calls.push("createPayee"); state.payees.push({ id: "payee-1", ...fields }); },
      aqlQuery: async () => ({ data: [] }),
      q: name => ({ select: fields => ({ name, fields }) }),
      updatePayee: async () => state.calls.push("updatePayee"),
      getRules: async () => { state.calls.push("getRules"); return state.rules; },
      createRule: async rule => { state.calls.push("createRule"); state.rules.push({ id: "rule-1", ...rule }); },
      deleteRule: async () => true,
      getSchedules: async () => { state.calls.push("getSchedules"); return state.schedules; },
      createSchedule: async schedule => { state.calls.push("createSchedule"); state.schedules.push({ id: "schedule-1", ...schedule }); },
      updateSchedule: async () => state.calls.push("updateSchedule"),
      getBudgetMonth: async () => { state.calls.push("getBudgetMonth"); return state.budget; },
      setBudgetAmount: async (...args) => state.calls.push(["setBudgetAmount", ...args]),
      setBudgetCarryover: async (...args) => state.calls.push(["setBudgetCarryover", ...args]),
      sync: async () => { state.calls.push("sync"); state.syncs += 1; },
    };
    return { api, state };
  };
  const config = {
    schema_version: 1,
    accounts: [{ name: "Savings", type: "checking", initial_balance: 100 }],
    category_groups: [{ name: "Food", categories: ["Groceries"] }],
    tags: [{ tag: "shared" }],
    payees: [{ name: "Market" }],
    rules: [{
      name: "Market category",
      conditions: [{ field: "payee", op: "is", value: { ref: "payee", name: "Market" } }],
      actions: [{ field: "category", op: "set", value: { ref: "category", name: "Groceries" } }],
    }],
    schedules: [{
      name: "Market bill",
      account: "Current",
      payee: "Market",
      amount_minor: -100,
      date: "2026-09-01",
    }],
    budget_months: [{ month: "2026-08", categories: [{ name: "Existing", amount_minor: 500 }] }],
  };

  const dry = makeApi();
  const planned = await reconcileBootstrapResources({
    api: dry.api,
    config,
    apply: false,
    configPath: "/tmp/actual-bootstrap.json",
    readJson: async () => [],
  });
  assert.equal(planned.status, "planned");
  assert.equal(dry.state.syncs, 0);
  assert.ok(!dry.state.calls.includes("createAccount"));
  assert.ok(planned.changes.some(change => change.type === "rule"));
  assert.ok(planned.changes.some(change => change.type === "schedule"));
  assert.ok(planned.changes.some(change => change.type === "budget"));

  const applied = makeApi();
  const result = await reconcileBootstrapResources({
    api: applied.api,
    config,
    apply: true,
    configPath: "/tmp/actual-bootstrap.json",
    readJson: async () => [],
  });
  assert.equal(result.status, "applied");
  assert.equal(applied.state.syncs, 1);
  assert.equal(applied.state.calls.at(-1), "sync");
  assert.deepEqual(applied.state.calls.filter(call => call === "sync"), ["sync"]);
  assert.ok(applied.state.calls.indexOf("createRule") < applied.state.calls.indexOf("createSchedule"));
});

test("bootstrap rule phase resolves references and refreshes retired rules before creates", async () => {
  const calls = [];
  const refs = {
    account: new Map(),
    category: new Map([["groceries", { id: "category-1" }]]),
    category_group: new Map(),
    payee: new Map([["market", { id: "payee-1" }]]),
    tag: new Map(),
  };
  const api = {
    getRules: async () => {
      calls.push("getRules");
      return calls.includes("deleteRule") ? [] : [{
        id: "old",
        stage: "pre",
        conditionsOp: "and",
        conditions: [{ field: "payee", op: "is", value: "payee-1" }],
        actions: [{ field: "category", op: "set", value: "category-1" }],
      }];
    },
    deleteRule: async id => { calls.push("deleteRule"); assert.equal(id, "old"); return true; },
    createRule: async rule => {
      calls.push("createRule");
      assert.equal(rule.conditions[0].value, "payee-1");
      assert.equal(rule.actions[0].value, "category-1");
    },
  };
  const changes = [];
  const compiled = await reconcileRules({
    api,
    config: {
      rules: [{
        name: "New market rule",
        conditions: [{ field: "payee", op: "is", value: { ref: "payee", name: "Market" } }],
        actions: [{ field: "category", op: "set", value: { ref: "category", name: "Groceries" } }],
      }],
      retired_rules: [{
        stage: "pre",
        conditions: [{ field: "payee", op: "is", value: { ref: "payee", name: "Market" } }],
        actions: [{ field: "category", op: "set", value: { ref: "category", name: "Groceries" } }],
      }],
    },
    apply: true,
    configPath: "/tmp/actual-bootstrap.json",
    refs,
    changes,
    readJson: async () => [],
  });

  assert.deepEqual(compiled, { rules: [], skipped: [], deferred: [] });
  assert.deepEqual(calls, ["getRules", "deleteRule", "getRules", "createRule"]);
  assert.equal(changes[0].type, "rule");
});
