'use strict';

// Wrap a normal n8n CLI command and fail before it runs unless the immutable
// finance extension registered every reviewed node and credential type.
const path = require('node:path');
const fs = require('node:fs');
const { createRequire } = require('node:module');

const expectedExtensionRoot = '/opt/finance-n8n/custom-extensions';
const immutablePackage = `${expectedExtensionRoot}/n8n-nodes-finance`;
const mutableLink = '/home/node/.n8n/nodes/node_modules/n8n-nodes-finance';
const immutableCommunityRoot = '/opt/finance-n8n/community-extensions/node_modules';
const requiredPackageLinks = new Map([
  [mutableLink, immutablePackage],
  ['/home/node/.n8n/nodes/node_modules/n8n-nodes-prodex', `${immutableCommunityRoot}/n8n-nodes-prodex`],
  ['/home/node/.n8n/nodes/node_modules/@ggomez91npm/n8n-nodes-claude-code', `${immutableCommunityRoot}/@ggomez91npm/n8n-nodes-claude-code`],
]);
if (process.env.N8N_CUSTOM_EXTENSIONS !== undefined) throw new Error('FINANCE_CUSTOM_DIRECTORY_NAMESPACE_FORBIDDEN');
for (const [link, target] of requiredPackageLinks) {
  if (!fs.lstatSync(link).isSymbolicLink()) throw new Error(`FINANCE_EXTENSION_LINK_REQUIRED:${link}`);
  if (fs.realpathSync(link) !== fs.realpathSync(target)) throw new Error(`FINANCE_EXTENSION_LINK_TARGET_MISMATCH:${link}`);
}

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
  'n8n-nodes-prodex.prodex',
  'n8n-nodes-prodex.prodexChatModel',
  'n8n-nodes-prodex.prodexSetup',
  '@ggomez91npm/n8n-nodes-claude-code.claude',
]);
const expectedCredentials = new Set([
  'actualBudgetApi',
  'financeStatementPassword',
  'prodexAuthApi',
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
  process.stdout.write('finance extension registration verified: 8 nodes, 3 credentials\n');
}
assertFinanceExtensionRegistration.financeExtensionRegistrationWrapper = true;
BaseCommand.prototype.init = assertFinanceExtensionRegistration;

require(path.join(n8nRoot, 'bin', 'n8n'));
