import fs from "node:fs/promises";
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
      const first = await importEnvelopes(payload, true, { syncRemote: false });
      const replay = await importEnvelopes(payload, true, { syncRemote: false });
      if (first.status !== "committed" || replay.status !== "committed") {
        throw new Error(`Disposable import did not commit: ${manifest.filename}`);
      }
      imports.push({
        source_id: manifest.source_id,
        manifest: path.relative(resolvedRoot, manifest.filename).replaceAll("\\", "/"),
        verification: first.verification,
        replay_verification: replay.verification,
      });
    }
    const rebuiltSnapshot = await snapshot(start, end);
    await fs.mkdir(path.dirname(snapshotPath), { recursive: true });
    await fs.writeFile(snapshotPath, `${JSON.stringify(rebuiltSnapshot, null, 2)}\n`, "utf8");
    result = {
      schema_version: "actual-disposable-full-rebuild-v1",
      status: "PASS",
      manifests: manifests.length,
      transactions: rebuiltSnapshot.transactions.filter(row => !row.tombstone).length,
      bootstrap_changes: bootstrapResult.changes.length,
      imports,
      snapshot: snapshotPath,
    };
    await fs.mkdir(path.dirname(resultPath), { recursive: true });
    await fs.writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
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
