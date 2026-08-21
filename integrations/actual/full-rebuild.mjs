import fs from "node:fs/promises";
import crypto from "node:crypto";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import * as actual from "@actual-app/api";

import { bootstrap, importEnvelopes, snapshot } from "./actualctl.mjs";

const normalized = value => String(value ?? "").trim().toLocaleLowerCase();

function parseArgs(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 1) {
    const token = values[index];
    if (!token.startsWith("--")) continue;
    const next = values[index + 1];
    if (!next || next.startsWith("--")) throw new Error(`${token} requires a value`);
    result[token.slice(2)] = next;
    index += 1;
  }
  return result;
}

const escapeRegex = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function stable(value) {
  if (value === undefined) return null;
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map(key => [key, stable(value[key])]),
    );
  }
  return value;
}

function sha256(value) {
  return crypto.createHash("sha256").update(JSON.stringify(stable(value))).digest("hex");
}

function transactionKey(row, index = 0) {
  const importedId = String(row.imported_id ?? "").trim();
  return importedId || `unimported:${index}`;
}

function canonicalEconomicProjection(row, index = 0) {
  if (!row) return null;
  return {
    key: transactionKey(row, index),
    account: String(row.account_name ?? ""),
    date: String(row.date ?? ""),
    amount: Number(row.amount ?? 0),
    imported_payee: String(row.imported_payee ?? ""),
    payee: String(row.payee_name ?? ""),
    category: String(row.category_name ?? ""),
    cleared: Boolean(row.cleared),
  };
}

function canonicalSubtransaction(child) {
  return {
    amount: Number(child.amount ?? 0),
    date: String(child.date ?? ""),
    notes: String(child.notes ?? ""),
    imported_payee: String(child.imported_payee ?? ""),
    payee: String(child.payee_name ?? child.payee_name_display ?? ""),
    category: String(child.category_name ?? child.category_name_display ?? ""),
    cleared: Boolean(child.cleared),
    reconciled: Boolean(child.reconciled),
    is_parent: Boolean(child.is_parent),
    is_child: Boolean(child.is_child),
  };
}

function semanticSubtransactions(children) {
  return (children ?? [])
    .map(canonicalSubtransaction)
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

function canonicalManualRecord(row, index, {
  rowsById = new Map(),
  rowIndexesById = new Map(),
  schedulesById = new Map(),
} = {}) {
  const transferPeer = row.transfer_id ? rowsById.get(row.transfer_id) : undefined;
  const parent = row.parent_id ? rowsById.get(row.parent_id) : undefined;
  const schedule = row.schedule ? schedulesById.get(row.schedule) : undefined;
  const transferPeerIndex = transferPeer ? rowIndexesById.get(transferPeer.id) ?? 0 : 0;
  const parentIndex = parent ? rowIndexesById.get(parent.id) ?? 0 : 0;
  return {
    key: transactionKey(row, index),
    notes: String(row.notes ?? ""),
    cleared: Boolean(row.cleared),
    reconciled: Boolean(row.reconciled),
    transfer: row.transfer_id
      ? { present: true, peer: canonicalEconomicProjection(transferPeer, transferPeerIndex) }
      : null,
    schedule: row.schedule
      ? { present: true, semantic: canonicalSchedule(schedule), missing: !schedule }
      : null,
    is_parent: Boolean(row.is_parent),
    is_child: Boolean(row.is_child),
    parent: row.parent_id
      ? {
          present: true,
          semantic: canonicalEconomicProjection(parent, parentIndex),
          missing: !parent,
        }
      : null,
    subtransactions: semanticSubtransactions(row.subtransactions),
  };
}

function canonicalSchedule(schedule) {
  const semantic = Object.fromEntries(
    Object.entries(schedule ?? {})
      .filter(([key]) => !["id", "account", "payee", "category"].includes(key)),
  );
  semantic.account = String(schedule?.account_name ?? schedule?.account_name_display ?? "");
  semantic.payee = String(schedule?.payee_name ?? schedule?.payee_name_display ?? "");
  semantic.category = String(schedule?.category_name ?? schedule?.category_name_display ?? "");
  return stable(semantic);
}

function sortByKey(rows) {
  return [...rows].sort((left, right) =>
    String(left.key ?? "").localeCompare(String(right.key ?? "")));
}

/**
 * Build a redacted, deterministic state summary for first-pass/replay proof.
 * Provider IDs stay inside hashes and are never emitted as receipt fields.
 */
export function summarizeRebuildState(snapshotDocument, {
  accounts = [],
  balances = [],
  schedules = [],
  manualCorrectionStatus = "unverified",
} = {}) {
  const transactions = Array.isArray(snapshotDocument?.transactions)
    ? snapshotDocument.transactions
    : [];
  const active = transactions.filter(row => !row.tombstone);
  const rowsById = new Map(active.filter(row => row.id).map(row => [row.id, row]));
  const rowIndexesById = new Map(active.filter(row => row.id).map((row, index) => [row.id, index]));
  const schedulesById = new Map(schedules.filter(row => row.id).map(row => [row.id, row]));
  const economic = sortByKey(active.map(canonicalEconomicProjection));
  const manual = sortByKey(active.map((row, index) =>
    canonicalManualRecord(row, index, { rowsById, rowIndexesById, schedulesById })));
  const notes = manual.filter(row => row.notes.length > 0);
  const splits = manual.filter(row =>
    row.is_parent || row.is_child || row.parent || row.subtransactions.length > 0);
  const splitSemantics = splits.map(row => ({
    key: row.key,
    is_parent: row.is_parent,
    is_child: row.is_child,
    parent: row.parent,
    subtransactions: row.subtransactions,
  }));
  const scheduleRows = schedules
    .filter(row => !row.tombstone)
    .map(canonicalSchedule)
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  const accountRows = accounts.map((account, index) => ({
    name: String(account.name ?? ""),
    offbudget: Boolean(account.offbudget),
    closed: Boolean(account.closed),
    balance: Number(balances[index] ?? 0),
  })).sort((left, right) => JSON.stringify(stable(left)).localeCompare(JSON.stringify(stable(right))));
  const transactionRows = sortByKey(active.map((row, index) => ({
    ...canonicalEconomicProjection(row, index),
    notes: String(row.notes ?? ""),
    reconciled: Boolean(row.reconciled),
    ...canonicalManualRecord(row, index, { rowsById, rowIndexesById, schedulesById }),
    is_parent: Boolean(row.is_parent),
    is_child: Boolean(row.is_child),
  })));
  const nestedSplitChildren = splits.reduce(
    (total, row) => total + row.subtransactions.length,
    0,
  );
  const amountSum = economic.reduce((total, row) => total + row.amount, 0);
  const positiveAmountSum = economic
    .filter(row => row.amount > 0)
    .reduce((total, row) => total + row.amount, 0);
  const negativeAmountSum = economic
    .filter(row => row.amount < 0)
    .reduce((total, row) => total + row.amount, 0);
  const transferLinks = active.filter(row => row.transfer_id).length;
  const scheduleLinks = active.filter(row => row.schedule).length;
  const splitParents = active.filter(row => row.is_parent || row.subtransactions?.length).length;
  const splitChildRows = active.filter(row => row.is_child || row.parent_id).length;
  const unresolvedTransferLinks = active.filter(row => row.transfer_id && !rowsById.has(row.transfer_id)).length;
  const unresolvedParentLinks = active.filter(row => row.parent_id && !rowsById.has(row.parent_id)).length;
  const unresolvedScheduleLinks = active.filter(row => row.schedule && !schedulesById.has(row.schedule)).length;
  const transferCoverage = transferLinks
    ? unresolvedTransferLinks ? "unresolved" : "verified"
    : "not-applicable";
  const splitCoverage = splitParents || splitChildRows
    ? unresolvedParentLinks ? "unresolved" : "verified"
    : "not-applicable";
  const scheduleCoverage = scheduleRows.length ? "verified" : "not-applicable";
  const scheduleLinkCoverage = scheduleLinks
    ? unresolvedScheduleLinks ? "unresolved" : "verified"
    : "not-applicable";

  return {
    schema_version: "actual-disposable-replay-state-v1",
    counts: {
      transactions: active.length,
      tombstones: transactions.length - active.length,
      accounts: accountRows.length,
      schedules: scheduleRows.length,
      transfer_links: transferLinks,
      schedule_links: scheduleLinks,
      unresolved_transfer_links: unresolvedTransferLinks,
      unresolved_parent_links: unresolvedParentLinks,
      unresolved_schedule_links: unresolvedScheduleLinks,
      notes: notes.length,
      reconciled: active.filter(row => row.reconciled).length,
      split_parents: splitParents,
      split_children: nestedSplitChildren + splitChildRows,
    },
    economics: {
      amount_sum_minor: amountSum,
      positive_amount_sum_minor: positiveAmountSum,
      negative_amount_sum_minor: negativeAmountSum,
    },
    balances: {
      account_count: accountRows.length,
      balance_sum_minor: accountRows.reduce((total, row) => total + row.balance, 0),
      balances_sha256: sha256(accountRows),
    },
    hashes: {
      transaction_sha256: sha256(transactionRows),
      economic_fields_sha256: sha256(economic),
      manual_state_sha256: sha256(manual),
      notes_sha256: sha256(notes),
      splits_sha256: sha256(splitSemantics),
      schedules_sha256: sha256(scheduleRows),
    },
    coverage: {
      manual_correction: manualCorrectionStatus,
      transfers: transferCoverage,
      splits: splitCoverage,
      schedules: scheduleCoverage,
      schedule_links: scheduleLinkCoverage,
      manual_state: active.length ? "verified" : "not-applicable",
    },
  };
}

const replayChecks = [
  ["counts.transactions", state => state.counts.transactions],
  ["counts.tombstones", state => state.counts.tombstones],
  ["counts.accounts", state => state.counts.accounts],
  ["counts.schedules", state => state.counts.schedules],
  ["counts.transfer_links", state => state.counts.transfer_links],
  ["counts.schedule_links", state => state.counts.schedule_links],
  ["counts.unresolved_transfer_links", state => state.counts.unresolved_transfer_links],
  ["counts.unresolved_parent_links", state => state.counts.unresolved_parent_links],
  ["counts.unresolved_schedule_links", state => state.counts.unresolved_schedule_links],
  ["counts.notes", state => state.counts.notes],
  ["counts.reconciled", state => state.counts.reconciled],
  ["counts.split_parents", state => state.counts.split_parents],
  ["counts.split_children", state => state.counts.split_children],
  ["economics.amount_sum_minor", state => state.economics.amount_sum_minor],
  ["economics.positive_amount_sum_minor", state => state.economics.positive_amount_sum_minor],
  ["economics.negative_amount_sum_minor", state => state.economics.negative_amount_sum_minor],
  ["balances.account_count", state => state.balances.account_count],
  ["balances.balance_sum_minor", state => state.balances.balance_sum_minor],
  ["balances.balances_sha256", state => state.balances.balances_sha256],
  ["hashes.transaction_sha256", state => state.hashes.transaction_sha256],
  ["hashes.economic_fields_sha256", state => state.hashes.economic_fields_sha256],
  ["hashes.manual_state_sha256", state => state.hashes.manual_state_sha256],
  ["hashes.notes_sha256", state => state.hashes.notes_sha256],
  ["hashes.splits_sha256", state => state.hashes.splits_sha256],
  ["hashes.schedules_sha256", state => state.hashes.schedules_sha256],
];

export function compareRebuildStates(first, replay, { enforceCoverage = false } = {}) {
  if (!first || !replay) throw new Error("Both first and replay rebuild states are required");
  const differences = [];
  for (const [field, read] of replayChecks) {
    const expected = read(first);
    const observed = read(replay);
    if (expected !== observed) differences.push({ field, expected, observed });
  }
  const blockers = [];
  if (enforceCoverage) {
    for (const dimension of [
      "manual_correction",
      "transfers",
      "splits",
      "schedules",
      "schedule_links",
      "manual_state",
    ]) {
      const firstCoverage = first.coverage?.[dimension] ?? "missing";
      const replayCoverage = replay.coverage?.[dimension] ?? "missing";
      if (firstCoverage !== "verified") blockers.push(`${dimension}:${firstCoverage}`);
      if (replayCoverage !== "verified") blockers.push(`${dimension}:${replayCoverage}`);
    }
  }
  return {
    status: differences.length ? "FAIL" : blockers.length ? "BLOCKED" : "PASS",
    checked: replayChecks.map(([field]) => field),
    differences,
    blockers,
  };
}

async function enrichSnapshotChildSemantics(snapshotDocument, api, references = {}) {
  const [categories, payees] = await Promise.all([
    references.categories ?? (typeof api.getCategories === "function" ? api.getCategories() : []),
    references.payees ?? (typeof api.getPayees === "function" ? api.getPayees() : []),
  ]);
  const categoryNames = new Map((categories ?? []).map(row => [row.id, row.name]));
  const payeeNames = new Map((payees ?? []).map(row => [row.id, row.name]));
  return {
    ...snapshotDocument,
    transactions: (snapshotDocument?.transactions ?? []).map(row => ({
      ...row,
      subtransactions: (row.subtransactions ?? []).map(child => ({
        ...child,
        payee_name: child.payee_name ?? payeeNames.get(child.payee) ?? "",
        category_name: child.category_name ?? categoryNames.get(child.category) ?? "",
      })),
    })),
  };
}

function enrichScheduleSemantics(schedules, accounts, categories, payees) {
  const accountNames = new Map((accounts ?? []).map(row => [row.id, row.name]));
  const categoryNames = new Map((categories ?? []).map(row => [row.id, row.name]));
  const payeeNames = new Map((payees ?? []).map(row => [row.id, row.name]));
  return (schedules ?? []).map(schedule => ({
    ...schedule,
    account_name: schedule.account_name ?? accountNames.get(schedule.account) ?? "",
    category_name: schedule.category_name ?? categoryNames.get(schedule.category) ?? "",
    payee_name: schedule.payee_name ?? payeeNames.get(schedule.payee) ?? "",
  }));
}

export async function captureRebuildState(snapshotDocument, api = actual, options = {}) {
  const [accounts, categories, payees, schedules] = await Promise.all([
    api.getAccounts(),
    typeof api.getCategories === "function" ? api.getCategories() : [],
    typeof api.getPayees === "function" ? api.getPayees() : [],
    api.getSchedules(),
  ]);
  const enrichedSnapshot = await enrichSnapshotChildSemantics(snapshotDocument, api, { categories, payees });
  const balances = await Promise.all(accounts.map(account => api.getAccountBalance(account.id)));
  return summarizeRebuildState(enrichedSnapshot, {
    accounts,
    balances,
    schedules: enrichScheduleSemantics(schedules, accounts, categories, payees),
    ...options,
  });
}

function assertWithinRoot(root, candidate) {
  const relative = path.relative(root, candidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Manifest path escapes repository root: ${candidate}`);
  }
}

async function expandPattern(root, pattern) {
  const normalizedPattern = String(pattern).replaceAll("\\", "/");
  const directory = path.resolve(root, path.dirname(normalizedPattern));
  assertWithinRoot(root, directory);
  const filenamePattern = path.basename(normalizedPattern);
  if (!filenamePattern.includes("*")) return [path.join(directory, filenamePattern)];
  if ((filenamePattern.match(/\*/g) ?? []).length !== 1) {
    throw new Error(`Only one filename wildcard is supported: ${pattern}`);
  }
  const [prefix, suffix] = filenamePattern.split("*");
  const matcher = new RegExp(`^${escapeRegex(prefix)}.*${escapeRegex(suffix)}$`, "i");
  const entries = await fs.readdir(directory, { withFileTypes: true });
  return entries
    .filter(entry => entry.isFile() && matcher.test(entry.name))
    .map(entry => path.join(directory, entry.name));
}

export async function loadFullRebuildManifests(rootPath, validationConfig) {
  const root = path.resolve(rootPath);
  const rows = [];
  for (const [sourceIndex, source] of (validationConfig.manifest_sources ?? []).entries()) {
    const paths = [];
    for (const filename of source.files ?? []) paths.push(path.resolve(root, filename));
    for (const pattern of source.globs ?? []) paths.push(...await expandPattern(root, pattern));
    if (!paths.length) throw new Error(`Manifest source ${source.id} resolved no files`);
    for (const filename of paths) {
      assertWithinRoot(root, filename);
      const stat = await fs.stat(filename);
      if (!stat.isFile()) throw new Error(`Manifest is not a file: ${filename}`);
      rows.push({
        source_id: source.id,
        import_order: Number(source.import_order ?? 100),
        source_index: sourceIndex,
        filename,
      });
    }
  }
  return rows.sort((left, right) =>
    left.import_order - right.import_order ||
    left.source_index - right.source_index ||
    normalized(left.filename).localeCompare(normalized(right.filename)));
}

export function isVerifiedEmptyManifest(payload) {
  return Array.isArray(payload?.envelopes) &&
    payload.envelopes.length === 0 &&
    Number(payload?.statement?.transaction_count) === 0 &&
    payload?.statement?.balance_tied === true &&
    Number(payload?.review_count ?? 0) === 0;
}

function manifestEnvelopes(payload) {
  return Array.isArray(payload) ? payload : payload?.envelopes;
}

export function validateRebuildManifestPayload(payload, filename = "manifest") {
  const envelopes = manifestEnvelopes(payload);
  if (!Array.isArray(envelopes)) {
    throw new Error(`Manifest ${filename} lacks an envelopes array`);
  }
  const importedIds = new Set();
  for (const [envelopeIndex, envelope] of envelopes.entries()) {
    if (!Array.isArray(envelope?.records)) {
      throw new Error(`Manifest ${filename} envelope ${envelopeIndex} lacks a records array`);
    }
    for (const [recordIndex, record] of envelope.records.entries()) {
      const importedId = typeof record?.imported_id === "string"
        ? record.imported_id.trim()
        : "";
      if (!importedId) {
        throw new Error(
          `Manifest ${filename} record ${envelopeIndex}/${recordIndex} lacks imported_id`,
        );
      }
      if (importedIds.has(importedId)) {
        throw new Error(`Manifest ${filename} contains duplicate imported_id: ${importedId}`);
      }
      importedIds.add(importedId);
    }
  }
  return [...importedIds];
}

export function validateRebuildManifestCorpus(manifestPayloads) {
  const importedIds = new Map();
  for (const manifest of manifestPayloads) {
    for (const importedId of validateRebuildManifestPayload(manifest.payload, manifest.filename)) {
      const previous = importedIds.get(importedId);
      if (previous) {
        throw new Error(
          `Duplicate imported_id across manifests: ${importedId} (${previous}, ${manifest.filename})`,
        );
      }
      importedIds.set(importedId, manifest.filename);
    }
  }
  return [...importedIds.keys()];
}

const disposableManualCorrectionNote = "#manual | Memo: disposable manual correction";

export async function applyDisposableManualCorrections(snapshotDocument, api = actual) {
  const target = (snapshotDocument?.transactions ?? [])
    .find(row => !row.tombstone && row.id && row.imported_id &&
      (String(row.notes ?? "") !== disposableManualCorrectionNote || !row.reconciled));
  if (!target) {
    return {
      status: "not-applicable",
      changed: 0,
      notes: 0,
      reconciled: 0,
      transfer_links: 0,
      split_states: 0,
      schedule_links: 0,
    };
  }
  const fields = {
    notes: disposableManualCorrectionNote,
    reconciled: true,
  };
  if (target.transfer_id) fields.transfer_id = target.transfer_id;
  if (target.schedule) fields.schedule = target.schedule;
  if (target.subtransactions?.length) {
    fields.subtransactions = target.subtransactions;
  }
  await api.updateTransaction(target.id, fields);
  if (typeof api.getTransactions !== "function") {
    throw new Error("Disposable manual correction lacks authoritative readback support");
  }
  const rows = await api.getTransactions(target.account, target.date, target.date);
  const observed = (rows ?? []).find(row =>
    (target.imported_id && row.imported_id === target.imported_id) || row.id === target.id);
  if (!observed) throw new Error("Disposable manual correction authoritative readback was empty");
  const readbackDifferences = [];
  if (String(observed.notes ?? "") !== fields.notes) readbackDifferences.push("notes");
  if (Boolean(observed.reconciled) !== true) readbackDifferences.push("reconciled");
  for (const field of ["transfer_id", "schedule"]) {
    if (Boolean(observed[field]) !== Boolean(target[field])) readbackDifferences.push(field);
  }
  if (JSON.stringify(semanticSubtransactions(observed.subtransactions)) !==
      JSON.stringify(semanticSubtransactions(target.subtransactions))) {
    readbackDifferences.push("subtransactions");
  }
  if (readbackDifferences.length) {
    throw new Error(`Disposable manual correction readback drift: ${readbackDifferences.join(",")}`);
  }
  return {
    status: "applied",
    changed: 1,
    notes: 1,
    reconciled: 1,
    transfer_links: target.transfer_id ? 1 : 0,
    split_states: target.subtransactions?.length ? 1 : 0,
    schedule_links: target.schedule ? 1 : 0,
    readback: { status: "PASS", checked: ["notes", "reconciled", "transfer", "schedule", "subtransactions"] },
  };
}

export function verifyManualCorrectionDelta(baseline, corrected, manualCorrections) {
  const differences = [];
  const blockers = [];
  if (manualCorrections?.status !== "applied") {
    blockers.push("manual_correction:not-applied");
  }
  for (const field of [
    "counts.transactions",
    "counts.tombstones",
    "counts.accounts",
    "counts.schedules",
    "counts.transfer_links",
    "counts.schedule_links",
    "counts.unresolved_transfer_links",
    "counts.unresolved_parent_links",
    "counts.unresolved_schedule_links",
    "counts.split_parents",
    "counts.split_children",
    "economics.amount_sum_minor",
    "economics.positive_amount_sum_minor",
    "economics.negative_amount_sum_minor",
    "balances.account_count",
    "balances.balance_sum_minor",
    "balances.balances_sha256",
    "hashes.economic_fields_sha256",
    "hashes.splits_sha256",
    "hashes.schedules_sha256",
  ]) {
    const read = field.split(".").reduce((value, key) => value?.[key], baseline);
    const observed = field.split(".").reduce((value, key) => value?.[key], corrected);
    if (read !== observed) differences.push({ field, expected: read, observed });
  }
  if (baseline.hashes.manual_state_sha256 === corrected.hashes.manual_state_sha256) {
    differences.push({ field: "hashes.manual_state_sha256", expected: "changed", observed: "unchanged" });
  }
  if (baseline.hashes.notes_sha256 === corrected.hashes.notes_sha256) {
    differences.push({ field: "hashes.notes_sha256", expected: "changed", observed: "unchanged" });
  }
  return {
    status: differences.length ? "FAIL" : blockers.length ? "BLOCKED" : "PASS",
    checked: ["authoritative_readback", "manual_delta", "economic_invariants", "link_invariants"],
    differences,
    blockers,
  };
}

export async function runDisposableFullRebuild({
  root,
  validationConfigPath,
  bootstrapConfigPath,
  start,
  end,
  snapshotPath,
  resultPath,
}) {
  const resolvedRoot = path.resolve(root);
  const validation = JSON.parse(await fs.readFile(validationConfigPath, "utf8"));
  const bootstrapConfig = JSON.parse(await fs.readFile(bootstrapConfigPath, "utf8"));
  const manifests = await loadFullRebuildManifests(resolvedRoot, validation);
  const manifestPayloads = [];
  for (const manifest of manifests) {
    manifestPayloads.push({
      ...manifest,
      payload: JSON.parse(await fs.readFile(manifest.filename, "utf8")),
    });
  }
  validateRebuildManifestCorpus(manifestPayloads);
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "finance-full-rebuild-"));
  const dataDir = path.join(tempRoot, "data");
  await fs.mkdir(dataDir);
  let result;
  try {
    await actual.init({ dataDir });
    await actual.runImport("Finance full ingestion disposable rebuild", async () => {});
    const bootstrapResult = await bootstrap(
      bootstrapConfig,
      true,
      bootstrapConfigPath,
      { syncRemote: false },
    );
    const imports = [];
    for (const manifest of manifestPayloads) {
      const payload = manifest.payload;
      if (isVerifiedEmptyManifest(payload)) {
        imports.push({
          source_id: manifest.source_id,
          manifest: path.relative(resolvedRoot, manifest.filename).replaceAll("\\", "/"),
          verification: { status: "verified-empty", records: 0 },
          replay_verification: { status: "verified-empty", records: 0 },
        });
        continue;
      }
      const first = await importEnvelopes(payload, true, { syncRemote: false });
      if (first.status !== "committed") {
        throw new Error(`Disposable import did not commit: ${manifest.filename}`);
      }
      imports.push({
        source_id: manifest.source_id,
        manifest: path.relative(resolvedRoot, manifest.filename).replaceAll("\\", "/"),
        verification: first.verification,
      });
    }
    const firstSnapshot = await snapshot(start, end);
    const baselineState = await captureRebuildState(firstSnapshot);
    const manualCorrections = await applyDisposableManualCorrections(firstSnapshot);
    const correctedSnapshot = await snapshot(start, end);
    const correctedState = await captureRebuildState(correctedSnapshot, actual, {
      manualCorrectionStatus: manualCorrections.status === "applied" ? "verified" : "not-applicable",
    });
    const manualDelta = verifyManualCorrectionDelta(
      baselineState,
      correctedState,
      manualCorrections,
    );
    for (const [index, manifest] of manifestPayloads.entries()) {
      const payload = manifest.payload;
      if (isVerifiedEmptyManifest(payload)) continue;
      const replay = await importEnvelopes(payload, true, { syncRemote: false });
      if (replay.status !== "committed") {
        throw new Error(`Disposable replay did not commit: ${manifest.filename}`);
      }
      imports[index].replay_verification = replay.verification;
    }
    const rebuiltSnapshot = await snapshot(start, end);
    const replayState = await captureRebuildState(rebuiltSnapshot, actual, {
      manualCorrectionStatus: manualCorrections.status === "applied" ? "verified" : "not-applicable",
    });
    const replayVerification = compareRebuildStates(correctedState, replayState, {
      enforceCoverage: true,
    });
    if (manualDelta.status !== "PASS") {
      replayVerification.status = manualDelta.status === "FAIL" ? "FAIL" : "BLOCKED";
      replayVerification.blockers.push(...manualDelta.blockers);
      replayVerification.differences.push(...manualDelta.differences);
    }
    await fs.mkdir(path.dirname(snapshotPath), { recursive: true });
    await fs.writeFile(snapshotPath, `${JSON.stringify(rebuiltSnapshot, null, 2)}\n`, "utf8");
    result = {
      schema_version: "actual-disposable-full-rebuild-v1",
      status: replayVerification.status,
      manifests: manifests.length,
      transactions: rebuiltSnapshot.transactions.filter(row => !row.tombstone).length,
      bootstrap_changes: bootstrapResult.changes.length,
      imports,
      replay: {
        first: correctedState,
        baseline: baselineState,
        manual_corrections: manualCorrections,
        manual_delta: manualDelta,
        replay: replayState,
        verification: replayVerification,
      },
      snapshot: snapshotPath,
    };
    await fs.mkdir(path.dirname(resultPath), { recursive: true });
    await fs.writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    if (replayVerification.status !== "PASS") {
      throw new Error(`Disposable replay drift: ${JSON.stringify({
        differences: replayVerification.differences,
        blockers: replayVerification.blockers,
      })}`);
    }
    return result;
  } finally {
    await actual.shutdown();
    const resolvedTemp = path.resolve(tempRoot);
    const safePrefix = path.resolve(os.tmpdir()) + path.sep;
    if (!resolvedTemp.startsWith(safePrefix) || !path.basename(resolvedTemp).startsWith("finance-full-rebuild-")) {
      throw new Error(`Refusing to remove unexpected rebuild directory: ${resolvedTemp}`);
    }
    await fs.rm(resolvedTemp, { recursive: true, force: true });
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  for (const required of ["root", "validation", "bootstrap", "start", "end", "snapshot", "result"]) {
    if (!args[required]) throw new Error(`Missing --${required}`);
  }
  const result = await runDisposableFullRebuild({
    root: path.resolve(args.root),
    validationConfigPath: path.resolve(args.validation),
    bootstrapConfigPath: path.resolve(args.bootstrap),
    start: args.start,
    end: args.end,
    snapshotPath: path.resolve(args.snapshot),
    resultPath: path.resolve(args.result),
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
}
