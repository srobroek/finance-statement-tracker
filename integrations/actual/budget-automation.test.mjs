import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalCleanup,
  cleanupGroupNames,
  compileCleanup,
  validateBudgetAutomationConfig,
} from "./budget-automation.mjs";

const config = {
  schema_version: "actual-budget-automation-v1",
  required_actual_version: "26.8.1",
  categories: [
    {
      category: "Electricity & Water",
      templates: [{ type: "average", directive: "template", priority: 10, numMonths: 3 }],
      cleanup: [{ role: "source", group: "Utilities" }],
    },
    {
      category: "Utilities Buffer",
      templates: [],
      cleanup: [{ role: "sink", group: "Utilities", weight: 1 }],
    },
  ],
};

test("validates and compiles UI budget automations and cleanup pools", () => {
  assert.doesNotThrow(() => validateBudgetAutomationConfig(config));
  assert.deepEqual(cleanupGroupNames(config), ["Utilities"]);
  const groups = new Map([["utilities", "group-1"]]);
  const compiled = compileCleanup(config.categories[1].cleanup, groups);
  assert.deepEqual(compiled, [{ role: "sink", groupId: "group-1", weight: 1 }]);
  assert.deepEqual(canonicalCleanup(compiled, new Map([["group-1", "Utilities"]])), [
    { role: "sink", group: "Utilities", weight: 1 },
  ]);
});

test("rejects unpinned or malformed automation configuration", () => {
  assert.throws(
    () => validateBudgetAutomationConfig({ ...config, required_actual_version: "" }),
    /required_actual_version/,
  );
  assert.throws(
    () => validateBudgetAutomationConfig({
      ...config,
      categories: [{ category: "Bad", templates: [], cleanup: [{ role: "sink", weight: 0 }] }],
    }),
    /weight must be positive/,
  );
});

