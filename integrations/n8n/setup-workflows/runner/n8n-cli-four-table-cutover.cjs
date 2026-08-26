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
const n8nPackageRoot = process.env.FINANCE_FOUR_TABLE_N8N_ROOT || '/usr/local/lib/node_modules';
const n8nPackageJson = require.resolve('n8n/package.json', { paths: [n8nPackageRoot] });
const n8nRoot = path.dirname(n8nPackageJson);
process.env.NODE_CONFIG_DIR ||= path.join(n8nRoot, 'bin', 'config');
const n8nRequire = createRequire(n8nPackageJson);
const { Container } = n8nRequire('@n8n/di');
const { BaseCommand } = n8nRequire('./dist/commands/base-command.js');
const { ListWorkflowCommand } = n8nRequire('./dist/commands/list/workflow.js');
const { DataTableService } = n8nRequire('./dist/modules/data-table/data-table.service.js');
const pg = n8nRequire('pg');

const EXPORT_SCHEMA = 'finance-four-table-live-export-v1';
const RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v1';
const JOURNAL_TABLE = 'finance_four_table_cutover_journal';
const TARGET_NAMES = new Set([
  'finance_ingestion_state',
  'finance_documents',
  'finance_actual_batches',
  'finance_ai_reviews',
]);
const LEGACY_TABLE_IDS = new Map([
  ['finance_source_contracts', 'sha256:73b62207'],
  ['finance_source_cursors', 'sha256:60e428cd'],
  ['finance_archive_receipts', 'sha256:49bf4e32'],
  ['finance_document_operations', 'sha256:2ad2a52a'],
  ['finance_pipeline_runs', 'sha256:48eb19e5'],
  ['finance_reconciliations', 'sha256:f47bf1e1'],
  ['finance_mcp_requests', 'sha256:3b9034f0'],
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

function bindingFromEnvironment(exported) {
  const binding = {
    operation_nonce: text(process.env.FINANCE_FOUR_TABLE_OPERATION_NONCE, 'OPERATION_NONCE_REQUIRED'),
    protected_quiescence_receipt_digest: text(
      process.env.FINANCE_FOUR_TABLE_PROTECTED_QUIESCENCE_RECEIPT_DIGEST,
      'PROTECTED_QUIESCENCE_RECEIPT_DIGEST_REQUIRED',
    ),
    required_live_export_digest: text(
      process.env.FINANCE_FOUR_TABLE_REQUIRED_LIVE_EXPORT_DIGEST,
      'REQUIRED_LIVE_EXPORT_DIGEST_REQUIRED',
    ),
    contract_bijection_digest: text(
      process.env.FINANCE_FOUR_TABLE_CONTRACT_BIJECTION_DIGEST,
      'CONTRACT_BIJECTION_DIGEST_REQUIRED',
    ),
  };
  for (const [field, value] of Object.entries(binding)) {
    if (field !== 'operation_nonce' && !/^[0-9a-f]{64}$/.test(value)) {
      throw new Error(`${field.toUpperCase()}_INVALID`);
    }
  }
  return binding;
}

function validateBinding(value, binding, code) {
  for (const [field, expected] of Object.entries(binding)) {
    if (!value || value[field] !== expected) throw new Error(`${code}_${field.toUpperCase()}_MISMATCH`);
  }
}

function selectorId(selector) {
  if (selector && typeof selector === 'object' && selector.__rl === true) return String(selector.value || '');
  return typeof selector === 'string' ? selector : '';
}

function sameJson(left, right) {
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
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
  const nodeAliases = new Set();
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
    if (reference.old_table_id !== LEGACY_TABLE_IDS.get(reference.old_table_name)) {
      throw new Error(`LIVE_REFERENCE_OLD_TABLE_ID_CONFLICT:${id}`);
    }
    const nodeKey = `${reference.workflow_id}:${text(reference.node_id, 'LIVE_REFERENCE_NODE_ID_INVALID')}`;
    if (nodeAliases.has(nodeKey)) throw new Error(`LIVE_REFERENCE_NODE_ALIAS_CONFLICT:${id}`);
    nodeAliases.add(nodeKey);
    references.set(id, reference);
  }
  if (references.size !== 33) throw new Error('COMPLETE_LIVE_REFERENCE_EXPORT_REQUIRED');
  const legacyTables = new Set([...references.values()].map((reference) => reference.old_table_name));
  if (legacyTables.size !== LEGACY_TABLE_IDS.size || [...legacyTables].some((name) => !LEGACY_TABLE_IDS.has(name))) {
    throw new Error('EXACT_SEVEN_LEGACY_TABLE_ID_MAP_REQUIRED');
  }
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

async function loadWorkflows(client, graph, strict = true) {
  const loaded = new Map();
  const workflowIds = [...graph.workflows.keys()];
  const result = await client.query(
    `SELECT w.id, w.active, w."activeVersionId", w."versionId", w.nodes, w.meta, w.settings
       FROM workflow_entity w
       JOIN shared_workflow s ON s."workflowId" = w.id
      WHERE s."projectId" = $1 AND s.role = 'workflow:owner' AND w.id = ANY($2::text[])
      FOR UPDATE`,
    [projectId, workflowIds],
  );
  const rows = new Map(result.rows.map((workflow) => [workflow.id, workflow]));
  for (const [workflowId, revision] of graph.workflows) {
    const workflow = rows.get(workflowId);
    if (strict) assertWorkflow(workflow, revision, workflowId);
    else if (!workflow || workflow.id !== workflowId || workflow.active === true || workflow.activeVersionId || !Array.isArray(workflow.nodes)) {
      throw new Error(`LIVE_WORKFLOW_POST_STATE_INVALID:${workflowId}`);
    }
    loaded.set(workflowId, workflow);
  }
  return loaded;
}

async function updateWorkflows(client, changes) {
  for (const [workflowId, nodes] of changes) {
    const revisionId = crypto.randomUUID();
    const result = await client.query(
      `UPDATE workflow_entity w
          SET nodes = $1::json, "versionId" = $3
        WHERE w.id = $2
          AND EXISTS (
            SELECT 1 FROM shared_workflow s
             WHERE s."workflowId" = w.id
               AND s."projectId" = $4
               AND s.role = 'workflow:owner'
          )
      RETURNING w.id, w."versionId"`,
      [JSON.stringify(nodes), workflowId, revisionId, projectId],
    );
    if (result.rowCount !== 1 || result.rows[0].versionId !== revisionId) {
      throw new Error(`LIVE_WORKFLOW_UPDATE_FAILED:${workflowId}`);
    }
    if (process.env.FINANCE_FOUR_TABLE_INJECT_FAILURE_AFTER_UPDATE === workflowId) {
      throw new Error(`INJECTED_FAILURE_AFTER_UPDATE:${workflowId}`);
    }
  }
}

function findReferences(graph, workflows) {
  const prestate = [];
  const nodeAliases = new Set();
  for (const reference of graph.references.values()) {
    const workflow = workflows.get(reference.workflow_id);
    const nodeKey = `${reference.workflow_id}:${reference.node_id}`;
    if (nodeAliases.has(nodeKey)) throw new Error(`LIVE_REFERENCE_NODE_ALIAS_CONFLICT:${reference.reference_id}`);
    nodeAliases.add(nodeKey);
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

function selectorReadback(prestate) {
  return prestate.map((item) => ({
    reference_id: item.reference.reference_id,
    workflow_id: item.reference.workflow_id,
    node_id: item.reference.node_id,
    selector: clone(item.node.parameters?.dataTableId),
    selector_id: selectorId(item.node.parameters?.dataTableId),
  })).sort((left, right) => left.reference_id.localeCompare(right.reference_id));
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

async function verifyInFlight(client) {
  const result = await client.query(
    `SELECT COUNT(*)::int AS count
       FROM execution_entity e
       JOIN shared_workflow s ON s."workflowId" = e."workflowId"
      WHERE s."projectId" = $1 AND e.finished IS NOT TRUE`,
    [projectId],
  );
  if (result.rows[0]?.count !== 0) throw new Error('LIVE_IN_FLIGHT_EXECUTIONS_PRESENT');
}

async function persistRecoveryJournal(client, receipt) {
  await client.query(
    `CREATE TABLE IF NOT EXISTS ${JOURNAL_TABLE} (
       receipt_sha256 varchar(64) PRIMARY KEY,
       project_id varchar(64) NOT NULL,
       operation varchar(16) NOT NULL,
       lock_resource varchar(128) NOT NULL,
       receipt jsonb NOT NULL,
       created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
     )`,
  );
  await client.query(
    `INSERT INTO ${JOURNAL_TABLE}
       (receipt_sha256, project_id, operation, lock_resource, receipt)
     VALUES ($1, $2, $3, $4, $5::jsonb)
     ON CONFLICT (receipt_sha256) DO NOTHING`,
    [
      receipt.runtime_plan_receipt_sha256,
      receipt.project_id,
      receipt.operation,
      receipt.lock_resource,
      JSON.stringify(receipt),
    ],
  );
}

function validateLockReceipt(lockReceipt, exported, binding, resource) {
  if (!lockReceipt || lockReceipt.schema_version !== 'finance-four-table-writer-lock-v1' ||
      lockReceipt.lock_name !== 'finance_four_table_cutover' ||
      lockReceipt.project_id !== projectId ||
      lockReceipt.export_sha256 !== exported.export_sha256 ||
      lockReceipt.migration_receipt_sha256 !== process.env.FINANCE_FOUR_TABLE_MIGRATION_SHA256 ||
      lockReceipt.source_backup_sha256 !== process.env.FINANCE_FOUR_TABLE_SOURCE_SHA256 ||
      lockReceipt.accepted_identity_sha256 !== process.env.FINANCE_FOUR_TABLE_IDENTITY_SHA256 ||
      lockReceipt.held !== true || lockReceipt.in_flight !== 0) {
    throw new Error('WRITER_LOCK_RECEIPT_BINDING_INVALID');
  }
  validateBinding(lockReceipt, binding, 'WRITER_LOCK_RECEIPT');
  const unsigned = { ...lockReceipt };
  delete unsigned.lock_receipt_sha256;
  if (digest(unsigned) !== lockReceipt.lock_receipt_sha256) {
    throw new Error('WRITER_LOCK_RECEIPT_INTEGRITY_INVALID');
  }
  if (lockReceipt.resource_key !== resource) throw new Error('WRITER_LOCK_RESOURCE_MISMATCH');
}

function validateForwardReceipt(receipt, exported, resource, binding) {
  if (!receipt || receipt.schema_version !== RUNTIME_SCHEMA || receipt.operation !== 'FORWARD' ||
      receipt.project_id !== projectId || receipt.lock_resource !== resource ||
      receipt.export_sha256 !== exported.export_sha256 || receipt.action_count !== 33 ||
      receipt.durable_journal !== true || receipt.commit_protocol !== 'postgresql_synchronous_wal' ||
      receipt.readback_verified !== true || typeof receipt.readback_digest_sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/.test(receipt.readback_digest_sha256) || !Array.isArray(receipt.actions)) {
    throw new Error('FORWARD_RUNTIME_RECEIPT_INTEGRITY_INVALID');
  }
  validateBinding(receipt, binding, 'FORWARD_RUNTIME_RECEIPT');
  const unsigned = { ...receipt };
  delete unsigned.runtime_plan_receipt_sha256;
  if (digest(unsigned) !== receipt.runtime_plan_receipt_sha256 ||
      new Map(receipt.actions.map((action) => [action.reference_id, action])).size !== 33) {
    throw new Error('FORWARD_RUNTIME_RECEIPT_INTEGRITY_INVALID');
  }
  return receipt;
}

async function recoverForwardJournal() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  validateExport(exported);
  const binding = bindingFromEnvironment(exported);
  const lockReceipt = decode('FINANCE_FOUR_TABLE_LOCK_B64');
  const resource = `finance_four_table_cutover:${projectId}`;
  validateLockReceipt(lockReceipt, exported, binding, resource);
  const pool = new pg.Pool(databaseOptions(process.env));
  let client;
  try {
    client = await pool.connect();
    const result = await client.query(
      `SELECT receipt
         FROM ${JOURNAL_TABLE}
        WHERE project_id = $1
          AND lock_resource = $2
          AND operation = $3
          AND receipt->>'export_sha256' = $4
        ORDER BY created_at DESC
        LIMIT 1`,
      [projectId, resource, 'FORWARD', exported.export_sha256],
    );
    const row = result.rows?.[0];
    if (!row) throw new Error('FORWARD_RUNTIME_JOURNAL_NOT_FOUND');
    const receipt = typeof row.receipt === 'string' ? JSON.parse(row.receipt) : row.receipt;
    await writeRuntimeReceipt(validateForwardReceipt(receipt, exported, resource, binding));
  } finally {
    client?.release();
    await pool.end();
  }
}

async function acquireProjectLock() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  const lockReceipt = decode('FINANCE_FOUR_TABLE_LOCK_B64');
  const resource = `finance_four_table_cutover:${projectId}`;
  const binding = bindingFromEnvironment(exported);
  validateLockReceipt(lockReceipt, exported, binding, resource);
  const pool = new pg.Pool(databaseOptions(process.env));
  let client;
  try {
    client = await pool.connect();
    await client.query('BEGIN');
    // Require the commit to flush PostgreSQL WAL before releasing the project lock.
    await client.query("SET LOCAL synchronous_commit = 'on'");
    const result = await client.query('SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0)) AS acquired', [resource]);
    if (result.rows?.[0]?.acquired !== true) throw new Error('PROJECT_WRITER_LOCK_BUSY');
    return { pool, client, resource, binding };
  } catch (error) {
    // BEGIN, SET, and lock acquisition all happen before execute() owns cleanup.
    if (client) await client.query('ROLLBACK').catch(() => {});
    client?.release();
    await pool.end().catch(() => {});
    throw error;
  }
}

async function execute() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  const graph = validateExport(exported);
  const forwardReceipt = operation === 'ROLLBACK' ? decode('FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64') : null;
  const lock = await acquireProjectLock();
  const dataTableService = Container.get(DataTableService);
  const commitAndJournal = async (unsignedReceipt) => {
    const journal = {
      ...unsignedReceipt,
      ...lock.binding,
      durable_journal: true,
      commit_protocol: 'postgresql_synchronous_wal',
    };
    validateBinding(journal, lock.binding, 'RUNTIME_JOURNAL');
    await persistRecoveryJournal(lock.client, journal);
    await lock.client.query('COMMIT');
    // COMMIT releases the transaction-scoped advisory lock before this output.
    // The committed journal is the recovery boundary for this read-only step.
    await writeRuntimeReceipt(journal);
    return journal;
  };
  try {
    await verifyInFlight(lock.client);
    await verifyTargets(dataTableService, graph.targetIds);
    const workflows = await loadWorkflows(lock.client, graph, operation === 'FORWARD');
    if (operation === 'FORWARD') {
      const prestate = findReferences(graph, workflows);
      const plan = applyForward(prestate);
      if (!plan.alreadyApplied) {
        await updateWorkflows(lock.client, plan.changed);
      }
      const updated = await loadWorkflows(lock.client, graph, false);
      const poststate = findReferences(graph, updated);
      const beforeById = new Map(prestate.map((item) => [item.reference.reference_id, item]));
      const actions = poststate.map((item) => {
        const before = beforeById.get(item.reference.reference_id);
        const expected = item.reference.canonical_table_id;
        if (selectorId(item.node.parameters?.dataTableId) !== (expected || '')) throw new Error(`LIVE_REFERENCE_POST_READBACK_MISMATCH:${item.reference.reference_id}`);
        return { reference_id: item.reference.reference_id, workflow_id: item.reference.workflow_id, revision_id: item.reference.revision_id, post_revision_id: String(updated.get(item.reference.workflow_id).versionId || updated.get(item.reference.workflow_id).revisionId || ''), node_id: item.reference.node_id, selector: before.selector, canonical_table_id: expected };
      });
      const readback = selectorReadback(poststate);
      const replayState = findReferences(graph, await loadWorkflows(lock.client, graph, false));
      const replayPlan = applyForward(replayState);
      if (!replayPlan.alreadyApplied || !sameJson(selectorReadback(replayState), readback)) {
        throw new Error('FORWARD_REPLAY_READBACK_MISMATCH');
      }
      const unsigned = { schema_version: RUNTIME_SCHEMA, operation, project_id: projectId, lock_resource: lock.resource, export_sha256: exported.export_sha256, ...lock.binding, action_count: 33, replay_noop: true, readback_verified: true, readback_digest_sha256: digest(readback), actions };
      return commitAndJournal({ ...unsigned, runtime_plan_receipt_sha256: digest({ ...unsigned, durable_journal: true, commit_protocol: 'postgresql_synchronous_wal' }) });
    }
    if (!forwardReceipt || forwardReceipt.schema_version !== RUNTIME_SCHEMA || forwardReceipt.operation !== 'FORWARD') throw new Error('FORWARD_RUNTIME_RECEIPT_REQUIRED');
    if (forwardReceipt.project_id !== projectId || forwardReceipt.lock_resource !== lock.resource) {
      throw new Error('FORWARD_RUNTIME_RECEIPT_BINDING_INVALID');
    }
    validateForwardReceipt(forwardReceipt, exported, lock.resource, lock.binding);
    const byId = new Map(forwardReceipt.actions.map((action) => [action.reference_id, action]));
    const prestate = findReferences(graph, workflows);
    const changed = new Map();
    for (const item of prestate) {
      const action = byId.get(item.reference.reference_id);
      if (!action || action.workflow_id !== item.reference.workflow_id || action.revision_id !== item.reference.revision_id ||
          action.node_id !== item.reference.node_id || action.canonical_table_id !== item.reference.canonical_table_id) {
        throw new Error(`FORWARD_RUNTIME_ACTION_MISMATCH:${item.reference.reference_id}`);
      }
      const currentRevision = String(item.workflow.versionId || item.workflow.revisionId || '');
      if (currentRevision !== action.post_revision_id) throw new Error(`ROLLBACK_WORKFLOW_REVISION_MISMATCH:${item.reference.reference_id}`);
      const nodes = changed.get(item.reference.workflow_id) || clone(item.workflow.nodes);
      const node = nodes.find((candidate) => candidate.id === item.reference.node_id);
      if (selectorId(node.parameters?.dataTableId) !== (item.reference.canonical_table_id || '')) throw new Error(`ROLLBACK_REFERENCE_SELECTOR_MISMATCH:${item.reference.reference_id}`);
      restoreSelector(node, action.selector);
      changed.set(item.reference.workflow_id, nodes);
    }
    await updateWorkflows(lock.client, changed);
    const restored = await loadWorkflows(lock.client, graph, false);
    const restoredState = findReferences(graph, restored);
    for (const item of restoredState) {
      const action = byId.get(item.reference.reference_id);
      if (!sameJson(item.node.parameters?.dataTableId, action.selector)) {
        throw new Error(`ROLLBACK_POST_READBACK_MISMATCH:${item.reference.reference_id}`);
      }
    }
    const readback = selectorReadback(restoredState);
    const unsignedRollback = { schema_version: RUNTIME_SCHEMA, operation, project_id: projectId, lock_resource: lock.resource, export_sha256: exported.export_sha256, ...lock.binding, action_count: 33, replay_noop: false, readback_verified: true, readback_digest_sha256: digest(readback), actions: [...byId.values()].map((action) => ({ reference_id: action.reference_id, workflow_id: action.workflow_id, node_id: action.node_id, restored: true })) };
    return commitAndJournal({ ...unsignedRollback, runtime_plan_receipt_sha256: digest({ ...unsignedRollback, durable_journal: true, commit_protocol: 'postgresql_synchronous_wal' }) });
  } catch (error) {
    // PostgreSQL transaction rollback is the only compensation path. A second
    // application update could itself fail or create a new revision.
    await lock.client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    lock.client.release();
    await lock.pool.end();
  }
}

async function writeRuntimeReceipt(receipt) {
  if (process.env.FINANCE_FOUR_TABLE_INJECT_RECEIPT_FAILURE === '1') {
    throw new Error('INJECTED_RECEIPT_FAILURE');
  }
  const line = `finance four-table runtime verified:${JSON.stringify(receipt)}\n`;
  if (process.stdout.write(line)) return;
  await new Promise((resolve, reject) => {
    process.stdout.once('drain', resolve);
    process.stdout.once('error', reject);
  });
}

let completed = false;
const originalInit = BaseCommand.prototype.init;
BaseCommand.prototype.init = async function financeFourTableCutover(...args) {
  await originalInit.apply(this, args);
  if (process.env.FINANCE_FOUR_TABLE_RECOVER_JOURNAL === '1') {
    if (operation !== 'FORWARD') throw new Error('FORWARD_JOURNAL_RECOVERY_ONLY');
    await recoverForwardJournal();
  } else {
    await execute();
  }
  completed = true;
};
ListWorkflowCommand.prototype.run = async function suppressWorkflowCommand() {
  if (!completed) throw new Error('FOUR_TABLE_RUNTIME_DID_NOT_COMPLETE');
};
require(path.join(n8nRoot, 'bin', 'n8n'));
