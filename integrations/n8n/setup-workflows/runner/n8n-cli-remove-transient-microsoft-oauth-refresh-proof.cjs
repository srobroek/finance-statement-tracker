'use strict';

if (process.env.FINANCE_MICROSOFT_OAUTH_PROOF_CLEANUP_ACK !== 'REMOVE_TRANSIENT_WF23_ONLY') {
  throw new Error('FINANCE_MICROSOFT_OAUTH_PROOF_CLEANUP_ACK=REMOVE_TRANSIENT_WF23_ONLY is required');
}
const projectId = process.env.N8N_FINANCE_PROJECT_ID;
if (typeof projectId !== 'string' || projectId.length === 0) {
  throw new Error('N8N_FINANCE_PROJECT_ID_REQUIRED');
}
if (!/^[A-Za-z0-9_-]{8,64}$/.test(projectId)) {
  throw new Error('N8N_FINANCE_PROJECT_ID_INVALID');
}

const path = require('node:path');
const { createRequire } = require('node:module');
const n8nPackageJson = require.resolve('n8n/package.json', { paths: ['/usr/local/lib/node_modules'] });
const n8nRoot = path.dirname(n8nPackageJson);
process.env.NODE_CONFIG_DIR ||= path.join(n8nRoot, 'bin', 'config');
const n8nRequire = createRequire(n8nPackageJson);
const { Container } = n8nRequire('@n8n/di');
const { WorkflowRepository, SharedWorkflowRepository } = n8nRequire('@n8n/db');
const { BaseCommand } = n8nRequire('./dist/commands/base-command.js');
const { ListWorkflowCommand } = n8nRequire('./dist/commands/list/workflow.js');

const workflowId = '10000000-0000-4000-8000-000000000023';
const workflowName = 'Finance · Microsoft OAuth Refresh Proof · Manual Read Only';
const workflowCode = 'MICROSOFT_OAUTH_REFRESH_PROOF';
let cleanupCompleted = false;
const originalInit = BaseCommand.prototype.init;

BaseCommand.prototype.init = async function removeTransientProof(...args) {
  let stage = 'base-init';
  try {
    await originalInit.apply(this, args);
    stage = 'workflow-read';
    const workflowRepository = Container.get(WorkflowRepository);
    const sharedWorkflowRepository = Container.get(SharedWorkflowRepository);
    const workflow = await workflowRepository.findOne({
      where: { id: workflowId },
      select: ['id', 'name', 'active', 'activeVersionId', 'meta', 'settings', 'nodes'],
    });
    if (!workflow) {
      process.stdout.write('transient WF23 cleanup verified:{"status":"ALREADY_ABSENT","workflows_removed":0,"secret_values_recorded":false}\n');
      cleanupCompleted = true;
      return;
    }
    const meta = workflow.meta || {};
    if (workflow.name !== workflowName || workflow.active !== false || workflow.activeVersionId !== null ||
        meta.financeWorkflowCode !== workflowCode || meta.migrationStatus !== 'READY_FOR_REVIEWED_MANUAL_IMPORT' ||
        meta.manualOnly !== true || meta.setupOnly !== true || meta.activationForbidden !== true ||
        meta.scheduleForbidden !== true || meta.providerMutationScope !== 'NONE') {
      throw new Error('TRANSIENT_WF23_CONTRACT_MISMATCH');
    }
    if (Object.prototype.hasOwnProperty.call(workflow.settings || {}, 'errorWorkflow') ||
        workflow.settings?.saveDataErrorExecution !== 'none' || workflow.settings?.saveDataSuccessExecution !== 'none') {
      throw new Error('TRANSIENT_WF23_EXECUTION_PERSISTENCE_MISMATCH');
    }
    const outlook = (workflow.nodes || []).filter((node) => node.type === 'n8n-nodes-base.microsoftOutlook');
    const drive = (workflow.nodes || []).filter((node) => node.type === 'n8n-nodes-base.microsoftOneDrive');
    if (outlook.length !== 1 || drive.length !== 1 ||
        !outlook[0].credentials?.microsoftOutlookOAuth2Api?.id ||
        outlook[0].credentials.microsoftOutlookOAuth2Api.id === 'BIND_OUTLOOK' ||
        outlook[0].parameters?.output !== 'fields' || JSON.stringify(outlook[0].parameters?.fields) !== '["id"]' ||
        !drive[0].credentials?.microsoftOneDriveOAuth2Api?.id ||
        drive[0].credentials.microsoftOneDriveOAuth2Api.id === 'BIND_ONEDRIVE') {
      throw new Error('TRANSIENT_WF23_BINDING_CONTRACT_MISMATCH');
    }
    stage = 'ownership-read';
    const shares = await sharedWorkflowRepository.find({ where: { workflowId } });
    if (shares.length !== 1 || shares[0].projectId !== projectId || shares[0].role !== 'workflow:owner') {
      throw new Error('TRANSIENT_WF23_PROJECT_OWNERSHIP_MISMATCH');
    }
    stage = 'workflow-delete';
    const deleted = await workflowRepository.delete(workflowId);
    if (deleted.affected !== 1) throw new Error('TRANSIENT_WF23_DELETE_COUNT_MISMATCH');
    stage = 'delete-readback';
    const remaining = await workflowRepository.findOne({ where: { id: workflowId } });
    const remainingShares = await sharedWorkflowRepository.find({ where: { workflowId } });
    if (remaining || remainingShares.length) throw new Error('TRANSIENT_WF23_DELETE_READBACK_MISMATCH');
    process.stdout.write('transient WF23 cleanup verified:{"status":"VERIFIED","workflows_removed":1,"secret_values_recorded":false}\n');
    cleanupCompleted = true;
  } catch (error) {
    const detail = error && typeof error.message === 'string' && /^[A-Za-z0-9_:-]{1,256}$/.test(error.message)
      ? error.message : 'ERROR';
    process.stderr.write(`transient WF23 cleanup failure:${stage}:${detail}\n`);
    throw new Error(`TRANSIENT_WF23_CLEANUP_FAILED:${stage}`);
  }
};
ListWorkflowCommand.prototype.run = async function suppressWorkflowList() {
  if (!cleanupCompleted) throw new Error('TRANSIENT_WF23_CLEANUP_DID_NOT_COMPLETE');
};
require(path.join(n8nRoot, 'bin', 'n8n'));
