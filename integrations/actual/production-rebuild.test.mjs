import assert from "node:assert/strict";
import test from "node:test";

import { assertReplacementGate, selectReplacementRows } from "./production-rebuild.mjs";

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
