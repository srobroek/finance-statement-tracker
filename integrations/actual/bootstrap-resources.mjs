import path from "node:path";

import {
  compileCanonicalRules,
  scheduleSignature,
  validateBootstrapConfig,
} from "./bootstrap-config.mjs";

const normalized = value => String(value ?? "").trim().toLocaleLowerCase();

const byName = (rows, property = "name") =>
  new Map(rows.map(row => [normalized(row[property]), row]));

export function schedulesDiffer(left, right) {
  return scheduleSignature(left) !== scheduleSignature(right);
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stable(item)]),
    );
  }
  return value;
}

function signature(rule) {
  const compact = item => {
    const result = { op: item.op, value: stable(item.value) };
    if (item.field !== undefined && !["append-notes", "prepend-notes"].includes(item.op)) {
      result.field = item.field;
    }
    if (item.options !== undefined) result.options = stable(item.options);
    return result;
  };
  return JSON.stringify(stable({
    stage: rule.stage,
    conditionsOp: rule.conditionsOp ?? "and",
    conditions: (rule.conditions ?? []).map(compact),
    actions: (rule.actions ?? []).map(compact),
  }));
}

export function selectRetiredRuleIds(existingRules, retiredRules) {
  const retiredSignatures = new Set(retiredRules.map(signature));
  return existingRules
    .filter(existing => retiredSignatures.has(signature(existing)))
    .map(existing => existing.id);
}

export function selectStageMigrationRuleIds(existingRules, desiredRules, migrations) {
  const stageValue = stage => stage === "default" ? null : stage;
  const candidates = [];
  for (const rule of desiredRules) {
    for (const migration of migrations ?? []) {
      if (stageValue(migration.to) === rule.stage) {
        candidates.push({ ...rule, stage: stageValue(migration.from) });
      }
    }
  }
  return selectRetiredRuleIds(existingRules, candidates);
}

const enabled = rows => rows.filter(item => item.enabled !== false);

/**
 * Reconcile configured accounts, including the zero-balance retirement guard.
 * The account refresh is deliberately kept after retirement so later phases
 * resolve references against the same post-account state as the CLI did.
 */
export async function reconcileAccounts({ api, config, apply, accounts, changes }) {
  for (const desired of enabled(config.accounts ?? [])) {
    const names = [desired.name, ...(desired.aliases ?? [])].map(normalized);
    const found = accounts.find(account => names.includes(normalized(account.name)));
    if (!found) {
      changes.push({ action: "create", type: "account", name: desired.name });
      if (apply) {
        await api.createAccount({
          name: desired.name,
          type: desired.type ?? "other",
          offbudget: Boolean(desired.offbudget),
          closed: false,
        }, Number(desired.initial_balance ?? 0));
      }
      continue;
    }
    const fields = {};
    if (found.name !== desired.name) fields.name = desired.name;
    if (Boolean(found.offbudget) !== Boolean(desired.offbudget)) {
      fields.offbudget = Boolean(desired.offbudget);
    }
    if (Object.keys(fields).length) {
      changes.push({ action: "update", type: "account", name: found.name, fields });
      if (apply) await api.updateAccount(found.id, fields);
    }
  }

  const desiredAccountNames = new Set(
    (config.accounts ?? [])
      .filter(item => item.enabled !== false)
      .flatMap(desired => [desired.name, ...(desired.aliases ?? [])])
      .map(normalized),
  );
  for (const retiredName of config.retired_accounts ?? []) {
    const retiredKey = normalized(retiredName);
    if (desiredAccountNames.has(retiredKey)) {
      throw new Error(`Account cannot be both active and retired: ${retiredName}`);
    }
    const found = accounts.find(account => normalized(account.name) === retiredKey);
    if (!found || Boolean(found.closed)) continue;
    const balance = await api.getAccountBalance(found.id);
    if (balance !== 0) {
      throw new Error(`Refusing to close non-zero retired account ${found.name}: ${balance}`);
    }
    changes.push({ action: "close", type: "account", name: found.name });
    if (apply) await api.closeAccount(found.id);
  }

  return apply ? api.getAccounts() : accounts;
}

export async function reconcileCategories({ api, config, apply, groups, categories, changes }) {
  const groupIndex = byName(groups);
  for (const desired of config.category_groups ?? []) {
    let group = groupIndex.get(normalized(desired.name));
    if (!group) {
      changes.push({ action: "create", type: "category_group", name: desired.name });
      if (apply) {
        const id = await api.createCategoryGroup({
          name: desired.name,
          is_income: Boolean(desired.is_income),
          hidden: Boolean(desired.hidden),
        });
        group = { id, name: desired.name };
        groupIndex.set(normalized(desired.name), group);
      }
    }
    for (const categoryName of desired.categories ?? []) {
      const existing = categories.find(category => normalized(category.name) === normalized(categoryName));
      if (!existing) {
        changes.push({ action: "create", type: "category", name: categoryName, group: desired.name });
        if (apply) {
          if (!group?.id) throw new Error(`Category group was not created: ${desired.name}`);
          await api.createCategory({
            name: categoryName,
            group_id: group.id,
            is_income: Boolean(desired.is_income),
            hidden: false,
          });
        }
      } else if (apply && group?.id && existing.group_id !== group.id) {
        changes.push({ action: "update", type: "category", name: categoryName, group: desired.name });
        await api.updateCategory(existing.id, { group_id: group.id });
      }
    }
  }
}

export async function reconcileTagsAndPayees({ api, config, apply, tags, payees, changes }) {
  const tagIndex = byName(tags, "tag");
  for (const desired of config.tags ?? []) {
    if (!tagIndex.has(normalized(desired.tag))) {
      changes.push({ action: "create", type: "tag", name: desired.tag });
      if (apply) {
        const tag = { tag: desired.tag, description: desired.description ?? "" };
        if (desired.color) tag.color = desired.color;
        await api.createTag(tag);
      }
    }
  }

  const payeeIndex = byName(payees);
  for (const desired of config.payees ?? []) {
    if (!payeeIndex.has(normalized(desired.name))) {
      changes.push({ action: "create", type: "payee", name: desired.name });
      if (apply) await api.createPayee({ name: desired.name });
    }
  }
}

export async function reconcileCategoryLearning({ api, config, apply, changes }) {
  if (config.actual_settings?.category_learning !== false) return;
  const learningPayees = (await api.aqlQuery(
    api.q("payees").select(["id", "name", "transfer_acct", "learn_categories"]),
  )).data;
  for (const payee of learningPayees.filter(item => !item.transfer_acct)) {
    if (payee.learn_categories !== false) {
      changes.push({ action: "disable", type: "payee_category_learning", name: payee.name });
      if (apply) await api.updatePayee(payee.id, { learn_categories: false });
    }
  }
}

export function resolveBootstrapReferences(value, refs, { strict = true } = {}) {
  if (Array.isArray(value)) return value.map(item => resolveBootstrapReferences(item, refs, { strict }));
  if (value && typeof value === "object" && value.ref && value.name) {
    const match = refs[value.ref]?.get(normalized(value.name));
    if (!match) {
      if (!strict) return `@${value.ref}:${value.name}`;
      throw new Error(`Unknown ${value.ref} reference: ${value.name}`);
    }
    return match.id;
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, resolveBootstrapReferences(item, refs, { strict })]),
    );
  }
  return value;
}

export async function loadCanonicalRules(config, configPath, readJson) {
  const collected = [];
  const skipped = [];
  const deferred = [];
  for (const source of config.canonical_rule_sources ?? []) {
    const filename = path.resolve(path.dirname(path.resolve(configPath)), source.path);
    const payload = await readJson(filename);
    const allRows = Array.isArray(payload) ? payload : [payload];
    const include = new Set(source.include_rule_ids ?? []);
    const rows = include.size ? allRows.filter(row => include.has(row.rule_id)) : allRows;
    const compiled = compileCanonicalRules(rows, { onlyMarked: source.only_marked !== false });
    collected.push(...compiled.rules);
    skipped.push(...compiled.skipped.map(item => ({ ...item, source: filename })));
    deferred.push(...compiled.deferred.map(item => ({ ...item, source: filename })));
  }
  return { rules: collected, skipped, deferred };
}

export async function reconcileRules({ api, config, apply, configPath, refs, changes, readJson }) {
  const compiled = await loadCanonicalRules(config, configPath, readJson);
  const desiredRules = [
    ...(config.rules ?? []).filter(item => item.enabled !== false),
    ...compiled.rules,
  ].map(desired => ({
    desired,
    rule: resolveBootstrapReferences({
      stage: desired.stage === undefined ? "pre" : desired.stage,
      conditionsOp: desired.conditionsOp ?? "and",
      conditions: desired.conditions ?? [],
      actions: desired.actions ?? [],
    }, refs, { strict: apply }),
  }));
  let existingRules = await api.getRules();
  const retiredRules = (config.retired_rules ?? []).map(desired => resolveBootstrapReferences({
    stage: desired.stage ?? "pre",
    conditionsOp: desired.conditionsOp ?? "and",
    conditions: desired.conditions ?? [],
    actions: desired.actions ?? [],
  }, refs, { strict: apply }));
  const retiredRuleIds = new Set([
    ...selectRetiredRuleIds(existingRules, retiredRules),
    ...selectStageMigrationRuleIds(
      existingRules,
      desiredRules.map(item => item.rule),
      config.rule_stage_migrations,
    ),
  ]);
  for (const existing of existingRules) {
    if (!retiredRuleIds.has(existing.id)) continue;
    changes.push({ action: "delete", type: "rule", id: existing.id });
    if (apply && await api.deleteRule(existing.id) === false) {
      throw new Error(`Actual refused to delete retired rule ${existing.id}`);
    }
  }
  if (apply && retiredRuleIds.size) existingRules = await api.getRules();
  const existingRuleSignatures = new Set(existingRules.map(signature));
  for (const { desired, rule } of desiredRules) {
    if (!existingRuleSignatures.has(signature(rule))) {
      changes.push({ action: "create", type: "rule", name: desired.name });
      if (apply) {
        await api.createRule(rule);
        existingRuleSignatures.add(signature(rule));
      }
    }
  }
  return compiled;
}

export async function reconcileSchedules({ api, config, apply, refs, changes }) {
  const existingSchedules = await api.getSchedules();
  const schedulesByName = byName(existingSchedules);
  for (const desired of enabled(config.schedules ?? [])) {
    const amountOp = desired.amount_op ?? "is";
    const amount = amountOp === "isbetween"
      ? { num1: desired.amount_min_minor, num2: desired.amount_max_minor }
      : desired.amount_minor;
    const schedule = resolveBootstrapReferences({
      name: desired.name,
      account: { ref: "account", name: desired.account },
      payee: { ref: "payee", name: desired.payee },
      amount,
      amountOp,
      date: desired.date,
      posts_transaction: Boolean(desired.posts_transaction),
    }, refs, { strict: apply });
    const existing = schedulesByName.get(normalized(desired.name));
    if (!existing) {
      changes.push({ action: "create", type: "schedule", name: desired.name });
      if (apply) await api.createSchedule(schedule);
    } else if (schedulesDiffer(existing, schedule)) {
      changes.push({ action: "update", type: "schedule", name: desired.name });
      if (apply) await api.updateSchedule(existing.id, schedule, true);
    }
  }
}

export async function reconcileBudgets({ api, config, apply, refs, changes }) {
  for (const desiredMonth of config.budget_months ?? []) {
    const month = await api.getBudgetMonth(desiredMonth.month);
    const current = new Map(
      month.categoryGroups.flatMap(group => (group.categories ?? []).map(category => [category.id, category])),
    );
    for (const desired of desiredMonth.categories) {
      const category = refs.category.get(normalized(desired.name));
      if (!category) throw new Error(`Unknown category reference: ${desired.name}`);
      const existing = current.get(category.id) ?? {};
      if (Number(existing.budgeted ?? 0) !== desired.amount_minor) {
        changes.push({
          action: "set",
          type: "budget",
          month: desiredMonth.month,
          category: desired.name,
          amount_minor: desired.amount_minor,
        });
        if (apply) await api.setBudgetAmount(desiredMonth.month, category.id, desired.amount_minor);
      }
      if (desired.carryover !== undefined && Boolean(existing.carryover) !== Boolean(desired.carryover)) {
        changes.push({
          action: "set",
          type: "budget_carryover",
          month: desiredMonth.month,
          category: desired.name,
          carryover: Boolean(desired.carryover),
        });
        if (apply) await api.setBudgetCarryover(desiredMonth.month, category.id, Boolean(desired.carryover));
      }
    }
  }
}

/**
 * Run all bootstrap phases in the historical order and perform one terminal
 * remote sync. Individual phases never sync so dry-run and write ordering are
 * observable and testable without a live Actual server.
 */
export async function reconcileBootstrapResources({
  api,
  config,
  apply,
  configPath,
  readJson,
  syncRemote = true,
}) {
  validateBootstrapConfig(config);
  const changes = [];
  let accounts = await api.getAccounts();
  let groups = await api.getCategoryGroups();
  let categories = await api.getCategories();
  let tags = await api.getTags();
  let payees = await api.getPayees();

  accounts = await reconcileAccounts({ api, config, apply, accounts, changes });
  await reconcileCategories({ api, config, apply, groups, categories, changes });
  await reconcileTagsAndPayees({ api, config, apply, tags, payees, changes });

  if (apply) {
    groups = await api.getCategoryGroups();
    categories = await api.getCategories();
    tags = await api.getTags();
    payees = await api.getPayees();
  }
  await reconcileCategoryLearning({ api, config, apply, changes });

  const refs = {
    account: byName(accounts),
    category: byName(categories),
    category_group: byName(groups),
    payee: byName(payees),
    tag: byName(tags, "tag"),
  };
  const compiled = await reconcileRules({
    api,
    config,
    apply,
    configPath,
    refs,
    changes,
    readJson,
  });
  await reconcileSchedules({ api, config, apply, refs, changes });
  await reconcileBudgets({ api, config, apply, refs, changes });

  if (apply && syncRemote) await api.sync();
  return {
    status: apply ? "applied" : "planned",
    changes,
    native_rule_compilation: {
      compiled: compiled.rules.length,
      skipped: compiled.skipped,
      deferred: compiled.deferred,
    },
  };
}
