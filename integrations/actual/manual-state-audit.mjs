import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import { loadFullRebuildManifests } from "./full-rebuild.mjs";
import {
  buildPreservationReport,
  indexIncomingRecords,
  selectReplacementRows,
} from "./production-rebuild.mjs";

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

export async function runManualStateAudit({ root, validationConfigPath, snapshotPath, outputPath }) {
  const resolvedRoot = path.resolve(root);
  const validation = JSON.parse(await fs.readFile(validationConfigPath, "utf8"));
  const snapshot = JSON.parse(await fs.readFile(snapshotPath, "utf8"));
  const manifests = await loadFullRebuildManifests(resolvedRoot, validation);
  const payloads = [];
  for (const manifest of manifests) {
    payloads.push({
      ...manifest,
      payload: JSON.parse(await fs.readFile(manifest.filename, "utf8")),
    });
  }
  const incoming = indexIncomingRecords(payloads);
  const targets = selectReplacementRows(snapshot, validation.snapshot_scope ?? {});
  const report = buildPreservationReport(snapshot, targets, incoming);
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return report;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  for (const required of ["root", "validation", "snapshot", "output"]) {
    if (!args[required]) throw new Error(`Missing --${required}`);
  }
  const report = await runManualStateAudit({
    root: path.resolve(args.root),
    validationConfigPath: path.resolve(args.validation),
    snapshotPath: path.resolve(args.snapshot),
    outputPath: path.resolve(args.output),
  });
  process.stdout.write(`${JSON.stringify({
    sha256: report.sha256,
    replacement_rows: report.replacement_rows,
    blocking_rows: report.blocking_rows.length,
    preserved_rows: report.preserved_rows.length,
  }, null, 2)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
}
