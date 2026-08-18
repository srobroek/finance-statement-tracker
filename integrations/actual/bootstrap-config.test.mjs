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
  assert.equal(result.rules[0].stage, "pre");
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
    retired_rules: [{ stage: "pre", conditions: [], actions: [] }],
    rule_stage_migrations: [{ from: "pre", to: "default" }],
  }));
  assert.throws(
    () => validateBootstrapConfig({ schema_version: 1, rule_stage_migrations: [{ from: "pre", to: "pre" }] }),
    /rule_stage_migrations require distinct/,
  );
});

test("compiles a grouped supermarket one-of rule with category and tags", () => {
  const result = compileCanonicalRules([{
    rule_id: "class-groceries",
    name: "Known supermarkets",
    stage: "CLASSIFICATION",
    match: { any: [{ all: [{
      field: "vendor",
      operator: "in",
      value: ["Carrefour", "LuLu Hypermarket", "Spinneys", "Waitrose"],
    }] }] },
    actions: [
      { action: "set_if_empty", field: "category", value: "Groceries" },
      { action: "add_tags", value: ["grocery", "shared"], sequence: 20 },
    ],
  }], { onlyMarked: false });

  assert.equal(result.rules.length, 1);
  assert.equal(result.rules[0].stage, null);
  assert.equal(result.rules[0].conditions[0].op, "oneOf");
  assert.equal(result.rules[0].conditions[0].value.length, 4);
  assert.deepEqual(result.rules[0].actions[1], {
    op: "append-notes",
    value: " #grocery #shared",
  });
});

test("maps canonical stages to Actual pre, default, and post stages", () => {
  const result = compileCanonicalRules([
    {
      rule_id: "vendor",
      name: "Vendor",
      stage: "VENDOR_NORMALIZATION",
      match: { any: [{ all: [{ field: "merchant_raw", operator: "contains", value: "SHOP" }] }] },
      actions: [{ action: "set", field: "vendor", value: "Shop" }],
    },
    {
      rule_id: "class",
      name: "Class",
      stage: "CLASSIFICATION",
      match: { any: [{ all: [{ field: "vendor", operator: "equals", value: "Shop" }] }] },
      actions: [{ action: "set", field: "category", value: "General Retail" }],
    },
    {
      rule_id: "tag",
      name: "Tag",
      stage: "TAGGING",
      match: { any: [{ all: [{ field: "category", operator: "equals", value: "General Retail" }] }] },
      actions: [{ action: "add_tag", value: "retail" }],
    },
  ], { onlyMarked: false });

  assert.deepEqual(result.rules.map(rule => rule.stage), ["pre", null, "post"]);
});
