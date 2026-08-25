import assert from "node:assert/strict";
import test from "node:test";

import {
  managedScheduleName,
  parseManagedScheduleName,
  reconcileAccounts,
  reconcileCategories,
  reconcileSchedules,
} from "./bootstrap-resources.mjs";

const refs = {
  account: new Map([["current", { id: "account-current", name: "Current" }]]),
  payee: new Map([["market", { id: "payee-market", name: "Market" }]]),
};

function scheduleConfig(overrides = {}) {
  return {
    name: "Market bill",
    managed_id: "market-bill",
    account: "Current",
    payee: "Market",
    amount_minor: -100,
    date: "2026-09-01",
    ...overrides,
  };
}

test("bootstrap account dry-run reports exact closed and type drift", async () => {
  const accounts = [{
    id: "card-1",
    name: "Card",
    offbudget: false,
    closed: true,
    type: "checking",
  }];
  const changes = [];
  const result = await reconcileAccounts({
    api: { getAccounts: async () => accounts },
    config: { accounts: [{ name: "Card", type: "credit", offbudget: true }] },
    apply: false,
    accounts,
    changes,
  });

  assert.equal(result, accounts);
  assert.deepEqual(changes, [
    { action: "drift", type: "account", name: "Card", field: "type", expected: "credit", actual: "checking", authorized: false },
    { action: "drift", type: "account", name: "Card", field: "closed", expected: false, actual: true, authorized: false },
    { action: "update", type: "account", name: "Card", fields: { offbudget: true } },
  ]);
});

test("bootstrap account apply refuses unauthorized lifecycle/type drift and applies an explicit repair", async () => {
  const accounts = [{
    id: "card-1",
    name: "Card",
    offbudget: false,
    closed: true,
    type: "checking",
  }];
  const calls = [];
  const api = {
    getAccounts: async () => accounts,
    updateAccount: async (id, fields) => {
      calls.push(["updateAccount", id, fields]);
      Object.assign(accounts[0], fields);
    },
    reopenAccount: async id => {
      calls.push(["reopenAccount", id]);
      accounts[0].closed = false;
    },
  };

  await assert.rejects(
    () => reconcileAccounts({
      api,
      config: { accounts: [{ name: "Card", type: "credit", offbudget: true }] },
      apply: true,
      accounts,
      changes: [],
    }),
    /Account type drift is not authorized/,
  );
  assert.deepEqual(calls, []);

  const changes = [];
  await reconcileAccounts({
    api,
    config: {
      accounts: [{
        name: "Card",
        type: "credit",
        offbudget: true,
        allow_type_change: true,
        allow_reopen: true,
      }],
    },
    apply: true,
    accounts,
    changes,
  });
  assert.deepEqual(calls, [
    ["reopenAccount", "card-1"],
    ["updateAccount", "card-1", { offbudget: true, type: "credit" }],
  ]);
  assert.equal(accounts[0].closed, false);
  assert.equal(accounts[0].type, "credit");
  assert.ok(changes.every(change => change.authorized === undefined || change.authorized === true));
});

test("bootstrap category group drift is visible in dry-run and apply projections match", async () => {
  const groups = [{ id: "group-food", name: "Food" }];
  const categories = [{ id: "category-market", name: "Market", group_id: "group-old" }];
  const config = {
    category_groups: [{ name: "Food", categories: ["Market"], allow_group_change: true }],
  };
  const plannedChanges = [];
  await reconcileCategories({
    api: {}, config, apply: false, groups, categories, changes: plannedChanges,
  });

  const applyChanges = [];
  const appliedGroups = structuredClone(groups);
  const appliedCategories = structuredClone(categories);
  await reconcileCategories({
    api: {
      updateCategory: async (id, fields) => Object.assign(appliedCategories.find(row => row.id === id), fields),
    },
    config,
    apply: true,
    groups: appliedGroups,
    categories: appliedCategories,
    changes: applyChanges,
  });

  assert.deepEqual(applyChanges, plannedChanges);
  assert.equal(appliedCategories[0].group_id, "group-food");
});

test("bootstrap created category group retains applied fields for parity", async () => {
  const config = {
    category_groups: [{ name: "Income", is_income: true, hidden: true, categories: [] }],
  };
  const plannedChanges = [];
  await reconcileCategories({
    api: {}, config, apply: false, groups: [], categories: [], changes: plannedChanges,
  });

  const calls = [];
  const appliedChanges = [];
  await reconcileCategories({
    api: {
      createCategoryGroup: async fields => {
        calls.push(fields);
        return "group-income";
      },
    },
    config,
    apply: true,
    groups: [],
    categories: [],
    changes: appliedChanges,
  });

  assert.deepEqual(appliedChanges, plannedChanges);
  assert.deepEqual(calls, [{ name: "Income", is_income: true, hidden: true }]);
});

test("managed schedule marker is durable and manual name collisions fail closed", async () => {
  const desired = scheduleConfig();
  const markerName = managedScheduleName(desired);
  assert.deepEqual(parseManagedScheduleName(markerName), {
    managed_id: "market-bill",
    name: "Market bill",
  });

  let creates = 0;
  await assert.rejects(
    () => reconcileSchedules({
      api: { getSchedules: async () => [{ id: "manual", name: "Market bill" }] },
      config: { schedules: [desired] },
      apply: true,
      refs,
      changes: [],
    }),
    /collides with manual schedule/,
  );
  assert.equal(creates, 0);
});

test("removed and disabled managed schedules retire while unrelated manual schedules survive", async () => {
  const keep = scheduleConfig();
  const disabled = scheduleConfig({ name: "Old bill", managed_id: "old-bill", enabled: false });
  const state = {
    schedules: [
      { id: "manual", name: "Manual reminder", account: "account-current", payee: "payee-market", amount: -20, amountOp: "is", date: "2026-09-02", posts_transaction: false },
      { id: "keep", name: managedScheduleName(keep), account: "account-current", payee: "payee-market", amount: -100, amountOp: "is", date: "2026-09-01", posts_transaction: false },
      { id: "old", name: managedScheduleName(disabled), account: "account-current", payee: "payee-market", amount: -30, amountOp: "is", date: "2026-09-03", posts_transaction: false },
      { id: "removed", name: managedScheduleName(scheduleConfig({ name: "Removed bill", managed_id: "removed-bill" })), account: "account-current", payee: "payee-market", amount: -40, amountOp: "is", date: "2026-09-04", posts_transaction: false },
    ],
  };
  const calls = [];
  const changes = [];
  await reconcileSchedules({
    api: {
      getSchedules: async () => state.schedules,
      deleteSchedule: async id => {
        calls.push(["deleteSchedule", id]);
        state.schedules = state.schedules.filter(row => row.id !== id);
      },
      updateSchedule: async () => calls.push(["updateSchedule"]),
      createSchedule: async () => calls.push(["createSchedule"]),
    },
    config: { schedules: [keep, disabled] },
    apply: true,
    refs,
    changes,
  });

  assert.deepEqual(calls, [
    ["deleteSchedule", "old"],
    ["deleteSchedule", "removed"],
  ]);
  assert.ok(state.schedules.some(row => row.id === "manual"));
  assert.ok(state.schedules.some(row => row.id === "keep"));
  assert.equal(changes.filter(change => change.action === "retire").length, 2);

  const second = [];
  await reconcileSchedules({
    api: {
      getSchedules: async () => state.schedules,
      deleteSchedule: async () => { throw new Error("unexpected second delete"); },
      updateSchedule: async () => { throw new Error("unexpected second update"); },
      createSchedule: async () => { throw new Error("unexpected second create"); },
    },
    config: { schedules: [keep, disabled] },
    apply: true,
    refs,
    changes: second,
  });
  assert.deepEqual(second, []);
});

test("managed schedule retirement fails closed when Actual rejects deletion", async () => {
  const existing = scheduleConfig({ name: "Retired bill", managed_id: "retired-bill" });
  await assert.rejects(
    () => reconcileSchedules({
      api: {
        getSchedules: async () => [{ id: "retired", name: managedScheduleName(existing) }],
        deleteSchedule: async () => false,
      },
      config: { schedules: [] },
      apply: true,
      refs,
      changes: [],
    }),
    /refused to retire schedule retired/,
  );
});
