import { createRequire } from 'node:module';
import { mkdir, rm, stat } from 'node:fs/promises';

const requireProbe = createRequire('/probe/package.json');

export async function preflightNativeDb() {
  const dataDir = `/tmp/actual-api-native-preflight-${process.pid}`;
  const databasePath = `${dataDir}/preflight.sqlite`;
  const Database = requireProbe('better-sqlite3');

  await rm(dataDir, { recursive: true, force: true });
  await mkdir(dataDir, { recursive: true, mode: 0o700 });
  try {
    const database = new Database(databasePath);
    database.prepare('CREATE TABLE native_binding_preflight (id INTEGER PRIMARY KEY)').run();
    database.prepare('INSERT INTO native_binding_preflight DEFAULT VALUES').run();
    const row = database.prepare('SELECT COUNT(*) AS count FROM native_binding_preflight').get();
    database.close();
    if (row?.count !== 1) throw new Error('native SQLite preflight query failed');
    const databaseStat = await stat(databasePath);
    if (!databaseStat.isFile()) throw new Error('native SQLite preflight path is not a file');
  } finally {
    await rm(dataDir, { recursive: true, force: true });
  }
}
