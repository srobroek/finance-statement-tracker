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
}) {
  assertReplacementGate(apply);
  const resolvedRoot = path.resolve(root);
  const validation = JSON.parse(await fs.readFile(validationConfigPath, "utf8"));
  const bootstrapConfig = JSON.parse(await fs.readFile(bootstrapConfigPath, "utf8"));
  const manifests = await loadFullRebuildManifests(resolvedRoot, validation);
  await openBudget();
  try {
    const before = await snapshot(start, end);
    const targets = selectReplacementRows(before, validation.snapshot_scope ?? {});
    const plan = {
      schema_version: "actual-production-rebuild-v1",
      status: apply ? "APPLYING" : "PLANNED",
      manifest_count: manifests.length,
      replacement_count: targets.length,
      preserved_count: before.transactions.filter(row => !row.tombstone).length - targets.length,
      scope: validation.snapshot_scope,
    };
    if (!apply) return plan;

    const backup = await actual.exportBudget();
    if (!(backup instanceof Uint8Array) || backup.byteLength < 1024) {
      throw new Error("Actual export backup is unexpectedly small or invalid");
    }
    await writeExclusive(backupPath, backup);
    const backupHash = crypto.createHash("sha256").update(backup).digest("hex");
    await writeExclusive(
      `${backupPath}.sha256.json`,
      `${JSON.stringify({ sha256: backupHash, size_bytes: backup.byteLength }, null, 2)}\n`,
    );

    for (const row of targets) await actual.deleteTransaction(row.id);

    const imports = [];
    for (const manifest of manifests) {
      const payload = JSON.parse(await fs.readFile(manifest.filename, "utf8"));
      if (isVerifiedEmptyManifest(payload)) {
        imports.push({
          source_id: manifest.source_id,
          manifest: path.relative(resolvedRoot, manifest.filename).replaceAll("\\", "/"),
          status: "verified-empty",
        });
        continue;
      }
      const result = await importEnvelopes(payload, true, {
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
    const bootstrapResult = await bootstrap(
      bootstrapConfig,
      true,
      bootstrapConfigPath,
      { syncRemote: false },
    );
    await actual.sync();
    const after = await snapshot(start, end);
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
    };
    await fs.mkdir(path.dirname(resultPath), { recursive: true });
    await fs.writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    return result;
  } finally {
    await actual.shutdown();
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
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
}
