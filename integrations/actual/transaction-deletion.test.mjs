import assert from "node:assert/strict";
import test from "node:test";

import { validateTransactionDeletionPlan } from "./actualctl.mjs";

test("transaction deletion plan pins an exact unimported row", () => {
  const plan = {
    schema_version: "actual-transaction-deletion-v1",
    expected_server_version: "26.8.1",
    reason: "Remove duplicate zero rows",
    deletions: [{
      transaction_id: "tx-1",
      account: "Account",
      date: "2026-08-17",
      expected_amount: 0,
      expected_payee: "Transfer within Uae",
      expected_category: "Needs Review",
      expected_notes: "",
      expected_imported_id: null,
    }],
  };
  assert.equal(validateTransactionDeletionPlan(plan).length, 1);
  assert.throws(
    () => validateTransactionDeletionPlan({
      ...plan,
      deletions: [{ ...plan.deletions[0], expected_imported_id: "statement:x" }],
    }),
    /only an explicitly unimported row/,
  );
});
