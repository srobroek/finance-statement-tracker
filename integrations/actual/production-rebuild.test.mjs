import assert from "node:assert/strict";
import test from "node:test";

import {
  assertPreservationGate,
  assertReplacementGate,
  buildPreservationReport,
  indexIncomingRecords,
  selectReplacementRows,
  verifyPreservedRows,
} from "./production-rebuild.mjs";

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
