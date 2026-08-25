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
const readbackPhase = process.env.FINANCE_DATA_TABLE_READBACK_PHASE || 'FORWARD_POST';
if (!['FORWARD_PRE', 'FORWARD_POST', 'ROLLBACK'].includes(readbackPhase)) {
  throw new Error('FINANCE_DATA_TABLE_READBACK_PHASE_INVALID');
}
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

function blockedGate(gate, requiredAck) {
  return {
    gate,
    status: 'BLOCKED',
    required_ack: requiredAck,
    migration_receipt_required: true,
    command_executed: false,
  };
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
    const tables = listed.data.filter((table) => CANONICAL_TABLES.has(String(table.name))).sort((a, b) => a.name.localeCompare(b.name));
    if (readbackPhase === 'FORWARD_PRE') {
      if (tables.length !== 0) throw new Error(`FORWARD_PRE_TARGETS_ALREADY_EXIST:${tables.length}`);
      process.stdout.write(`finance data table digest verified:${JSON.stringify({
        schema_version: 1,
        receipt_contract: 'finance-data-table-readback-receipt-v1',
        status: 'FORWARD_PRE_READBACK',
        phase: readbackPhase,
        scope: 'READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST',
        finance_tables: 0,
        tables: [],
        total_rows: 0,
        digest_sha256: sha256(JSON.stringify([])),
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
      })}\n`);
      completed = true;
      return;
    }
    if (tables.length !== CANONICAL_TABLES.size) throw new Error(`EXACT_FINANCE_DATA_TABLE_COUNT_REQUIRED:${tables.length}`);
    assertCanonicalTableNames(tables);
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
      stage = `rows-${table.name}`;
      const rows = [];
      let skip = 0;
      let expectedCount = null;
      while (true) {
        const page = await service.getManyRowsAndCount(table.id, projectId, { skip, take: 1000 });
        if (!Number.isInteger(page.count) || page.count < 0 || page.count > 100000) throw new Error('DATA_TABLE_ROW_BOUND_INVALID');
        if (!Array.isArray(page.data) || page.data.length > 1000) throw new Error('DATA_TABLE_ROW_PAGE_INVALID');
        if (expectedCount === null) expectedCount = page.count;
        if (page.count !== expectedCount) throw new Error(`DATA_TABLE_ROW_COUNT_DRIFT:${table.name}`);
        rows.push(...page.data.map((row) => JSON.stringify(canonical(row))));
        skip += page.data.length;
        if (skip >= page.count) break;
        if (page.data.length === 0) throw new Error('DATA_TABLE_PAGINATION_STALLED');
      }
      rows.sort();
      if (rows.length !== expectedCount) throw new Error(`DATA_TABLE_ROW_COUNT_MISMATCH:${table.name}`);
      totalRows += rows.length;
      const tableReceipt = {
        name: table.name,
        table_id_sha256: sha256(table.id),
        schema,
        schema_sha256: sha256(JSON.stringify(canonical(schema))),
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
    const digestSha256 = sha256(JSON.stringify(canonical(tableReceipts)));
    process.stdout.write(`finance data table digest verified:${JSON.stringify({
      schema_version: 1,
      receipt_contract: 'finance-data-table-readback-receipt-v1',
      status: 'VERIFIED',
      phase: readbackPhase,
      scope: 'READ_ONLY_IN_MEMORY_FINANCE_DATA_TABLE_DIGEST',
      finance_tables: tables.length,
      tables: tableReceipts,
      total_rows: totalRows,
      digest_sha256: digestSha256,
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
