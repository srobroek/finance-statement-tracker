import assert from "node:assert/strict";
import test from "node:test";

import { buildTagReport, matchesTagFilter, parseTags } from "./tag-report.mjs";

test("supports any, all, and excluded tag semantics", () => {
  const tags = parseTags("#shared #rental #owner-a");
  assert.equal(matchesTagFilter(tags, { any: ["business", "shared"] }), true);
  assert.equal(matchesTagFilter(tags, { all: ["shared", "rental"] }), true);
  assert.equal(matchesTagFilter(tags, { none: ["private"] }), true);
  assert.equal(matchesTagFilter(tags, { none: ["shared"] }), false);
});

test("does not double count a split parent and its children", () => {
  const report = buildTagReport([
    { id: "parent", is_parent: true, amount: -10000, notes: "#shared", category_name: null },
    { id: "child-1", is_child: true, parent_id: "parent", amount: -7000, notes: "#shared", category_name: "Travel" },
    { id: "child-2", is_child: true, parent_id: "parent", amount: -3000, notes: "#shared", category_name: "Dining" },
  ], { all: ["shared"], groupBy: "category" });

  assert.equal(report.matched_transaction_count, 2);
  assert.equal(report.groups.reduce((total, row) => total + row.spend, 0), 10000);
});

test("a parent tag is inherited by split children for filtering", () => {
  const report = buildTagReport([
    { id: "parent", is_parent: true, amount: -10000, notes: "#shared" },
    { id: "child-1", is_child: true, parent_id: "parent", amount: -7000, notes: "", category_name: "Travel" },
    { id: "child-2", is_child: true, parent_id: "parent", amount: -3000, notes: "", category_name: "Dining" },
  ], { all: ["shared"], groupBy: "category" });

  assert.equal(report.matched_transaction_count, 2);
  assert.equal(report.matched_spend_minor, 10000);
});

test("grouping by tag declares duplicated values for multi-tag transactions", () => {
  const report = buildTagReport([
    { id: "one", amount: -2500, notes: "#shared #holiday" },
  ], { groupBy: "tag" });

  assert.deepEqual(report.groups.map(row => row.key), ["#holiday", "#shared"]);
  assert.equal(report.groups.reduce((total, row) => total + row.spend, 0), 5000);
  assert.equal(report.duplicated_spend_minor, 2500);
});
