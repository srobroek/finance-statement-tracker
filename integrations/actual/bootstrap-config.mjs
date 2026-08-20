const normalized = value => String(value ?? "").trim().toLocaleLowerCase();

const fieldMap = {
  merchant_raw: "imported_payee",
  vendor: "payee",
  category: "category",
  account: "account",
};

const operatorMap = {
  equals: "is",
  not_equals: "isNot",
  contains: "contains",
  not_contains: "doesNotContain",
  regex: "matches",
  in: "oneOf",
  not_in: "notOneOf",
  is_empty: "is",
  not_empty: "isNot",
};

const refField = { payee: "payee", category: "category", account: "account" };

const stageMap = {
  VENDOR_NORMALIZATION: "pre",
  CLASSIFICATION: null,
  TAGGING: "post",
};

function caseInsensitiveRegex(pattern) {
  let output = "";
  let escaped = false;
  let inCharacterClass = false;
  for (const character of String(pattern)) {
    if (escaped) {
      output += character;
      escaped = false;
      continue;
    }
    if (character === "\\") {
      output += character;
      escaped = true;
      continue;
    }
    if (character === "[") {
      output += character;
      inCharacterClass = true;
      continue;
    }
    if (character === "]") {
      output += character;
      inCharacterClass = false;
      continue;
    }
    if (!inCharacterClass && /[A-Za-z]/.test(character)) {
      output += `[${character.toUpperCase()}${character.toLowerCase()}]`;
    } else {
      output += character;
    }
  }
  return output;
}

function referencedValue(field, operator, value) {
  const ref = refField[field];
  if (!ref || ["contains", "doesNotContain", "matches"].includes(operator)) return value;
  if (Array.isArray(value)) return value.map(name => ({ ref, name }));
  if (value == null) return null;
  return { ref, name: value };
}

function flattenConditions(groups) {
  if (groups.length === 1) return { op: "and", rows: groups[0] };
  if (groups.every(group => group.length === 1)) {
    return { op: "or", rows: groups.map(group => group[0]) };
  }
  return null;
}

function expandContainsAny(condition) {
  if (condition.operator !== "contains_any") return [condition];
  if (!Array.isArray(condition.value) || !condition.value.length) return [];
  return condition.value.map(value => ({ ...condition, operator: "contains", value }));
}

export function compileCanonicalRules(rows, { onlyMarked = true } = {}) {
  const rules = [];
  const skipped = [];
  const deferred = [];
  for (const row of rows) {
    const ruleId = String(row.rule_id ?? row.name ?? "unnamed");
    if (onlyMarked && row.native_actual !== true) {
      skipped.push({ rule_id: ruleId, reason: "NOT_MARKED_NATIVE" });
      continue;
    }
    if (!row.enabled && row.enabled !== undefined) {
      skipped.push({ rule_id: ruleId, reason: "DISABLED" });
      continue;
    }
    if (!["VENDOR_NORMALIZATION", "CLASSIFICATION", "TAGGING"].includes(row.stage)) {
      skipped.push({ rule_id: ruleId, reason: "UNSUPPORTED_STAGE" });
      continue;
    }

    const groups = (row.match?.any ?? []).map(group => group.all ?? []);
    let flat = flattenConditions(groups);
    if (!flat || !flat.rows.length) {
      skipped.push({ rule_id: ruleId, reason: "OR_OF_AND_NOT_REPRESENTABLE" });
      continue;
    }
    if (flat.rows.length === 1 && flat.rows[0].operator === "contains_any") {
      const expanded = expandContainsAny(flat.rows[0]);
      flat = { op: "or", rows: expanded };
    } else if (flat.rows.some(condition => condition.operator === "contains_any")) {
      skipped.push({ rule_id: ruleId, reason: "CONTAINS_ANY_WITH_AND_NOT_REPRESENTABLE" });
      continue;
    }

    const conditions = [];
    let unsupported = null;
    for (const source of flat.rows) {
      const field = fieldMap[source.field];
      const op = operatorMap[source.operator];
      if (!field || !op || source.negate) {
        unsupported = "UNSUPPORTED_CONDITION";
        break;
      }
      const value = source.operator === "regex" && source.case_sensitive !== true
        ? caseInsensitiveRegex(source.value ?? "")
        : source.value ?? null;
      conditions.push({
        field,
        op,
        value: referencedValue(field, op, value),
      });
    }
    if (unsupported) {
      skipped.push({ rule_id: ruleId, reason: unsupported });
      continue;
    }

    const actions = [];
    const emptyGuards = [];
    for (const source of [...(row.actions ?? [])].sort((a, b) => (a.sequence ?? 10) - (b.sequence ?? 10))) {
      if (["set", "set_if_empty"].includes(source.action) && ["vendor", "category"].includes(source.field)) {
        const field = source.field === "vendor" ? "payee" : "category";
        actions.push({ field, op: "set", value: { ref: field, name: source.value } });
        if (source.action === "set_if_empty") {
          emptyGuards.push({ field, op: "is", value: null });
        }
      } else if (source.action === "add_tag" ||
                 (source.action === "add_tags" && Array.isArray(source.value))) {
        // Actual can prepend arbitrary note text but cannot guarantee tag
        // de-duplication or canonical ordering after several matching rules.
        // The deterministic worker applies these actions before import.
        deferred.push({
          rule_id: ruleId,
          action: source.action,
          reason: "NOTE_CONTRACT_REQUIRES_DETERMINISTIC_WORKER",
        });
      } else {
        unsupported = "UNSUPPORTED_ACTION";
        break;
      }
    }
    if (unsupported || !actions.length) {
      skipped.push({
        rule_id: ruleId,
        reason: unsupported ?? "WORKER_ONLY_NOTE_ACTIONS",
      });
      continue;
    }
    if (emptyGuards.length && flat.op === "or") {
      skipped.push({ rule_id: ruleId, reason: "EMPTY_GUARD_WITH_OR_NOT_REPRESENTABLE" });
      continue;
    }
    conditions.push(...emptyGuards);
    rules.push({
      name: `[canonical:${ruleId}] ${row.name}`,
      stage: stageMap[row.stage],
      conditionsOp: flat.op,
      conditions,
      actions,
      source_rule_id: ruleId,
    });
  }
  return { rules, skipped, deferred };
}

export function validateBootstrapConfig(config) {
  if (config.schema_version !== 1) throw new Error("Bootstrap schema_version must be 1");
  if (config.actual_settings !== undefined) {
    if (!config.actual_settings || typeof config.actual_settings !== "object" ||
        Array.isArray(config.actual_settings)) {
      throw new Error("actual_settings must be an object");
    }
    if (config.actual_settings.category_learning !== undefined &&
        typeof config.actual_settings.category_learning !== "boolean") {
      throw new Error("actual_settings.category_learning must be boolean");
    }
  }
  for (const property of [
    "accounts",
    "retired_accounts",
    "category_groups",
    "tags",
    "payees",
    "rules",
    "retired_rules",
    "rule_stage_migrations",
    "canonical_rule_sources",
    "schedules",
    "budget_months",
  ]) {
    if (config[property] !== undefined && !Array.isArray(config[property])) {
      throw new Error(`${property} must be an array`);
    }
  }
  for (const name of config.retired_accounts ?? []) {
    if (!String(name ?? "").trim()) throw new Error("retired_accounts entries must be names")
  }
  for (const account of config.accounts ?? []) {
    if (!String(account.name ?? "").trim()) throw new Error("accounts require a name");
    if (account.initial_balance !== undefined && !Number.isInteger(account.initial_balance)) {
      throw new Error(`account ${account.name} initial_balance must be integer minor units`);
    }
  }
  for (const rule of config.rules ?? []) {
    if (!String(rule.name ?? "").trim()) throw new Error("rules require a name");
    if (rule.enabled === false) continue;
    for (const action of rule.actions ?? []) {
      if (action.field === "amount" || action.op === "set-split-amount") {
        throw new Error(
          `active rule ${rule.name} must not mutate source amounts; normalize direction in the ingestion adapter`,
        );
      }
      if (action.options?.formula && !String(action.options.formula).startsWith("=")) {
        throw new Error(`rule ${rule.name} formulas must start with =`);
      }
    }
  }
  for (const migration of config.rule_stage_migrations ?? []) {
    if (!["pre", "default", "post"].includes(migration.from) ||
        !["pre", "default", "post"].includes(migration.to) ||
        migration.from === migration.to) {
      throw new Error("rule_stage_migrations require distinct pre, default, or post stages");
    }
  }
  for (const month of config.budget_months ?? []) {
    if (!/^\d{4}-\d{2}$/.test(String(month.month ?? ""))) {
      throw new Error(`Invalid budget month: ${month.month}`);
    }
    if (!Array.isArray(month.categories)) throw new Error("budget month categories must be an array");
    for (const category of month.categories) {
      if (!category.name || !Number.isInteger(category.amount_minor)) {
        throw new Error("budget categories require name and integer amount_minor");
      }
    }
  }
  for (const schedule of config.schedules ?? []) {
    if (schedule.enabled === false) continue;
    if (!schedule.name || !schedule.account || !schedule.payee || !schedule.date) {
      throw new Error("enabled schedules require name, account, payee, and date");
    }
    const amountOp = schedule.amount_op ?? "is";
    if (!["is", "isapprox", "isbetween"].includes(amountOp)) {
      throw new Error(`schedule ${schedule.name} uses unsupported amount_op ${amountOp}`);
    }
    if (amountOp === "isbetween") {
      if (!Number.isInteger(schedule.amount_min_minor) ||
          !Number.isInteger(schedule.amount_max_minor) ||
          schedule.amount_min_minor > schedule.amount_max_minor) {
        throw new Error(`schedule ${schedule.name} requires an ordered integer amount range`);
      }
    } else if (!Number.isInteger(schedule.amount_minor)) {
      throw new Error(`schedule ${schedule.name} requires integer amount_minor`);
    }
  }
}

export function scheduleSignature(schedule) {
  return JSON.stringify({
    name: normalized(schedule.name),
    account: schedule.account,
    payee: schedule.payee,
    amount: schedule.amount,
    amountOp: schedule.amountOp ?? "is",
    date: schedule.date,
    posts_transaction: Boolean(schedule.posts_transaction),
  });
}
