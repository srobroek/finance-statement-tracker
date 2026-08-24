'use strict';

if (process.env.FINANCE_DATA_TABLE_DIGEST_ACK !== 'READ_ONLY_IN_MEMORY') {
  throw new Error('FINANCE_DATA_TABLE_DIGEST_ACK=READ_ONLY_IN_MEMORY is required');
}
const projectId = process.env.N8N_FINANCE_PROJECT_ID;
if (typeof projectId !== 'string' || projectId.length === 0) {
  throw new Error('N8N_FINANCE_PROJECT_ID_REQUIRED');
}
if (!/^[A-Za-z0-9_-]{8,64}$/.test(projectId)) {
  throw new Error('N8N_FINANCE_PROJECT_ID_INVALID');
}

const crypto = require('node:crypto');
const path = require('node:path');
const { createRequire } = require('node:module');
const n8nPackageJson = require.resolve('n8n/package.json', { paths: ['/usr/local/lib/node_modules'] });
const n8nRoot = path.dirname(n8nPackageJson);
process.env.NODE_CONFIG_DIR ||= path.join(n8nRoot, 'bin', 'config');
const n8nRequire = createRequire(n8nPackageJson);
const { Container } = n8nRequire('@n8n/di');
const { BaseCommand } = n8nRequire('./dist/commands/base-command.js');
const { ListWorkflowCommand } = n8nRequire('./dist/commands/list/workflow.js');
const { DataTableService } = n8nRequire('./dist/modules/data-table/data-table.service.js');

const CANONICAL_TABLES = new Set([
  'finance_ingestion_state',
  'finance_documents',
  'finance_actual_batches',
  'finance_ai_reviews',
]);

function canonical(value) {
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

const originalInit = BaseCommand.prototype.init;
let completed = false;
BaseCommand.prototype.init = async function financeDataTableDigest(...args) {
  let stage = 'base-init';
  try {
    await originalInit.apply(this, args);
    const service = Container.get(DataTableService);
    stage = 'table-list';
    const listed = await service.getManyAndCount({ filter: { projectId }, take: 100 });
    if (listed.count !== CANONICAL_TABLES.size || listed.data.length !== CANONICAL_TABLES.size) {
      throw new Error(`EXACT_FINANCE_DATA_TABLE_COUNT_REQUIRED:${listed.count}`);
    }
    const tables = listed.data.filter((table) => CANONICAL_TABLES.has(String(table.name))).sort((a, b) => a.name.localeCompare(b.name));
    if (tables.length !== CANONICAL_TABLES.size) throw new Error(`EXACT_FINANCE_DATA_TABLE_COUNT_REQUIRED:${tables.length}`);
    const digest = crypto.createHash('sha256');
    let totalRows = 0;
    for (const table of tables) {
      stage = `schema-${table.name}`;
      const columns = await service.getColumns(table.id, projectId);
      digest.update(`${table.name}\n${JSON.stringify(canonical(columns))}\n`);
      stage = `rows-${table.name}`;
      const rows = [];
      let skip = 0;
      while (true) {
        const page = await service.getManyRowsAndCount(table.id, projectId, { skip, take: 1000 });
        if (!Number.isInteger(page.count) || page.count < 0 || page.count > 100000) throw new Error('DATA_TABLE_ROW_BOUND_INVALID');
        rows.push(...page.data.map((row) => JSON.stringify(canonical(row))));
        skip += page.data.length;
        if (skip >= page.count) break;
        if (page.data.length === 0) throw new Error('DATA_TABLE_PAGINATION_STALLED');
      }
      rows.sort();
      totalRows += rows.length;
      digest.update(`${rows.length}\n${rows.join('\n')}\n`);
    }
    process.stdout.write(`finance data table digest verified:${JSON.stringify({
      schema_version: 1,
      status: 'VERIFIED',
      scope: 'READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST',
      finance_tables: tables.length,
      total_rows: totalRows,
      digest_sha256: digest.digest('hex'),
      writes_performed: false,
      provider_calls: false,
      row_values_recorded: false,
      secret_values_recorded: false,
    })}\n`);
    completed = true;
  } catch (error) {
    const detail = error && typeof error.message === 'string' && /^[A-Za-z0-9_:-]{1,256}$/.test(error.message)
      ? error.message : 'ERROR';
    process.stderr.write(`finance data table digest failed:${stage}:${detail}\n`);
    throw new Error(`FINANCE_DATA_TABLE_DIGEST_FAILED:${stage}`);
  }
};
ListWorkflowCommand.prototype.run = async function suppressWorkflowList() {
  if (!completed) throw new Error('FINANCE_DATA_TABLE_DIGEST_DID_NOT_COMPLETE');
};
require(path.join(n8nRoot, 'bin', 'n8n'));
