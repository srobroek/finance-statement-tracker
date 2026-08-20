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
  return String(row.imported_id ?? row.id ?? `${row.date ?? ""}:${row.amount ?? 0}:${index}`);
}

function canonicalEconomicRecord(row, index) {
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

function canonicalManualRecord(row, index) {
  return {
    key: transactionKey(row, index),
    notes: String(row.notes ?? ""),
    cleared: Boolean(row.cleared),
    reconciled: Boolean(row.reconciled),
    transfer_id: row.transfer_id ?? null,
    schedule: row.schedule ?? null,
    is_parent: Boolean(row.is_parent),
    is_child: Boolean(row.is_child),
    parent_id: row.parent_id ?? null,
    subtransactions: (row.subtransactions ?? []).map(child => stable(child)),
  };
}

function canonicalSchedule(schedule) {
  return stable(schedule ?? {});
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
} = {}) {
  const transactions = Array.isArray(snapshotDocument?.transactions)
    ? snapshotDocument.transactions
    : [];
  const active = transactions.filter(row => !row.tombstone);
  const economic = sortByKey(active.map(canonicalEconomicRecord));
  const manual = sortByKey(active.map(canonicalManualRecord));
  const notes = manual.filter(row => row.notes.length > 0);
  const splits = manual.filter(row =>
    row.is_parent || row.is_child || row.parent_id || row.subtransactions.length > 0);
  const scheduleRows = schedules.filter(row => !row.tombstone).map(canonicalSchedule);
  const accountRows = accounts.map((account, index) => ({
    name: String(account.name ?? ""),
    offbudget: Boolean(account.offbudget),
    closed: Boolean(account.closed),
    balance: Number(balances[index] ?? 0),
  }));
  const transactionRows = sortByKey(active.map((row, index) => ({
    ...canonicalEconomicRecord(row, index),
    notes: String(row.notes ?? ""),
    reconciled: Boolean(row.reconciled),
    transfer_id: row.transfer_id ?? null,
    schedule: row.schedule ?? null,
    is_parent: Boolean(row.is_parent),
    is_child: Boolean(row.is_child),
    parent_id: row.parent_id ?? null,
    subtransactions: (row.subtransactions ?? []).map(child => stable(child)),
  })));
  const splitChildren = splits.reduce(
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

  return {
    schema_version: "actual-disposable-replay-state-v1",
    counts: {
      transactions: active.length,
      tombstones: transactions.length - active.length,
      accounts: accountRows.length,
      schedules: scheduleRows.length,
      notes: notes.length,
      split_parents: splits.filter(row => row.is_parent).length,
      split_children: splitChildren,
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
      splits_sha256: sha256(splits),
      schedules_sha256: sha256(scheduleRows),
    },
  };
}

const replayChecks = [
  ["counts.transactions", state => state.counts.transactions],
  ["counts.tombstones", state => state.counts.tombstones],
  ["counts.accounts", state => state.counts.accounts],
  ["counts.schedules", state => state.counts.schedules],
  ["counts.notes", state => state.counts.notes],
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

export function compareRebuildStates(first, replay) {
  if (!first || !replay) throw new Error("Both first and replay rebuild states are required");
  const differences = [];
  for (const [field, read] of replayChecks) {
    const expected = read(first);
    const observed = read(replay);
    if (expected !== observed) differences.push({ field, expected, observed });
  }
  return {
    status: differences.length ? "FAIL" : "PASS",
    checked: replayChecks.map(([field]) => field),
    differences,
  };
}

export async function captureRebuildState(snapshotDocument, api = actual) {
  const accounts = await api.getAccounts();
  const balances = await Promise.all(accounts.map(account => api.getAccountBalance(account.id)));
  const schedules = await api.getSchedules();
  return summarizeRebuildState(snapshotDocument, { accounts, balances, schedules });
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
    for (const manifest of manifests) {
      const payload = JSON.parse(await fs.readFile(manifest.filename, "utf8"));
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
    const firstState = await captureRebuildState(firstSnapshot);
    for (const [index, manifest] of manifests.entries()) {
      const payload = JSON.parse(await fs.readFile(manifest.filename, "utf8"));
      if (isVerifiedEmptyManifest(payload)) continue;
      const replay = await importEnvelopes(payload, true, { syncRemote: false });
      if (replay.status !== "committed") {
        throw new Error(`Disposable replay did not commit: ${manifest.filename}`);
      }
      imports[index].replay_verification = replay.verification;
    }
    const rebuiltSnapshot = await snapshot(start, end);
    const replayState = await captureRebuildState(rebuiltSnapshot);
    const replayVerification = compareRebuildStates(firstState, replayState);
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
        first: firstState,
        replay: replayState,
        verification: replayVerification,
      },
      snapshot: snapshotPath,
    };
    await fs.mkdir(path.dirname(resultPath), { recursive: true });
    await fs.writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    if (replayVerification.status !== "PASS") {
      throw new Error(`Disposable replay drift: ${JSON.stringify(replayVerification.differences)}`);
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
