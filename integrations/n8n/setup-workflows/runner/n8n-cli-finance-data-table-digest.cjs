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

const CANONICAL_TABLE_NAMES = [
  'finance_ingestion_state',
  'finance_documents',
  'finance_actual_batches',
  'finance_ai_reviews',
];
const CANONICAL_TABLES = new Set(CANONICAL_TABLE_NAMES);
const PRESERVED_TABLE_NAMES = [
  'finance_archive_receipts',
  'finance_document_operations',
  'finance_mcp_requests',
  'finance_pipeline_runs',
  'finance_reconciliations',
  'finance_source_contracts',
  'finance_source_cursors',
  'finance_execution_failures',
  'finance_acquisition_receipts',
  'finance_actual_outbox',
  'finance_actual_verifications',
  'finance_config_versions',
  'finance_provider_circuits',
  'finance_agent_jobs',
  'finance_ai_policy_contracts',
];
const ALLOWED_PROJECT_TABLES = new Set([
  ...CANONICAL_TABLE_NAMES,
  ...PRESERVED_TABLE_NAMES,
]);
const PINNED_PRESERVED_TABLE_IDS = new Map([
  ['finance_source_contracts', 'sha256:73b62207'],
  ['finance_source_cursors', 'sha256:60e428cd'],
  ['finance_archive_receipts', 'sha256:49bf4e32'],
  ['finance_document_operations', 'sha256:2ad2a52a'],
  ['finance_pipeline_runs', 'sha256:48eb19e5'],
  ['finance_reconciliations', 'sha256:f47bf1e1'],
  ['finance_mcp_requests', 'sha256:3b9034f0'],
  ['finance_execution_failures', 'sha256:59c34ab8'],
]);
const TARGET_SCHEMA_DIGESTS = new Map([
  ['finance_actual_batches', 'e85b91693673a2cc19a3cf7cd27be7886a8f41dea38f4c8b818f73431a750511'],
  ['finance_ai_reviews', '30add9a2089cd56bff376b97388f7b7208bf29fb360032f649ec38cb84c1566f'],
  ['finance_documents', '6a8d8d48855e1f77f09f596a86cdb46a44b75aa118ca0b60bed32ac5de1e2412'],
  ['finance_ingestion_state', '0ac33f034857541d217c25ab4f61ff017dfbf0ccf69685c1d02419b4b475f49f'],
]);
const migrationReceiptSha256 = process.env.FINANCE_DATA_TABLE_MIGRATION_RECEIPT_SHA256 || null;
if (migrationReceiptSha256 !== null && !/^[0-9a-f]{64}$/.test(migrationReceiptSha256)) {
  throw new Error('FINANCE_DATA_TABLE_MIGRATION_RECEIPT_SHA256_INVALID');
}

function assertCanonicalTableNames(tables) {
  const observed = tables.map((table) => String(table.name || '')).sort((left, right) => left.localeCompare(right));
  const expected = [...CANONICAL_TABLE_NAMES].sort((left, right) => left.localeCompare(right));
  if (observed.length !== expected.length || observed.some((name, index) => name !== expected[index])) {
    throw new Error('EXACT_FINANCE_DATA_TABLE_NAMES_REQUIRED');
  }
}
function assertAllowedProjectTables(listed) {
  if (!listed || !Number.isInteger(listed.count) || !Array.isArray(listed.data) ||
      listed.count !== listed.data.length || listed.data.length !== ALLOWED_PROJECT_TABLES.size) {
    throw new Error('CLOSED_FINANCE_DATA_TABLE_SET_REQUIRED');
  }
  const observed = listed.data.map((table) => String(table.name || ''));
  if (new Set(observed).size !== observed.length ||
      observed.some((name) => !ALLOWED_PROJECT_TABLES.has(name)) ||
      listed.data.some((table) => {
        const expectedId = PINNED_PRESERVED_TABLE_IDS.get(String(table.name || ''));
        return expectedId !== undefined && String(table.id || '') !== expectedId;
      })) {
    throw new Error('CLOSED_FINANCE_DATA_TABLE_SET_REQUIRED');
  }
}


function canonical(value) {
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}
function assertTargetSchemaDigest(tableName, schema) {
  const observed = sha256(JSON.stringify(canonical(schema)));
  if (observed !== TARGET_SCHEMA_DIGESTS.get(tableName)) {
    throw new Error(`TARGET_SCHEMA_DIGEST_MISMATCH:${tableName}`);
  }
  return observed;
}


function blockedGate(gate, requiredAck) {
  return {
    gate,
    status: 'BLOCKED',
    required_ack: requiredAck,
    migration_receipt_required: true,
    command_executed: false,
  };
}
const READBACK_PHASES = new Set([
  'FORWARD_PRE',
  'FORWARD_POST',
  'ROLLBACK_PRE',
  'ROLLBACK_POST',
]);

function readbackReceipt(phase, tables, totalRows) {
  if (!READBACK_PHASES.has(phase)) {
    throw new Error(`DATA_TABLE_READBACK_PHASE_INVALID:${phase}`);
  }
  const forwardPre = phase === 'FORWARD_PRE';
  if (tables.length !== CANONICAL_TABLES.size ||
      tables.some((table) => !Number.isInteger(table.row_count) || table.row_count < 0) ||
      totalRows !== tables.reduce((count, table) => count + table.row_count, 0)) {
    throw new Error('DATA_TABLE_READBACK_TARGET_SET_INVALID');
  }
  const receiptTables = tables;
  return {
    schema_version: 1,
    receipt_contract: 'finance-data-table-readback-receipt-v1',
    status: forwardPre ? 'FORWARD_PRE_READBACK' : 'VERIFIED',
    phase,
    scope: 'READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST',
    finance_tables: receiptTables.length,
    tables: receiptTables,
    total_rows: totalRows,
    digest_sha256: sha256(JSON.stringify(canonical(receiptTables))),
    migration_receipt: {
      schema_version: 'data-table-migration-receipt-v1',
      required: true,
      bound: migrationReceiptSha256 !== null,
      sha256: migrationReceiptSha256,
    },
    forward_gate: blockedGate('FORWARD', 'FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE'),
    rollback_gate: blockedGate('ROLLBACK', 'FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE'),
    writes_performed: false,
    provider_calls: false,
    row_values_recorded: false,
    secret_values_recorded: false,
  };
}


async function readCanonicalRows(service, table, schema) {
  const rows = [];
  let skip = 0;
  let expectedCount = null;
  while (true) {
    const page = await service.getManyRowsAndCount(table.id, projectId, { skip, take: 1000 });
    if (!Number.isInteger(page.count) || page.count < 0 || page.count > 100000) {
      throw new Error('DATA_TABLE_ROW_BOUND_INVALID');
    }
    if (!Array.isArray(page.data) || page.data.length > 1000) {
      throw new Error('DATA_TABLE_ROW_PAGE_INVALID');
    }
    if (expectedCount === null) expectedCount = page.count;
    if (page.count !== expectedCount) throw new Error(`DATA_TABLE_ROW_COUNT_DRIFT:${table.name}`);
    for (const row of page.data) {
      rows.push(JSON.stringify(canonical(Object.fromEntries(
        schema.map((column) => [column.name, row[column.name] ?? null]),
      ))));
    }
    skip += page.data.length;
    if (skip >= expectedCount) break;
    if (page.data.length === 0) throw new Error('DATA_TABLE_PAGINATION_STALLED');
  }
  rows.sort();
  if (rows.length !== expectedCount) throw new Error(`DATA_TABLE_ROW_COUNT_MISMATCH:${table.name}`);
  return rows;
}


const originalInit = BaseCommand.prototype.init;
let completed = false;
BaseCommand.prototype.init = async function financeDataTableDigest(...args) {
  let stage = 'base-init';
  try {
    await originalInit.apply(this, args);
    const service = Container.get(DataTableService);
    stage = 'table-list';
    const readbackPhase = process.env.FINANCE_DATA_TABLE_READBACK_PHASE || 'FORWARD_POST';
    if (!READBACK_PHASES.has(readbackPhase)) {
      throw new Error(`DATA_TABLE_READBACK_PHASE_INVALID:${readbackPhase}`);
    }
    const listed = await service.getManyAndCount({ filter: { projectId }, take: 100 });
    assertAllowedProjectTables(listed);
    const tables = listed.data.filter((table) => CANONICAL_TABLES.has(String(table.name))).sort((a, b) => a.name.localeCompare(b.name));
    if (tables.length !== CANONICAL_TABLES.size) throw new Error(`EXACT_FINANCE_DATA_TABLE_COUNT_REQUIRED:${tables.length}`);
    assertCanonicalTableNames(tables);
    if (tables.some((table) => typeof table.id !== 'string' || table.id.length === 0)) {
      throw new Error('DATA_TABLE_ID_INVALID');
    }
    const tableReceipts = [];
    let totalRows = 0;
    for (const table of tables) {
      if (typeof table.id !== 'string' || table.id.length === 0) throw new Error(`DATA_TABLE_ID_INVALID:${table.name}`);
      stage = `schema-${table.name}`;
      const columns = await service.getColumns(table.id, projectId);
      if (!Array.isArray(columns) || columns.length === 0) throw new Error(`DATA_TABLE_SCHEMA_EMPTY:${table.name}`);
      const schema = columns.map((column) => ({
        name: String(column.name || ''),
        type: String(column.type || '').toLowerCase(),
      })).sort((left, right) => left.name.localeCompare(right.name));
      if (schema.some((column) => !column.name || !column.type)) throw new Error(`DATA_TABLE_SCHEMA_INVALID:${table.name}`);
      if (new Set(schema.map((column) => column.name)).size !== schema.length) {
        throw new Error(`DATA_TABLE_SCHEMA_DUPLICATE:${table.name}`);
      }
      const schemaSha256 = assertTargetSchemaDigest(table.name, schema);
      stage = `rows-${table.name}`;
      const rows = await readCanonicalRows(service, table, schema);
      totalRows += rows.length;
      const tableReceipt = {
        name: table.name,
        table_id_sha256: sha256(table.id),
        schema,
        schema_sha256: schemaSha256,
        row_count: rows.length,
        rows_sha256: sha256(JSON.stringify(rows)),
      };
      tableReceipts.push({
        ...tableReceipt,
        digest_sha256: sha256(JSON.stringify(canonical(tableReceipt))),
      });
    }
    if (new Set(tableReceipts.map((table) => table.table_id_sha256)).size !== CANONICAL_TABLES.size) {
      throw new Error('DATA_TABLE_IDENTITY_DUPLICATE');
    }
    process.stdout.write(
      `finance data table digest verified:${JSON.stringify(readbackReceipt(readbackPhase, tableReceipts, totalRows))}\n`,
    );
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
