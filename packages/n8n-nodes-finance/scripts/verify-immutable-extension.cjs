'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const financeRoot = '/opt/finance-n8n/custom-extensions/n8n-nodes-finance';
const communityRoot = '/opt/finance-n8n/community-extensions';
const receipt = '/opt/finance-n8n/extension-tree.sha256';

function updateTree(hash, directory, relative = '') {
  const entries = fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name));
  for (const entry of entries) {
    const rel = relative ? `${relative}/${entry.name}` : entry.name;
    const absolute = path.join(directory, entry.name);
    const stat = fs.lstatSync(absolute);
    if (stat.isDirectory()) {
      hash.update(`d\0${rel}\0`);
      updateTree(hash, absolute, rel);
    } else if (stat.isSymbolicLink()) {
      hash.update(`l\0${rel}\0${fs.readlinkSync(absolute)}\0`);
    } else if (stat.isFile()) {
      hash.update(`f\0${rel}\0`);
      hash.update(fs.readFileSync(absolute));
      hash.update('\0');
    } else {
      throw new Error(`FINANCE_EXTENSION_UNSUPPORTED_ENTRY:${rel}`);
    }
  }
}

const manifest = JSON.parse(fs.readFileSync(path.join(financeRoot, 'package.json'), 'utf8'));
if (manifest.name !== 'n8n-nodes-finance' || manifest.version !== '0.1.0') {
  throw new Error('FINANCE_EXTENSION_VERSION_MISMATCH');
}
const communityManifest = JSON.parse(fs.readFileSync(path.join(communityRoot, 'package.json'), 'utf8'));
const expectedCommunity = {
  '@anthropic-ai/claude-code': '2.1.235',
  '@ggomez91npm/n8n-nodes-claude-code': '0.8.0',
  'n8n-nodes-prodex': '0.5.1',
};
if (JSON.stringify(communityManifest.dependencies) !== JSON.stringify(expectedCommunity)) {
  throw new Error('COMMUNITY_AI_EXTENSION_VERSION_MISMATCH');
}
for (const [name, version] of Object.entries(expectedCommunity)) {
  const installed = JSON.parse(fs.readFileSync(path.join(communityRoot, 'node_modules', name, 'package.json'), 'utf8'));
  if (installed.version !== version) throw new Error(`COMMUNITY_AI_INSTALLED_VERSION_MISMATCH:${name}`);
}
for (const forbidden of ['OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN']) {
  if (process.env[forbidden] !== undefined) throw new Error(`COMMUNITY_AI_API_KEY_FORBIDDEN:${forbidden}`);
}
const hash = crypto.createHash('sha256');
hash.update('finance\0');
updateTree(hash, financeRoot);
hash.update('community\0');
updateTree(hash, communityRoot);
const observed = hash.digest('hex');
if (process.argv[2] === '--write') {
  fs.writeFileSync(receipt, `${observed}\n`, { encoding: 'ascii', mode: 0o444 });
} else {
  const expected = fs.readFileSync(receipt, 'ascii').trim();
  if (!/^[0-9a-f]{64}$/.test(expected) || observed !== expected) {
    throw new Error('FINANCE_EXTENSION_TREE_HASH_MISMATCH');
  }
  process.stdout.write('finance immutable extension hash verified\n');
}
