import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import * as actual from "@actual-app/api";

import { bootstrap, importEnvelopes } from "./actualctl.mjs";

const configPath = path.resolve(process.argv[2] ?? "../../config/actual-bootstrap.json");
const config = JSON.parse(await fs.readFile(configPath, "utf8"));
const statementManifestPath = process.argv[3] ? path.resolve(process.argv[3]) : null;
const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "finance-actual-integration-"));
const dataDir = path.join(tempRoot, "data");
await fs.mkdir(dataDir);

let result;
try {
  await actual.init({ dataDir });
  await actual.runImport("Finance bridge integration test", async () => {});
  const firstBootstrap = await bootstrap(config, true, configPath, { syncRemote: false });
  const secondBootstrap = await bootstrap(config, false, configPath, { syncRemote: false });
  const account = config.accounts[0].name;
  const envelope = [{
    account,
    default_cleared: true,
    records: [{
      date: "2026-08-16",
      amount: -12345,
      imported_payee: "Integration Test Merchant",
      imported_id: "finance-bridge-integration:1",
      notes: "#integration-test",
      cleared: true,
    }],
  }];
  const firstImport = await importEnvelopes(envelope, true, { syncRemote: false });
  const secondImport = await importEnvelopes(envelope, true, { syncRemote: false });
  const formulaGuardAccountName = "Emirates Islamic Amazon Credit Card · 0082";
  const formulaGuardId = "finance-bridge-integration:card-payment-sign";
  const formulaGuardImport = await importEnvelopes([{
    account: formulaGuardAccountName,
    default_cleared: true,
    records: [{
      date: "2026-08-16",
      amount: -10000,
      imported_payee: "TRANSFER PAYMENT RECEIVED THANK YOU",
      imported_id: formulaGuardId,
      cleared: true,
    }],
  }], true, { syncRemote: false });
  const formulaGuardAccount = (await actual.getAccounts())
    .find(item => item.name === formulaGuardAccountName);
  if (!formulaGuardAccount) throw new Error("Formula guard integration account was not created");
  const formulaGuardRow = (await actual.getTransactions(
    formulaGuardAccount.id,
    "2026-08-16",
    "2026-08-16",
  )).find(item => item.imported_id === formulaGuardId);
  if (formulaGuardRow?.amount !== 10000) {
    throw new Error(`Card-payment formula guard did not flip the sign: ${formulaGuardRow?.amount}`);
  }
  if (secondBootstrap.changes.length) {
    throw new Error(`Bootstrap is not idempotent: ${JSON.stringify(secondBootstrap.changes)}`);
  }
  if (firstImport.status !== "committed" || secondImport.status !== "committed") {
    throw new Error("Actual import did not commit in both idempotency passes");
  }
  let statementImport = null;
  let statementReplay = null;
  if (statementManifestPath) {
    const statementManifest = JSON.parse(await fs.readFile(statementManifestPath, "utf8"));
    statementImport = await importEnvelopes(statementManifest, true, { syncRemote: false });
    statementReplay = await importEnvelopes(statementManifest, true, { syncRemote: false });
  }
  result = {
    status: "ok",
    bootstrap_changes: firstBootstrap.changes.length,
    native_rules: firstBootstrap.native_rule_compilation.compiled,
    second_bootstrap_changes: secondBootstrap.changes.length,
    first_import_verified: firstImport.verification,
    second_import_verified: secondImport.verification,
    formula_guard_verified: formulaGuardImport.verification,
    formula_guard_amount: formulaGuardRow.amount,
    statement_import_verified: statementImport?.verification ?? null,
    statement_replay_verified: statementReplay?.verification ?? null,
    statement_payment_reminder: statementImport?.payment_reminder ?? null,
    statement_payment_reminder_replay: statementReplay?.payment_reminder ?? null,
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} finally {
  await actual.shutdown();
  const resolved = path.resolve(tempRoot);
  if (!resolved.startsWith(path.resolve(os.tmpdir()) + path.sep)) {
    throw new Error(`Refusing to remove unexpected integration directory: ${resolved}`);
  }
  await fs.rm(resolved, { recursive: true, force: true });
}
