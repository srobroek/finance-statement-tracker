import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import * as actual from "@actual-app/api";
import { buildTagReport, csvTags } from "./tag-report.mjs";
import { statementPaymentReminderSpec } from "./payment-reminder.mjs";
import { validateCanonicalActualNotes } from "./note-contract.mjs";
import {
  compileCanonicalRules,
  scheduleSignature,
  validateBootstrapConfig,
} from "./bootstrap-config.mjs";

let actualInternal = null;

function parseArgs(values) {
  const result = { _: [] };
  for (let index = 0; index < values.length; index += 1) {
    const token = values[index];
    if (!token.startsWith("--")) {
      result._.push(token);
      continue;
    }
    const key = token.slice(2);
    const next = values[index + 1];
    if (next && !next.startsWith("--")) {
      result[key] = next;
      index += 1;
    } else {
      result[key] = true;
    }
  }
  return result;
}

function requireEnv(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

async function readJson(filename) {
  return JSON.parse(await fs.readFile(filename, "utf8"));
}

async function writeResult(filename, value) {
  if (!filename) return;
  await fs.mkdir(path.dirname(path.resolve(filename)), { recursive: true });
  await fs.writeFile(filename, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

const normalized = value => String(value ?? "").trim().toLocaleLowerCase();
const byName = (rows, property = "name") =>
  new Map(rows.map(row => [normalized(row[property]), row]));

const importedPayeeKey = value => normalized(value).replace(/[^a-z0-9]+/g, " ").trim();
const economicKey = row => JSON.stringify([
  String(row.date ?? ""),
  Number(row.amount ?? 0),
  importedPayeeKey(row.imported_payee),
]);

export function partitionCrossSourceStatementDuplicates(records, existingRows) {
  const existingIds = new Set(
    existingRows.map(row => String(row.imported_id ?? "")).filter(Boolean),
  );
  const incomingCounts = new Map();
  for (const record of records) {
    const key = economicKey(record);
    incomingCounts.set(key, (incomingCounts.get(key) ?? 0) + 1);
  }
  const browserByKey = new Map();
  for (const row of existingRows) {
    if (row.tombstone || !String(row.imported_id ?? "").startsWith("browser:")) continue;
    const key = economicKey(row);
    const matches = browserByKey.get(key) ?? [];
    matches.push(row);
    browserByKey.set(key, matches);
  }

  const kept = [];
  const suppressed = [];
  for (const record of records) {
    const importedId = String(record.imported_id ?? "");
    const key = economicKey(record);
    const browserMatches = browserByKey.get(key) ?? [];
    const isUncommittedStatement = importedId.startsWith("statement:") && !existingIds.has(importedId);
    if (isUncommittedStatement && incomingCounts.get(key) === 1 && browserMatches.length === 1) {
      suppressed.push({
        imported_id: importedId,
        matched_existing_id: String(browserMatches[0].imported_id),
        date: record.date,
        amount: record.amount,
        imported_payee: record.imported_payee,
      });
      continue;
    }
    kept.push(record);
  }
  return { records: kept, suppressed };
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

async function withoutActualReconciliationNoise(callback) {
  if (process.env.ACTUAL_VERBOSE === "true") return callback();
  const original = console.log;
  console.log = (...values) => {
    const message = String(values[0] ?? "");
    if (message.startsWith("Performing transaction reconciliation") || message.startsWith("Debug data for the operations")) {
      return;
    }
    original(...values);
  };
  try {
    return await callback();
  } finally {
    console.log = original;
  }
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

async function openBudget() {
  const dataDir = path.resolve(process.env.ACTUAL_DATA_DIR || ".actual-cache");
  await fs.mkdir(dataDir, { recursive: true });
  actualInternal = await actual.init({
    dataDir,
    serverURL: requireEnv("ACTUAL_SERVER_URL"),
    password: requireEnv("ACTUAL_PASSWORD"),
    verbose: process.env.ACTUAL_VERBOSE === "true",
  });
  const syncId = requireEnv("ACTUAL_SYNC_ID");
  const encryptionPassword = process.env.ACTUAL_ENCRYPTION_PASSWORD;
  await actual.downloadBudget(
    syncId,
    encryptionPassword ? { password: encryptionPassword } : undefined,
  );
  // A cached budget may be older than the server. Always pull remote changes
  // before a read, preflight, or bootstrap decision.
  await actual.sync();
}

async function doctor() {
  const server = await actual.getServerVersion();
  const accounts = await actual.getAccounts();
  const categories = await actual.getCategories();
  const groups = await actual.getCategoryGroups();
  const tags = await actual.getTags();
  const rules = await actual.getRules();
  const schedules = await actual.getSchedules();
  const balances = [];
  for (const account of accounts) {
    balances.push({
      id: account.id,
      name: account.name,
      offbudget: Boolean(account.offbudget),
      closed: Boolean(account.closed),
      balance: await actual.getAccountBalance(account.id),
    });
  }
  return {
    status: "ok",
    server,
    sync_id: requireEnv("ACTUAL_SYNC_ID"),
    counts: {
      accounts: accounts.length,
      category_groups: groups.length,
      categories: categories.length,
      tags: tags.length,
      rules: rules.length,
      schedules: schedules.length,
    },
    accounts: balances,
  };
}

async function snapshot(start, end) {
  if (!start || !end) throw new Error("snapshot requires --start and --end (YYYY-MM-DD)");
  const accounts = await actual.getAccounts();
  const categories = new Map((await actual.getCategories()).map(row => [row.id, row.name]));
  const payees = new Map((await actual.getPayees()).map(row => [row.id, row.name]));
  const transactions = [];
  for (const account of accounts) {
    for (const row of await actual.getTransactions(account.id, start, end)) {
      transactions.push({
        ...row,
        account_name: account.name,
        category_name: categories.get(row.category) ?? null,
        payee_name: payees.get(row.payee) ?? null,
      });
    }
  }
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    period: { start, end },
    transactions,
  };
}

async function dashboardRows() {
  const pages = (await actual.aqlQuery(actual.q("dashboard_pages").select("*"))).data;
  const widgets = (await actual.aqlQuery(actual.q("dashboard").select("*"))).data;
  if (!actualInternal) throw new Error("Actual internal API is not initialized");
  const reports = await actualInternal.send("report/get");
  return { pages, widgets, reports };
}

function parseDashboardMeta(value) {
  if (value === null || value === undefined || value === "") return {};
  if (typeof value === "string") return JSON.parse(value);
  return value;
}

export function exportDashboardDocument(page, widgets, reports) {
  const reportsById = new Map(reports.map(report => [report.id, report]));
  return {
    version: 1,
    name: page.name,
    widgets: widgets
      .filter(widget => widget.dashboard_page_id === page.id && !widget.tombstone)
      .sort((left, right) => left.y - right.y || left.x - right.x)
      .map(widget => {
        const meta = parseDashboardMeta(widget.meta);
        const exportedMeta = widget.type === "custom-report"
          ? reportsById.get(meta.id) ?? meta
          : meta;
        return {
          type: widget.type,
          x: widget.x,
          y: widget.y,
          width: widget.width,
          height: widget.height,
          meta: exportedMeta,
        };
      }),
  };
}

async function dashboardAudit() {
  const { pages, widgets, reports } = await dashboardRows();
  return {
    status: "ok",
    dashboards: pages
      .filter(page => !page.tombstone)
      .sort((left, right) => String(left.name).localeCompare(String(right.name)))
      .map(page => ({
        id: page.id,
        name: page.name,
        widgets: widgets.filter(widget => widget.dashboard_page_id === page.id && !widget.tombstone).length,
      })),
    custom_reports: reports.map(report => ({ id: report.id, name: report.name })),
  };
}

async function dashboardExport(name) {
  if (!name) throw new Error("dashboard-export requires --name <dashboard>");
  const { pages, widgets, reports } = await dashboardRows();
  const page = pages.find(item => !item.tombstone && normalized(item.name) === normalized(name));
  if (!page) throw new Error(`Unknown dashboard: ${name}`);
  return exportDashboardDocument(page, widgets, reports);
}

async function dashboardApply(configPath, apply) {
  if (!configPath) throw new Error("dashboard-apply requires --config <file>");
  const config = await readJson(configPath);
  if (config.schema_version !== "actual-dashboard-suite-v1" || !Array.isArray(config.dashboards)) {
    throw new Error("dashboard suite must use actual-dashboard-suite-v1 and provide dashboards");
  }
  const existing = (await dashboardRows()).pages.filter(page => !page.tombstone);
  const byDashboardName = byName(existing);
  const planned = [];
  for (const entry of config.dashboards) {
    if (!entry.name || !entry.file) throw new Error("dashboard entries require name and file");
    const filename = path.resolve(path.dirname(path.resolve(configPath)), entry.file);
    const document = await readJson(filename);
    if (!Array.isArray(document.widgets) || document.widgets.length === 0) {
      throw new Error(`dashboard ${entry.name} must contain at least one widget`);
    }
    let page = byDashboardName.get(normalized(entry.name));
    planned.push({
      action: page ? "replace" : "create",
      dashboard: entry.name,
      widgets: document.widgets.length,
      source: filename,
    });
    if (!apply) continue;
    if (!page) {
      const id = await actualInternal.send("dashboard-create", { name: entry.name });
      page = { id, name: entry.name };
      byDashboardName.set(normalized(entry.name), page);
    }
    await actualInternal.send("dashboard-import", {
      filePath: filename,
      dashboardPageId: page.id,
    });
  }
  if (apply) await actual.sync();
  return { status: apply ? "applied" : "planned", dashboards: planned };
}

async function canonicalRules(config, configPath) {
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

export async function bootstrap(config, apply, configPath, { syncRemote = true } = {}) {
  validateBootstrapConfig(config);
  const changes = [];
  let accounts = await actual.getAccounts();
  let groups = await actual.getCategoryGroups();
  let categories = await actual.getCategories();
  let tags = await actual.getTags();
  let payees = await actual.getPayees();

  for (const desired of (config.accounts ?? []).filter(item => item.enabled !== false)) {
    const names = [desired.name, ...(desired.aliases ?? [])].map(normalized);
    const found = accounts.find(account => names.includes(normalized(account.name)));
    if (!found) {
      changes.push({ action: "create", type: "account", name: desired.name });
      if (apply) {
        await actual.createAccount({
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
      if (apply) await actual.updateAccount(found.id, fields);
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
    const balance = await actual.getAccountBalance(found.id);
    if (balance !== 0) {
      throw new Error(`Refusing to close non-zero retired account ${found.name}: ${balance}`);
    }
    changes.push({ action: "close", type: "account", name: found.name });
    if (apply) await actual.closeAccount(found.id);
  }

  if (apply) accounts = await actual.getAccounts();
  const groupIndex = byName(groups);
  for (const desired of config.category_groups ?? []) {
    let group = groupIndex.get(normalized(desired.name));
    if (!group) {
      changes.push({ action: "create", type: "category_group", name: desired.name });
      if (apply) {
        const id = await actual.createCategoryGroup({
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
          await actual.createCategory({
            name: categoryName,
            group_id: group.id,
            is_income: Boolean(desired.is_income),
            hidden: false,
          });
        }
      } else if (apply && group?.id && existing.group_id !== group.id) {
        changes.push({ action: "update", type: "category", name: categoryName, group: desired.name });
        await actual.updateCategory(existing.id, { group_id: group.id });
      }
    }
  }

  const tagIndex = byName(tags, "tag");
  for (const desired of config.tags ?? []) {
    if (!tagIndex.has(normalized(desired.tag))) {
      changes.push({ action: "create", type: "tag", name: desired.tag });
      if (apply) {
        const tag = { tag: desired.tag, description: desired.description ?? "" };
        if (desired.color) tag.color = desired.color;
        await actual.createTag(tag);
      }
    }
  }

  const payeeIndex = byName(payees);
  for (const desired of config.payees ?? []) {
    if (!payeeIndex.has(normalized(desired.name))) {
      changes.push({ action: "create", type: "payee", name: desired.name });
      if (apply) await actual.createPayee({ name: desired.name });
    }
  }

  if (apply) {
    groups = await actual.getCategoryGroups();
    categories = await actual.getCategories();
    tags = await actual.getTags();
    payees = await actual.getPayees();
  }
  if (config.actual_settings?.category_learning === false) {
    const learningPayees = (await actual.aqlQuery(
      actual.q("payees").select(["id", "name", "transfer_acct", "learn_categories"]),
    )).data;
    for (const payee of learningPayees.filter(item => !item.transfer_acct)) {
      if (payee.learn_categories !== false) {
        changes.push({ action: "disable", type: "payee_category_learning", name: payee.name });
        if (apply) await actual.updatePayee(payee.id, { learn_categories: false });
      }
    }
  }
  const refs = {
    account: byName(accounts),
    category: byName(categories),
    category_group: byName(groups),
    payee: byName(payees),
    tag: byName(tags, "tag"),
  };
  function resolve(value) {
    if (Array.isArray(value)) return value.map(resolve);
    if (value && typeof value === "object" && value.ref && value.name) {
      const match = refs[value.ref]?.get(normalized(value.name));
      if (!match) {
        if (!apply) return `@${value.ref}:${value.name}`;
        throw new Error(`Unknown ${value.ref} reference: ${value.name}`);
      }
      return match.id;
    }
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolve(item)]));
    }
    return value;
  }

  const compiled = await canonicalRules(config, configPath);
  const desiredRules = [
    ...(config.rules ?? []).filter(item => item.enabled !== false),
    ...compiled.rules,
  ].map(desired => ({
    desired,
    rule: resolve({
      stage: desired.stage === undefined ? "pre" : desired.stage,
      conditionsOp: desired.conditionsOp ?? "and",
      conditions: desired.conditions ?? [],
      actions: desired.actions ?? [],
    }),
  }));
  let existingRules = await actual.getRules();
  const retiredRules = (config.retired_rules ?? []).map(desired => resolve({
      stage: desired.stage ?? "pre",
      conditionsOp: desired.conditionsOp ?? "and",
      conditions: desired.conditions ?? [],
      actions: desired.actions ?? [],
    }));
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
    if (apply && await actual.deleteRule(existing.id) === false) {
      throw new Error(`Actual refused to delete retired rule ${existing.id}`);
    }
  }
  if (apply && retiredRuleIds.size) existingRules = await actual.getRules();
  const existingRuleSignatures = new Set(existingRules.map(signature));
  for (const { desired, rule } of desiredRules) {
    if (!existingRuleSignatures.has(signature(rule))) {
      changes.push({ action: "create", type: "rule", name: desired.name });
      if (apply) {
        await actual.createRule(rule);
        existingRuleSignatures.add(signature(rule));
      }
    }
  }

  const existingSchedules = await actual.getSchedules();
  const schedulesByName = byName(existingSchedules);
  for (const desired of (config.schedules ?? []).filter(item => item.enabled !== false)) {
    const amountOp = desired.amount_op ?? "is";
    const amount = amountOp === "isbetween"
      ? { num1: desired.amount_min_minor, num2: desired.amount_max_minor }
      : desired.amount_minor;
    const schedule = resolve({
      name: desired.name,
      account: { ref: "account", name: desired.account },
      payee: { ref: "payee", name: desired.payee },
      amount,
      amountOp,
      date: desired.date,
      posts_transaction: Boolean(desired.posts_transaction),
    });
    const existing = schedulesByName.get(normalized(desired.name));
    if (!existing) {
      changes.push({ action: "create", type: "schedule", name: desired.name });
      if (apply) await actual.createSchedule(schedule);
    } else if (scheduleSignature(existing) !== scheduleSignature(schedule)) {
      changes.push({ action: "update", type: "schedule", name: desired.name });
      if (apply) await actual.updateSchedule(existing.id, schedule, true);
    }
  }

  for (const desiredMonth of config.budget_months ?? []) {
    const month = await actual.getBudgetMonth(desiredMonth.month);
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
        if (apply) await actual.setBudgetAmount(desiredMonth.month, category.id, desired.amount_minor);
      }
      if (desired.carryover !== undefined && Boolean(existing.carryover) !== Boolean(desired.carryover)) {
        changes.push({
          action: "set",
          type: "budget_carryover",
          month: desiredMonth.month,
          category: desired.name,
          carryover: Boolean(desired.carryover),
        });
        if (apply) await actual.setBudgetCarryover(desiredMonth.month, category.id, Boolean(desired.carryover));
      }
    }
  }

  if (apply && syncRemote) await actual.sync();
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

export async function importEnvelopes(payload, commit, { syncRemote = true } = {}) {
  const envelopes = Array.isArray(payload) ? payload : payload.envelopes;
  if (!Array.isArray(envelopes) || !envelopes.length) throw new Error("No import envelopes found");
  const accounts = byName(await actual.getAccounts());
  const categories = byName(await actual.getCategories());

  const prepared = [];
  for (const envelope of envelopes) {
    const account = accounts.get(normalized(envelope.account));
    if (!account) throw new Error(`Unknown Actual account: ${envelope.account}`);
    const records = envelope.records.map(source => {
      const record = { ...source };
      record.notes = record.notes ?? "";
      validateCanonicalActualNotes(record.notes);
      if (record.category_name) {
        const category = categories.get(normalized(record.category_name));
        if (!category) throw new Error(`Unknown Actual category: ${record.category_name}`);
        record.category = category.id;
        delete record.category_name;
      }
      return record;
    });
    const dates = records.map(record => record.date).sort();
    const existingRows = dates.length
      ? await actual.getTransactions(account.id, dates[0], dates[dates.length - 1])
      : [];
    const partition = partitionCrossSourceStatementDuplicates(records, existingRows);
    prepared.push({ account, envelope, records: partition.records, suppressed: partition.suppressed });
  }

  const preflight = [];
  for (const item of prepared) {
    const result = item.records.length
      ? await withoutActualReconciliationNoise(() =>
        actual.importTransactions(item.account.id, item.records, {
          defaultCleared: Boolean(item.envelope.default_cleared),
          dryRun: true,
          reimportDeleted: false,
        }))
      : { added: [], updated: [], errors: [] };
    preflight.push({
      account: item.account.name,
      result,
      suppressed_cross_source: item.suppressed,
    });
  }
  const errors = preflight.flatMap(item => item.result.errors ?? []);
  if (errors.length || !commit) {
    return { status: errors.length ? "rejected" : "dry-run", preflight, imported: [] };
  }

  const imported = [];
  for (const item of prepared) {
    const result = item.records.length
      ? await withoutActualReconciliationNoise(() =>
        actual.importTransactions(item.account.id, item.records, {
          defaultCleared: Boolean(item.envelope.default_cleared),
          dryRun: false,
          reimportDeleted: false,
        }))
      : { added: [], updated: [], errors: [] };
    imported.push({
      account: item.account.name,
      result,
      suppressed_cross_source: item.suppressed,
    });
  }
  const verification = [];
  for (const item of prepared) {
    if (!item.records.length) {
      verification.push({
        account: item.account.name,
        expected: 0,
        verified: 0,
        duplicated: 0,
        suppressed_cross_source: item.suppressed.length,
      });
      continue;
    }
    const dates = item.records.map(record => record.date).sort();
    const rows = await actual.getTransactions(
      item.account.id,
      dates[0],
      dates[dates.length - 1],
    );
    const counts = new Map();
    for (const row of rows) {
      if (row.imported_id) counts.set(row.imported_id, (counts.get(row.imported_id) ?? 0) + 1);
    }
    const expected = item.records.map(record => record.imported_id).filter(Boolean);
    const missing = expected.filter(importedId => !counts.has(importedId));
    const duplicated = expected.filter(importedId => (counts.get(importedId) ?? 0) > 1);
    if (missing.length || duplicated.length) {
      throw new Error(
        `Actual import verification failed for ${item.account.name}: ` +
        `missing=${JSON.stringify(missing)} duplicated=${JSON.stringify(duplicated)}`,
      );
    }
    verification.push({
      account: item.account.name,
      expected: expected.length,
      verified: expected.length,
      duplicated: 0,
      suppressed_cross_source: item.suppressed.length,
    });
  }
  const reminderSpec = statementPaymentReminderSpec(payload);
  let paymentReminder = reminderSpec;
  if (reminderSpec.status === "ready") {
    const source = reminderSpec.schedule;
    const account = accounts.get(normalized(source.account));
    if (!account) throw new Error(`Unknown Actual account for payment reminder: ${source.account}`);
    const schedule = { ...source, account: account.id };
    const existingSchedules = await actual.getSchedules();
    const existing = byName(existingSchedules).get(normalized(schedule.name));
    if (!existing) {
      await actual.createSchedule(schedule);
      paymentReminder = {
        status: "created",
        name: schedule.name,
        date: schedule.date,
        amount_minor: schedule.amount,
      };
    } else if (scheduleSignature(existing) !== scheduleSignature(schedule)) {
      await actual.updateSchedule(existing.id, schedule, true);
      paymentReminder = {
        status: "updated",
        name: schedule.name,
        date: schedule.date,
        amount_minor: schedule.amount,
      };
    } else {
      paymentReminder = {
        status: "unchanged",
        name: schedule.name,
        date: schedule.date,
        amount_minor: schedule.amount,
      };
    }
  }
  if (syncRemote) await actual.sync();
  return { status: "committed", preflight, imported, verification, payment_reminder: paymentReminder };
}

export function assertCommitEnabled(commit, environment = process.env) {
  if (commit && String(environment.ALLOW_ACTUAL_WRITES ?? "").toLowerCase() !== "true") {
    throw new Error("Actual commits are disabled; set ALLOW_ACTUAL_WRITES=true explicitly");
  }
}

export function validateTransactionRepairPlan(plan) {
  if (plan?.schema_version !== "actual-transaction-repair-v1") {
    throw new Error("Unsupported transaction repair plan schema");
  }
  if (!String(plan.reason ?? "").trim()) throw new Error("Transaction repair plan requires a reason");
  if (!Array.isArray(plan.repairs) || !plan.repairs.length) {
    throw new Error("Transaction repair plan requires at least one repair");
  }

  const importedIds = new Set();
  for (const [index, repair] of plan.repairs.entries()) {
    const prefix = `Repair ${index + 1}`;
    if (!String(repair.imported_id ?? "").trim()) throw new Error(`${prefix} requires imported_id`);
    if (!String(repair.account ?? "").trim()) throw new Error(`${prefix} requires account`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(repair.date ?? ""))) {
      throw new Error(`${prefix} requires an ISO date`);
    }
    if (!Number.isSafeInteger(repair.expected_current_amount) ||
        !Number.isSafeInteger(repair.corrected_amount)) {
      throw new Error(`${prefix} amounts must be integer minor units`);
    }
    if (repair.corrected_amount !== -repair.expected_current_amount) {
      throw new Error(`${prefix} must be an exact sign reversal`);
    }
    if (importedIds.has(repair.imported_id)) {
      throw new Error(`Duplicate imported_id in transaction repair plan: ${repair.imported_id}`);
    }
    importedIds.add(repair.imported_id);
  }
  return plan.repairs;
}

export async function repairTransactions(plan, apply, api = actual, { syncRemote = true } = {}) {
  const repairs = validateTransactionRepairPlan(plan);
  const accounts = byName(await api.getAccounts());
  const byAccount = new Map();
  for (const repair of repairs) {
    const key = normalized(repair.account);
    const rows = byAccount.get(key) ?? [];
    rows.push(repair);
    byAccount.set(key, rows);
  }

  const pending = [];
  const alreadyCorrected = [];
  for (const [accountKey, accountRepairs] of byAccount) {
    const account = accounts.get(accountKey);
    if (!account) throw new Error(`Unknown Actual account in repair plan: ${accountRepairs[0].account}`);
    const dates = accountRepairs.map(repair => repair.date).sort();
    const currentRows = await api.getTransactions(account.id, dates[0], dates[dates.length - 1]);

    for (const repair of accountRepairs) {
      const matches = currentRows.filter(row => row.imported_id === repair.imported_id);
      if (matches.length !== 1) {
        throw new Error(
          `Repair target ${repair.imported_id} matched ${matches.length} transactions in ${account.name}`,
        );
      }
      const row = matches[0];
      if (row.date !== repair.date) {
        throw new Error(`Repair target ${repair.imported_id} date drifted from ${repair.date} to ${row.date}`);
      }
      if (row.transfer_id) {
        throw new Error(`Repair target ${repair.imported_id} is an Actual transfer and cannot be repaired here`);
      }
      const item = {
        imported_id: repair.imported_id,
        transaction_id: row.id,
        account: account.name,
        date: row.date,
        expected_current_amount: repair.expected_current_amount,
        corrected_amount: repair.corrected_amount,
      };
      if (row.amount === repair.corrected_amount) {
        alreadyCorrected.push(item);
      } else if (row.amount === repair.expected_current_amount) {
        pending.push(item);
      } else {
        throw new Error(
          `Repair target ${repair.imported_id} amount drifted: expected ${repair.expected_current_amount}, ` +
          `already-corrected ${repair.corrected_amount}, found ${row.amount}`,
        );
      }
    }
  }

  if (!apply) {
    return {
      status: "planned",
      reason: plan.reason,
      pending,
      already_corrected: alreadyCorrected,
    };
  }

  for (const item of pending) {
    await api.updateTransaction(item.transaction_id, { amount: item.corrected_amount });
  }
  if (pending.length && syncRemote) await api.sync();

  const verification = [];
  for (const [accountKey, accountRepairs] of byAccount) {
    const account = accounts.get(accountKey);
    const dates = accountRepairs.map(repair => repair.date).sort();
    const currentRows = await api.getTransactions(account.id, dates[0], dates[dates.length - 1]);
    for (const repair of accountRepairs) {
      const matches = currentRows.filter(row => row.imported_id === repair.imported_id);
      if (matches.length !== 1 || matches[0].amount !== repair.corrected_amount) {
        throw new Error(`Repair verification failed for ${repair.imported_id}`);
      }
      verification.push({ imported_id: repair.imported_id, amount: matches[0].amount });
    }
  }
  return {
    status: "applied",
    reason: plan.reason,
    repaired: pending,
    already_corrected: alreadyCorrected,
    verification,
  };
}

const noteParts = value => String(value ?? "")
  .split("|")
  .map(part => part.trim())
  .filter(Boolean);

function mutateNoteParts(current, add = [], remove = []) {
  const removed = new Set(remove.map(normalized));
  const parts = noteParts(current).filter(part => !removed.has(normalized(part)));
  const present = new Set(parts.map(normalized));
  for (const token of add) {
    if (!present.has(normalized(token))) {
      parts.push(token);
      present.add(normalized(token));
    }
  }
  return parts.join(" | ");
}

function safeNoteToken(value, prefix) {
  const token = String(value ?? "").trim();
  if (!token || token.length > 500 || /[|\r\n\t]/.test(token)) {
    throw new Error(`${prefix} contains an unsafe note token`);
  }
  return token;
}

export function validateTransactionEnrichmentPlan(plan) {
  if (plan?.schema_version !== "actual-transaction-enrichment-v1") {
    throw new Error("Unsupported transaction enrichment plan schema");
  }
  if (plan.expected_server_version !== "26.8.1") {
    throw new Error("Transaction enrichment plan must pin Actual server version 26.8.1");
  }
  if (!String(plan.reason ?? "").trim()) {
    throw new Error("Transaction enrichment plan requires a reason");
  }
  if (!Array.isArray(plan.changes) || !plan.changes.length) {
    throw new Error("Transaction enrichment plan requires at least one change");
  }
  const importedIds = new Set();
  for (const [index, change] of plan.changes.entries()) {
    const prefix = `Change ${index + 1}`;
    const importedId = String(change.imported_id ?? "").trim();
    if (!importedId) throw new Error(`${prefix} requires imported_id`);
    if (importedIds.has(importedId)) {
      throw new Error(`Duplicate imported_id in transaction enrichment plan: ${importedId}`);
    }
    importedIds.add(importedId);
    if (!String(change.account ?? "").trim()) throw new Error(`${prefix} requires account`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(change.date ?? ""))) {
      throw new Error(`${prefix} requires an ISO date`);
    }
    if (!Number.isSafeInteger(change.expected_current_amount)) {
      throw new Error(`${prefix} expected_current_amount must be integer minor units`);
    }
    if (typeof change.expected_current_notes !== "string") {
      throw new Error(`${prefix} requires expected_current_notes`);
    }
    if (change.desired_notes !== undefined) {
      if (typeof change.desired_notes !== "string") {
        throw new Error(`${prefix} desired_notes must be a string`);
      }
      noteParts(change.desired_notes).forEach((value, tokenIndex) =>
        safeNoteToken(value, `${prefix} desired_notes[${tokenIndex}]`));
    }
    for (const [field, values] of [
      ["add_note_tokens", change.add_note_tokens ?? []],
      ["remove_note_tokens", change.remove_note_tokens ?? []],
    ]) {
      if (!Array.isArray(values)) throw new Error(`${prefix} ${field} must be an array`);
      values.forEach((value, tokenIndex) => safeNoteToken(value, `${prefix} ${field}[${tokenIndex}]`));
    }
    if (change.split !== undefined) {
      if (!Array.isArray(change.split) || change.split.length < 2) {
        throw new Error(`${prefix} split requires at least two children`);
      }
      let total = 0;
      for (const [childIndex, child] of change.split.entries()) {
        if (!Number.isSafeInteger(child.amount) || child.amount === 0) {
          throw new Error(`${prefix} split[${childIndex}] requires a non-zero integer amount`);
        }
        total += child.amount;
        if (typeof child.notes !== "string") {
          throw new Error(`${prefix} split[${childIndex}] requires notes`);
        }
        noteParts(child.notes).forEach((value, tokenIndex) =>
          safeNoteToken(value, `${prefix} split[${childIndex}].notes[${tokenIndex}]`));
      }
      if (total !== change.expected_current_amount) {
        throw new Error(`${prefix} split children must sum to the parent amount`);
      }
    }
  }
  return plan.changes;
}

function splitMatches(row, desiredChildren) {
  const current = row.subtransactions ?? [];
  if (!row.is_parent || current.length !== desiredChildren.length) return false;
  return current.every((child, index) => {
    const desired = desiredChildren[index];
    return child.amount === desired.amount &&
      String(child.notes ?? "") === desired.notes &&
      (desired.category === undefined || child.category === desired.category);
  });
}

export async function enrichTransactions(plan, apply, api = actual, { syncRemote = true } = {}) {
  const changes = validateTransactionEnrichmentPlan(plan);
  const serverResponse = await api.getServerVersion();
  const serverVersion = String(
    serverResponse && typeof serverResponse === "object"
      ? serverResponse.version ?? ""
      : serverResponse ?? "",
  );
  if (serverVersion !== plan.expected_server_version) {
    throw new Error(
      `Actual server version drifted: expected ${plan.expected_server_version}, found ${serverVersion}`,
    );
  }
  const accounts = byName(await api.getAccounts());
  const categories = byName(await api.getCategories());
  const grouped = new Map();
  for (const change of changes) {
    const key = normalized(change.account);
    const rows = grouped.get(key) ?? [];
    rows.push(change);
    grouped.set(key, rows);
  }

  const pending = [];
  const alreadyApplied = [];
  for (const [accountKey, accountChanges] of grouped) {
    const account = accounts.get(accountKey);
    if (!account) throw new Error(`Unknown Actual account: ${accountChanges[0].account}`);
    const dates = accountChanges.map(change => change.date).sort();
    const currentRows = await api.getTransactions(account.id, dates[0], dates[dates.length - 1]);
    for (const change of accountChanges) {
      const matches = currentRows.filter(row => row.imported_id === change.imported_id);
      if (matches.length !== 1) {
        throw new Error(`Enrichment target ${change.imported_id} matched ${matches.length} transactions`);
      }
      const row = matches[0];
      if (row.date !== change.date || row.amount !== change.expected_current_amount) {
        throw new Error(`Enrichment target ${change.imported_id} date or amount drifted`);
      }
      if (row.transfer_id) throw new Error(`Enrichment target ${change.imported_id} is a transfer`);
      const desiredNotes = change.desired_notes ?? mutateNoteParts(
        change.expected_current_notes,
        change.add_note_tokens,
        change.remove_note_tokens,
      );
      const desiredChildren = change.split?.map(child => {
        const category = child.category_name
          ? categories.get(normalized(child.category_name))
          : null;
        if (child.category_name && !category) {
          throw new Error(`Unknown Actual category in split: ${child.category_name}`);
        }
        return {
          amount: child.amount,
          notes: child.notes,
          ...(category ? { category: category.id } : {}),
        };
      });
      const item = {
        transaction_id: row.id,
        imported_id: row.imported_id,
        account: account.name,
        date: row.date,
        amount: row.amount,
        expected_current_notes: change.expected_current_notes,
        desired_notes: desiredNotes,
        desired_children: desiredChildren,
      };
      const already = String(row.notes ?? "") === desiredNotes &&
        (desiredChildren ? splitMatches(row, desiredChildren) : !row.is_parent);
      if (already) {
        alreadyApplied.push(item);
      } else if (String(row.notes ?? "") === change.expected_current_notes && !row.is_parent) {
        pending.push(item);
      } else {
        throw new Error(`Enrichment target ${change.imported_id} notes or split state drifted`);
      }
    }
  }

  if (!apply) {
    return { status: "planned", reason: plan.reason, pending, already_applied: alreadyApplied };
  }
  for (const item of pending) {
    const fields = { notes: item.desired_notes };
    if (item.desired_children) fields.subtransactions = item.desired_children;
    await api.updateTransaction(item.transaction_id, fields);
  }
  if (pending.length && syncRemote) await api.sync();

  const verification = [];
  for (const [accountKey, accountChanges] of grouped) {
    const account = accounts.get(accountKey);
    const dates = accountChanges.map(change => change.date).sort();
    const currentRows = await api.getTransactions(account.id, dates[0], dates[dates.length - 1]);
    for (const change of accountChanges) {
      const row = currentRows.find(item => item.imported_id === change.imported_id);
      const desiredNotes = change.desired_notes ?? mutateNoteParts(
        change.expected_current_notes,
        change.add_note_tokens,
        change.remove_note_tokens,
      );
      const desiredChildren = change.split?.map(child => {
        const category = child.category_name
          ? categories.get(normalized(child.category_name))
          : null;
        return { amount: child.amount, notes: child.notes, ...(category ? { category: category.id } : {}) };
      });
      if (!row || String(row.notes ?? "") !== desiredNotes ||
          (desiredChildren && !splitMatches(row, desiredChildren))) {
        throw new Error(`Enrichment verification failed for ${change.imported_id}`);
      }
      verification.push({ imported_id: row.imported_id, notes: row.notes, split_count: row.subtransactions?.length ?? 0 });
    }
  }
  return {
    status: "applied",
    reason: plan.reason,
    enriched: pending,
    already_applied: alreadyApplied,
    verification,
  };
}

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  const args = parseArgs(rest);
  if (!command || !["dashboard-apply", "dashboard-audit", "dashboard-export", "doctor", "bootstrap", "enrich-transactions", "import", "repair-transactions", "snapshot", "tag-report"].includes(command)) {
    throw new Error("Usage: node actualctl.mjs <dashboard-apply|dashboard-audit|dashboard-export|doctor|bootstrap|enrich-transactions|import|repair-transactions|snapshot|tag-report> [options]");
  }
  if (command === "import") assertCommitEnabled(Boolean(args.commit));
  if (command === "repair-transactions") assertCommitEnabled(Boolean(args.apply));
  if (command === "enrich-transactions") assertCommitEnabled(Boolean(args.apply));
  if (command === "dashboard-apply") assertCommitEnabled(Boolean(args.apply));
  await openBudget();
  try {
    let result;
    if (command === "doctor") {
      result = await doctor();
    } else if (command === "dashboard-audit") {
      result = await dashboardAudit();
    } else if (command === "dashboard-export") {
      result = await dashboardExport(args.name);
    } else if (command === "dashboard-apply") {
      result = await dashboardApply(args.config, Boolean(args.apply));
    } else if (command === "snapshot") {
      result = await snapshot(args.start, args.end);
    } else if (command === "tag-report") {
      const source = await snapshot(args.start, args.end);
      result = buildTagReport(source.transactions, {
        any: csvTags(args["any-tags"]),
        all: csvTags(args["all-tags"]),
        none: csvTags(args["without-tags"]),
        groupBy: args["group-by"] ?? "category",
      });
      result.period = source.period;
      result.generated_at = source.generated_at;
    } else if (command === "bootstrap") {
      if (!args.config) throw new Error("bootstrap requires --config <file>");
      result = await bootstrap(await readJson(args.config), Boolean(args.apply), args.config);
    } else if (command === "repair-transactions") {
      if (!args.plan) throw new Error("repair-transactions requires --plan <file>");
      result = await repairTransactions(await readJson(args.plan), Boolean(args.apply));
    } else if (command === "enrich-transactions") {
      if (!args.plan) throw new Error("enrich-transactions requires --plan <file>");
      result = await enrichTransactions(await readJson(args.plan), Boolean(args.apply));
    } else {
      if (!args.input) throw new Error("import requires --input <file>");
      result = await importEnvelopes(await readJson(args.input), Boolean(args.commit));
    }
    await writeResult(args.result, result);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (result.status === "rejected") process.exitCode = 2;
  } finally {
    await actual.shutdown();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
}
