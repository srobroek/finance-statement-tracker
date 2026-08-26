'use strict';

const operation = process.env.FINANCE_FOUR_TABLE_OPERATION;
if (!['FORWARD', 'ROLLBACK'].includes(operation)) throw new Error('FOUR_TABLE_OPERATION_INVALID');
const requiredAck = operation === 'FORWARD'
  ? 'FOUR_TABLE_FORWARD_REQUIRES_NAMED_OPERATOR_GATE'
  : 'FOUR_TABLE_ROLLBACK_REQUIRES_NAMED_OPERATOR_GATE';
if (process.env.FINANCE_FOUR_TABLE_ACK !== requiredAck) throw new Error('FOUR_TABLE_OPERATOR_GATE_REQUIRED');

const projectId = process.env.N8N_FINANCE_PROJECT_ID;
if (typeof projectId !== 'string' || !/^[A-Za-z0-9_-]{8,64}$/.test(projectId)) {
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
const { WorkflowRepository } = n8nRequire('@n8n/db');
const { DataTableService } = n8nRequire('./dist/modules/data-table/data-table.service.js');
const pg = n8nRequire('pg');

const EXPORT_SCHEMA = 'finance-four-table-live-export-v1';
const RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v1';
const TARGET_NAMES = new Set([
  'finance_ingestion_state',
  'finance_documents',
  'finance_actual_batches',
  'finance_ai_reviews',
]);

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

function digest(value) {
  return crypto.createHash('sha256').update(`${JSON.stringify(canonical(value))}\n`).digest('hex');
}

function decode(name) {
  const encoded = process.env[name];
  if (typeof encoded !== 'string' || encoded.length === 0) throw new Error(`${name}_REQUIRED`);
  try {
    return JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
  } catch {
    throw new Error(`${name}_INVALID`);
  }
}

function text(value, code) {
  if (typeof value !== 'string' || value.length === 0) throw new Error(code);
  return value;
}

function selectorId(selector) {
  if (selector && typeof selector === 'object' && selector.__rl === true) return String(selector.value || '');
  return typeof selector === 'string' ? selector : '';
}

function setSelector(node, tableId) {
  const parameters = node.parameters && typeof node.parameters === 'object' ? node.parameters : {};
  const selector = parameters.dataTableId;
  if (tableId === null) {
    delete parameters.dataTableId;
    node.parameters = parameters;
    return;
  }
  if (selector && typeof selector === 'object' && selector.__rl === true) {
    parameters.dataTableId = { ...selector, mode: 'id', value: tableId };
  } else {
    parameters.dataTableId = tableId;
  }
  node.parameters = parameters;
}

function restoreSelector(node, selector) {
  const parameters = node.parameters && typeof node.parameters === 'object' ? node.parameters : {};
  if (selector === undefined || selector === null) delete parameters.dataTableId;
  else parameters.dataTableId = clone(selector);
  node.parameters = parameters;
}

function databaseOptions(env) {
  return {
    host: env.DB_POSTGRESDB_HOST,
    port: Number(env.DB_POSTGRESDB_PORT || 5432),
    database: env.DB_POSTGRESDB_DATABASE,
    user: env.DB_POSTGRESDB_USER,
    password: env.DB_POSTGRESDB_PASSWORD,
    max: 1,
    connectionTimeoutMillis: 5_000,
    idleTimeoutMillis: 1_000,
  };
}

function validateExport(exported) {
  if (!exported || exported.schema_version !== EXPORT_SCHEMA || exported.redacted !== true) {
    throw new Error('LIVE_EXPORT_SCHEMA_INVALID');
  }
  const exportDigest = text(exported.export_sha256, 'LIVE_EXPORT_SHA256_INVALID');
  const unsigned = { ...exported };
  delete unsigned.export_sha256;
  if (digest(unsigned) !== exportDigest) throw new Error('LIVE_EXPORT_INTEGRITY_INVALID');
  if (exported.project_id !== projectId || exported.workflow_count !== 19 || exported.in_flight !== 0) {
    throw new Error('LIVE_EXPORT_PROJECT_OR_QUIESCENCE_MISMATCH');
  }
  if (!Array.isArray(exported.workflows) || exported.workflows.length !== 19) {
    throw new Error('EXACT_19_WORKFLOW_EXPORT_REQUIRED');
  }
  const workflows = new Map();
  for (const workflow of exported.workflows) {
    const id = text(workflow.workflow_id, 'LIVE_WORKFLOW_ID_INVALID');
    const revision = text(workflow.revision_id, 'LIVE_WORKFLOW_REVISION_INVALID');
    if (workflows.has(id) || workflow.active !== false || workflow.published !== false || workflow.in_flight !== 0) {
      throw new Error('LIVE_WORKFLOW_GRAPH_INVALID');
    }
    workflows.set(id, revision);
  }
  if (!Array.isArray(exported.targets) || exported.targets.length !== 4 ||
      exported.targets.some((target) => !TARGET_NAMES.has(target.name) || !text(target.table_id, 'LIVE_TARGET_ID_INVALID'))) {
    throw new Error('EXACT_TARGET_EXPORT_REQUIRED');
  }
  const targetIds = new Map(exported.targets.map((target) => [target.name, target.table_id]));
  if (targetIds.size !== 4 || new Set(targetIds.values()).size !== 4) throw new Error('LIVE_TARGET_ID_DUPLICATE');
  if (!Array.isArray(exported.references) || exported.references.length !== 33) {
    throw new Error('COMPLETE_LIVE_REFERENCE_EXPORT_REQUIRED');
  }
  const references = new Map();
  for (const reference of exported.references) {
    const id = text(reference.reference_id, 'LIVE_REFERENCE_ID_INVALID');
    if (references.has(id) || !workflows.has(reference.workflow_id) ||
        workflows.get(reference.workflow_id) !== reference.revision_id) {
      throw new Error(`LIVE_REFERENCE_GRAPH_INVALID:${id}`);
    }
    if (reference.canonical_table_name !== null && !targetIds.has(reference.canonical_table_name)) {
      throw new Error(`LIVE_REFERENCE_TARGET_INVALID:${id}`);
    }
    if (reference.canonical_table_name !== null && reference.canonical_table_id !== targetIds.get(reference.canonical_table_name)) {
      throw new Error(`LIVE_REFERENCE_TARGET_ID_INVALID:${id}`);
    }
    if (reference.canonical_table_name === null && reference.canonical_table_id !== null) {
      throw new Error(`LIVE_REFERENCE_UNDECLARED_TARGET:${id}`);
    }
    references.set(id, reference);
  }
  if (references.size !== 33) throw new Error('COMPLETE_LIVE_REFERENCE_EXPORT_REQUIRED');
  return { workflows, references, targetIds, exportDigest };
}

function assertWorkflow(workflow, expectedRevision, workflowId) {
  if (!workflow || workflow.id !== workflowId || workflow.active === true || workflow.activeVersionId) {
    throw new Error(`LIVE_WORKFLOW_STATE_INVALID:${workflowId}`);
  }
  const revision = String(workflow.versionId || workflow.revisionId || workflow.activeVersionId || '');
  if (revision !== expectedRevision) throw new Error(`LIVE_WORKFLOW_REVISION_MISMATCH:${workflowId}`);
  if (!Array.isArray(workflow.nodes)) throw new Error(`LIVE_WORKFLOW_NODES_INVALID:${workflowId}`);
  return revision;
}

async function loadWorkflows(repository, graph, strict = true) {
  const loaded = new Map();
  for (const [workflowId, revision] of graph.workflows) {
    const workflow = await repository.findOne({
      where: { id: workflowId },
      select: ['id', 'active', 'activeVersionId', 'versionId', 'nodes', 'meta', 'settings'],
    });
    if (strict) assertWorkflow(workflow, revision, workflowId);
    else if (!workflow || workflow.id !== workflowId || workflow.active === true || workflow.activeVersionId || !Array.isArray(workflow.nodes)) {
      throw new Error(`LIVE_WORKFLOW_POST_STATE_INVALID:${workflowId}`);
    }
    loaded.set(workflowId, workflow);
  }
  return loaded;
}

function findReferences(graph, workflows) {
  const prestate = [];
  for (const reference of graph.references.values()) {
    const workflow = workflows.get(reference.workflow_id);
    const node = workflow.nodes.find((candidate) => candidate.id === reference.node_id);
    if (!node || node.name !== reference.node_name) throw new Error(`LIVE_REFERENCE_NODE_INVALID:${reference.reference_id}`);
    const observed = selectorId(node.parameters?.dataTableId);
    const oldMatches = observed === reference.old_table_id || observed === reference.old_table_name;
    const targetMatches = reference.canonical_table_id === null
      ? !node.parameters?.dataTableId
      : observed === reference.canonical_table_id;
    prestate.push({
      reference,
      workflow,
      node,
      observed,
      oldMatches,
      targetMatches,
      selector: clone(node.parameters?.dataTableId),
    });
  }
  return prestate;
}

function applyForward(prestate) {
  const changed = new Map();
  let alreadyApplied = true;
  for (const item of prestate) {
    if (!item.targetMatches) alreadyApplied = false;
    if (!item.oldMatches && !item.targetMatches) {
      throw new Error(`LIVE_REFERENCE_SELECTOR_DRIFT:${item.reference.reference_id}`);
    }
    if (!item.targetMatches) {
      const nodes = changed.get(item.reference.workflow_id) || clone(item.workflow.nodes);
      const node = nodes.find((candidate) => candidate.id === item.reference.node_id);
      setSelector(node, item.reference.canonical_table_id);
      changed.set(item.reference.workflow_id, nodes);
    }
  }
  return { changed, alreadyApplied };
}

async function verifyTargets(service, targetIds) {
  const listed = await service.getManyAndCount({ filter: { projectId }, take: 100 });
  const tables = listed.data.filter((table) => TARGET_NAMES.has(String(table.name)));
  if (tables.length !== 4 || new Set(tables.map((table) => table.id)).size !== 4) throw new Error('EXACT_TARGET_READBACK_REQUIRED');
  for (const table of tables) {
    if (table.id !== targetIds.get(table.name)) throw new Error(`TARGET_ID_READBACK_MISMATCH:${table.name}`);
  }
}

async function acquireProjectLock() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  const lockReceipt = decode('FINANCE_FOUR_TABLE_LOCK_B64');
  if (lockReceipt.schema_version !== 'finance-four-table-writer-lock-v1' ||
      lockReceipt.lock_name !== 'finance_four_table_cutover' ||
      lockReceipt.project_id !== projectId ||
      lockReceipt.export_sha256 !== exported.export_sha256 ||
      lockReceipt.source_backup_sha256 !== process.env.FINANCE_FOUR_TABLE_SOURCE_SHA256 ||
      lockReceipt.held !== true || lockReceipt.in_flight !== 0) {
    throw new Error('WRITER_LOCK_RECEIPT_BINDING_INVALID');
  }
  const unsigned = { ...lockReceipt };
  delete unsigned.lock_receipt_sha256;
  if (digest(unsigned) !== lockReceipt.lock_receipt_sha256) {
    throw new Error('WRITER_LOCK_RECEIPT_INTEGRITY_INVALID');
  }
  const resource = `finance_four_table_cutover:${projectId}:${exported.export_sha256}:${process.env.FINANCE_FOUR_TABLE_SOURCE_SHA256 || ''}`;
  if (lockReceipt.resource_key !== resource) throw new Error('WRITER_LOCK_RESOURCE_MISMATCH');
  const pool = new pg.Pool(databaseOptions(process.env));
  const client = await pool.connect();
  await client.query('BEGIN');
  const result = await client.query('SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0)) AS acquired', [resource]);
  if (result.rows?.[0]?.acquired !== true) {
    await client.query('ROLLBACK');
    client.release();
    await pool.end();
    throw new Error('PROJECT_WRITER_LOCK_BUSY');
  }
  return { pool, client, resource };
}

async function execute() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  const graph = validateExport(exported);
  const forwardReceipt = operation === 'ROLLBACK' ? decode('FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64') : null;
  const lock = await acquireProjectLock();
  const workflowRepository = Container.get(WorkflowRepository);
  const dataTableService = Container.get(DataTableService);
  let workflows;
  const originalNodes = new Map();
  const mutated = [];
  try {
    await verifyTargets(dataTableService, graph.targetIds);
    workflows = await loadWorkflows(workflowRepository, graph);
    if (operation === 'FORWARD') {
      const prestate = findReferences(graph, workflows);
      const plan = applyForward(prestate);
      if (plan.alreadyApplied) {
        return { schema_version: RUNTIME_SCHEMA, operation, project_id: projectId, lock_resource: lock.resource, action_count: 33, replay_noop: true, actions: prestate.map((item) => ({ reference_id: item.reference.reference_id, workflow_id: item.reference.workflow_id, revision_id: item.reference.revision_id, node_id: item.reference.node_id, canonical_table_id: item.reference.canonical_table_id })) };
      }
      for (const [workflowId, nodes] of plan.changed) {
        originalNodes.set(workflowId, clone(workflows.get(workflowId).nodes));
        await workflowRepository.update(workflowId, { nodes });
        mutated.push(workflowId);
      }
      const updated = await loadWorkflows(workflowRepository, graph, false);
      const actions = prestate.map((item) => {
        const workflow = updated.get(item.reference.workflow_id);
        const node = workflow.nodes.find((candidate) => candidate.id === item.reference.node_id);
        const expected = item.reference.canonical_table_id;
        if (selectorId(node.parameters?.dataTableId) !== (expected || '')) throw new Error(`LIVE_REFERENCE_POST_READBACK_MISMATCH:${item.reference.reference_id}`);
        return { reference_id: item.reference.reference_id, workflow_id: item.reference.workflow_id, revision_id: item.reference.revision_id, post_revision_id: String(workflow.versionId || workflow.revisionId || ''), node_id: item.reference.node_id, selector: item.selector, canonical_table_id: expected };
      });
      const unsigned = { schema_version: RUNTIME_SCHEMA, operation, project_id: projectId, lock_resource: lock.resource, action_count: 33, replay_noop: false, actions };
      return { ...unsigned, runtime_plan_receipt_sha256: digest(unsigned) };
    }
    if (!forwardReceipt || forwardReceipt.schema_version !== RUNTIME_SCHEMA || forwardReceipt.operation !== 'FORWARD') throw new Error('FORWARD_RUNTIME_RECEIPT_REQUIRED');
    const unsigned = { ...forwardReceipt };
    delete unsigned.runtime_plan_receipt_sha256;
    if (digest(unsigned) !== forwardReceipt.runtime_plan_receipt_sha256 || forwardReceipt.action_count !== 33) throw new Error('FORWARD_RUNTIME_RECEIPT_INTEGRITY_INVALID');
    const byId = new Map(forwardReceipt.actions.map((action) => [action.reference_id, action]));
    if (byId.size !== 33) throw new Error('FORWARD_RUNTIME_ACTION_COUNT_INVALID');
    const prestate = findReferences(graph, workflows);
    const changed = new Map();
    for (const item of prestate) {
      const action = byId.get(item.reference.reference_id);
      if (!action || action.workflow_id !== item.reference.workflow_id || action.node_id !== item.reference.node_id) throw new Error(`FORWARD_RUNTIME_ACTION_MISMATCH:${item.reference.reference_id}`);
      const currentRevision = String(item.workflow.versionId || item.workflow.revisionId || '');
      if (currentRevision !== action.post_revision_id) throw new Error(`ROLLBACK_WORKFLOW_REVISION_MISMATCH:${item.reference.reference_id}`);
      const nodes = changed.get(item.reference.workflow_id) || clone(item.workflow.nodes);
      const node = nodes.find((candidate) => candidate.id === item.reference.node_id);
      if (selectorId(node.parameters?.dataTableId) !== (item.reference.canonical_table_id || '')) throw new Error(`ROLLBACK_REFERENCE_SELECTOR_MISMATCH:${item.reference.reference_id}`);
      restoreSelector(node, action.selector);
      changed.set(item.reference.workflow_id, nodes);
    }
    for (const [workflowId, nodes] of changed) {
      originalNodes.set(workflowId, clone(workflows.get(workflowId).nodes));
      await workflowRepository.update(workflowId, { nodes });
      mutated.push(workflowId);
    }
    return { schema_version: RUNTIME_SCHEMA, operation, project_id: projectId, lock_resource: lock.resource, action_count: 33, replay_noop: false, actions: [...byId.values()].map((action) => ({ reference_id: action.reference_id, workflow_id: action.workflow_id, node_id: action.node_id, restored: true })) };
  } catch (error) {
    for (const workflowId of mutated.reverse()) {
      await workflowRepository.update(workflowId, { nodes: originalNodes.get(workflowId) }).catch(() => {});
    }
    throw error;
  } finally {
    await lock.client.query('COMMIT').catch(async () => { await lock.client.query('ROLLBACK').catch(() => {}); });
    lock.client.release();
    await lock.pool.end();
  }
}

let completed = false;
const originalInit = BaseCommand.prototype.init;
BaseCommand.prototype.init = async function financeFourTableCutover(...args) {
  await originalInit.apply(this, args);
  const receipt = await execute();
  process.stdout.write(`finance four-table runtime verified:${JSON.stringify(receipt)}\n`);
  completed = true;
};
ListWorkflowCommand.prototype.run = async function suppressWorkflowCommand() {
  if (!completed) throw new Error('FOUR_TABLE_RUNTIME_DID_NOT_COMPLETE');
};
require(path.join(n8nRoot, 'bin', 'n8n'));
