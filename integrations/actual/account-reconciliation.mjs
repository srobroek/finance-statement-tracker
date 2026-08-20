import crypto from "node:crypto";

const normalized = value => String(value ?? "").trim().toLocaleLowerCase();

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

export function reconciliationPlanSha256(plan) {
  return crypto.createHash("sha256")
    .update(JSON.stringify(stable(plan)))
    .digest("hex");
}

export function validateAccountReconciliationPlan(plan) {
  if (!plan || plan.schema_version !== 1) {
    throw new Error("Account reconciliation plan schema_version must be 1");
  }
  if (plan.mode !== "ACTUAL_NATIVE_RECONCILIATION") {
    throw new Error("Account reconciliation plan must use ACTUAL_NATIVE_RECONCILIATION mode");
  }
  if (!Array.isArray(plan.accounts) || !plan.accounts.length) {
    throw new Error("Account reconciliation plan requires accounts");
  }
  const names = new Set();
  const importedIds = new Set();
  for (const row of plan.accounts) {
    if (!String(row.name ?? "").trim()) throw new Error("Reconciliation accounts require names");
    const key = normalized(row.name);
    if (names.has(key)) throw new Error(`Duplicate reconciliation account: ${row.name}`);
    names.add(key);
    if (!Number.isInteger(row.target_balance_minor)) {
      throw new Error(`Reconciliation target must be integer minor units: ${row.name}`);
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(row.as_of ?? ""))) {
      throw new Error(`Reconciliation account requires an as_of date: ${row.name}`);
    }
    if (!row.expected_before || typeof row.expected_before.exists !== "boolean") {
      throw new Error(`Reconciliation account requires exact expected_before state: ${row.name}`);
    }
    if (row.expected_before.exists) {
      if (!Number.isInteger(row.expected_before.balance_minor) ||
          typeof row.expected_before.closed !== "boolean" ||
          typeof row.expected_before.offbudget !== "boolean" ||
          !String(row.expected_before.account_id ?? "").trim()) {
        throw new Error(`Existing reconciliation account requires id, balance, closed, and offbudget: ${row.name}`);
      }
    }
    if (typeof row.offbudget !== "boolean" || typeof row.close_after !== "boolean") {
      throw new Error(`Reconciliation account requires offbudget and close_after flags: ${row.name}`);
    }
    if (!String(row.source_evidence_id ?? "").startsWith("sha256:")) {
      throw new Error(`Reconciliation account requires SHA-256 source evidence: ${row.name}`);
    }
    const importedId = String(row.adjustment_imported_id ?? "");
    if (!/^reconcile:[a-z0-9:_-]+$/.test(importedId) || importedIds.has(importedId)) {
      throw new Error(`Invalid or duplicate reconciliation imported id: ${row.name}`);
    }
    importedIds.add(importedId);
    if (String(row.adjustment_notes ?? "").split(" | ")[0] !== "Reconciliation balance adjustment") {
      throw new Error(`Reconciliation note must be visibly named: ${row.name}`);
    }
  }
}

function findAccount(accounts, row) {
  const allowed = new Set([row.name, ...(row.aliases ?? [])].map(normalized));
  const matches = accounts.filter(account => allowed.has(normalized(account.name)));
  if (matches.length > 1) throw new Error(`Ambiguous Actual account identity: ${row.name}`);
  return matches[0] ?? null;
}

async function observedState(api, accounts, row) {
  const account = findAccount(accounts, row);
  if (!account) return { exists: false };
  return {
    exists: true,
    account_id: account.id,
    name: account.name,
    balance_minor: await api.getAccountBalance(account.id),
    closed: Boolean(account.closed),
    offbudget: Boolean(account.offbudget),
  };
}

function assertExpected(row, observed) {
  const expected = row.expected_before;
  if (observed.exists !== expected.exists) {
    throw new Error(`Actual account existence drifted: ${row.name}`);
  }
  if (!expected.exists) return;
  for (const field of ["account_id", "balance_minor", "closed", "offbudget"]) {
    if (observed[field] !== expected[field]) {
      throw new Error(`Actual account ${field} drifted: ${row.name}`);
    }
  }
}

export async function reconcileAccounts(api, plan, apply, { syncRemote = true } = {}) {
  validateAccountReconciliationPlan(plan);
  let accounts = await api.getAccounts();
  const before = [];
  const actions = [];
  for (const row of plan.accounts) {
    const observed = await observedState(api, accounts, row);
    assertExpected(row, observed);
    before.push({ provider_account_id: row.provider_account_id, ...observed });
    if (!observed.exists) actions.push({ action: "create", account: row.name, offbudget: row.offbudget });
    const difference = row.target_balance_minor - (observed.balance_minor ?? 0);
    if (difference !== 0) {
      actions.push({
        action: "create-reconciliation-adjustment",
        account: row.name,
        amount_minor: difference,
        imported_id: row.adjustment_imported_id,
        as_of: row.as_of,
      });
    }
    if (row.close_after && observed.closed !== true) actions.push({ action: "close", account: row.name });
  }
  const planSha256 = reconciliationPlanSha256(plan);
  if (!apply) return { status: "planned", plan_sha256: planSha256, before, actions };

  for (const row of plan.accounts) {
    let account = findAccount(accounts, row);
    if (!account) {
      await api.createAccount({
        name: row.name,
        type: row.type ?? "other",
        offbudget: row.offbudget,
        closed: false,
      }, 0);
      accounts = await api.getAccounts();
      account = findAccount(accounts, row);
      if (!account) throw new Error(`Actual account creation readback failed: ${row.name}`);
    }
    if (Boolean(account.offbudget) !== row.offbudget) {
      throw new Error(`Actual account offbudget state changed during apply: ${row.name}`);
    }
    const current = await api.getAccountBalance(account.id);
    const difference = row.target_balance_minor - current;
    if (difference !== 0) {
      const existing = await api.getTransactions(account.id, row.as_of, row.as_of);
      if (existing.some(transaction => transaction.imported_id === row.adjustment_imported_id)) {
        throw new Error(`Reconciliation imported id already exists before target balance: ${row.name}`);
      }
      await api.addTransactions(account.id, [{
        date: row.as_of,
        amount: difference,
        notes: row.adjustment_notes,
        imported_id: row.adjustment_imported_id,
        cleared: true,
      }], false, false);
    }
    const adjusted = await api.getAccountBalance(account.id);
    if (adjusted !== row.target_balance_minor) {
      throw new Error(`Reconciliation balance readback failed: ${row.name}`);
    }
    if (row.close_after && !account.closed) await api.closeAccount(account.id);
  }
  if (syncRemote) await api.sync();

  accounts = await api.getAccounts();
  const after = [];
  for (const row of plan.accounts) {
    const observed = await observedState(api, accounts, row);
    if (!observed.exists || observed.balance_minor !== row.target_balance_minor ||
        observed.closed !== row.close_after || observed.offbudget !== row.offbudget) {
      throw new Error(`Final Actual reconciliation state mismatch: ${row.name}`);
    }
    after.push({ provider_account_id: row.provider_account_id, ...observed });
  }
  return { status: "applied", plan_sha256: planSha256, before, actions, after };
}
