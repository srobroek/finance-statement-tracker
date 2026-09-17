import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assertPreservationGate,
  assertReplacementGate,
  buildPreservationReport,
  indexIncomingRecords,
  runProductionRebuild,
  selectReplacementRows,
  verifyPreservedRows,
} from "./production-rebuild.mjs";

const rebuildFixture = async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "actual-production-rebuild-"));
  const manifestPath = path.join(root, "manifest.json");
  const validationConfigPath = path.join(root, "validation.json");
  const bootstrapConfigPath = path.join(root, "bootstrap.json");
  const target = {
    id: "target-before",
    account: "account-card",
    account_name: "Card",
    category: "category-utilities",
    category_name: "Utilities",
    payee: "payee-merchant",
    payee_name: "Merchant",
    amount: -100,
    date: "2026-08-01",
    notes: "#statement",
    imported_id: "statement:one",
    imported_payee: "MERCHANT",
    cleared: false,
    reconciled: false,
    transfer_id: null,
    schedule: null,
    is_parent: false,
    is_child: false,
    parent_id: null,
    subtransactions: [],
  };
  const manual = {
    id: "manual-row",
    account: "account-card",
    category: "category-manual",
    payee: "payee-manual",
    amount: -250,
    date: "2026-08-02",
    notes: "#manual",
    imported_id: null,
    imported_payee: null,
    cleared: false,
    reconciled: true,
    transfer_id: null,
    schedule: null,
    is_parent: false,
    is_child: false,
    parent_id: null,
    subtransactions: [],
  };
  await fs.writeFile(manifestPath, `${JSON.stringify({
    envelopes: [{
      account: "Card",
      records: [{
        imported_id: target.imported_id,
        date: target.date,
        amount: target.amount,
        imported_payee: target.imported_payee,
        payee_name: target.payee_name,
        category_name: target.category_name,
        notes: target.notes,
        cleared: target.cleared,
      }],
    }],
  })}\n`, "utf8");
  await fs.writeFile(validationConfigPath, `${JSON.stringify({
    snapshot_scope: { accounts: ["Card"], imported_id_prefixes: ["statement:"] },
  })}\n`, "utf8");
  await fs.writeFile(bootstrapConfigPath, "{}\n", "utf8");
  return {
    root,
    manifestPath,
    validationConfigPath,
    bootstrapConfigPath,
    backupPath: path.join(root, "backup.actualzip"),
    snapshotPath: path.join(root, "after.json"),
    resultPath: path.join(root, "result.json"),
    target,
    manual,
  };
};

const fakeDependencies = (fixture, calls, {
  importResult = { status: "committed", verification: [{ expected: 1, verified: 1 }] },
  mutateAfterSnapshot,
  importOptions = [],
} = {}) => {
  let rows = [fixture.target, fixture.manual];
  return {
    api: {
      exportBudget: async () => {
        calls.push("backup");
        return new Uint8Array(1024).fill(7);
      },
      deleteTransaction: async id => {
        calls.push(`delete:${id}`);
        rows = rows.filter(row => row.id !== id);
      },
      sync: async () => calls.push("sync"),
      shutdown: async () => calls.push("shutdown"),
    },
    openBudget: async () => calls.push("open"),
    snapshot: async () => {
      calls.push("snapshot");
      if (calls.filter(value => value === "snapshot").length > 1 && mutateAfterSnapshot) {
        rows = mutateAfterSnapshot(rows);
      }
      return { transactions: rows };
    },
    importEnvelopes: async (_payload, _commit, options) => {
      calls.push("import");
      importOptions.push(options);
      if (importResult.status === "committed") rows = [...rows, { ...fixture.target, id: "target-after" }];
      return importResult;
    },
    bootstrap: async () => {
      calls.push("bootstrap");
      return { changes: [{ type: "category" }] };
    },
    loadFullRebuildManifests: async () => [{
      source_id: "fixture",
      filename: fixture.manifestPath,
    }],
  };
};

test("production replacement requires its dedicated write gate", () => {
  assert.doesNotThrow(() => assertReplacementGate(false, {}));
  assert.throws(() => assertReplacementGate(true, {}), /replacement is disabled/);
  assert.doesNotThrow(() =>
    assertReplacementGate(true, { ALLOW_ACTUAL_LEDGER_REPLACEMENT: "TRUE" })
  );
});

test("replacement selects only configured imported ids on configured accounts", () => {
  const document = {
    transactions: [
      { id: "replace", account_name: "Card", imported_id: "statement:x:1" },
      { id: "manual", account_name: "Card", imported_id: null },
      { id: "other", account_name: "Other", imported_id: "statement:x:2" },
      { id: "prefix", account_name: "Card", imported_id: "external:1" },
      { id: "deleted", account_name: "Card", imported_id: "statement:x:3", tombstone: true },
    ],
  };
  const selected = selectReplacementRows(document, {
    accounts: ["Card"],
    imported_id_prefixes: ["statement:x:"],
  });
  assert.deepEqual(selected.map(row => row.id), ["replace"]);
});

test("incoming manifest index rejects duplicates and retains account identity", () => {
  const payloads = [{
    filename: "one.json",
    payload: { envelopes: [{ account: "Card", records: [{ imported_id: "statement:1" }] }] },
  }];
  const index = indexIncomingRecords(payloads);
  assert.equal(index.get("statement:1").account, "Card");
  assert.throws(
    () => indexIncomingRecords([...payloads, ...payloads]),
    /Duplicate manifest imported_id/,
  );
});

test("preservation report blocks structural state and managed field drift", () => {
  const target = {
    id: "managed",
    account: "account-id",
    account_name: "Card",
    category: "category-id",
    category_name: "Manual Category",
    payee: "payee-id",
    payee_name: "Merchant",
    amount: -100,
    date: "2026-08-01",
    notes: "#manual",
    imported_id: "statement:1",
    imported_payee: "MERCHANT",
    cleared: true,
    reconciled: true,
    transfer_id: null,
    schedule: null,
    is_parent: false,
    is_child: false,
    parent_id: null,
    subtransactions: [],
  };
  const incoming = indexIncomingRecords([{
    filename: "one.json",
    payload: {
      envelopes: [{
        account: "Card",
        records: [{
          imported_id: "statement:1",
          date: "2026-08-01",
          amount: -100,
          imported_payee: "MERCHANT",
          payee_name: "Merchant",
          category_name: "Online Shopping",
          notes: "#online",
          cleared: true,
        }],
      }],
    },
  }]);
  const report = buildPreservationReport({ transactions: [target] }, [target], incoming);
  assert.equal(report.blocking_rows.length, 1);
  assert.deepEqual(report.blocking_rows[0].reasons, ["RECONCILED", "MANAGED_FIELD_DRIFT"]);
  assert.deepEqual(
    report.blocking_rows[0].differences.map(row => row.field),
    ["category_name", "notes"],
  );
  assert.throws(
    () => assertPreservationGate(report, true, null, {}),
    /manual or divergent state/,
  );
  assert.throws(
    () => assertPreservationGate(
      report,
      true,
      "wrong",
      { ALLOW_ACTUAL_MANUAL_STATE_REPLACEMENT: "true" },
    ),
    /exact reviewed preservation report sha256/,
  );
  assert.doesNotThrow(() => assertPreservationGate(
    report,
    true,
    report.sha256,
    { ALLOW_ACTUAL_MANUAL_STATE_REPLACEMENT: "true" },
  ));
});

test("preserved unmanaged rows are verified exactly after replacement", () => {
  const manual = {
    id: "manual",
    account: "account-id",
    category: "category-id",
    payee: "payee-id",
    amount: -250,
    date: "2026-08-02",
    notes: "#manual",
    imported_id: null,
    imported_payee: null,
    cleared: false,
    reconciled: true,
    transfer_id: null,
    schedule: null,
    is_parent: false,
    is_child: false,
    parent_id: null,
    subtransactions: [],
  };
  const report = buildPreservationReport(
    { transactions: [manual] },
    [],
    new Map(),
  );
  assert.equal(verifyPreservedRows(report, { transactions: [manual] }).status, "PASS");
  assert.equal(
    verifyPreservedRows(report, { transactions: [{ ...manual, notes: "changed" }] }).status,
    "FAIL",
  );
  assert.equal(verifyPreservedRows(report, { transactions: [] }).status, "FAIL");
});

test("production rebuild executes backup, replacement, import, bootstrap, sync, and readback through fakes", async () => {
  const fixture = await rebuildFixture();
  const calls = [];
  const importOptions = [];
  const result = await runProductionRebuild({
    ...fixture,
    start: "2026-08-01",
    end: "2026-08-31",
    apply: true,
    environment: { ALLOW_ACTUAL_LEDGER_REPLACEMENT: "true" },
    dependencies: fakeDependencies(fixture, calls, { importOptions }),
  });

  assert.equal(result.status, "APPLIED");
  assert.equal(result.deleted, 1);
  assert.equal(result.bootstrap_changes, 1);
  assert.equal(result.preservation.verification.status, "PASS");
  assert.deepEqual(importOptions, [{ syncRemote: false, reimportDeleted: true }]);
  assert.deepEqual(calls, [
    "open",
    "snapshot",
    "backup",
    "delete:target-before",
    "import",
    "bootstrap",
    "sync",
    "snapshot",
    "shutdown",
  ]);
  assert.equal((await fs.stat(fixture.backupPath)).size, 1024);
  assert.equal(JSON.parse(await fs.readFile(fixture.snapshotPath, "utf8")).transactions.length, 2);
  assert.equal(JSON.parse(await fs.readFile(fixture.resultPath, "utf8")).status, "APPLIED");
});

test("production rebuild exact replay is a read-only no-op when apply is false", async () => {
  const fixture = await rebuildFixture();
  const calls = [];
  const options = {
    ...fixture,
    start: "2026-08-01",
    end: "2026-08-31",
    apply: false,
  };
  const result = await runProductionRebuild({
    ...options,
    dependencies: fakeDependencies(fixture, calls),
  });
  const replay = await runProductionRebuild({
    ...options,
    dependencies: fakeDependencies(fixture, calls),
  });

  assert.equal(result.status, "PLANNED");
  assert.equal(result.replacement_count, 1);
  assert.deepEqual(replay, result);
  assert.deepEqual(calls, [
    "open",
    "snapshot",
    "shutdown",
    "open",
    "snapshot",
    "shutdown",
  ]);
  await assert.rejects(() => fs.stat(fixture.backupPath), { code: "ENOENT" });
  await assert.rejects(() => fs.stat(fixture.resultPath), { code: "ENOENT" });
});

test("production rebuild stops after import failure and always shuts down", async () => {
  const fixture = await rebuildFixture();
  const calls = [];
  await assert.rejects(
    () => runProductionRebuild({
      ...fixture,
      start: "2026-08-01",
      end: "2026-08-31",
      apply: true,
      environment: { ALLOW_ACTUAL_LEDGER_REPLACEMENT: "true" },
      dependencies: fakeDependencies(fixture, calls, {
        importResult: { status: "rejected", verification: [] },
      }),
    }),
    /Production import failed/,
  );
  assert.deepEqual(calls, [
    "open",
    "snapshot",
    "backup",
    "delete:target-before",
    "import",
    "shutdown",
  ]);
});

test("production rebuild rejects preserved-row readback drift", async () => {
  const fixture = await rebuildFixture();
  const calls = [];
  await assert.rejects(
    () => runProductionRebuild({
      ...fixture,
      start: "2026-08-01",
      end: "2026-08-31",
      apply: true,
      environment: { ALLOW_ACTUAL_LEDGER_REPLACEMENT: "true" },
      dependencies: fakeDependencies(fixture, calls, {
        mutateAfterSnapshot: rows => rows.map(row => row.id === "manual-row"
          ? { ...row, notes: "#changed" }
          : row),
      }),
    }),
    /Preserved Actual state changed/,
  );
  assert.equal(calls.at(-1), "shutdown");
});
