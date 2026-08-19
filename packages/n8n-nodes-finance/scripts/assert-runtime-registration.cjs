'use strict';

// Wrap a normal n8n CLI command and fail before it runs unless the immutable
// finance extension registered every reviewed node and credential type.
const path = require('node:path');
const fs = require('node:fs');
const { createRequire } = require('node:module');

const expectedExtensionRoot = '/opt/finance-n8n/custom-extensions';
const immutablePackage = `${expectedExtensionRoot}/n8n-nodes-finance`;
const mutableLink = '/home/node/.n8n/nodes/node_modules/n8n-nodes-finance';
if (process.env.N8N_CUSTOM_EXTENSIONS !== undefined) throw new Error('FINANCE_CUSTOM_DIRECTORY_NAMESPACE_FORBIDDEN');
if (!fs.lstatSync(mutableLink).isSymbolicLink()) throw new Error('FINANCE_EXTENSION_LINK_REQUIRED');
if (fs.realpathSync(mutableLink) !== fs.realpathSync(immutablePackage)) throw new Error('FINANCE_EXTENSION_LINK_TARGET_MISMATCH');

const n8nPackageJson = require.resolve('n8n/package.json', {
  paths: ['/usr/local/lib/node_modules'],
});
const n8nRoot = path.dirname(n8nPackageJson);
process.env.NODE_CONFIG_DIR ||= path.join(n8nRoot, 'bin', 'config');
const n8nRequire = createRequire(n8nPackageJson);
const { Container } = n8nRequire('@n8n/di');
const { BaseCommand } = n8nRequire('./dist/commands/base-command.js');
const { LoadNodesAndCredentials } = n8nRequire('./dist/load-nodes-and-credentials.js');

const expectedNodes = new Set([
  'n8n-nodes-finance.actualBudget',
  'n8n-nodes-finance.financePdf',
  'n8n-nodes-finance.financeRules',
  'n8n-nodes-finance.financeStatement',
]);
const expectedCredentials = new Set([
  'actualBudgetApi',
  'financeStatementPassword',
]);

const originalInit = BaseCommand.prototype.init;
if (originalInit.financeExtensionRegistrationWrapper === true) {
  throw new Error('FINANCE_EXTENSION_ASSERTION_LOADED_TWICE');
}

async function assertFinanceExtensionRegistration(...args) {
  await originalInit.apply(this, args);
  const loader = Container.get(LoadNodesAndCredentials);
  const registeredNodes = new Set(Object.keys(loader.known.nodes));
  const registeredCredentials = new Set(Object.keys(loader.known.credentials));
  for (const type of expectedNodes) {
    if (!registeredNodes.has(type)) throw new Error(`FINANCE_NODE_NOT_REGISTERED:${type}`);
  }
  for (const type of expectedCredentials) {
    if (!registeredCredentials.has(type)) throw new Error(`FINANCE_CREDENTIAL_NOT_REGISTERED:${type}`);
  }
  process.stdout.write('finance extension registration verified: 4 nodes, 2 credentials\n');
}
assertFinanceExtensionRegistration.financeExtensionRegistrationWrapper = true;
BaseCommand.prototype.init = assertFinanceExtensionRegistration;

require(path.join(n8nRoot, 'bin', 'n8n'));
