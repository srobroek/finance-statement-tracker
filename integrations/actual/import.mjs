import fs from "node:fs/promises";
import process from "node:process";
import * as actual from "@actual-app/api";

function requireEnv(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

async function main() {
  const [inputPath] = process.argv.slice(2);
  if (!inputPath) throw new Error("Usage: node import.mjs <import-envelopes.json>");

  const envelopes = JSON.parse(await fs.readFile(inputPath, "utf8"));
  await actual.init({
    dataDir: process.env.ACTUAL_DATA_DIR || ".actual-cache",
    serverURL: requireEnv("ACTUAL_SERVER_URL"),
    password: requireEnv("ACTUAL_PASSWORD"),
  });

  try {
    const syncId = requireEnv("ACTUAL_SYNC_ID");
    if (process.env.ACTUAL_ENCRYPTION_PASSWORD) {
      await actual.downloadBudget(syncId, {
        password: process.env.ACTUAL_ENCRYPTION_PASSWORD,
      });
    } else {
      await actual.downloadBudget(syncId);
    }
    const accounts = await actual.getAccounts();
    const accountIds = new Map(accounts.map((account) => [account.name, account.id]));

    for (const envelope of envelopes) {
      const accountId = accountIds.get(envelope.account);
      if (!accountId) throw new Error(`Unknown Actual account: ${envelope.account}`);
      const result = await actual.importTransactions(accountId, envelope.records, {
        defaultCleared: false,
        dryRun: process.env.ACTUAL_DRY_RUN !== "false",
      });
      process.stdout.write(`${JSON.stringify({ account: envelope.account, result })}\n`);
    }
    await actual.sync();
  } finally {
    await actual.shutdown();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
