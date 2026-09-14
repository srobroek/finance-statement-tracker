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
const { createRequire } = require('node:module');
const n8nPackageRoot = process.env.FINANCE_FOUR_TABLE_N8N_ROOT || '/usr/local/lib/node_modules';
const n8nPackageJson = require.resolve('n8n/package.json', { paths: [n8nPackageRoot] });
const pg = createRequire(n8nPackageJson)('pg');

const EXPORT_SCHEMA = 'finance-four-table-live-export-v1';
const CANONICAL_SOURCE_SCHEMA = 'finance-four-table-canonical-source-v2';
const LEGACY_RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v1';
const PREVIOUS_RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v2';
const RUNTIME_SCHEMA = 'finance-four-table-runtime-plan-v3';
const JOURNAL_TABLE = 'finance_four_table_cutover_journal';
const APPROVED_LOCK_TIMEOUT_MS = 5_000;
const APPROVED_STATEMENT_TIMEOUT_MS = 30_000;
const FORWARD_RECOVERY_REASON = 'FORWARD_RUNTIME_FAILURE';
const ROLLBACK_RECOVERY_REASON = 'ROLLBACK_RUNTIME_FAILURE';
const APPROVED_LEGACY_REFERENCE_INVENTORY_SHA256 = 'dd8dc76fa2b46adf894f3ae9896f9ea7ed9836fb7442be7eefd3428498e5148c';
const TARGET_NAMES = new Set([
  'finance_ingestion_state',
  'finance_documents',
  'finance_actual_batches',
  'finance_ai_reviews',
]);
const TARGET_LOGICAL_KEYS = new Map([
  ['finance_actual_batches', ['idempotency_key']],
  ['finance_ai_reviews', ['idempotency_key']],
  ['finance_documents', ['document_id']],
  ['finance_ingestion_state', ['source_code']],
]);
const TARGET_COLUMN_TYPES = new Set(['string', 'number', 'boolean', 'date']);
const TARGET_SYSTEM_COLUMNS = ['id', 'createdAt', 'updatedAt'];
const LEGACY_TABLE_IDS = new Map([
  ['finance_source_contracts', 'sha256:73b62207'],
  ['finance_source_cursors', 'sha256:60e428cd'],
  ['finance_archive_receipts', 'sha256:49bf4e32'],
  ['finance_document_operations', 'sha256:2ad2a52a'],
  ['finance_pipeline_runs', 'sha256:48eb19e5'],
  ['finance_reconciliations', 'sha256:f47bf1e1'],
  ['finance_mcp_requests', 'sha256:3b9034f0'],
  ['finance_execution_failures', 'sha256:59c34ab8'],
]);
const COMPATIBILITY_TABLE_NAMES = new Set([
  ...LEGACY_TABLE_IDS.keys(),
  'finance_acquisition_receipts',
  'finance_actual_outbox',
  'finance_actual_verifications',
  'finance_config_versions',
  'finance_provider_circuits',
  'finance_execution_failures',
  'finance_agent_jobs',
  'finance_ai_policy_contracts',
  ...TARGET_NAMES,
]);
const LIVE_EXPORT_FIELDS = new Set([
  'schema_version',
  'export_sha256',
  'repository_root',
  'project_id',
  'source_head',
  'generator_head',
  'migration_receipt_sha256',
  'source_backup_sha256',
  'accepted_identity_sha256',
  'redacted',
  'workflow_count',
  'in_flight',
  'workflows',
  'targets',
  'references',
]);
const WORKFLOW_SEMANTIC_FIELDS = ['workflow_id', 'active', 'published', 'in_flight', 'workflow_body_sha256'];
const TARGET_SEMANTIC_FIELDS = ['name', 'table_id', 'schema_sha256'];
const REFERENCE_SEMANTIC_FIELDS = [
  'reference_id',
  'workflow_id',
  'workflow_path',
  'node_id',
  'node_name',
  'operation',
  'old_table_name',
  'old_table_id',
  'canonical_table_name',
  'canonical_table_id',
  'active',
  'published',
  'in_flight',
];
const WORKFLOW_BODY_FIELDS = ['name', 'nodes', 'connections', 'settings', 'meta', 'pinData'];
const CREDENTIAL_BINDINGS_SCHEMA = 1;
const PRESERVED_SOURCE_TABLE = 'finance_source_contracts';
const PRESERVED_OPERATIONAL_SELECTOR_NAMES = new Set([
  PRESERVED_SOURCE_TABLE,
  'finance_pipeline_runs',
  'finance_mcp_requests',
  'finance_execution_failures',
]);
const PRESERVED_OPERATIONAL_SELECTOR_IDS = new Set(
  [...PRESERVED_OPERATIONAL_SELECTOR_NAMES]
    .map((name) => LEGACY_TABLE_IDS.get(name))
    .filter((id) => typeof id === 'string'),
);


function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function canonical(value) {
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}
function canonicalTargetValue(value, type, code) {
  if (value === null) return null;
  if (type === 'string' && typeof value === 'string') return value;
  if (type === 'number' && typeof value === 'number' && Number.isFinite(value)) return value;
  if (type === 'boolean' && typeof value === 'boolean') return value;
  if (type === 'date' && typeof value === 'string') {
    const explicit = /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? `${value}T00:00:00.000Z`
      : value;
    const zoned = /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(explicit)
      ? explicit
      : `${explicit}Z`;
    const parsed = new Date(zoned);
    if (Number.isFinite(parsed.valueOf())) return parsed.toISOString();
  }
  throw new Error(code);
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
function decodedSha256(name) {
  const encoded = process.env[name];
  if (typeof encoded !== 'string' || encoded.length === 0) throw new Error(`${name}_REQUIRED`);
  return crypto.createHash('sha256').update(Buffer.from(encoded, 'base64')).digest('hex');
}

function text(value, code) {
  if (typeof value !== 'string' || value.length === 0) throw new Error(code);
  return value;
}

function digestText(value, code) {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) throw new Error(code);
  return value;
}

function credentialBindingsFromEnvironment() {
  const encoded = process.env.FINANCE_FOUR_TABLE_CREDENTIAL_BINDINGS_B64;
  if (typeof encoded !== 'string' || encoded.length === 0) throw new Error('CREDENTIAL_BINDINGS_REQUIRED');
  const raw = Buffer.from(encoded, 'base64');
  const approvedSha256 = digestText(
    process.env.FINANCE_FOUR_TABLE_CREDENTIAL_BINDINGS_SHA256,
    'CREDENTIAL_BINDINGS_SHA256_INVALID',
  );
  if (crypto.createHash('sha256').update(raw).digest('hex') !== approvedSha256) {
    throw new Error('CREDENTIAL_BINDINGS_CURRENTNESS_DRIFT');
  }
  let contract;
  try { contract = JSON.parse(raw.toString('utf8')); } catch { throw new Error('CREDENTIAL_BINDINGS_INVALID'); }
  if (!contract || typeof contract !== 'object' || Array.isArray(contract) ||
      Object.keys(contract).sort().join(',') !== 'bindings,schema_version,source,workflow_code_metadata_key' ||
      contract.schema_version !== CREDENTIAL_BINDINGS_SCHEMA || contract.workflow_code_metadata_key !== 'financeWorkflowCode' ||
      !contract.source || typeof contract.source !== 'object' || Array.isArray(contract.source) ||
      Object.keys(contract.source).sort().join(',') !== 'file_count,path,sha256' || contract.source.path !== 'integrations/n8n/workflows' ||
      contract.source.file_count !== 19 || !/^[0-9a-f]{64}$/.test(contract.source.sha256) || !Array.isArray(contract.bindings)) {
    throw new Error('CREDENTIAL_BINDINGS_SCHEMA_INVALID');
  }
  const leaves = new Map();
  const placeholders = new Set();
  for (const binding of contract.bindings) {
    if (!binding || typeof binding !== 'object' || Array.isArray(binding) ||
        !['credential_type,node_type,nodes,placeholder', 'credential_name,credential_type,node_type,nodes,placeholder'].includes(Object.keys(binding).sort().join(','))) {
      throw new Error('CREDENTIAL_BINDING_KEYS_INVALID');
    }
    if (!/^BIND_[A-Z0-9_]+$/.test(text(binding.placeholder, 'CREDENTIAL_PLACEHOLDER_INVALID')) ||
        !text(binding.credential_type, 'CREDENTIAL_TYPE_INVALID') || !text(binding.node_type, 'CREDENTIAL_NODE_TYPE_INVALID') ||
        (binding.credential_name !== undefined && !text(binding.credential_name, 'CREDENTIAL_NAME_INVALID')) ||
        !Array.isArray(binding.nodes) || binding.nodes.length === 0) throw new Error('CREDENTIAL_BINDING_INVALID');
    if (placeholders.has(binding.placeholder)) throw new Error('CREDENTIAL_BINDING_AMBIGUOUS');
    placeholders.add(binding.placeholder);
    for (const item of binding.nodes) {
      const workflow = item?.workflow; const node = item?.node;
      const key = `${workflow?.id}:${node?.id}`;
      if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).sort().join(',') !== 'node,workflow' ||
          !workflow || typeof workflow !== 'object' || Array.isArray(workflow) || Object.keys(workflow).sort().join(',') !== 'code,file,id' ||
          !node || typeof node !== 'object' || Array.isArray(node) || Object.keys(node).sort().join(',') !== 'id,name' ||
          !text(workflow.id, 'CREDENTIAL_WORKFLOW_ID_INVALID') || !text(workflow.code, 'CREDENTIAL_WORKFLOW_CODE_INVALID') ||
          !text(workflow.file, 'CREDENTIAL_WORKFLOW_FILE_INVALID') || !/^\S+\.json$/.test(workflow.file) ||
          !text(node.id, 'CREDENTIAL_NODE_ID_INVALID') || !text(node.name, 'CREDENTIAL_NODE_NAME_INVALID') || leaves.has(key)) {
        throw new Error('CREDENTIAL_BINDING_AMBIGUOUS');
      }
      leaves.set(key, { ...binding, workflow, node });
    }
  }
  if (contract.bindings.length !== 9 || leaves.size !== 40) throw new Error('CREDENTIAL_BINDING_COVERAGE_INVALID');
  return contract.bindings;
}

function credentialLeavesFromEnvironment() {
  return credentialBindingsFromEnvironment().flatMap((binding) => binding.nodes.map((item) => ({
    key: `${item.workflow.id}:${item.node.id}`,
    credential_type: binding.credential_type,
    node_type: binding.node_type,
    placeholder: binding.placeholder,
    workflow: item.workflow,
    node: item.node,
  }))).sort((left, right) => `${left.key}:${left.credential_type}`.localeCompare(`${right.key}:${right.credential_type}`));
}

function bindingFromEnvironment() {
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

function provenanceFromEnvironment() {
  const provenance = {
    repository_root: text(process.env.FINANCE_FOUR_TABLE_REPOSITORY_ROOT, 'REPOSITORY_ROOT_REQUIRED'),
    source_head: text(process.env.FINANCE_FOUR_TABLE_SOURCE_HEAD, 'SOURCE_HEAD_REQUIRED'),
    generator_head: text(process.env.FINANCE_FOUR_TABLE_GENERATOR_HEAD, 'GENERATOR_HEAD_REQUIRED'),
    migration_receipt_sha256: text(process.env.FINANCE_FOUR_TABLE_MIGRATION_SHA256, 'MIGRATION_RECEIPT_SHA256_REQUIRED'),
    source_backup_sha256: text(process.env.FINANCE_FOUR_TABLE_SOURCE_SHA256, 'SOURCE_BACKUP_SHA256_REQUIRED'),
    accepted_identity_sha256: text(process.env.FINANCE_FOUR_TABLE_IDENTITY_SHA256, 'IDENTITY_SHA256_REQUIRED'),
  };
  if (!/^[0-9a-f]{40,64}$/.test(provenance.source_head) || !/^[0-9a-f]{40,64}$/.test(provenance.generator_head)) {
    throw new Error('SOURCE_OR_GENERATOR_HEAD_INVALID');
  }
  for (const field of ['migration_receipt_sha256', 'source_backup_sha256', 'accepted_identity_sha256']) {
    if (!/^[0-9a-f]{64}$/.test(provenance[field])) throw new Error(`${field.toUpperCase()}_INVALID`);
  }
  return provenance;
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

function semanticProjection(exported) {
  if (Object.keys(exported).length !== LIVE_EXPORT_FIELDS.size ||
      Object.keys(exported).some((field) => !LIVE_EXPORT_FIELDS.has(field))) {
    throw new Error('LIVE_EXPORT_FIELDS_INVALID');
  }
  if (!Array.isArray(exported.workflows) || !Array.isArray(exported.targets) || !Array.isArray(exported.references)) {
    throw new Error('LIVE_EXPORT_SEMANTIC_COLLECTIONS_INVALID');
  }
  const project = (records, fields, key) => records
    .map((record) => {
      if (!record || typeof record !== 'object' || fields.some((field) => !Object.hasOwn(record, field))) {
        throw new Error('LIVE_EXPORT_SEMANTIC_RECORD_INVALID');
      }
      return Object.fromEntries(fields.map((field) => [field, record[field]]));
    })
    .sort((left, right) => String(left[key]).localeCompare(String(right[key])));
  const workflows = project(exported.workflows, WORKFLOW_SEMANTIC_FIELDS, 'workflow_id');
  for (const workflow of workflows) {
    digestText(workflow.workflow_body_sha256, 'WORKFLOW_BODY_SHA256_INVALID');
  }
  const targets = project(exported.targets, TARGET_SEMANTIC_FIELDS, 'name');
  for (const target of targets) {
    digestText(target.schema_sha256, 'TARGET_SCHEMA_SHA256_INVALID');
  }
  return {
    schema_version: exported.schema_version,
    workflow_count: exported.workflow_count,
    in_flight: exported.in_flight,
    workflows,
    targets,
    references: project(exported.references, REFERENCE_SEMANTIC_FIELDS, 'reference_id'),
  };
}

function workflowBodyProjection(workflow) {
  const body = clone(Object.fromEntries(WORKFLOW_BODY_FIELDS.map((field) => [field, workflow[field]])));
  const leaves = new Map(credentialLeavesFromEnvironment().map((leaf) => [leaf.key, leaf]));
  for (const node of body.nodes || []) {
    const binding = leaves.get(`${workflow.id}:${node.id}`);
    if (!binding || !node.credentials) continue;
    if (Object.keys(node.credentials).length !== 1 || !Object.hasOwn(node.credentials, binding.credential_type)) throw new Error('CREDENTIAL_REFERENCE_INVALID');
    const ref = node.credentials[binding.credential_type];
    if (!ref || typeof ref !== 'object' || Array.isArray(ref) || Object.keys(ref).sort().join(',') !== 'id,name' ||
        !text(ref.id, 'CREDENTIAL_ID_INVALID') || !text(ref.name, 'CREDENTIAL_NAME_INVALID')) throw new Error('CREDENTIAL_REFERENCE_INVALID');
    ref.id = binding.placeholder;
    ref.name = binding.placeholder;
  }
  for (const leaf of leaves.values()) {
    if (leaf.workflow.id === String(workflow.id) && !(body.nodes || []).some((node) => String(node.id) === leaf.node.id)) {
      throw new Error('CREDENTIAL_BINDING_MISSING');
    }
  }
  return body;
}

function workflowBodyDigest(workflow) {
  return digest(workflowBodyProjection(workflow));
}

function setSelector(node, tableId) {
  const parameters = node.parameters && typeof node.parameters === 'object' ? node.parameters : {};
  const selector = parameters.dataTableId;
  if (typeof tableId !== 'string' || !tableId) throw new Error('NULL_TABLE_SELECTOR_GRAPH_REFUSED');
  if (selector && typeof selector === 'object' && selector.__rl === true) {
    parameters.dataTableId = { ...selector, mode: 'id', value: tableId };
  } else {
    parameters.dataTableId = tableId;
  }
  node.parameters = parameters;
}


function transactionTimeouts(env) {
  const exact = (name, approved, code) => {
    const raw = env[name] === undefined ? String(approved) : env[name];
    if (typeof raw !== 'string' || !/^[0-9]+$/.test(raw) || Number(raw) !== approved) {
      throw new Error(code);
    }
    return approved;
  };
  return {
    lockTimeoutMs: exact('FINANCE_FOUR_TABLE_LOCK_TIMEOUT_MS', APPROVED_LOCK_TIMEOUT_MS, 'LOCK_TIMEOUT_SLO_INVALID'),
    statementTimeoutMs: exact('FINANCE_FOUR_TABLE_STATEMENT_TIMEOUT_MS', APPROVED_STATEMENT_TIMEOUT_MS, 'STATEMENT_TIMEOUT_SLO_INVALID'),
  };
}

function databaseOptions(env) {
  return {
    host: env.DB_POSTGRESDB_HOST,
    port: Number(env.DB_POSTGRESDB_PORT || 5432),
    database: env.DB_POSTGRESDB_DATABASE,
    user: env.DB_POSTGRESDB_USER,
    password: env.DB_POSTGRESDB_PASSWORD,
    connectionTimeoutMillis: 5_000,
  };
}

function validateExport(exported) {
  if (!exported || typeof exported !== 'object' || Array.isArray(exported) ||
      exported.schema_version !== EXPORT_SCHEMA || exported.redacted !== true) {
    throw new Error('LIVE_EXPORT_SCHEMA_INVALID');
  }
  semanticProjection(exported);
  const exportDigest = text(exported.export_sha256, 'LIVE_EXPORT_SHA256_INVALID');
  const unsigned = { ...exported };
  delete unsigned.export_sha256;
  if (digest(unsigned) !== exportDigest) throw new Error('LIVE_EXPORT_INTEGRITY_INVALID');
  const semanticDigest = digest(semanticProjection(exported));
  const binding = bindingFromEnvironment();
  if (binding.required_live_export_digest !== semanticDigest) {
    throw new Error('LIVE_EXPORT_REQUIRED_DIGEST_MISMATCH');
  }
  const provenance = provenanceFromEnvironment();
  for (const [field, expected] of Object.entries(provenance)) {
    if (exported[field] !== expected) throw new Error(`LIVE_EXPORT_${field.toUpperCase()}_MISMATCH`);
  }
  if (exported.project_id !== projectId || exported.workflow_count !== 19 || exported.in_flight !== 0) {
    throw new Error('LIVE_EXPORT_PROJECT_OR_QUIESCENCE_MISMATCH');
  }
  if (!Array.isArray(exported.workflows) || exported.workflows.length !== 19) {
    throw new Error('EXACT_19_WORKFLOW_EXPORT_REQUIRED');
  }
  const workflows = new Map();
  const workflowBodyDigests = new Map();
  for (const workflow of exported.workflows) {
    const id = text(workflow.workflow_id, 'LIVE_WORKFLOW_ID_INVALID');
    const revision = text(workflow.revision_id, 'LIVE_WORKFLOW_REVISION_INVALID');
    const bodyDigest = digestText(workflow.workflow_body_sha256, 'WORKFLOW_BODY_SHA256_INVALID');
    if (workflows.has(id) || workflow.active !== false || workflow.published !== false || workflow.in_flight !== 0) {
      throw new Error('LIVE_WORKFLOW_GRAPH_INVALID');
    }
    workflows.set(id, revision);
    workflowBodyDigests.set(id, bodyDigest);
  }
  if (!Array.isArray(exported.targets) || exported.targets.length !== 4 ||
      exported.targets.some((target) => !TARGET_NAMES.has(target.name) || !text(target.table_id, 'LIVE_TARGET_ID_INVALID'))) {
    throw new Error('EXACT_TARGET_EXPORT_REQUIRED');
  }
  const targetIds = new Map(exported.targets.map((target) => [target.name, target.table_id]));
  if (targetIds.size !== 4 || new Set(targetIds.values()).size !== 4) throw new Error('LIVE_TARGET_ID_DUPLICATE');
  if (!Array.isArray(exported.references) || exported.references.length !== 37) {
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
    if (Object.hasOwn(reference, 'source_table') && reference.source_table !== reference.old_table_name) {
      throw new Error(`LIVE_REFERENCE_SOURCE_TABLE_MISMATCH:${id}`);
    }
    if (reference.active !== false || reference.published !== false || reference.in_flight !== 0) {
      throw new Error(`LIVE_REFERENCE_STATE_INVALID:${id}`);
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
  if (references.size !== 37) throw new Error('COMPLETE_LIVE_REFERENCE_EXPORT_REQUIRED');
  const legacyTables = new Set([...references.values()].map((reference) => reference.old_table_name));
  if (legacyTables.size !== LEGACY_TABLE_IDS.size || [...legacyTables].some((name) => !LEGACY_TABLE_IDS.has(name))) {
    throw new Error('EXACT_SEVEN_LEGACY_TABLE_ID_MAP_REQUIRED');
  }
  return { workflows, workflowBodyDigests, references, targetIds, exportDigest, semanticDigest };
}

function assertWorkflow(workflow, expectedRevision, expectedBodyDigest, workflowId) {
  if (!workflow || workflow.id !== workflowId || workflow.active === true || workflow.activeVersionId) {
    throw new Error(`LIVE_WORKFLOW_STATE_INVALID:${workflowId}`);
  }
  const revision = String(workflow.versionId || workflow.revisionId || workflow.activeVersionId || '');
  if (revision !== expectedRevision) throw new Error(`LIVE_WORKFLOW_REVISION_MISMATCH:${workflowId}`);
  if (!Array.isArray(workflow.nodes)) throw new Error(`LIVE_WORKFLOW_NODES_INVALID:${workflowId}`);
  if (workflowBodyDigest(workflow) !== expectedBodyDigest) {
    throw new Error(`LIVE_WORKFLOW_BODY_MISMATCH:${workflowId}`);
  }
  return revision;
}

async function loadWorkflows(client, graph, strict = true) {
  await client.query('LOCK TABLE workflow_entity, shared_workflow IN SHARE MODE');
  const loaded = new Map();
  const result = await client.query(
    `SELECT w.id, w.name, w.active, w."activeVersionId", w."versionId", w.nodes, w.connections,
            w.meta, w.settings, w."pinData"
       FROM workflow_entity w
      WHERE EXISTS (
        SELECT 1 FROM shared_workflow s
         WHERE s."workflowId" = w.id AND s."projectId" = $1 AND s.role = 'workflow:owner'
      )
      FOR UPDATE`,
    [projectId],
  );
  const sharesResult = await client.query(
    `SELECT "workflowId" AS workflow_id, "projectId" AS project_id, role
       FROM shared_workflow
      WHERE "workflowId" = ANY($1::text[])
      ORDER BY "workflowId", "projectId"`,
    [[...graph.workflows.keys()]],
  );
  const shares = sharesResult.rows || [];
  if (shares.length !== graph.workflows.size ||
      shares.some((share) => !graph.workflows.has(String(share.workflow_id)) ||
        String(share.project_id) !== projectId || share.role !== 'workflow:owner')) {
    throw new Error('WORKFLOW_OWNER_SHARE_SCOPE_INVALID');
  }
  const rows = new Map(result.rows.map((workflow) => [workflow.id, workflow]));
  if (result.rows.length !== graph.workflows.size || rows.size !== graph.workflows.size ||
      [...rows.keys()].some((workflowId) => !graph.workflows.has(workflowId))) {
    throw new Error('EXACT_PROJECT_WORKFLOW_SET_REQUIRED');
  }
  for (const [workflowId, revision] of graph.workflows) {
    const workflow = rows.get(workflowId);
    if (strict) assertWorkflow(workflow, revision, graph.workflowBodyDigests.get(workflowId), workflowId);
    else if (!workflow || workflow.id !== workflowId || workflow.active === true || workflow.activeVersionId || !Array.isArray(workflow.nodes)) {
      throw new Error(`LIVE_WORKFLOW_POST_STATE_INVALID:${workflowId}`);
    }
    loaded.set(workflowId, workflow);
  }
  return loaded;
}

function canonicalSourceFromInput(graph) {
  const fs = require('node:fs');
  const chunks = [];
  let length = 0;
  for (;;) {
    const chunk = Buffer.allocUnsafe(64 * 1024);
    const count = fs.readSync(0, chunk, 0, chunk.length, null);
    if (count === 0) break;
    length += count;
    if (length > 64 * 1024 * 1024) throw new Error('CANONICAL_SOURCE_SIZE_EXCEEDED');
    chunks.push(chunk.subarray(0, count));
  }
  const rawBuffer = Buffer.concat(chunks, length);
  const expectedFileSha256 = digestText(
    process.env.FINANCE_FOUR_TABLE_CANONICAL_SOURCE_FILE_SHA256,
    'CANONICAL_SOURCE_FILE_SHA256_INVALID',
  );
  if (crypto.createHash('sha256').update(rawBuffer).digest('hex') !== expectedFileSha256) {
    throw new Error('CANONICAL_SOURCE_FILE_CURRENTNESS_DRIFT');
  }
  const raw = rawBuffer.toString('utf8');
  let source;
  try { source = JSON.parse(raw); } catch { throw new Error('CANONICAL_SOURCE_JSON_INVALID'); }
  const provenance = provenanceFromEnvironment();
  if (!source || source.schema_version !== CANONICAL_SOURCE_SCHEMA ||
      source.source_head !== provenance.source_head || source.generator_head !== provenance.generator_head ||
      source.accepted_identity_sha256 !== provenance.accepted_identity_sha256 ||
      source.legacy_reference_inventory_sha256 !== APPROVED_LEGACY_REFERENCE_INVENTORY_SHA256 ||
      !Array.isArray(source.files) || source.files.length !== graph.workflows.size) {
    throw new Error('CANONICAL_SOURCE_BINDING_MISMATCH');
  }
  const targets = validateTargetProjection(source, graph);
  digestText(source.source_corpus_sha256, 'CANONICAL_SOURCE_SHA256_INVALID');
  const corpus = crypto.createHash('sha256');
  const workflows = new Map();
  let previousPath = '';
  for (const file of source.files) {
    if (!file || typeof file.path !== 'string' || !/^integrations\/n8n\/workflows\/[A-Za-z0-9_-]+\.json$/.test(file.path) ||
        file.path <= previousPath || typeof file.content !== 'string') throw new Error('CANONICAL_SOURCE_PATH_INVALID');
    const normalizedContent = file.content.replace(/\r\n/g, '\n');
    previousPath = file.path;
    corpus.update(file.path).update('\0').update(normalizedContent).update('\0');
    let workflow;
    try { workflow = JSON.parse(normalizedContent); } catch { throw new Error('CANONICAL_SOURCE_WORKFLOW_INVALID'); }
    if (!workflow || !graph.workflows.has(workflow.id) || workflows.has(workflow.id) || workflow.active !== false || workflow.activeVersionId) {
      throw new Error('CANONICAL_SOURCE_WORKFLOW_IDENTITY_MISMATCH');
    }
    validateCanonicalGraph(workflow, graph.targetIds);
    workflows.set(workflow.id, workflow);
  }
  if (corpus.digest('hex') !== source.source_corpus_sha256) throw new Error('CANONICAL_SOURCE_CORPUS_DIGEST_MISMATCH');
  return {
    workflows,
    targets,
    sha256: source.source_corpus_sha256,
    targetProjectionSha256: source.target_projection_sha256,
    targetDigest: source.target_digest,
  };
}
function digestWithoutNewline(value) {
  return crypto.createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}

function exactKeys(value, expected, code) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== [...expected].sort().join(',')) {
    throw new Error(code);
  }
}

function validateTargetProjection(source, graph) {
  const expectedSourceKeys = new Set([
    'schema_version',
    'source_head',
    'generator_head',
    'accepted_identity_sha256',
    'source_corpus_sha256',
    'legacy_reference_inventory_sha256',
    'files',
    'source_backup_sha256',
    'migration_receipt_sha256',
    'migration_matrix_sha256',
    'target_digest',
    'target_projection_sha256',
    'targets',
  ]);
  exactKeys(source, expectedSourceKeys, 'CANONICAL_SOURCE_FIELDS_INVALID');
  const provenance = provenanceFromEnvironment();
  if (source.schema_version !== CANONICAL_SOURCE_SCHEMA ||
      source.source_backup_sha256 !== provenance.source_backup_sha256 ||
      source.migration_receipt_sha256 !== provenance.migration_receipt_sha256) {
    throw new Error('CANONICAL_TARGET_PROJECTION_BINDING_MISMATCH');
  }
  for (const field of ['migration_matrix_sha256', 'target_digest', 'target_projection_sha256']) {
    digestText(source[field], `CANONICAL_${field.toUpperCase()}_INVALID`);
  }
  if (!Array.isArray(source.targets) || source.targets.length !== TARGET_NAMES.size ||
      digest(source.targets) !== source.target_projection_sha256) {
    throw new Error('CANONICAL_TARGET_PROJECTION_INTEGRITY_MISMATCH');
  }
  const targets = new Map();
  for (const target of source.targets) {
    exactKeys(
      target,
      ['name', 'schema_sha256', 'columns', 'logical_key', 'row_count', 'rows_sha256', 'rows'],
      'CANONICAL_TARGET_FIELDS_INVALID',
    );
    const name = text(target.name, 'CANONICAL_TARGET_NAME_INVALID');
    if (!TARGET_NAMES.has(name) || targets.has(name) || !graph.targetIds.has(name)) {
      throw new Error(`CANONICAL_TARGET_NAME_INVALID:${name}`);
    }
    digestText(target.schema_sha256, `CANONICAL_TARGET_SCHEMA_SHA256_INVALID:${name}`);
    digestText(target.rows_sha256, `CANONICAL_TARGET_ROWS_SHA256_INVALID:${name}`);
    if (target.schema_sha256 !== graph.targetSchemaDigests.get(name) ||
        !Array.isArray(target.columns) || target.columns.length === 0 ||
        !Array.isArray(target.logical_key) ||
        !sameJson(target.logical_key, TARGET_LOGICAL_KEYS.get(name)) ||
        !Array.isArray(target.rows) || target.row_count !== target.rows.length ||
        !Number.isInteger(target.row_count) || target.row_count < 0 || target.row_count > 100000) {
      throw new Error(`CANONICAL_TARGET_SCHEMA_INVALID:${name}`);
    }
    const columnTypes = new Map();
    let previousColumn = '';
    for (const column of target.columns) {
      exactKeys(column, ['name', 'type'], `CANONICAL_TARGET_COLUMN_INVALID:${name}`);
      if (typeof column.name !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(column.name) ||
          column.name <= previousColumn || !TARGET_COLUMN_TYPES.has(column.type)) {
        throw new Error(`CANONICAL_TARGET_COLUMN_INVALID:${name}`);
      }
      previousColumn = column.name;
      columnTypes.set(column.name, column.type);
    }
    if (target.logical_key.some((field) => !columnTypes.has(field))) {
      throw new Error(`CANONICAL_TARGET_LOGICAL_KEY_INVALID:${name}`);
    }
    const rowStrings = [];
    const normalizedRows = [];
    const logicalKeys = new Set();
    for (const row of target.rows) {
      exactKeys(row, columnTypes.keys(), `CANONICAL_TARGET_ROW_FIELDS_INVALID:${name}`);
      const normalized = Object.fromEntries(
        [...columnTypes].map(([field, type]) => [
          field,
          canonicalTargetValue(
            row[field],
            type,
            `CANONICAL_TARGET_ROW_TYPE_INVALID:${name}:${field}`,
          ),
        ]),
      );
      const logicalKey = JSON.stringify(target.logical_key.map((field) => normalized[field]));
      if (target.logical_key.some((field) => normalized[field] === null) || logicalKeys.has(logicalKey)) {
        throw new Error(`CANONICAL_TARGET_LOGICAL_KEY_INVALID:${name}`);
      }
      logicalKeys.add(logicalKey);
      normalizedRows.push(normalized);
      rowStrings.push(JSON.stringify(canonical(normalized)));
    }
    if (digestWithoutNewline(rowStrings) !== target.rows_sha256) {
      throw new Error(`CANONICAL_TARGET_ROWS_DIGEST_MISMATCH:${name}`);
    }
    targets.set(name, {
      ...target,
      rows: normalizedRows,
      tableId: graph.targetIds.get(name),
      columnTypes,
    });
  }
  if (targets.size !== TARGET_NAMES.size) throw new Error('CANONICAL_TARGET_SET_INVALID');
  return targets;
}


function validateCanonicalGraph(workflow, targetIds) {
  if (!Array.isArray(workflow.nodes) || !workflow.connections || typeof workflow.connections !== 'object' || Array.isArray(workflow.connections)) {
    throw new Error('CANONICAL_SOURCE_GRAPH_INVALID');
  }
  const names = new Set();
  const ids = new Set();
  for (const node of workflow.nodes) {
    if (!node || typeof node.id !== 'string' || !node.id || typeof node.name !== 'string' || !node.name ||
        typeof node.type !== 'string' || !node.type || ids.has(node.id) || names.has(node.name)) throw new Error('CANONICAL_SOURCE_NODE_IDENTITY_INVALID');
    ids.add(node.id);
    names.add(node.name);
    if (node.type !== 'n8n-nodes-base.dataTable') continue;
    const parameters = node.parameters && typeof node.parameters === 'object' ? node.parameters : {};
    if (parameters.resource === 'table') {
      if (parameters.operation === 'list') continue;
      if (
        parameters.operation !== 'create'
        || (!targetIds.has(parameters.tableName)
          && !COMPATIBILITY_TABLE_NAMES.has(parameters.tableName))
      ) throw new Error('CANONICAL_SOURCE_TABLE_OPERATION_INVALID');
      continue;
    }
    const selected = selectorId(parameters.dataTableId);
    if (!selected) throw new Error('NULL_TABLE_SELECTOR_GRAPH_REFUSED');
    if (
      PRESERVED_OPERATIONAL_SELECTOR_NAMES.has(selected)
      || PRESERVED_OPERATIONAL_SELECTOR_IDS.has(selected)
    ) continue;
    const tableId = targetIds.get(selected) || ([...targetIds.values()].includes(selected) ? selected : null);
    if (!tableId) throw new Error(`CANONICAL_SOURCE_TABLE_SELECTOR_INVALID:${node.id}`);
    setSelector(node, tableId);
  }
  for (const [name, outputs] of Object.entries(workflow.connections)) {
    if (!names.has(name) || !outputs || typeof outputs !== 'object' || Array.isArray(outputs)) throw new Error('CANONICAL_SOURCE_CONNECTION_INVALID');
    for (const ports of Object.values(outputs)) {
      if (!Array.isArray(ports)) throw new Error('CANONICAL_SOURCE_CONNECTION_INVALID');
      for (const edges of ports) {
        if (!Array.isArray(edges) || edges.some((edge) => !edge || !names.has(edge.node))) throw new Error('CANONICAL_SOURCE_CONNECTION_INVALID');
      }
    }
  }
}

function workflowReadback(workflows) {
  return [...workflows.values()].map((workflow) => ({
    workflow_id: workflow.id,
    workflow_body_sha256: workflowBodyDigest(workflow),
  })).sort((left, right) => left.workflow_id.localeCompare(right.workflow_id));
}

function credentialBindingForNode(workflow, node) {
  return credentialLeavesFromEnvironment().find((leaf) => leaf.key === `${workflow.id}:${node.id}`);
}

function validateCredentialBindings(workflows, credentials) {
  const expected = new Map(credentialLeavesFromEnvironment().map((leaf) => [leaf.key, leaf]));
  const seen = new Set();
  const origins = new Map();
  for (const workflow of workflows.values()) {
    for (const node of workflow.nodes) {
      const key = `${workflow.id}:${node.id}`;
      if (!node.credentials && !expected.has(key)) continue;
      if (!expected.has(key)) throw new Error(`CREDENTIAL_BINDING_EXTRA:${key}`);
      const binding = expected.get(key);
      if (workflow.meta?.financeWorkflowCode !== binding.workflow.code ||
          node.name !== binding.node.name || node.type !== binding.node_type ||
          !node.credentials || typeof node.credentials !== 'object' || Array.isArray(node.credentials) ||
          Object.keys(node.credentials).length !== 1 || !Object.hasOwn(node.credentials, binding.credential_type)) {
        throw new Error(`CREDENTIAL_BINDING_TUPLE_MISMATCH:${key}`);
      }
      const ref = node.credentials[binding.credential_type];
      const associated = credentials.get(binding.placeholder);
      if (!ref || typeof ref !== 'object' || Array.isArray(ref) || Object.keys(ref).sort().join(',') !== 'id,name' ||
          typeof ref.id !== 'string' || !ref.id || typeof ref.name !== 'string' || !ref.name) {
        throw new Error(`CREDENTIAL_BINDING_ASSOCIATION_MISMATCH:${key}`);
      }
      const placeholder = ref.id === binding.placeholder;
      const opaque = associated && ref.id === associated.id && ref.name === associated.name;
      if (!placeholder && !opaque) throw new Error(`CREDENTIAL_BINDING_ASSOCIATION_MISMATCH:${key}`);
      origins.set(key, placeholder ? 'placeholder' : 'opaque');
      seen.add(key);
    }
  }
  if (seen.size !== expected.size || [...expected.keys()].some((key) => !seen.has(key))) throw new Error('CREDENTIAL_BINDING_COVERAGE_INVALID');
  return origins;
}

function workflowCredentialObjectsDigest(workflows) {
  const objects = [];
  for (const workflow of workflows.values()) for (const node of workflow.nodes) {
    if (node.credentials) objects.push({ workflow_id: workflow.id, node_id: String(node.id), credentials: clone(node.credentials) });
  }
  return digest(objects.sort((a, b) => `${a.workflow_id}:${a.node_id}`.localeCompare(`${b.workflow_id}:${b.node_id}`)));
}

function workflowRevisionDigest(workflows) {
  const revisions = [...workflows.values()].map((workflow) => ({
    workflow_id: String(workflow.id),
    revision_id: String(workflow.versionId || workflow.revisionId || ''),
  }));
  return digest(revisions.sort((left, right) => left.workflow_id.localeCompare(right.workflow_id)));
}

function credentialOriginBitset(origins) {
  return credentialLeavesFromEnvironment().map((leaf) => {
    const origin = origins.get(leaf.key);
    if (origin === 'placeholder') return '1';
    if (origin === 'opaque') return '0';
    throw new Error(`CREDENTIAL_BINDING_ORIGIN_MISSING:${leaf.key}`);
  }).join('');
}
function credentialLeafCount() {
  return credentialLeavesFromEnvironment().length;
}

function credentialOriginDigest(bitset) {
  return digest({ credential_contract_digest: digest(credentialBindingsFromEnvironment()), credential_leaf_count: credentialLeafCount(), credential_origin_bitset: bitset });
}

function credentialOriginsFromBitset(bitset) {
  if (typeof bitset !== 'string' || !new RegExp(`^[01]{${credentialLeafCount()}}$`).test(bitset)) throw new Error('CREDENTIAL_ORIGIN_BITSET_INVALID');
  return new Map(credentialLeavesFromEnvironment().map((leaf, index) => [leaf.key, bitset[index] === '1' ? 'placeholder' : 'opaque']));
}

function allCredentialOrigins(origins, origin) {
  return [...origins.values()].every((value) => value === origin);
}

function workflowOpaqueCredentialObjectsDigest(workflows, origins) {
  const objects = [];
  for (const workflow of workflows.values()) for (const node of workflow.nodes) {
    const binding = credentialBindingForNode(workflow, node);
    const ref = binding && node.credentials?.[binding.credential_type];
    if (ref && ref.id !== binding.placeholder && ref.name !== binding.placeholder &&
        (!origins || origins.get(`${workflow.id}:${node.id}`) === 'opaque')) {
      objects.push({ workflow_id: workflow.id, node_id: String(node.id), credentials: clone(node.credentials) });
    }
  }
  return digest(objects.sort((a, b) => `${a.workflow_id}:${a.node_id}`.localeCompare(`${b.workflow_id}:${b.node_id}`)));
}

async function credentialState(client) {
  await client.query('LOCK TABLE credentials_entity, shared_credentials IN SHARE MODE');
  const result = await client.query(
    `SELECT c.id, c.name, c.type, s."projectId" AS project_id, s.role
       FROM credentials_entity c
       JOIN shared_credentials s ON s."credentialsId" = c.id
      WHERE c.id IN (
        SELECT "credentialsId" FROM shared_credentials WHERE "projectId" = $1
      )
      ORDER BY c.id, s."projectId"`,
    [projectId],
  );
  const rows = result.rows || [];
  const ownerShares = new Map();
  for (const row of rows) {
    if (!row.id || !row.name || !row.type || !row.project_id ||
        !['credential:owner', 'credential:user'].includes(row.role)) {
      throw new Error('CREDENTIAL_ASSOCIATION_INVALID');
    }
    const normalized = { id: String(row.id), name: String(row.name), type: String(row.type), project_id: String(row.project_id), role: row.role };
    const shares = ownerShares.get(normalized.id) || [];
    shares.push(normalized);
    ownerShares.set(normalized.id, shares);
  }
  const byType = new Map();
  const byTypeName = new Map();
  for (const row of rows.filter((candidate) =>
    String(candidate.project_id) === projectId && candidate.role === 'credential:owner')) {
    const type = String(row.type);
    const name = String(row.name);
    const value = { id: String(row.id), name, type, project_id: String(row.project_id), role: row.role };
    const shares = ownerShares.get(value.id) || [];
    if (shares.some((share) => share.project_id !== projectId)) throw new Error('CREDENTIAL_OWNER_SHARE_FOREIGN');
    if (shares.length !== 1) throw new Error('CREDENTIAL_OWNER_SHARE_AMBIGUOUS');
    if (shares[0].name !== value.name || shares[0].type !== value.type) throw new Error('CREDENTIAL_OWNER_SHARE_AMBIGUOUS');
    const typeRows = byType.get(type) || [];
    typeRows.push(value);
    byType.set(type, typeRows);
    const nameKey = `${type}\0${name}`;
    const namedRows = byTypeName.get(nameKey) || [];
    namedRows.push(value);
    byTypeName.set(nameKey, namedRows);
  }
  const bindings = credentialBindingsFromEnvironment();
  const values = [];
  for (const binding of bindings) {
    const matches = binding.credential_name
      ? byTypeName.get(`${binding.credential_type}\0${binding.credential_name}`) || []
      : byType.get(binding.credential_type) || [];
    if (matches.length !== 1) throw new Error(`CREDENTIAL_ASSOCIATION_COVERAGE_INVALID:${binding.placeholder}`);
    values.push({ ...matches[0], placeholder: binding.placeholder });
  }
  const ids = new Set(values.map((value) => value.id));
  if (ids.size !== values.length) throw new Error('CREDENTIAL_ASSOCIATION_AMBIGUOUS');
  values.sort((a, b) => a.placeholder.localeCompare(b.placeholder));
  return { values, digest: digest(values) };
}

function credentialContractSummary() {
  const bindings = credentialBindingsFromEnvironment();
  return {
    credential_contract_digest: digest(bindings),
    credential_binding_count: bindings.length,
    credential_leaf_count: bindings.reduce((count, binding) => count + binding.nodes.length, 0),
  };
}

async function updateWorkflows(client, changes) {
  for (const [workflowId, body] of changes) {
    const revisionId = crypto.randomUUID();
    const result = await client.query(
      `UPDATE workflow_entity w
          SET nodes = $1::json, "versionId" = $3, name = $5, connections = $6::json,
              settings = $7::json, meta = $8::json, "pinData" = $9::json
        WHERE w.id = $2
          AND EXISTS (
            SELECT 1 FROM shared_workflow s
             WHERE s."workflowId" = w.id
               AND s."projectId" = $4
               AND s.role = 'workflow:owner'
          )
      RETURNING w.id, w."versionId"`,
      [JSON.stringify(body.nodes), workflowId, revisionId, projectId, body.name,
        JSON.stringify(body.connections), JSON.stringify(body.settings ?? null),
        JSON.stringify(body.meta ?? null), JSON.stringify(body.pinData ?? null)],
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

function selectorReadback(references) {
  return references.map((item) => ({
    reference_id: item.reference.reference_id,
    workflow_id: item.reference.workflow_id,
    node_id: item.reference.node_id,
    selector: clone(item.node.parameters?.dataTableId),
    selector_id: selectorId(item.node.parameters?.dataTableId),
  })).sort((left, right) => left.reference_id.localeCompare(right.reference_id));
}

async function verifyLegacyForwardJournal(client, receipt) {
  const result = await client.query(
    `SELECT receipt FROM ${JOURNAL_TABLE}
      WHERE receipt_sha256 = $1 AND project_id = $2 AND operation = 'FORWARD' AND lock_resource = $3`,
    [receipt.runtime_plan_receipt_sha256, projectId, receipt.lock_resource],
  );
  const stored = result.rows?.[0]?.receipt;
  if (result.rows?.length !== 1 || !sameJson(typeof stored === 'string' ? JSON.parse(stored) : stored, receipt)) {
    throw new Error('LEGACY_FORWARD_JOURNAL_RECEIPT_MISMATCH');
  }
}

function rollbackSelectorReceipt(graph, workflows, receipt, origins) {
  const prestate = findReferences(graph, workflows);
  if (digest(selectorReadback(prestate)) !== receipt.readback_digest_sha256) {
    throw new Error('ROLLBACK_REFERENCE_READBACK_DRIFT');
  }
  const actions = new Map(receipt.actions.map((action) => [action.reference_id, action]));
  const restored = new Map([...workflows].map(([id, workflow]) => [
    id, { id, ...clone(Object.fromEntries(WORKFLOW_BODY_FIELDS.map((field) => [field, workflow[field] ?? null]))) },
  ]));
  for (const item of prestate) {
    const action = actions.get(item.reference.reference_id);
    if (!item.targetMatches || String(item.workflow.versionId || item.workflow.revisionId || '') !== action.post_revision_id) {
      throw new Error(`ROLLBACK_REFERENCE_STATE_MISMATCH:${item.reference.reference_id}`);
    }
    const node = restored.get(item.reference.workflow_id).nodes.find((candidate) => candidate.id === item.reference.node_id);
    if (action.selector === undefined || action.selector === null) delete node.parameters.dataTableId;
    else node.parameters.dataTableId = clone(action.selector);
  }
  for (const leaf of credentialLeavesFromEnvironment()) {
    if (origins.get(leaf.key) !== 'placeholder') continue;
    const node = restored.get(leaf.workflow.id)?.nodes.find((candidate) => candidate.id === leaf.node.id);
    if (!node) throw new Error(`ROLLBACK_CREDENTIAL_NODE_MISSING:${leaf.key}`);
    node.credentials = { [leaf.credential_type]: { id: leaf.placeholder, name: leaf.placeholder } };
  }
  for (const [id, workflow] of restored) {
    if (workflowBodyDigest(workflow) !== graph.workflowBodyDigests.get(id)) {
      throw new Error(`ROLLBACK_ORIGINAL_WORKFLOW_BODY_MISMATCH:${id}`);
    }
  }
  return restored;
}


function applyForward(workflows, credentials, canonicalWorkflows) {
  const changed = new Map();
  const expected = new Map();
  for (const [workflowId, source] of canonicalWorkflows) {
    const workflow = workflows.get(workflowId);
    if (!workflow) throw new Error(`CANONICAL_WORKFLOW_MISSING:${workflowId}`);
    const body = clone(Object.fromEntries(WORKFLOW_BODY_FIELDS.map((field) => [field, source[field] ?? null])));
    for (const leaf of credentialLeavesFromEnvironment()) {
      if (leaf.workflow.id !== workflowId) continue;
      const originalNode = workflow.nodes.find((node) => node.id === leaf.node.id);
      const node = body.nodes.find((candidate) => candidate.id === leaf.node.id);
      const ref = originalNode?.credentials?.[leaf.credential_type];
      const sourceRef = node?.credentials?.[leaf.credential_type];
      if (!node || node.type !== leaf.node_type || sourceRef?.id !== leaf.placeholder || sourceRef?.name !== leaf.placeholder) {
        throw new Error(`CANONICAL_CREDENTIAL_BINDING_INVALID:${leaf.key}`);
      }
      if (ref?.id === leaf.placeholder) {
        const associated = credentials.get(leaf.placeholder);
        if (!associated) throw new Error(`CREDENTIAL_BINDING_ASSOCIATION_MISSING:${leaf.key}`);
        node.credentials = { [leaf.credential_type]: { id: associated.id, name: associated.name } };
      } else {
        node.credentials = clone(originalNode.credentials);
      }
    }
    const expectedWorkflow = { id: workflowId, ...body };
    expected.set(workflowId, expectedWorkflow);
    const currentBody = Object.fromEntries(WORKFLOW_BODY_FIELDS.map((field) => [field, workflow[field] ?? null]));
    if (!sameJson(currentBody, body)) changed.set(workflowId, body);
  }
  return { changed, expected, alreadyApplied: changed.size === 0 };
}


function quoteIdentifier(value, code) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) {
    throw new Error(code);
  }
  return `"${value.replace(/"/g, '""')}"`;
}

function physicalTargetTable(tableId) {
  const prefix = process.env.DB_TABLE_PREFIX || '';
  if (!/^[A-Za-z0-9_]{0,64}$/.test(prefix)) throw new Error('DB_TABLE_PREFIX_INVALID');
  return quoteIdentifier(`${prefix}data_table_user_${tableId}`, 'TARGET_TABLE_ID_INVALID');
}

function sortedUserRows(rows, columns) {
  return rows
    .map((row) => Object.fromEntries(columns.map((column) => [column.name, canonical(row[column.name] ?? null)])))
    .sort((left, right) => Buffer.compare(
      Buffer.from(JSON.stringify(canonical(left))),
      Buffer.from(JSON.stringify(canonical(right))),
    ));
}

function targetStateEvidence(state) {
  return [...state.values()].map((table) => {
    const rowStrings = table.userRows.map((row) => JSON.stringify(canonical(row)));
    return {
      name: table.name,
      table_id_sha256: crypto.createHash('sha256').update(table.tableId).digest('hex'),
      row_count: table.userRows.length,
      rows_sha256: digestWithoutNewline(rowStrings),
    };
  }).sort((left, right) => left.name.localeCompare(right.name));
}

function targetStateDigest(state) {
  return digest(targetStateEvidence(state));
}

function targetSnapshot(state) {
  return [...state.values()].map((table) => ({
    name: table.name,
    table_id: table.tableId,
    columns: table.columns,
    rows: table.systemRows,
  })).sort((left, right) => left.name.localeCompare(right.name));
}

async function loadTargetState(client, targets) {
  const targetIds = new Map([...targets].map(([name, target]) => [name, target.tableId]));
  await client.query('LOCK TABLE data_table, data_table_column IN SHARE MODE');
  const result = await client.query(
    `SELECT id, name
       FROM data_table
      WHERE "projectId" = $1
      ORDER BY name
      FOR UPDATE`,
    [projectId],
  );
  const projectTables = result.rows || [];
  if (projectTables.length !== COMPATIBILITY_TABLE_NAMES.size ||
      projectTables.some((table) => !COMPATIBILITY_TABLE_NAMES.has(String(table.name))) ||
      projectTables.some((table) => {
        const expectedId = LEGACY_TABLE_IDS.get(String(table.name));
        return expectedId !== undefined && String(table.id) !== expectedId;
      }) ||
      new Set(projectTables.map((table) => String(table.name))).size !== projectTables.length ||
      new Set(projectTables.map((table) => String(table.id))).size !== projectTables.length) {
    throw new Error('CLOSED_PROJECT_DATA_TABLE_SET_REQUIRED');
  }
  const tables = projectTables.filter((table) => TARGET_NAMES.has(String(table.name)));
  if (tables.length !== TARGET_NAMES.size || new Set(tables.map((table) => table.name)).size !== TARGET_NAMES.size ||
      new Set(tables.map((table) => table.id)).size !== TARGET_NAMES.size) {
    throw new Error('EXACT_TARGET_READBACK_REQUIRED');
  }
  const columnResult = await client.query(
    `SELECT "dataTableId" AS table_id, name, type, "index"
       FROM data_table_column
      WHERE "dataTableId" = ANY($1::text[])
      ORDER BY "dataTableId", name
      FOR UPDATE`,
    [[...targetIds.values()]],
  );
  const columnsById = new Map([...targetIds.values()].map((id) => [id, []]));
  for (const column of columnResult.rows || []) {
    if (!columnsById.has(column.table_id)) throw new Error('UNEXPECTED_TARGET_COLUMN');
    columnsById.get(column.table_id).push({ name: String(column.name), type: String(column.type).toLowerCase() });
  }
  const state = new Map();
  for (const table of tables) {
    const name = String(table.name);
    if (!TARGET_NAMES.has(name)) throw new Error(`UNEXPECTED_TARGET_READBACK:${name}`);
    if (table.id !== targetIds.get(name)) throw new Error(`TARGET_ID_READBACK_MISMATCH:${name}`);
    const target = targets.get(name);
    const columns = (columnsById.get(table.id) || []).sort((left, right) => left.name.localeCompare(right.name));
    if (!sameJson(columns, target.columns) || digestWithoutNewline(columns) !== target.schema_sha256) {
      throw new Error(`TARGET_SCHEMA_READBACK_MISMATCH:${name}`);
    }
    const rowsResult = await client.query(`SELECT * FROM ${physicalTargetTable(table.id)} ORDER BY id FOR UPDATE`);
    const systemRows = (rowsResult.rows || []).map((row) => canonical(row));
    const expectedFields = new Set([...TARGET_SYSTEM_COLUMNS, ...columns.map((column) => column.name)]);
    for (const row of systemRows) {
      if (Object.keys(row).length !== expectedFields.size ||
          Object.keys(row).some((field) => !expectedFields.has(field)) ||
          !Number.isInteger(row.id)) {
        throw new Error(`TARGET_ROW_SHAPE_INVALID:${name}`);
      }
    }
    state.set(name, {
      name,
      tableId: table.id,
      columns,
      systemRows,
      userRows: sortedUserRows(systemRows, columns),
    });
  }
  return state;
}

async function insertTargetRows(client, target, rows, includeSystemColumns) {
  if (rows.length === 0) return;
  const fields = includeSystemColumns
    ? [...TARGET_SYSTEM_COLUMNS, ...target.columns.map((column) => column.name)]
    : ['id', ...target.columns.map((column) => column.name)];
  const rowsPerBatch = Math.max(1, Math.floor(1000 / fields.length));
  for (let offset = 0; offset < rows.length; offset += rowsPerBatch) {
    const batch = rows.slice(offset, offset + rowsPerBatch);
    const parameters = [];
    const values = batch.map((row, rowIndex) => {
      const source = includeSystemColumns
        ? row
        : { id: -(offset + rowIndex + 1), ...row };
      const placeholders = fields.map((field) => {
        parameters.push(source[field] ?? null);
        return `$${parameters.length}`;
      });
      return `(${placeholders.join(', ')})`;
    });
    await client.query(
      `INSERT INTO ${physicalTargetTable(target.tableId)}
         (${fields.map((field) => quoteIdentifier(field, 'TARGET_COLUMN_NAME_INVALID')).join(', ')})
       OVERRIDING SYSTEM VALUE
       VALUES ${values.join(', ')}`,
      parameters,
    );
  }
}

async function replaceTargetRows(client, targets, rowsByName, includeSystemColumns) {
  for (const name of [...TARGET_NAMES].sort((left, right) => left.localeCompare(right))) {
    const target = targets.get(name);
    if (!target) throw new Error(`TARGET_PROJECTION_MISSING:${name}`);
    await client.query(`DELETE FROM ${physicalTargetTable(target.tableId)}`);
    await insertTargetRows(client, target, rowsByName.get(name) || [], includeSystemColumns);
    if (process.env.FINANCE_FOUR_TABLE_INJECT_FAILURE_AFTER_TARGET === name) {
      throw new Error(`INJECTED_FAILURE_AFTER_TARGET:${name}`);
    }
  }
}

async function applyTargetProjection(client, targets) {
  const before = await loadTargetState(client, targets);
  const matches = [...before].every(([name, table]) => sameJson(table.userRows, targets.get(name).rows));
  if (matches) {
    return {
      alreadyApplied: true,
      before,
      after: before,
      rollbackTargets: null,
    };
  }
  if ([...before.values()].some((table) => table.systemRows.length !== 0)) {
    throw new Error('TARGET_PRESTATE_NOT_EMPTY_OR_PROJECTED');
  }
  const rollbackTargets = targetSnapshot(before);
  await replaceTargetRows(
    client,
    targets,
    new Map([...targets].map(([name, target]) => [name, target.rows])),
    false,
  );
  const after = await loadTargetState(client, targets);
  if ([...after].some(([name, table]) => !sameJson(table.userRows, targets.get(name).rows))) {
    throw new Error('TARGET_PROJECTION_POST_READBACK_MISMATCH');
  }
  return {
    alreadyApplied: false,
    before,
    after,
    rollbackTargets,
  };
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

async function persistRecoveryJournal(
  client,
  receipt,
  rollbackWorkflows = null,
  rollbackTargets = null,
) {
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
  await client.query(`ALTER TABLE ${JOURNAL_TABLE} ADD COLUMN IF NOT EXISTS rollback_workflows jsonb`);
  await client.query(`ALTER TABLE ${JOURNAL_TABLE} ADD COLUMN IF NOT EXISTS rollback_targets jsonb`);
  await client.query(
    `INSERT INTO ${JOURNAL_TABLE}
       (receipt_sha256, project_id, operation, lock_resource, receipt, rollback_workflows, rollback_targets)
     VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb)
     ON CONFLICT (receipt_sha256) DO NOTHING`,
    [
      receipt.runtime_plan_receipt_sha256,
      receipt.project_id,
      receipt.operation,
      receipt.lock_resource,
      JSON.stringify(receipt),
      JSON.stringify(rollbackWorkflows),
      JSON.stringify(rollbackTargets),
    ],
  );
}

async function loadRollbackWorkflows(client, receipt, graph, requireGraphBodies = true) {
  const result = await client.query(
    `SELECT rollback_workflows FROM ${JOURNAL_TABLE}
      WHERE receipt_sha256 = $1 AND project_id = $2 AND operation = 'FORWARD' AND lock_resource = $3`,
    [receipt.runtime_plan_receipt_sha256, projectId, receipt.lock_resource],
  );
  const bodies = result.rows?.[0]?.rollback_workflows;
  if (result.rows?.length !== 1 || !Array.isArray(bodies) || bodies.length !== graph.workflows.size ||
      digest(bodies) !== receipt.rollback_workflows_sha256) throw new Error('ROLLBACK_WORKFLOW_SNAPSHOT_MISMATCH');
  const restored = new Map();
  for (const workflow of bodies) {
    if (!workflow || !graph.workflows.has(workflow.id) || restored.has(workflow.id) ||
        (requireGraphBodies && workflowBodyDigest(workflow) !== graph.workflowBodyDigests.get(workflow.id))) {
      throw new Error('ROLLBACK_WORKFLOW_BODY_MISMATCH');
    }
    restored.set(workflow.id, workflow);
  }
  if (!requireGraphBodies &&
      findReferences(graph, restored).some((item) => !item.oldMatches)) {
    throw new Error('ROLLBACK_WORKFLOW_SNAPSHOT_NOT_LEGACY');
  }
  return restored;
}


function validateRollbackTargets(snapshot, receipt, targets) {
  if (!Array.isArray(snapshot) || snapshot.length !== TARGET_NAMES.size ||
      digest(snapshot) !== receipt.rollback_targets_sha256) {
    throw new Error('ROLLBACK_TARGET_SNAPSHOT_MISMATCH');
  }
  const restored = new Map();
  for (const table of snapshot) {
    exactKeys(table, ['name', 'table_id', 'columns', 'rows'], 'ROLLBACK_TARGET_SNAPSHOT_INVALID');
    const target = targets.get(table.name);
    if (!target || restored.has(table.name) || table.table_id !== target.tableId ||
        !sameJson(table.columns, target.columns) || !Array.isArray(table.rows)) {
      throw new Error('ROLLBACK_TARGET_SNAPSHOT_INVALID');
    }
    const expectedFields = new Set([...TARGET_SYSTEM_COLUMNS, ...target.columns.map((column) => column.name)]);
    for (const row of table.rows) {
      if (!row || typeof row !== 'object' || Array.isArray(row) ||
          Object.keys(row).length !== expectedFields.size ||
          Object.keys(row).some((field) => !expectedFields.has(field)) ||
          !Number.isInteger(row.id)) {
        throw new Error('ROLLBACK_TARGET_SNAPSHOT_INVALID');
      }
    }
    restored.set(table.name, table.rows.map((row) => canonical(row)));
  }
  return restored;
}

async function loadRollbackTargets(client, receipt, targets) {
  const result = await client.query(
    `SELECT rollback_targets FROM ${JOURNAL_TABLE}
      WHERE receipt_sha256 = $1 AND project_id = $2 AND operation = 'FORWARD' AND lock_resource = $3`,
    [receipt.runtime_plan_receipt_sha256, projectId, receipt.lock_resource],
  );
  if (result.rows?.length !== 1) throw new Error('ROLLBACK_TARGET_SNAPSHOT_MISMATCH');
  return validateRollbackTargets(result.rows[0].rollback_targets, receipt, targets);
}

async function restoreTargetSnapshot(client, targets, rowsByName) {
  await replaceTargetRows(client, targets, rowsByName, true);
  const restored = await loadTargetState(client, targets);
  for (const [name, table] of restored) {
    const expected = (rowsByName.get(name) || []).map((row) => canonical(row));
    if (!sameJson(table.systemRows, expected)) {
      throw new Error(`ROLLBACK_TARGET_POST_READBACK_MISMATCH:${name}`);
    }
  }
  return restored;
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

function validateForwardReceipt(receipt, exported, resource, binding, canonicalSourceSha256 = null) {
  if (!receipt || ![LEGACY_RUNTIME_SCHEMA, PREVIOUS_RUNTIME_SCHEMA, RUNTIME_SCHEMA].includes(receipt.schema_version) || receipt.operation !== 'FORWARD' ||
      receipt.project_id !== projectId || receipt.lock_resource !== resource ||
      receipt.export_sha256 !== exported.export_sha256 || receipt.action_count !== 37 ||
      receipt.durable_journal !== true || receipt.commit_protocol !== 'postgresql_synchronous_wal' ||
      receipt.readback_verified !== true || typeof receipt.readback_digest_sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/.test(receipt.readback_digest_sha256) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_state_digest_before) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_state_digest_after) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_credential_objects_digest_before) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_credential_objects_digest_after) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_revision_digest_before) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_revision_digest_after) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_contract_digest) ||
      !Number.isInteger(receipt.credential_binding_count) || !Number.isInteger(receipt.credential_leaf_count) ||
      receipt.credential_binding_count !== credentialBindingsFromEnvironment().length || receipt.credential_leaf_count !== credentialLeafCount() ||
      !new RegExp(`^[01]{${credentialLeafCount()}}$`).test(receipt.credential_origin_bitset) ||
      !new RegExp(`^[01]{${credentialLeafCount()}}$`).test(receipt.credential_origin_post_bitset) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_origin_digest) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_origin_post_digest) ||
      receipt.credential_ids_recorded !== false || receipt.secret_values_recorded !== false || !Array.isArray(receipt.actions)) {
    throw new Error('FORWARD_RUNTIME_RECEIPT_INTEGRITY_INVALID');
  }
  if ([PREVIOUS_RUNTIME_SCHEMA, RUNTIME_SCHEMA].includes(receipt.schema_version)) {
    if (!/^[0-9a-f]{64}$/.test(receipt.rollback_workflows_sha256) ||
        !/^[0-9a-f]{64}$/.test(receipt.canonical_source_sha256) ||
        (canonicalSourceSha256 !== null && receipt.canonical_source_sha256 !== canonicalSourceSha256)) {
      throw new Error('FORWARD_RUNTIME_GRAPH_RECEIPT_INTEGRITY_INVALID');
    }
  } else if (Object.hasOwn(receipt, 'rollback_workflows_sha256') || Object.hasOwn(receipt, 'canonical_source_sha256')) {
    throw new Error('FORWARD_RUNTIME_RECEIPT_VERSION_MISMATCH');
  }
  if (receipt.schema_version === RUNTIME_SCHEMA) {
    if (!/^[0-9a-f]{64}$/.test(receipt.rollback_targets_sha256) ||
        !/^[0-9a-f]{64}$/.test(receipt.target_projection_sha256) ||
        !/^[0-9a-f]{64}$/.test(receipt.target_readback_sha256) ||
        !/^[0-9a-f]{64}$/.test(receipt.target_digest) ||
        receipt.preserved_table_writes !== false ||
        !receipt.target_row_counts || typeof receipt.target_row_counts !== 'object' ||
        Array.isArray(receipt.target_row_counts) ||
        Object.keys(receipt.target_row_counts).sort().join(',') !== [...TARGET_NAMES].sort().join(',') ||
        Object.values(receipt.target_row_counts).some((count) => !Number.isInteger(count) || count < 0)) {
      throw new Error('FORWARD_RUNTIME_TARGET_RECEIPT_INTEGRITY_INVALID');
    }
  }
  if (receipt.schema_version === RUNTIME_SCHEMA &&
      (!Array.isArray(receipt.target_prestate) ||
       receipt.target_prestate.length !== TARGET_NAMES.size ||
       receipt.target_prestate.some((table) =>
         !table || typeof table !== 'object' || Array.isArray(table) ||
         !TARGET_NAMES.has(table.name) ||
         !/^[0-9a-f]{64}$/.test(table.table_id_sha256) ||
         !/^[0-9a-f]{64}$/.test(table.rows_sha256) ||
         !Number.isInteger(table.row_count) || table.row_count < 0))) {
    throw new Error('FORWARD_RUNTIME_TARGET_PRESTATE_INVALID');
  }
  validateBinding(receipt, binding, 'FORWARD_RUNTIME_RECEIPT');
  if (receipt.credential_contract_digest !== digest(credentialBindingsFromEnvironment()) ||
      receipt.credential_origin_digest !== credentialOriginDigest(receipt.credential_origin_bitset) ||
      receipt.credential_origin_post_digest !== credentialOriginDigest(receipt.credential_origin_post_bitset) ||
      receipt.credential_origin_post_bitset !== '0'.repeat(credentialLeafCount())) {
    throw new Error('FORWARD_RUNTIME_CREDENTIAL_ORIGIN_INTEGRITY_INVALID');
  }
  const unsigned = { ...receipt };
  delete unsigned.runtime_plan_receipt_sha256;
  if (digest(unsigned) !== receipt.runtime_plan_receipt_sha256 ||
      receipt.actions.some((action) => !action || typeof action !== 'object' || Array.isArray(action)) ||
      new Map(receipt.actions.map((action) => [action.reference_id, action])).size !== 37) {
    throw new Error('FORWARD_RUNTIME_RECEIPT_INTEGRITY_INVALID');
  }
  for (const action of receipt.actions) {
    if (!action || typeof action !== 'object' || Array.isArray(action) ||
        typeof action.workflow_id !== 'string' || typeof action.node_id !== 'string' ||
        typeof action.credential_origin !== 'string' || typeof action.credential_tuple_digest !== 'string') {
      throw new Error('FORWARD_RUNTIME_CREDENTIAL_ORIGIN_INTEGRITY_INVALID');
    }
    const reference = exported.references.find((item) => item.reference_id === action.reference_id);
    if (!reference || action.workflow_id !== reference.workflow_id || action.node_id !== reference.node_id ||
        action.revision_id !== reference.revision_id || action.canonical_table_id !== reference.canonical_table_id ||
        typeof action.post_revision_id !== 'string' || !action.post_revision_id) {
      throw new Error('FORWARD_RUNTIME_ACTION_MISMATCH');
    }
    const leaf = credentialLeavesFromEnvironment().find((candidate) => candidate.key === `${action.workflow_id}:${action.node_id}`);
    const expectedOrigin = leaf
      ? receipt.credential_origin_bitset[credentialLeavesFromEnvironment().findIndex((candidate) => candidate.key === leaf.key)] === '1' ? 'placeholder' : 'opaque'
      : 'none';
    const expectedTupleDigest = digest({
      workflow_id: action.workflow_id,
      node_id: action.node_id,
      credential_type: leaf?.credential_type || '',
      placeholder: leaf?.placeholder || '',
    });
    if (!action || action.credential_origin !== expectedOrigin || action.credential_tuple_digest !== expectedTupleDigest) {
      throw new Error('FORWARD_RUNTIME_CREDENTIAL_ORIGIN_INTEGRITY_INVALID');
    }
  }
  return receipt;
}
function replayValidationExport(exported, receipt) {
  const actions = new Map(receipt.actions.map((action) => [action.reference_id, action]));
  if (actions.size !== exported.references.length) {
    throw new Error('FORWARD_REPLAY_JOURNAL_ACTION_MISMATCH');
  }
  return {
    ...exported,
    export_sha256: receipt.export_sha256,
    references: exported.references.map((reference) => {
      const action = actions.get(reference.reference_id);
      if (!action) throw new Error('FORWARD_REPLAY_JOURNAL_ACTION_MISMATCH');
      return { ...reference, revision_id: action.revision_id };
    }),
  };
}

function receiptMatchesCommittedState(receipt, readback, state, canonicalSource = null) {
  return receipt.readback_digest_sha256 === digest(readback) &&
    receipt.credential_state_digest_after === state.credentialStateDigest &&
    receipt.workflow_credential_objects_digest_after === state.workflowCredentialObjectsDigest &&
    receipt.workflow_revision_digest_after === state.workflowRevisionDigest &&
    (state.credentialOriginBitset === undefined ||
      receipt.credential_origin_post_bitset === state.credentialOriginBitset) &&
    (canonicalSource === null ||
      (receipt.canonical_source_sha256 === canonicalSource.sha256 &&
       receipt.target_projection_sha256 === canonicalSource.targetProjectionSha256 &&
       receipt.target_digest === canonicalSource.targetDigest &&
       receipt.target_readback_sha256 === state.targetReadbackDigest));
}


function selectForwardReplayJournal(rows, graph, lock, canonicalSource, exported, readback, state) {
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error('FORWARD_REPLAY_ORIGINAL_JOURNAL_NOT_FOUND');
  }
  const matching = rows.map((row) => {
    const receipt = typeof row.receipt === 'string' ? JSON.parse(row.receipt) : row.receipt;
    validateForwardReceipt(
      receipt,
      replayValidationExport(exported, receipt),
      lock.resource,
      lock.binding,
      canonicalSource.sha256,
    );
    return { row, receipt };
  }).filter(({ receipt }) =>
    receiptMatchesCommittedState(receipt, readback, state, canonicalSource));
  if (matching.length === 0) {
    throw new Error('FORWARD_REPLAY_ORIGINAL_JOURNAL_STATE_MISMATCH');
  }
  if (matching.length !== 1) {
    throw new Error('FORWARD_REPLAY_ORIGINAL_JOURNAL_AMBIGUOUS');
  }
  const { row, receipt } = matching[0];
  const bodies = row.rollback_workflows;
  if (!Array.isArray(bodies) || bodies.length !== graph.workflows.size ||
      digest(bodies) !== receipt.rollback_workflows_sha256) {
    throw new Error('FORWARD_REPLAY_ROLLBACK_SNAPSHOT_MISMATCH');
  }
  const rollbackWorkflows = new Map();
  for (const workflow of bodies) {
    if (!workflow || !graph.workflows.has(workflow.id) || rollbackWorkflows.has(workflow.id)) {
      throw new Error('FORWARD_REPLAY_ROLLBACK_SNAPSHOT_MISMATCH');
    }
    rollbackWorkflows.set(workflow.id, workflow);
  }
  if (findReferences(graph, rollbackWorkflows).some((item) => !item.oldMatches)) {
    throw new Error('FORWARD_REPLAY_ROLLBACK_SNAPSHOT_NOT_LEGACY');
  }
  validateRollbackTargets(row.rollback_targets, receipt, canonicalSource.targets);
  return receipt;
}


async function loadForwardReplayJournal(client, graph, lock, canonicalSource, exported, readback, state) {
  const result = await client.query(
    `SELECT receipt, rollback_workflows, rollback_targets
       FROM ${JOURNAL_TABLE}
      WHERE project_id = $1
        AND operation = 'FORWARD'
        AND lock_resource = $2
        AND receipt->>'schema_version' = $3
        AND receipt->>'canonical_source_sha256' = $4
        AND receipt->>'operation_nonce' = $5
        AND receipt->>'protected_quiescence_receipt_digest' = $6
        AND receipt->>'required_live_export_digest' = $7
        AND receipt->>'contract_bijection_digest' = $8
      ORDER BY created_at DESC`,
    [
      projectId,
      lock.resource,
      RUNTIME_SCHEMA,
      canonicalSource.sha256,
      lock.binding.operation_nonce,
      lock.binding.protected_quiescence_receipt_digest,
      lock.binding.required_live_export_digest,
      lock.binding.contract_bijection_digest,
    ],
  );
  return selectForwardReplayJournal(
    result.rows,
    graph,
    lock,
    canonicalSource,
    exported,
    readback,
    state,
  );
}


function validateRollbackJournalReceipt(receipt, exported, resource, binding) {
  if (!receipt || receipt.operation !== 'ROLLBACK' ||
      ![LEGACY_RUNTIME_SCHEMA, PREVIOUS_RUNTIME_SCHEMA, RUNTIME_SCHEMA].includes(receipt.schema_version) ||
      receipt.project_id !== projectId || receipt.lock_resource !== resource ||
      receipt.export_sha256 !== exported.export_sha256 ||
      receipt.durable_journal !== true ||
      receipt.commit_protocol !== 'postgresql_synchronous_wal' ||
      receipt.readback_verified !== true || receipt.action_count !== 37 ||
      !Array.isArray(receipt.actions) || receipt.actions.length !== 37 ||
      !/^[0-9a-f]{64}$/.test(receipt.readback_digest_sha256) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_state_digest_after) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_credential_objects_digest_after) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_revision_digest_after) ||
      receipt.credential_contract_digest !== digest(credentialBindingsFromEnvironment()) ||
      !new RegExp(`^[01]{${credentialLeafCount()}}$`).test(receipt.credential_origin_post_bitset) ||
      receipt.credential_origin_post_digest !== credentialOriginDigest(receipt.credential_origin_post_bitset)) {
    throw new Error('ROLLBACK_RUNTIME_JOURNAL_INTEGRITY_INVALID');
  }
  if (receipt.schema_version === RUNTIME_SCHEMA &&
      (!/^[0-9a-f]{64}$/.test(receipt.canonical_source_sha256) ||
       !/^[0-9a-f]{64}$/.test(receipt.target_digest) ||
       !/^[0-9a-f]{64}$/.test(receipt.target_projection_sha256) ||
       !/^[0-9a-f]{64}$/.test(receipt.target_readback_sha256) ||
       !/^[0-9a-f]{64}$/.test(receipt.rollback_targets_sha256) ||
       !/^[0-9a-f]{64}$/.test(receipt.forward_runtime_receipt_sha256) ||
       receipt.target_rows_restored !== true ||
       receipt.preserved_table_writes !== false)) {
    throw new Error('ROLLBACK_RUNTIME_TARGET_JOURNAL_INTEGRITY_INVALID');
  }
  validateBinding(receipt, binding, 'ROLLBACK_RUNTIME_JOURNAL');
  const unsigned = { ...receipt };
  delete unsigned.runtime_plan_receipt_sha256;
  if (digest(unsigned) !== receipt.runtime_plan_receipt_sha256) {
    throw new Error('ROLLBACK_RUNTIME_JOURNAL_INTEGRITY_INVALID');
  }
  return receipt;
}


async function recoverRuntimeJournal() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  const graph = validateExport(exported);
  const recoveryForwardReceipt = operation === 'ROLLBACK'
    ? decode('FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64')
    : null;
  const canonicalSource = operation === 'FORWARD' ||
    recoveryForwardReceipt?.schema_version !== LEGACY_RUNTIME_SCHEMA
    ? canonicalSourceFromInput(graph)
    : null;
  const expectedForwardReceiptSha = recoveryForwardReceipt?.schema_version === RUNTIME_SCHEMA
    ? decodedSha256('FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64')
    : null;
  const lock = await acquireProjectLock();
  let committed = false;
  try {
    await verifyInFlight(lock.client);
    const credentials = await credentialState(lock.client);
    const credentialsByBinding = new Map(
      credentials.values.map((value) => [value.placeholder, value]),
    );
    const workflows = await loadWorkflows(lock.client, graph, false);
    const origins = validateCredentialBindings(workflows, credentialsByBinding);
    const state = {
      credentialStateDigest: credentials.digest,
      workflowCredentialObjectsDigest: workflowCredentialObjectsDigest(workflows),
      workflowRevisionDigest: workflowRevisionDigest(workflows),
      credentialOriginBitset: credentialOriginBitset(origins),
      targetReadbackDigest: null,
    };
    let targetState = null;
    if (canonicalSource) {
      targetState = await loadTargetState(lock.client, canonicalSource.targets);
      state.targetReadbackDigest = targetStateDigest(targetState);
    }
    let validated;
    if (operation === 'FORWARD') {
      if ([...targetState].some(([name, table]) =>
        !sameJson(table.userRows, canonicalSource.targets.get(name).rows))) {
        throw new Error('FORWARD_RUNTIME_JOURNAL_STATE_MISMATCH');
      }
      validated = await loadForwardReplayJournal(
        lock.client,
        graph,
        lock,
        canonicalSource,
        exported,
        workflowReadback(workflows),
        state,
      );
    } else {
      const result = await lock.client.query(
        `SELECT receipt, rollback_targets
           FROM ${JOURNAL_TABLE}
          WHERE project_id = $1
            AND lock_resource = $2
            AND operation = 'ROLLBACK'
            AND receipt->>'export_sha256' = $3
            AND receipt->>'operation_nonce' = $4
            AND receipt->>'protected_quiescence_receipt_digest' = $5
            AND receipt->>'required_live_export_digest' = $6
            AND receipt->>'contract_bijection_digest' = $7
          ORDER BY created_at DESC`,
        [
          projectId,
          lock.resource,
          exported.export_sha256,
          lock.binding.operation_nonce,
          lock.binding.protected_quiescence_receipt_digest,
          lock.binding.required_live_export_digest,
          lock.binding.contract_bijection_digest,
        ],
      );
      const matches = (result.rows || []).map((row) => {
        const receipt = typeof row.receipt === 'string' ? JSON.parse(row.receipt) : row.receipt;
        validateRollbackJournalReceipt(receipt, exported, lock.resource, lock.binding);
        if (receipt.schema_version === RUNTIME_SCHEMA &&
            receipt.forward_runtime_receipt_sha256 !== expectedForwardReceiptSha) {
          throw new Error('ROLLBACK_RUNTIME_FORWARD_RECEIPT_BINDING_INVALID');
        }
        const readback = receipt.schema_version === LEGACY_RUNTIME_SCHEMA
          ? selectorReadback(findReferences(graph, workflows))
          : workflowReadback(workflows);
        return { row, receipt, readback };
      }).filter(({ receipt, readback }) =>
        receiptMatchesCommittedState(receipt, readback, state, canonicalSource));
      if (matches.length === 0) throw new Error('ROLLBACK_RUNTIME_JOURNAL_STATE_MISMATCH');
      if (matches.length !== 1) throw new Error('ROLLBACK_RUNTIME_JOURNAL_AMBIGUOUS');
      const { row: matchedRow, receipt: matchedReceipt } = matches[0];
      validated = matchedReceipt;
      if (validated.schema_version === RUNTIME_SCHEMA) {
        validateRollbackTargets(
          matchedRow.rollback_targets,
          validated,
          canonicalSource.targets,
        );
      }
    }
    await lock.client.query('COMMIT');
    committed = true;
    await writeRuntimeReceipt(validated);
  } catch (error) {
    if (!committed) await lock.client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    await lock.client.end();
  }
}

async function acquireProjectLock() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  const lockReceipt = decode('FINANCE_FOUR_TABLE_LOCK_B64');
  const resource = `finance_four_table_cutover:${projectId}`;
  const binding = bindingFromEnvironment();
  validateLockReceipt(lockReceipt, exported, binding, resource);
  const client = new pg.Client(databaseOptions(process.env));
  const timeouts = transactionTimeouts(process.env);
  try {
    await client.connect();
    await client.query('BEGIN');
    await client.query(`SET LOCAL lock_timeout = '${timeouts.lockTimeoutMs}ms'`);
    await client.query(`SET LOCAL statement_timeout = '${timeouts.statementTimeoutMs}ms'`);
    // Require the commit to flush PostgreSQL WAL before releasing the project lock.
    await client.query("SET LOCAL synchronous_commit = 'on'");
    const result = await client.query('SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0)) AS acquired', [resource]);
    if (result.rows?.[0]?.acquired !== true) throw new Error('PROJECT_WRITER_LOCK_BUSY');
    return { client, resource, binding };
  } catch (error) {
    // BEGIN, SET, and lock acquisition all happen before execute() owns cleanup.
    await client.query('ROLLBACK').catch(() => {});
    await client.end().catch(() => {});
    throw error;
  }
}

async function execute() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  const graph = validateExport(exported);
  const forwardReceipt = operation === 'ROLLBACK' ? decode('FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64') : null;
  if (operation === 'ROLLBACK' && (!forwardReceipt || forwardReceipt.operation !== 'FORWARD' ||
      ![LEGACY_RUNTIME_SCHEMA, PREVIOUS_RUNTIME_SCHEMA, RUNTIME_SCHEMA].includes(forwardReceipt.schema_version))) {
    throw new Error('FORWARD_RUNTIME_RECEIPT_REQUIRED');
  }
  const canonicalSource = operation === 'FORWARD' || forwardReceipt.schema_version !== LEGACY_RUNTIME_SCHEMA
    ? canonicalSourceFromInput(graph) : null;
  const lock = await acquireProjectLock();
  const commitAndJournal = async (
    unsignedReceipt,
    rollbackWorkflows = null,
    rollbackTargets = null,
  ) => {
    const journal = {
      ...unsignedReceipt,
      ...lock.binding,
      durable_journal: true,
      commit_protocol: 'postgresql_synchronous_wal',
    };
    validateBinding(journal, lock.binding, 'RUNTIME_JOURNAL');
    await persistRecoveryJournal(lock.client, journal, rollbackWorkflows, rollbackTargets);
    await lock.client.query('COMMIT');
    // COMMIT releases the transaction-scoped advisory lock before this output.
    // The committed journal is the recovery boundary for this read-only step.
    await writeRuntimeReceipt(journal);
    return journal;
  };
  try {
    await verifyInFlight(lock.client);
    const credentialsBefore = await credentialState(lock.client);
    const credentialsByBinding = new Map(credentialsBefore.values.map((value) => [value.placeholder, value]));
    const workflows = await loadWorkflows(lock.client, graph, false);
    const credentialOriginsBefore = validateCredentialBindings(workflows, credentialsByBinding);
    const credentialOriginBitsetBefore = credentialOriginBitset(credentialOriginsBefore);
    const workflowCredentialsBefore = workflowCredentialObjectsDigest(workflows);
    const workflowRevisionDigestBefore = workflowRevisionDigest(workflows);
    const workflowOpaqueCredentialsBefore = workflowOpaqueCredentialObjectsDigest(workflows, credentialOriginsBefore);
    if (operation === 'FORWARD') {
      const prestate = findReferences(graph, workflows);
      if (prestate.some((item) => !item.oldMatches && !item.targetMatches)) throw new Error('LIVE_REFERENCE_SELECTOR_DRIFT');
      const rebound = prestate.filter((item) => item.reference.canonical_table_id !== null);
      const firstRun = rebound.every((item) => item.oldMatches);
      const replay = rebound.every((item) => item.targetMatches);
      if (!firstRun && !replay) throw new Error('LIVE_REFERENCE_SELECTOR_MIXED_STATE');
      if (firstRun) {
        for (const [workflowId, revision] of graph.workflows) {
          assertWorkflow(
            workflows.get(workflowId),
            revision,
            graph.workflowBodyDigests.get(workflowId),
            workflowId,
          );
        }
      }
      const plan = applyForward(workflows, credentialsByBinding, canonicalSource.workflows);
      const targetPlan = await applyTargetProjection(lock.client, canonicalSource.targets);
      if (plan.alreadyApplied) {
        if (!targetPlan.alreadyApplied) throw new Error('FORWARD_REPLAY_TARGET_STATE_MISMATCH');
        const readback = workflowReadback(workflows);
        if (!sameJson(readback, workflowReadback(plan.expected)) ||
            !allCredentialOrigins(credentialOriginsBefore, 'opaque')) {
          throw new Error('FORWARD_REPLAY_CANONICAL_STATE_MISMATCH');
        }
        const original = await loadForwardReplayJournal(
          lock.client,
          graph,
          lock,
          canonicalSource,
          exported,
          readback,
          {
            credentialStateDigest: credentialsBefore.digest,
            workflowCredentialObjectsDigest: workflowCredentialsBefore,
            workflowRevisionDigest: workflowRevisionDigestBefore,
            targetReadbackDigest: targetStateDigest(targetPlan.after),
          },
        );
        await lock.client.query('COMMIT');
        await writeRuntimeReceipt(original);
        return original;
      }
      if (targetPlan.alreadyApplied) throw new Error('FORWARD_FIRST_RUN_TARGET_STATE_MISMATCH');
      const rollbackWorkflows = [...workflows.values()].map((workflow) => ({
        id: workflow.id, ...Object.fromEntries(WORKFLOW_BODY_FIELDS.map((field) => [field, workflow[field] ?? null])),
      })).sort((left, right) => left.id.localeCompare(right.id));
      await updateWorkflows(lock.client, plan.changed);
      const updated = await loadWorkflows(lock.client, graph, false);
      if (!sameJson(workflowReadback(updated), workflowReadback(plan.expected))) throw new Error('CANONICAL_GRAPH_POST_READBACK_MISMATCH');
      const postCredentials = await loadWorkflows(lock.client, graph, false);
      const credentialOriginsAfter = validateCredentialBindings(postCredentials, credentialsByBinding);
      if (!allCredentialOrigins(credentialOriginsAfter, 'opaque')) throw new Error('CREDENTIAL_BINDING_POST_STATE_NOT_OPAQUE');
      const credentialOriginBitsetAfter = credentialOriginBitset(credentialOriginsAfter);
      const workflowRevisionDigestAfter = workflowRevisionDigest(updated);
      const actions = prestate.map((before) => {
        const reference = before.reference;
        const credentialBinding = credentialBindingForNode(before.workflow, before.node);
        const credentialOrigin = credentialBinding ? credentialOriginsBefore.get(credentialBinding.key) : 'none';
        return { reference_id: reference.reference_id, workflow_id: reference.workflow_id, revision_id: reference.revision_id, post_revision_id: String(updated.get(reference.workflow_id).versionId || ''), node_id: reference.node_id, selector: before.selector, canonical_table_id: reference.canonical_table_id, credential_origin: credentialOrigin, credential_tuple_digest: digest({ workflow_id: reference.workflow_id, node_id: reference.node_id, credential_type: credentialBinding?.credential_type || '', placeholder: credentialBinding?.placeholder || '' }) };
      });
      const readback = workflowReadback(updated);
      const replayWorkflows = await loadWorkflows(lock.client, graph, false);
      const replayPlan = applyForward(replayWorkflows, credentialsByBinding, canonicalSource.workflows);
      if (!replayPlan.alreadyApplied || !sameJson(workflowReadback(replayWorkflows), readback)) {
        throw new Error('FORWARD_REPLAY_READBACK_MISMATCH');
      }
      const credentialsAfter = await credentialState(lock.client);
      if (credentialsAfter.digest !== credentialsBefore.digest) throw new Error('CREDENTIAL_STATE_CHANGED');
      const workflowCredentialsAfter = workflowCredentialObjectsDigest(postCredentials);
      if (workflowOpaqueCredentialObjectsDigest(postCredentials, credentialOriginsBefore) !== workflowOpaqueCredentialsBefore) throw new Error('WORKFLOW_OPAQUE_CREDENTIAL_OBJECTS_CHANGED');
      const targetRowCounts = Object.fromEntries(
        [...canonicalSource.targets].map(([name, target]) => [name, target.rows.length]),
      );
      const unsigned = { schema_version: RUNTIME_SCHEMA, operation, project_id: projectId, lock_resource: lock.resource, export_sha256: exported.export_sha256, ...lock.binding, ...credentialContractSummary(), action_count: 37, replay_noop: false, readback_verified: true, readback_digest_sha256: digest(readback), credential_state_digest_before: credentialsBefore.digest, credential_state_digest_after: credentialsAfter.digest, workflow_credential_objects_digest_before: workflowCredentialsBefore, workflow_credential_objects_digest_after: workflowCredentialsAfter, workflow_revision_digest_before: workflowRevisionDigestBefore, workflow_revision_digest_after: workflowRevisionDigestAfter, credential_origin_bitset: credentialOriginBitsetBefore, credential_origin_post_bitset: credentialOriginBitsetAfter, credential_origin_digest: credentialOriginDigest(credentialOriginBitsetBefore), credential_origin_post_digest: credentialOriginDigest(credentialOriginBitsetAfter), credential_ids_recorded: false, secret_values_recorded: false, actions, canonical_source_sha256: canonicalSource.sha256, rollback_workflows_sha256: digest(rollbackWorkflows), target_digest: canonicalSource.targetDigest, target_projection_sha256: canonicalSource.targetProjectionSha256, target_readback_sha256: targetStateDigest(targetPlan.after), rollback_targets_sha256: digest(targetPlan.rollbackTargets), target_row_counts: targetRowCounts, preserved_table_writes: false };
      unsigned.target_prestate = targetStateEvidence(targetPlan.before);
      return await commitAndJournal({ ...unsigned, runtime_plan_receipt_sha256: digest({ ...unsigned, durable_journal: true, commit_protocol: 'postgresql_synchronous_wal' }) }, rollbackWorkflows, targetPlan.rollbackTargets);
    }
    if (forwardReceipt.project_id !== projectId || forwardReceipt.lock_resource !== lock.resource) {
      throw new Error('FORWARD_RUNTIME_RECEIPT_BINDING_INVALID');
    }
    const replayBoundRollback = (
      forwardReceipt.schema_version === RUNTIME_SCHEMA
      && forwardReceipt.export_sha256 !== exported.export_sha256
    );
    validateForwardReceipt(
      forwardReceipt,
      replayBoundRollback
        ? replayValidationExport(exported, forwardReceipt)
        : exported,
      lock.resource,
      lock.binding,
      canonicalSource?.sha256 || null,
    );
    if (forwardReceipt.credential_state_digest_before !== forwardReceipt.credential_state_digest_after) throw new Error('FORWARD_CREDENTIAL_STATE_CHANGED');
    if (forwardReceipt.credential_state_digest_after !== credentialsBefore.digest) throw new Error('FORWARD_CREDENTIAL_STATE_DRIFT');
    if (forwardReceipt.workflow_credential_objects_digest_after !== workflowCredentialsBefore) throw new Error('ROLLBACK_WORKFLOW_CREDENTIAL_STATE_DRIFT');
    if (forwardReceipt.workflow_revision_digest_after !== workflowRevisionDigestBefore) throw new Error('ROLLBACK_WORKFLOW_REVISION_STATE_DRIFT');
    if (!allCredentialOrigins(credentialOriginsBefore, 'opaque')) throw new Error('ROLLBACK_CREDENTIAL_STATE_NOT_OPAQUE');
    const expectedCredentialOrigins = credentialOriginsFromBitset(forwardReceipt.credential_origin_bitset);
    const workflowOpaqueCredentialsBeforeRollback = workflowOpaqueCredentialObjectsDigest(workflows, expectedCredentialOrigins);
    const byId = new Map(forwardReceipt.actions.map((action) => [action.reference_id, action]));
    let rollbackWorkflows;
    if (forwardReceipt.schema_version === LEGACY_RUNTIME_SCHEMA) {
      await verifyLegacyForwardJournal(lock.client, forwardReceipt);
      rollbackWorkflows = rollbackSelectorReceipt(graph, workflows, forwardReceipt, expectedCredentialOrigins);
    } else {
      const canonicalPlan = applyForward(workflows, credentialsByBinding, canonicalSource.workflows);
      if (!canonicalPlan.alreadyApplied || digest(workflowReadback(workflows)) !== forwardReceipt.readback_digest_sha256) {
        throw new Error('ROLLBACK_CANONICAL_GRAPH_DRIFT');
      }
      rollbackWorkflows = await loadRollbackWorkflows(
        lock.client,
        forwardReceipt,
        graph,
        !replayBoundRollback,
      );
    }
    let rollbackTargetRows = null;
    if (forwardReceipt.schema_version === RUNTIME_SCHEMA) {
      const currentTargets = await loadTargetState(lock.client, canonicalSource.targets);
      if ([...currentTargets].some(([name, table]) =>
        !sameJson(table.userRows, canonicalSource.targets.get(name).rows)) ||
          targetStateDigest(currentTargets) !== forwardReceipt.target_readback_sha256 ||
          canonicalSource.targetProjectionSha256 !== forwardReceipt.target_projection_sha256 ||
          canonicalSource.targetDigest !== forwardReceipt.target_digest) {
        throw new Error('ROLLBACK_TARGET_STATE_DRIFT');
      }
      rollbackTargetRows = await loadRollbackTargets(
        lock.client,
        forwardReceipt,
        canonicalSource.targets,
      );
    }
    const changed = new Map([...rollbackWorkflows].map(([id, workflow]) => [
      id, Object.fromEntries(WORKFLOW_BODY_FIELDS.map((field) => [field, workflow[field]])),
    ]));
    await updateWorkflows(lock.client, changed);
    let restoredTargets = null;
    if (rollbackTargetRows !== null) {
      restoredTargets = await restoreTargetSnapshot(
        lock.client,
        canonicalSource.targets,
        rollbackTargetRows,
      );
    }
    const restored = await loadWorkflows(lock.client, graph, false);
    const restoredCredentialOrigins = validateCredentialBindings(restored, credentialsByBinding);
    if (credentialOriginBitset(restoredCredentialOrigins) !== forwardReceipt.credential_origin_bitset) {
      throw new Error('ROLLBACK_CREDENTIAL_ORIGIN_POST_READBACK_MISMATCH');
    }
    if (!sameJson(workflowReadback(restored), workflowReadback(rollbackWorkflows))) throw new Error('ROLLBACK_POST_READBACK_MISMATCH');
    const readback = forwardReceipt.schema_version === LEGACY_RUNTIME_SCHEMA
      ? selectorReadback(findReferences(graph, restored)) : workflowReadback(restored);
    const credentialsAfter = await credentialState(lock.client);
    if (credentialsAfter.digest !== credentialsBefore.digest) throw new Error('CREDENTIAL_STATE_CHANGED');
    const postCredentials = await loadWorkflows(lock.client, graph, false);
    const workflowCredentialsAfter = workflowCredentialObjectsDigest(postCredentials);
    const workflowRevisionDigestAfter = workflowRevisionDigest(restored);
    if (workflowOpaqueCredentialObjectsDigest(postCredentials, expectedCredentialOrigins) !== workflowOpaqueCredentialsBeforeRollback) throw new Error('WORKFLOW_OPAQUE_CREDENTIAL_OBJECTS_CHANGED');
    const credentialOriginBitsetAfter = credentialOriginBitset(restoredCredentialOrigins);
    if (restoredTargets !== null &&
        !sameJson(targetStateEvidence(restoredTargets), forwardReceipt.target_prestate)) {
      throw new Error('ROLLBACK_TARGET_PRESTATE_RESTORATION_MISMATCH');
    }
    const unsignedRollback = { schema_version: forwardReceipt.schema_version, operation, project_id: projectId, lock_resource: lock.resource, export_sha256: exported.export_sha256, ...lock.binding, ...credentialContractSummary(), action_count: 37, replay_noop: false, readback_verified: true, readback_digest_sha256: digest(readback), credential_state_digest_before: credentialsBefore.digest, credential_state_digest_after: credentialsAfter.digest, workflow_credential_objects_digest_before: workflowCredentialsBefore, workflow_credential_objects_digest_after: workflowCredentialsAfter, workflow_revision_digest_before: workflowRevisionDigestBefore, workflow_revision_digest_after: workflowRevisionDigestAfter, credential_origin_bitset: credentialOriginBitsetBefore, credential_origin_post_bitset: credentialOriginBitsetAfter, credential_origin_digest: credentialOriginDigest(credentialOriginBitsetBefore), credential_origin_post_digest: credentialOriginDigest(credentialOriginBitsetAfter), credential_ids_recorded: false, secret_values_recorded: false, actions: byId.size === 37 ? [...byId.values()] : [] };
    if (forwardReceipt.schema_version === RUNTIME_SCHEMA) {
      Object.assign(unsignedRollback, {
        canonical_source_sha256: canonicalSource.sha256,
        target_digest: forwardReceipt.target_digest,
        target_projection_sha256: forwardReceipt.target_projection_sha256,
        target_readback_sha256: targetStateDigest(restoredTargets),
        rollback_targets_sha256: forwardReceipt.rollback_targets_sha256,
        forward_runtime_receipt_sha256: decodedSha256('FINANCE_FOUR_TABLE_FORWARD_RECEIPT_B64'),
        target_rows_restored: true,
        preserved_table_writes: false,
      });
    }
    const rollbackReceipt = { ...unsignedRollback, runtime_plan_receipt_sha256: digest({ ...unsignedRollback, durable_journal: true, commit_protocol: 'postgresql_synchronous_wal' }) };
    return await commitAndJournal(rollbackReceipt, null, restoredTargets === null ? null : targetSnapshot(restoredTargets));
  } catch (error) {
    // PostgreSQL transaction rollback is the only compensation path. A second
    // application update could itself fail or create a new revision.
    await lock.client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    await lock.client.end();
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

async function main() {
  if (process.env.FINANCE_FOUR_TABLE_RECOVER_JOURNAL === '1') {
    const expectedReason = operation === 'FORWARD'
      ? FORWARD_RECOVERY_REASON
      : ROLLBACK_RECOVERY_REASON;
    if (process.env.FINANCE_FOUR_TABLE_RECOVERY_REASON !== expectedReason) {
      throw new Error(`${operation}_JOURNAL_RECOVERY_REASON_REQUIRED`);
    }
    await recoverRuntimeJournal();
  } else {
    await execute();
  }
}

(async () => {
  try {
    await main();
  } catch (error) {
    const detail = error instanceof Error ? error.stack || error.message : String(error);
    process.stderr.write(`finance four-table runtime failure:${detail}\n`);
    process.exitCode = 1;
  }
})();
