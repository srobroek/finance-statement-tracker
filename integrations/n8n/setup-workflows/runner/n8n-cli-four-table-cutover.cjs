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

function digestText(value, code) {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) throw new Error(code);
  return value;
}

function credentialBindingsFromEnvironment() {
  const encoded = process.env.FINANCE_FOUR_TABLE_CREDENTIAL_BINDINGS_B64;
  if (typeof encoded !== 'string' || encoded.length === 0) throw new Error('CREDENTIAL_BINDINGS_REQUIRED');
  let contract;
  try { contract = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8')); } catch { throw new Error('CREDENTIAL_BINDINGS_INVALID'); }
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
  const types = new Set();
  for (const binding of contract.bindings) {
    if (!binding || typeof binding !== 'object' || Array.isArray(binding) ||
        Object.keys(binding).sort().join(',') !== 'credential_type,node_type,nodes,placeholder') {
      throw new Error('CREDENTIAL_BINDING_KEYS_INVALID');
    }
    if (!/^BIND_[A-Z0-9_]+$/.test(text(binding.placeholder, 'CREDENTIAL_PLACEHOLDER_INVALID')) ||
        !text(binding.credential_type, 'CREDENTIAL_TYPE_INVALID') || !text(binding.node_type, 'CREDENTIAL_NODE_TYPE_INVALID') ||
        !Array.isArray(binding.nodes) || binding.nodes.length === 0) throw new Error('CREDENTIAL_BINDING_INVALID');
    if (placeholders.has(binding.placeholder) || types.has(binding.credential_type)) throw new Error('CREDENTIAL_BINDING_AMBIGUOUS');
    placeholders.add(binding.placeholder); types.add(binding.credential_type);
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
  if (contract.bindings.length !== 8 || leaves.size !== 36) throw new Error('CREDENTIAL_BINDING_COVERAGE_INVALID');
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
  if (references.size !== 33) throw new Error('COMPLETE_LIVE_REFERENCE_EXPORT_REQUIRED');
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
  const loaded = new Map();
  const workflowIds = [...graph.workflows.keys()];
  const result = await client.query(
    `SELECT w.id, w.name, w.active, w."activeVersionId", w."versionId", w.nodes, w.connections,
            w.meta, w.settings, w."pinData"
       FROM workflow_entity w
       JOIN shared_workflow s ON s."workflowId" = w.id
      WHERE s."projectId" = $1 AND s.role = 'workflow:owner' AND w.id = ANY($2::text[])
      FOR UPDATE`,
    [projectId, workflowIds],
  );
  const rows = new Map(result.rows.map((workflow) => [workflow.id, workflow]));
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
      const associated = credentials.get(binding.credential_type);
      if (!ref || typeof ref !== 'object' || Array.isArray(ref) || Object.keys(ref).sort().join(',') !== 'id,name' ||
          typeof ref.id !== 'string' || !ref.id || typeof ref.name !== 'string' || !ref.name) {
        throw new Error(`CREDENTIAL_BINDING_ASSOCIATION_MISMATCH:${key}`);
      }
      const placeholder = ref.id === binding.placeholder && ref.name === binding.placeholder;
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

function credentialOriginDigest(bitset) {
  return digest({ credential_contract_digest: digest(credentialBindingsFromEnvironment()), credential_leaf_count: 36, credential_origin_bitset: bitset });
}

function credentialOriginsFromBitset(bitset) {
  if (typeof bitset !== 'string' || !/^[01]{36}$/.test(bitset)) throw new Error('CREDENTIAL_ORIGIN_BITSET_INVALID');
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
  const result = await client.query(
    `SELECT c.id, c.name, c.type, s."projectId" AS project_id, s.role
       FROM credentials_entity c
       JOIN shared_credentials s ON s."credentialsId" = c.id
      WHERE s.role = 'credential:owner'
      ORDER BY c.id, s."projectId"`,
  );
  const rows = result.rows || [];
  const ownerShares = new Map();
  for (const row of rows) {
    if (!row.id || !row.name || !row.type || !row.project_id || row.role !== 'credential:owner') throw new Error('CREDENTIAL_ASSOCIATION_INVALID');
    const normalized = { id: String(row.id), name: String(row.name), type: String(row.type), project_id: String(row.project_id), role: row.role };
    const shares = ownerShares.get(normalized.id) || [];
    shares.push(normalized);
    ownerShares.set(normalized.id, shares);
  }
  const byType = new Map();
  for (const row of rows.filter((candidate) => String(candidate.project_id) === projectId)) {
    const type = String(row.type);
    const value = { id: String(row.id), name: String(row.name), type, project_id: String(row.project_id), role: row.role };
    const shares = ownerShares.get(value.id) || [];
    if (shares.some((share) => share.project_id !== projectId)) throw new Error(`CREDENTIAL_OWNER_SHARE_FOREIGN:${value.id}`);
    if (shares.length !== 1) throw new Error(`CREDENTIAL_OWNER_SHARE_AMBIGUOUS:${value.id}`);
    if (shares[0].name !== value.name || shares[0].type !== value.type) throw new Error(`CREDENTIAL_OWNER_SHARE_AMBIGUOUS:${value.id}`);
    if (byType.has(type)) throw new Error(`CREDENTIAL_TYPE_AMBIGUOUS:${type}`);
    byType.set(type, value);
  }
  const bindings = credentialBindingsFromEnvironment();
  const types = new Set(bindings.map((binding) => binding.credential_type));
  if (byType.size !== types.size || [...types].some((type) => !byType.has(type))) throw new Error('CREDENTIAL_ASSOCIATION_COVERAGE_INVALID');
  const values = [...byType.values()].sort((a, b) => a.type.localeCompare(b.type));
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

function applyForward(prestate, credentials, workflows = new Map(prestate.map((item) => [item.workflow.id, item.workflow]))) {
  const changed = new Map();
  let alreadyApplied = true;
  const nodesFor = (workflowId) => {
    const workflow = workflows.get(workflowId);
    if (!workflow) throw new Error(`CREDENTIAL_BINDING_WORKFLOW_MISSING:${workflowId}`);
    return changed.get(workflowId) || clone(workflow.nodes);
  };
  for (const item of prestate) {
    if (!item.targetMatches) alreadyApplied = false;
    if (!item.oldMatches && !item.targetMatches) {
      throw new Error(`LIVE_REFERENCE_SELECTOR_DRIFT:${item.reference.reference_id}`);
    }
    if (!item.targetMatches) {
      const nodes = nodesFor(item.reference.workflow_id);
      const node = nodes.find((candidate) => candidate.id === item.reference.node_id);
      setSelector(node, item.reference.canonical_table_id);
      changed.set(item.reference.workflow_id, nodes);
    }
  }
  for (const leaf of credentialLeavesFromEnvironment()) {
    const workflow = workflows.get(leaf.workflow.id);
    if (!workflow) throw new Error(`CREDENTIAL_BINDING_WORKFLOW_MISSING:${leaf.workflow.id}`);
    const originalNode = workflow.nodes.find((candidate) => candidate.id === leaf.node.id);
    if (!originalNode) throw new Error(`CREDENTIAL_BINDING_NODE_MISSING:${leaf.key}`);
    const ref = originalNode.credentials?.[leaf.credential_type];
    if (ref.id !== leaf.placeholder || ref.name !== leaf.placeholder) continue;
    const associated = credentials?.get(leaf.credential_type);
    if (!associated) throw new Error(`CREDENTIAL_BINDING_ASSOCIATION_MISSING:${leaf.key}`);
    const nodes = nodesFor(leaf.workflow.id);
    const node = nodes.find((candidate) => candidate.id === leaf.node.id);
    node.credentials = { [leaf.credential_type]: { id: associated.id, name: associated.name } };
    changed.set(leaf.workflow.id, nodes);
    alreadyApplied = false;
  }
  return { changed, alreadyApplied };
}

function restoreCredentialOrigins(changed, workflows, origins) {
  for (const leaf of credentialLeavesFromEnvironment()) {
    if (origins.get(leaf.key) !== 'placeholder') continue;
    const workflow = workflows.get(leaf.workflow.id);
    if (!workflow) throw new Error(`ROLLBACK_CREDENTIAL_WORKFLOW_MISSING:${leaf.workflow.id}`);
    const nodes = changed.get(leaf.workflow.id) || clone(workflow.nodes);
    const node = nodes.find((candidate) => candidate.id === leaf.node.id);
    if (!node) throw new Error(`ROLLBACK_CREDENTIAL_NODE_MISSING:${leaf.key}`);
    node.credentials = { [leaf.credential_type]: { id: leaf.placeholder, name: leaf.placeholder } };
    changed.set(leaf.workflow.id, nodes);
  }
}

async function verifyTargets(client, targetIds) {
  const result = await client.query(
    `SELECT id, name
       FROM data_table
      WHERE "projectId" = $1 AND name = ANY($2::text[])
      ORDER BY name
      FOR UPDATE`,
    [projectId, [...targetIds.keys()]],
  );
  const tables = result.rows || [];
  if (tables.length !== TARGET_NAMES.size || new Set(tables.map((table) => table.name)).size !== TARGET_NAMES.size ||
      new Set(tables.map((table) => table.id)).size !== TARGET_NAMES.size) {
    throw new Error('EXACT_TARGET_READBACK_REQUIRED');
  }
  for (const table of tables) {
    if (!TARGET_NAMES.has(String(table.name))) throw new Error(`UNEXPECTED_TARGET_READBACK:${table.name}`);
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
      !/^[0-9a-f]{64}$/.test(receipt.readback_digest_sha256) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_state_digest_before) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_state_digest_after) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_credential_objects_digest_before) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_credential_objects_digest_after) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_revision_digest_before) ||
      !/^[0-9a-f]{64}$/.test(receipt.workflow_revision_digest_after) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_contract_digest) ||
      !Number.isInteger(receipt.credential_binding_count) || !Number.isInteger(receipt.credential_leaf_count) ||
      receipt.credential_binding_count !== 8 || receipt.credential_leaf_count !== 36 ||
      !/^[01]{36}$/.test(receipt.credential_origin_bitset) ||
      !/^[01]{36}$/.test(receipt.credential_origin_post_bitset) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_origin_digest) ||
      !/^[0-9a-f]{64}$/.test(receipt.credential_origin_post_digest) ||
      receipt.credential_ids_recorded !== false || receipt.secret_values_recorded !== false || !Array.isArray(receipt.actions)) {
    throw new Error('FORWARD_RUNTIME_RECEIPT_INTEGRITY_INVALID');
  }
  validateBinding(receipt, binding, 'FORWARD_RUNTIME_RECEIPT');
  if (receipt.credential_contract_digest !== digest(credentialBindingsFromEnvironment()) ||
      receipt.credential_origin_digest !== credentialOriginDigest(receipt.credential_origin_bitset) ||
      receipt.credential_origin_post_digest !== credentialOriginDigest(receipt.credential_origin_post_bitset) ||
      receipt.credential_origin_post_bitset !== '0'.repeat(36)) {
    throw new Error('FORWARD_RUNTIME_CREDENTIAL_ORIGIN_INTEGRITY_INVALID');
  }
  const unsigned = { ...receipt };
  delete unsigned.runtime_plan_receipt_sha256;
  if (digest(unsigned) !== receipt.runtime_plan_receipt_sha256 ||
      receipt.actions.some((action) => !action || typeof action !== 'object' || Array.isArray(action)) ||
      new Map(receipt.actions.map((action) => [action.reference_id, action])).size !== 33) {
    throw new Error('FORWARD_RUNTIME_RECEIPT_INTEGRITY_INVALID');
  }
  for (const action of receipt.actions) {
    if (!action || typeof action !== 'object' || Array.isArray(action) ||
        typeof action.workflow_id !== 'string' || typeof action.node_id !== 'string' ||
        typeof action.credential_origin !== 'string' || typeof action.credential_tuple_digest !== 'string') {
      throw new Error('FORWARD_RUNTIME_CREDENTIAL_ORIGIN_INTEGRITY_INVALID');
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

async function recoverForwardJournal() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  validateExport(exported);
  const binding = bindingFromEnvironment();
  const lockReceipt = decode('FINANCE_FOUR_TABLE_LOCK_B64');
  const resource = `finance_four_table_cutover:${projectId}`;
  validateLockReceipt(lockReceipt, exported, binding, resource);
  const client = new pg.Client(databaseOptions(process.env));
  try {
    await client.connect();
    const result = await client.query(
      `SELECT receipt
         FROM ${JOURNAL_TABLE}
        WHERE project_id = $1
          AND lock_resource = $2
          AND operation = $3
          AND receipt->>'export_sha256' = $4
          AND receipt->>'operation_nonce' = $5
          AND receipt->>'protected_quiescence_receipt_digest' = $6
          AND receipt->>'required_live_export_digest' = $7
          AND receipt->>'contract_bijection_digest' = $8
        ORDER BY created_at DESC`,
      [projectId, resource, 'FORWARD', exported.export_sha256, binding.operation_nonce, binding.protected_quiescence_receipt_digest, binding.required_live_export_digest, binding.contract_bijection_digest],
    );
    const rows = result.rows || [];
    if (rows.length === 0) throw new Error('FORWARD_RUNTIME_JOURNAL_NOT_FOUND');
    if (rows.length !== 1) throw new Error('FORWARD_RUNTIME_JOURNAL_AMBIGUOUS');
    const row = rows[0];
    const receipt = typeof row.receipt === 'string' ? JSON.parse(row.receipt) : row.receipt;
    await writeRuntimeReceipt(validateForwardReceipt(receipt, exported, resource, binding));
  } finally {
    await client.end();
  }
}

async function acquireProjectLock() {
  const exported = decode('FINANCE_FOUR_TABLE_EXPORT_B64');
  const lockReceipt = decode('FINANCE_FOUR_TABLE_LOCK_B64');
  const resource = `finance_four_table_cutover:${projectId}`;
  const binding = bindingFromEnvironment();
  validateLockReceipt(lockReceipt, exported, binding, resource);
  const client = new pg.Client(databaseOptions(process.env));
  try {
    await client.connect();
    await client.query('BEGIN');
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
  const lock = await acquireProjectLock();
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
    await verifyTargets(lock.client, graph.targetIds);
    const credentialsBefore = await credentialState(lock.client);
    const credentialsByType = new Map(credentialsBefore.values.map((value) => [value.type, value]));
    const workflows = await loadWorkflows(lock.client, graph, operation === 'FORWARD');
    const credentialOriginsBefore = validateCredentialBindings(workflows, credentialsByType);
    const credentialOriginBitsetBefore = credentialOriginBitset(credentialOriginsBefore);
    const workflowCredentialsBefore = workflowCredentialObjectsDigest(workflows);
    const workflowRevisionDigestBefore = workflowRevisionDigest(workflows);
    const workflowOpaqueCredentialsBefore = workflowOpaqueCredentialObjectsDigest(workflows, credentialOriginsBefore);
    if (operation === 'FORWARD') {
      const prestate = findReferences(graph, workflows);
      const plan = applyForward(prestate, credentialsByType, workflows);
      if (!plan.alreadyApplied) {
        await updateWorkflows(lock.client, plan.changed);
      }
      const updated = await loadWorkflows(lock.client, graph, false);
      const poststate = findReferences(graph, updated);
      const postCredentials = await loadWorkflows(lock.client, graph, false);
      const credentialOriginsAfter = validateCredentialBindings(postCredentials, credentialsByType);
      if (!allCredentialOrigins(credentialOriginsAfter, 'opaque')) throw new Error('CREDENTIAL_BINDING_POST_STATE_NOT_OPAQUE');
      const credentialOriginBitsetAfter = credentialOriginBitset(credentialOriginsAfter);
      const workflowRevisionDigestAfter = workflowRevisionDigest(updated);
      const beforeById = new Map(prestate.map((item) => [item.reference.reference_id, item]));
      const actions = poststate.map((item) => {
        const before = beforeById.get(item.reference.reference_id);
        const expected = item.reference.canonical_table_id;
        const credentialBinding = credentialBindingForNode(before.workflow, before.node);
        const credentialOrigin = credentialBinding ? credentialOriginsBefore.get(credentialBinding.key) : 'none';
        if (selectorId(item.node.parameters?.dataTableId) !== (expected || '')) throw new Error(`LIVE_REFERENCE_POST_READBACK_MISMATCH:${item.reference.reference_id}`);
        return { reference_id: item.reference.reference_id, workflow_id: item.reference.workflow_id, revision_id: item.reference.revision_id, post_revision_id: String(updated.get(item.reference.workflow_id).versionId || updated.get(item.reference.workflow_id).revisionId || ''), node_id: item.reference.node_id, selector: before.selector, canonical_table_id: expected, credential_origin: credentialOrigin, credential_tuple_digest: digest({ workflow_id: item.reference.workflow_id, node_id: item.reference.node_id, credential_type: credentialBinding?.credential_type || '', placeholder: credentialBinding?.placeholder || '' }) };
      });
      const readback = selectorReadback(poststate);
      const replayWorkflows = await loadWorkflows(lock.client, graph, false);
      const replayState = findReferences(graph, replayWorkflows);
      const replayPlan = applyForward(replayState, credentialsByType, replayWorkflows);
      if (!replayPlan.alreadyApplied || !sameJson(selectorReadback(replayState), readback)) {
        throw new Error('FORWARD_REPLAY_READBACK_MISMATCH');
      }
      const credentialsAfter = await credentialState(lock.client);
      if (credentialsAfter.digest !== credentialsBefore.digest) throw new Error('CREDENTIAL_STATE_CHANGED');
      const workflowCredentialsAfter = workflowCredentialObjectsDigest(postCredentials);
      if (workflowOpaqueCredentialObjectsDigest(postCredentials, credentialOriginsBefore) !== workflowOpaqueCredentialsBefore) throw new Error('WORKFLOW_OPAQUE_CREDENTIAL_OBJECTS_CHANGED');
      const unsigned = { schema_version: RUNTIME_SCHEMA, operation, project_id: projectId, lock_resource: lock.resource, export_sha256: exported.export_sha256, ...lock.binding, ...credentialContractSummary(), action_count: 33, replay_noop: true, readback_verified: true, readback_digest_sha256: digest(readback), credential_state_digest_before: credentialsBefore.digest, credential_state_digest_after: credentialsAfter.digest, workflow_credential_objects_digest_before: workflowCredentialsBefore, workflow_credential_objects_digest_after: workflowCredentialsAfter, workflow_revision_digest_before: workflowRevisionDigestBefore, workflow_revision_digest_after: workflowRevisionDigestAfter, credential_origin_bitset: credentialOriginBitsetBefore, credential_origin_post_bitset: credentialOriginBitsetAfter, credential_origin_digest: credentialOriginDigest(credentialOriginBitsetBefore), credential_origin_post_digest: credentialOriginDigest(credentialOriginBitsetAfter), credential_ids_recorded: false, secret_values_recorded: false, actions };
      return commitAndJournal({ ...unsigned, runtime_plan_receipt_sha256: digest({ ...unsigned, durable_journal: true, commit_protocol: 'postgresql_synchronous_wal' }) });
    }
    if (!forwardReceipt || forwardReceipt.schema_version !== RUNTIME_SCHEMA || forwardReceipt.operation !== 'FORWARD') throw new Error('FORWARD_RUNTIME_RECEIPT_REQUIRED');
    if (forwardReceipt.project_id !== projectId || forwardReceipt.lock_resource !== lock.resource) {
      throw new Error('FORWARD_RUNTIME_RECEIPT_BINDING_INVALID');
    }
    validateForwardReceipt(forwardReceipt, exported, lock.resource, lock.binding);
    if (forwardReceipt.credential_state_digest_before !== forwardReceipt.credential_state_digest_after) throw new Error('FORWARD_CREDENTIAL_STATE_CHANGED');
    if (forwardReceipt.credential_state_digest_after !== credentialsBefore.digest) throw new Error('FORWARD_CREDENTIAL_STATE_DRIFT');
    if (forwardReceipt.workflow_credential_objects_digest_after !== workflowCredentialsBefore) throw new Error('ROLLBACK_WORKFLOW_CREDENTIAL_STATE_DRIFT');
    if (forwardReceipt.workflow_revision_digest_after !== workflowRevisionDigestBefore) throw new Error('ROLLBACK_WORKFLOW_REVISION_STATE_DRIFT');
    if (!allCredentialOrigins(credentialOriginsBefore, 'opaque')) throw new Error('ROLLBACK_CREDENTIAL_STATE_NOT_OPAQUE');
    const expectedCredentialOrigins = credentialOriginsFromBitset(forwardReceipt.credential_origin_bitset);
    const workflowOpaqueCredentialsBeforeRollback = workflowOpaqueCredentialObjectsDigest(workflows, expectedCredentialOrigins);
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
    restoreCredentialOrigins(changed, workflows, expectedCredentialOrigins);
    await updateWorkflows(lock.client, changed);
    const restored = await loadWorkflows(lock.client, graph, false);
    const restoredCredentialOrigins = validateCredentialBindings(restored, credentialsByType);
    if (credentialOriginBitset(restoredCredentialOrigins) !== forwardReceipt.credential_origin_bitset) {
      throw new Error('ROLLBACK_CREDENTIAL_ORIGIN_POST_READBACK_MISMATCH');
    }
    const restoredState = findReferences(graph, restored);
    for (const item of restoredState) {
      const action = byId.get(item.reference.reference_id);
      if (!sameJson(item.node.parameters?.dataTableId, action.selector)) {
        throw new Error(`ROLLBACK_POST_READBACK_MISMATCH:${item.reference.reference_id}`);
      }
    }
    const readback = selectorReadback(restoredState);
    const credentialsAfter = await credentialState(lock.client);
    if (credentialsAfter.digest !== credentialsBefore.digest) throw new Error('CREDENTIAL_STATE_CHANGED');
    const postCredentials = await loadWorkflows(lock.client, graph, false);
    const workflowCredentialsAfter = workflowCredentialObjectsDigest(postCredentials);
    const workflowRevisionDigestAfter = workflowRevisionDigest(restored);
    if (workflowOpaqueCredentialObjectsDigest(postCredentials, expectedCredentialOrigins) !== workflowOpaqueCredentialsBeforeRollback) throw new Error('WORKFLOW_OPAQUE_CREDENTIAL_OBJECTS_CHANGED');
    const credentialOriginBitsetAfter = credentialOriginBitset(restoredCredentialOrigins);
    const unsignedRollback = { schema_version: RUNTIME_SCHEMA, operation, project_id: projectId, lock_resource: lock.resource, export_sha256: exported.export_sha256, ...lock.binding, ...credentialContractSummary(), action_count: 33, replay_noop: false, readback_verified: true, readback_digest_sha256: digest(readback), credential_state_digest_before: credentialsBefore.digest, credential_state_digest_after: credentialsAfter.digest, workflow_credential_objects_digest_before: workflowCredentialsBefore, workflow_credential_objects_digest_after: workflowCredentialsAfter, workflow_revision_digest_before: workflowRevisionDigestBefore, workflow_revision_digest_after: workflowRevisionDigestAfter, credential_origin_bitset: credentialOriginBitsetBefore, credential_origin_post_bitset: credentialOriginBitsetAfter, credential_origin_digest: credentialOriginDigest(credentialOriginBitsetBefore), credential_origin_post_digest: credentialOriginDigest(credentialOriginBitsetAfter), credential_ids_recorded: false, secret_values_recorded: false, actions: [...byId.values()].map((action) => { const leaf = credentialLeavesFromEnvironment().find((candidate) => candidate.key === `${action.workflow_id}:${action.node_id}`); return { reference_id: action.reference_id, workflow_id: action.workflow_id, node_id: action.node_id, restored: true, credential_origin: leaf ? expectedCredentialOrigins.get(leaf.key) : 'none', credential_tuple_digest: digest({ workflow_id: action.workflow_id, node_id: action.node_id, credential_type: leaf?.credential_type || '', placeholder: leaf?.placeholder || '' }) }; }) };
    return commitAndJournal({ ...unsignedRollback, runtime_plan_receipt_sha256: digest({ ...unsignedRollback, durable_journal: true, commit_protocol: 'postgresql_synchronous_wal' }) });
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
    if (operation !== 'FORWARD') throw new Error('FORWARD_JOURNAL_RECOVERY_ONLY');
    await recoverForwardJournal();
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
