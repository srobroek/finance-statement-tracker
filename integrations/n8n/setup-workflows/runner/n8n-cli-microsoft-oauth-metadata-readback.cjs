'use strict';

if (process.env.FINANCE_MICROSOFT_OAUTH_METADATA_ACK !== 'READ_ONLY_REDACTED') {
  throw new Error('FINANCE_MICROSOFT_OAUTH_METADATA_ACK=READ_ONLY_REDACTED is required');
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
const { CredentialsRepository, SharedCredentialsRepository } = n8nRequire('@n8n/db');
const { Cipher } = n8nRequire('n8n-core');
const { BaseCommand } = n8nRequire('./dist/commands/base-command.js');
const { ListWorkflowCommand } = n8nRequire('./dist/commands/list/workflow.js');

const requirements = new Map([
  ['outlook', 'microsoftOutlookOAuth2Api'],
  ['onedrive', 'microsoftOneDriveOAuth2Api'],
]);

function parseTokenData(value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value;
  if (typeof value === 'string') {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
  }
  throw new Error('OAUTH_TOKEN_DATA_OBJECT_REQUIRED');
}

function expiryMetadata(token) {
  const numeric = Number(token.n8n_expires_at);
  if (!Number.isFinite(numeric) || numeric <= 0) throw new Error('OAUTH_EXPIRY_METADATA_REQUIRED');
  const milliseconds = numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
  const expires = new Date(milliseconds);
  if (Number.isNaN(expires.getTime())) throw new Error('OAUTH_EXPIRY_METADATA_INVALID');
  return {
    expiration_observed: true,
    expires_at_utc: expires.toISOString(),
    expired_at_readback: Date.now() >= milliseconds,
  };
}

const originalInit = BaseCommand.prototype.init;
let completed = false;
BaseCommand.prototype.init = async function microsoftOAuthMetadataReadback(...args) {
  let stage = 'base-init';
  try {
    await originalInit.apply(this, args);
    const credentialsRepository = Container.get(CredentialsRepository);
    const sharedCredentialsRepository = Container.get(SharedCredentialsRepository);
    const cipher = Container.get(Cipher);
    const credentials = {};
    for (const [label, type] of requirements) {
      stage = `${label}-query`;
      const rows = await credentialsRepository.find({ select: ['id', 'type', 'data', 'updatedAt'], where: { type } });
      const candidates = [];
      for (const row of rows) {
        const shares = await sharedCredentialsRepository.find({ where: { credentialsId: row.id } });
        const owners = shares.filter((share) => share.role === 'credential:owner' && share.projectId === projectId);
        if (owners.length === 1 && shares.length === 1) candidates.push(row);
      }
      if (candidates.length !== 1) throw new Error(`EXACT_${label.toUpperCase()}_OWNER_CREDENTIAL_REQUIRED`);
      stage = `${label}-decrypt`;
      const row = candidates[0];
      const data = JSON.parse(await cipher.decryptV2(row.data));
      const token = parseTokenData(data.oauthTokenData);
      if (typeof token.access_token !== 'string' || token.access_token.length === 0) throw new Error('ACCESS_TOKEN_REQUIRED');
      if (typeof token.refresh_token !== 'string' || token.refresh_token.length === 0) throw new Error('REFRESH_TOKEN_REQUIRED');
      const updated = new Date(row.updatedAt);
      if (Number.isNaN(updated.getTime())) throw new Error('CREDENTIAL_UPDATED_AT_INVALID');
      credentials[label] = {
        credential_type: type,
        credential_updated_at_utc: updated.toISOString(),
        access_token_present: true,
        refresh_token_present: true,
        ...expiryMetadata(token),
      };
    }
    process.stdout.write(`microsoft oauth metadata readback verified:${JSON.stringify({
      schema_version: 1,
      status: 'VERIFIED',
      scope: 'READ_ONLY_MICROSOFT_OAUTH_METADATA',
      observed_at_utc: new Date().toISOString(),
      credentials,
      provider_calls: false,
      database_writes: false,
      credential_ids_recorded: false,
      secret_values_recorded: false,
      token_fingerprints_recorded: false,
    })}\n`);
    completed = true;
  } catch (error) {
    const detail = error && typeof error.message === 'string' && /^[A-Z0-9_:-]{1,128}$/.test(error.message)
      ? error.message : 'ERROR';
    process.stderr.write(`microsoft oauth metadata readback failed:${stage}:${detail}\n`);
    throw new Error(`MICROSOFT_OAUTH_METADATA_READBACK_FAILED:${stage}`);
  }
};
ListWorkflowCommand.prototype.run = async function suppressWorkflowList() {
  if (!completed) throw new Error('MICROSOFT_OAUTH_METADATA_READBACK_DID_NOT_COMPLETE');
};
require(path.join(n8nRoot, 'bin', 'n8n'));
