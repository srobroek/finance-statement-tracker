import assert from "node:assert/strict";
import test from "node:test";

import { compileCanonicalRules, validateBootstrapConfig } from "./bootstrap-config.mjs";

test("compiles explicitly marked canonical rules without duplicating the source", () => {
  const result = compileCanonicalRules([{
    rule_id: "vendor-dewa",
    name: "Normalize DEWA",
    native_actual: true,
    stage: "VENDOR_NORMALIZATION",
    match: { any: [{ all: [{ field: "merchant_raw", operator: "contains", value: "DEWA" }] }] },
    actions: [{ action: "set", field: "vendor", value: "DEWA" }],
  }]);
  assert.equal(result.rules.length, 1);
  assert.equal(result.rules[0].conditions[0].field, "imported_payee");
  assert.deepEqual(result.rules[0].actions[0].value, { ref: "payee", name: "DEWA" });
});

test("refuses native compilation when Actual cannot preserve OR-of-AND semantics", () => {
  const result = compileCanonicalRules([{
    rule_id: "complex",
    name: "Complex",
    native_actual: true,
    stage: "CLASSIFICATION",
    match: { any: [
      { all: [{ field: "merchant_raw", operator: "contains", value: "A" }, { field: "vendor", operator: "equals", value: "A" }] },
      { all: [{ field: "merchant_raw", operator: "contains", value: "B" }] },
    ] },
    actions: [{ action: "set", field: "category", value: "Groceries" }],
  }]);
  assert.equal(result.rules.length, 0);
  assert.equal(result.skipped[0].reason, "OR_OF_AND_NOT_REPRESENTABLE");
});

test("validates schedule and budget contracts", () => {
  assert.throws(
    () => validateBootstrapConfig({ schema_version: 1, schedules: [{ name: "Bill", enabled: true }] }),
    /enabled schedules require/,
  );
  assert.doesNotThrow(() => validateBootstrapConfig({
    schema_version: 1,
    schedules: [],
    budget_months: [{ month: "2026-08", categories: [{ name: "Groceries", amount_minor: 10000 }] }],
  }));
  assert.throws(
    () => validateBootstrapConfig({ schema_version: 1, retired_accounts: [""] }),
    /retired_accounts entries must be names/,
  );
  assert.doesNotThrow(() => validateBootstrapConfig({
    schema_version: 1,
    retired_accounts: ["Legacy empty account"],
  }));
});
