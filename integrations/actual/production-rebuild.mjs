import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import * as actual from "@actual-app/api";

import { bootstrap, importEnvelopes, openBudget, snapshot } from "./actualctl.mjs";
import {
  isVerifiedEmptyManifest,
  loadFullRebuildManifests,
} from "./full-rebuild.mjs";

const normalized = value => String(value ?? "").trim().toLocaleLowerCase();

function parseArgs(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 1) {
    const token = values[index];
    if (!token.startsWith("--")) continue;
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

export function assertReplacementGate(apply, environment = process.env) {
  if (!apply) return;
  if (String(environment.ALLOW_ACTUAL_LEDGER_REPLACEMENT ?? "").toLowerCase() !== "true") {
    throw new Error(
      "Production replacement is disabled; set ALLOW_ACTUAL_LEDGER_REPLACEMENT=true explicitly",
    );
  }
}

export function selectReplacementRows(snapshotDocument, scope) {
  const accounts = new Set((scope.accounts ?? []).map(normalized));
  const prefixes = (scope.imported_id_prefixes ?? []).map(String);
  if (!accounts.size || !prefixes.length) throw new Error("Replacement scope is empty");
  return (snapshotDocument.transactions ?? []).filter(row =>
    !row.tombstone &&
    accounts.has(normalized(row.account_name)) &&
    prefixes.some(prefix => String(row.imported_id ?? "").startsWith(prefix))
  );
}

const preservationFields = [
  "id",
  "account",
  "category",
  "payee",
  "amount",
  "date",
  "notes",
  "imported_id",
  "imported_payee",
  "cleared",
  "reconciled",
  "transfer_id",
  "schedule",
  "is_parent",
  "is_child",
  "parent_id",
  "subtransactions",
];

const managedComparisonFields = [
  ["account_name", "account"],
  ["date", "date"],
  ["amount", "amount"],
  ["imported_payee", "imported_payee"],
  ["payee_name", "payee_name"],
  ["category_name", "category_name"],
  ["notes", "notes"],
  ["cleared", "cleared"],
];

const stableValue = value => value === undefined ? null : value;

function equivalentField(field, observed, desired) {
  if (["account_name", "payee_name", "category_name"].includes(field)) {
    return normalized(observed) === normalized(desired);
  }
  if (field === "notes") {
    return String(observed ?? "").trim() === String(desired ?? "").trim();
  }
  return stableValue(observed) === stableValue(desired);
}

function stableObject(value) {
  if (Array.isArray(value)) return value.map(stableObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map(key => [key, stableObject(value[key])]),
    );
  }
  return stableValue(value);
}

function digest(value) {
  return crypto.createHash("sha256")
    .update(JSON.stringify(stableObject(value)))
    .digest("hex");
}

export function indexIncomingRecords(manifestPayloads) {
  const result = new Map();
  for (const item of manifestPayloads) {
    for (const envelope of item.payload.envelopes ?? []) {
      for (const record of envelope.records ?? []) {
        const importedId = String(record.imported_id ?? "").trim();
        if (!importedId) throw new Error(`Manifest record lacks imported_id: ${item.filename}`);
        if (result.has(importedId)) throw new Error(`Duplicate manifest imported_id: ${importedId}`);
        result.set(importedId, { account: envelope.account, record });
      }
    }
  }
  return result;
}

function structuralState(row) {
  const reasons = [];
  if (row.reconciled) reasons.push("RECONCILED");
  if (row.transfer_id) reasons.push("TRANSFER_LINK");
  if (row.schedule) reasons.push("SCHEDULE_LINK");
  if (row.is_parent || row.is_child || row.parent_id || (row.subtransactions ?? []).length) {
    reasons.push("SPLIT_STATE");
  }
  return reasons;
}

export function buildPreservationReport(snapshotDocument, targets, incomingById) {
  const targetIds = new Set(targets.map(row => row.id));
  const blockers = [];
  for (const row of targets) {
    const importedId = String(row.imported_id ?? "");
    const expected = incomingById.get(importedId);
    const reasons = structuralState(row);
    const differences = [];
    if (!expected) {
      reasons.push("MISSING_INCOMING_RECORD");
    } else {
      for (const [actualField, expectedField] of managedComparisonFields) {
        const desired = expectedField === "account"
          ? expected.account
          : expected.record[expectedField];
        const observed = row[actualField];
        if (!equivalentField(actualField, observed, desired)) {
          differences.push({ field: actualField, actual: observed ?? null, incoming: desired ?? null });
        }
      }
      if (differences.length) reasons.push("MANAGED_FIELD_DRIFT");
    }
    if (reasons.length) {
      blockers.push({
        actual_id: row.id,
        imported_id: importedId,
        account: row.account_name,
        reasons,
        differences,
      });
    }
  }
  const preservedRows = (snapshotDocument.transactions ?? [])
    .filter(row => !row.tombstone && !targetIds.has(row.id))
    .map(row => Object.fromEntries(preservationFields.map(field => [field, stableValue(row[field])])));
  const body = {
    schema_version: "actual-manual-state-preservation-v1",
    replacement_rows: targets.length,
    blocking_rows: blockers,
    preserved_rows: preservedRows,
  };
  return { ...body, sha256: digest(body) };
}

export function assertPreservationGate(report, apply, approvalSha256, environment = process.env) {
  if (!apply || report.blocking_rows.length === 0) return;
  if (String(environment.ALLOW_ACTUAL_MANUAL_STATE_REPLACEMENT ?? "").toLowerCase() !== "true") {
    throw new Error(
      `Production replacement would overwrite ${report.blocking_rows.length} rows with manual or divergent state`,
    );
  }
  if (!approvalSha256 || approvalSha256 !== report.sha256) {
    throw new Error("Manual-state replacement requires the exact reviewed preservation report sha256");
  }
}

export function verifyPreservedRows(report, snapshotDocument) {
  const afterById = new Map(
    (snapshotDocument.transactions ?? [])
      .filter(row => !row.tombstone)
      .map(row => [row.id, row]),
  );
  const missing = [];
  const mismatched = [];
  for (const expected of report.preserved_rows) {
    const actualRow = afterById.get(expected.id);
    if (!actualRow) {
      missing.push(expected.id);
      continue;
    }
    const actual = Object.fromEntries(
      preservationFields.map(field => [field, stableValue(actualRow[field])]),
    );
    if (digest(actual) !== digest(expected)) mismatched.push(expected.id);
  }
  return {
    status: missing.length || mismatched.length ? "FAIL" : "PASS",
    checked: report.preserved_rows.length,
    missing,
    mismatched,
  };
}

async function writeExclusive(filename, contents) {
  await fs.mkdir(path.dirname(filename), { recursive: true });
  const handle = await fs.open(filename, "wx", 0o600);
  try {
    await handle.writeFile(contents);
  } finally {
    await handle.close();
  }
}

export async function runProductionRebuild({
  root,
  validationConfigPath,
  bootstrapConfigPath,
  start,
  end,
  backupPath,
  snapshotPath,
  resultPath,
  apply,
  preservationApprovalSha256,
  environment = process.env,
  dependencies = {},
}) {
  const {
    api = actual,
    openBudget: openBudgetImpl = openBudget,
    snapshot: snapshotImpl = snapshot,
    bootstrap: bootstrapImpl = bootstrap,
    importEnvelopes: importEnvelopesImpl = importEnvelopes,
    loadFullRebuildManifests: loadFullRebuildManifestsImpl = loadFullRebuildManifests,
  } = dependencies;
  assertReplacementGate(apply, environment);
  const resolvedRoot = path.resolve(root);
  const validation = JSON.parse(await fs.readFile(validationConfigPath, "utf8"));
  const bootstrapConfig = JSON.parse(await fs.readFile(bootstrapConfigPath, "utf8"));
  const manifests = await loadFullRebuildManifestsImpl(resolvedRoot, validation);
  const manifestPayloads = [];
  for (const manifest of manifests) {
    manifestPayloads.push({
      ...manifest,
      payload: JSON.parse(await fs.readFile(manifest.filename, "utf8")),
    });
  }
  const incomingById = indexIncomingRecords(manifestPayloads);
  await openBudgetImpl();
  try {
    const before = await snapshotImpl(start, end);
    const targets = selectReplacementRows(before, validation.snapshot_scope ?? {});
    const preservation = buildPreservationReport(before, targets, incomingById);
    const plan = {
      schema_version: "actual-production-rebuild-v1",
      status: apply ? "APPLYING" : "PLANNED",
      manifest_count: manifests.length,
      replacement_count: targets.length,
      preserved_count: before.transactions.filter(row => !row.tombstone).length - targets.length,
      scope: validation.snapshot_scope,
      preservation: {
        sha256: preservation.sha256,
        blocking_rows: preservation.blocking_rows.length,
        preserved_rows: preservation.preserved_rows.length,
        blocking_sample: preservation.blocking_rows.slice(0, 25),
      },
    };
    if (!apply) return plan;
    assertPreservationGate(
      preservation,
      apply,
      preservationApprovalSha256,
      environment,
    );

    const backup = await api.exportBudget();
    if (!(backup instanceof Uint8Array) || backup.byteLength < 1024) {
      throw new Error("Actual export backup is unexpectedly small or invalid");
    }
    await writeExclusive(backupPath, backup);
    const backupHash = crypto.createHash("sha256").update(backup).digest("hex");
    await writeExclusive(
      `${backupPath}.sha256.json`,
      `${JSON.stringify({ sha256: backupHash, size_bytes: backup.byteLength }, null, 2)}\n`,
    );

    for (const row of targets) await api.deleteTransaction(row.id);

    const imports = [];
    for (const manifest of manifestPayloads) {
      const payload = manifest.payload;
      if (isVerifiedEmptyManifest(payload)) {
        imports.push({
          source_id: manifest.source_id,
          manifest: path.relative(resolvedRoot, manifest.filename).replaceAll("\\", "/"),
          status: "verified-empty",
        });
        continue;
      }
      const result = await importEnvelopesImpl(payload, true, {
        syncRemote: false,
        reimportDeleted: true,
      });
      if (result.status !== "committed") {
        throw new Error(`Production import failed: ${manifest.filename}`);
      }
      imports.push({
        source_id: manifest.source_id,
        manifest: path.relative(resolvedRoot, manifest.filename).replaceAll("\\", "/"),
        verification: result.verification,
      });
    }
    const bootstrapResult = await bootstrapImpl(
      bootstrapConfig,
      true,
      bootstrapConfigPath,
      { syncRemote: false },
    );
    await api.sync();
    const after = await snapshotImpl(start, end);
    const preservationVerification = verifyPreservedRows(preservation, after);
    if (preservationVerification.status !== "PASS") {
      throw new Error(
        `Preserved Actual state changed: ${JSON.stringify(preservationVerification)}`,
      );
    }
    await fs.mkdir(path.dirname(snapshotPath), { recursive: true });
    await fs.writeFile(snapshotPath, `${JSON.stringify(after, null, 2)}\n`, "utf8");
    const result = {
      ...plan,
      status: "APPLIED",
      backup: backupPath,
      backup_sha256: backupHash,
      backup_size_bytes: backup.byteLength,
      deleted: targets.length,
      imports,
      bootstrap_changes: bootstrapResult.changes.length,
      snapshot: snapshotPath,
      preservation: {
        sha256: preservation.sha256,
        blocking_rows: preservation.blocking_rows.length,
        verification: preservationVerification,
      },
    };
    await fs.mkdir(path.dirname(resultPath), { recursive: true });
    await fs.writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    return result;
  } finally {
    await api.shutdown();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  for (const required of [
    "root", "validation", "bootstrap", "start", "end", "backup", "snapshot", "result",
  ]) {
    if (!args[required]) throw new Error(`Missing --${required}`);
  }
  const result = await runProductionRebuild({
    root: args.root,
    validationConfigPath: path.resolve(args.validation),
    bootstrapConfigPath: path.resolve(args.bootstrap),
    start: args.start,
    end: args.end,
    backupPath: path.resolve(args.backup),
    snapshotPath: path.resolve(args.snapshot),
    resultPath: path.resolve(args.result),
    apply: Boolean(args.apply),
    preservationApprovalSha256: args["approve-preservation-sha256"],
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
}
