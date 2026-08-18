import assert from "node:assert/strict";
import test from "node:test";
import { HyperFormula } from "hyperformula";

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

test("preserves canonical case-insensitive regex semantics in Actual", () => {
  const source = [{
    rule_id: "pharmacy-regex",
    name: "Pharmacy regex",
    stage: "CLASSIFICATION",
    priority: 10,
    match: { any: [{ all: [{ field: "merchant_raw", operator: "regex", value: "\\bPHARMACY\\b" }] }] },
    actions: [{ action: "set_if_empty", field: "category", value: "Pharmacy" }],
  }];

  const result = compileCanonicalRules(source, { onlyMarked: false });

  assert.equal(result.rules.length, 1);
  assert.equal(result.rules[0].conditions[0].op, "matches");
  assert.equal(result.rules[0].conditions[0].value, "\\b[Pp][Hh][Aa][Rr][Mm][Aa][Cc][Yy]\\b");
  assert.match("Life Pharmacy", new RegExp(result.rules[0].conditions[0].value));
  assert.match("LIFE PHARMACY", new RegExp(result.rules[0].conditions[0].value));
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
    actual_settings: { category_learning: false },
  }));
  assert.throws(
    () => validateBootstrapConfig({ schema_version: 1, actual_settings: { category_learning: "no" } }),
    /category_learning must be boolean/,
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
  assert.doesNotThrow(() => validateBootstrapConfig({
    schema_version: 1,
    accounts: [{ name: "Future mortgage", enabled: false, initial_balance: -260000000 }],
    rules: [{
      name: "Future loan formula",
      enabled: false,
      actions: [{ field: "amount", op: "set", options: { formula: "__START_DATE_REQUIRED__" } }],
    }],
  }));
  assert.throws(() => validateBootstrapConfig({
    schema_version: 1,
    rules: [{
      name: "Broken active formula",
      actions: [{ field: "amount", op: "set", options: { formula: "ABS(amount)" } }],
    }],
  }), /formulas must start with =/);
});

test("ADIB mortgage profile is disabled but contains complete IPMT and PPMT actions", async () => {
  const fs = await import("node:fs/promises");
  const config = JSON.parse(await fs.readFile(new URL("../../config/actual-bootstrap.json", import.meta.url), "utf8"));
  const account = config.accounts.find(item => item.loan_code === "ADIB_BLUEWATERS_B7_306");
  const rule = config.rules.find(item => item.name.includes("ADIB_BLUEWATERS_B7_306"));

  assert.equal(account.enabled, false);
  assert.equal(account.initial_balance, -260000000);
  assert.equal(rule.enabled, false);
  const formulas = rule.actions.map(action => action.options?.formula).filter(Boolean);
  assert.equal(formulas.length, 2);
  assert.match(formulas[0], /IPMT\(0\.0399\/12/);
  assert.match(formulas[0], /240, -2600000/);
  assert.match(formulas[1], /PPMT\(0\.0399\/12/);
  assert.match(formulas[1], /240, -2600000/);
  assert.ok(formulas.every(formula => formula.includes("__LOAN_START_DATE__")));
});

test("ADIB mortgage terms produce balanced first-period interest and principal", () => {
  const sheet = HyperFormula.buildFromArray([[
    "=IPMT(0.0399/12, 1, 240, -2600000)",
    "=PPMT(0.0399/12, 1, 240, -2600000)",
  ]], { licenseKey: "gpl-v3" });
  const [interest, principal] = sheet.getSheetValues(0)[0];

  assert.equal(interest, 8645);
  assert.ok(Math.abs(principal - 7096.7917045) < 0.000001);
  assert.ok(Math.abs((interest + principal) - 15741.7917045) < 0.000001);
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
  assert.equal(result.rules[0].actions.length, 1);
  assert.deepEqual(result.deferred, [{
    rule_id: "class-groceries",
    action: "add_tags",
    reason: "NOTE_CONTRACT_REQUIRES_DETERMINISTIC_WORKER",
  }]);
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

  assert.deepEqual(result.rules.map(rule => rule.stage), ["pre", null]);
  assert.equal(result.skipped.at(-1).reason, "WORKER_ONLY_NOTE_ACTIONS");
});
