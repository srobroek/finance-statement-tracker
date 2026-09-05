'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const financeRoot = '/opt/finance-n8n/custom-extensions/n8n-nodes-finance';
const communityRoot = '/opt/finance-n8n/community-extensions';
const receipt = '/opt/finance-n8n/extension-tree.sha256';
// Keep startup memory bounded even when the community extension tree contains
// large native/vendor artifacts. The old readFileSync path briefly retained
// every file's complete contents while hashing it.
const HASH_CHUNK_SIZE = 64 * 1024;
const hashBuffer = Buffer.allocUnsafe(HASH_CHUNK_SIZE);

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
      const fd = fs.openSync(absolute, 'r');
      try {
        let bytesRead;
        do {
          bytesRead = fs.readSync(fd, hashBuffer, 0, hashBuffer.length, null);
          if (bytesRead > 0) hash.update(hashBuffer.subarray(0, bytesRead));
        } while (bytesRead > 0);
      } finally {
        fs.closeSync(fd);
      }
      hash.update('\0');
    } else {
      throw new Error(`FINANCE_EXTENSION_UNSUPPORTED_ENTRY:${rel}`);
    }
  }
}

function hashExtensionTrees(financeDirectory, communityDirectory) {
  const hash = crypto.createHash('sha256');
  hash.update('finance\0');
  updateTree(hash, financeDirectory);
  hash.update('community\0');
  updateTree(hash, communityDirectory);
  return hash.digest('hex');
}

if (require.main !== module) {
  module.exports = { HASH_CHUNK_SIZE, hashExtensionTrees, updateTree };
  return;
}

const manifest = JSON.parse(fs.readFileSync(path.join(financeRoot, 'package.json'), 'utf8'));
if (manifest.name !== 'n8n-nodes-finance' || manifest.version !== '0.1.0') {
  throw new Error('FINANCE_EXTENSION_VERSION_MISMATCH');
}
const communityManifest = JSON.parse(fs.readFileSync(path.join(communityRoot, 'package.json'), 'utf8'));
const expectedCommunity = {
  'n8n-nodes-prodex': '0.5.1',
};
if (JSON.stringify(communityManifest.dependencies) !== JSON.stringify(expectedCommunity)) {
  throw new Error('COMMUNITY_AI_EXTENSION_VERSION_MISMATCH');
}
for (const [name, version] of Object.entries(expectedCommunity)) {
  const installed = JSON.parse(fs.readFileSync(path.join(communityRoot, 'node_modules', name, 'package.json'), 'utf8'));
  if (installed.version !== version) throw new Error(`COMMUNITY_AI_INSTALLED_VERSION_MISMATCH:${name}`);
}
for (const forbidden of ['OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN']) {
  if (process.env[forbidden] !== undefined) {
    // This check runs before the entrypoint can do any work. Emit one stable
    // rejection line so CI can distinguish the policy failure from a crash.
    process.stderr.write(`COMMUNITY_AI_API_KEY_FORBIDDEN:${forbidden}\n`);
    process.exit(1);
  }
}
const observed = hashExtensionTrees(financeRoot, communityRoot);
if (process.argv[2] === '--write') {
  fs.writeFileSync(receipt, `${observed}\n`, { encoding: 'ascii', mode: 0o444 });
} else {
  const expected = fs.readFileSync(receipt, 'ascii').trim();
  if (!/^[0-9a-f]{64}$/.test(expected) || observed !== expected) {
    throw new Error('FINANCE_EXTENSION_TREE_HASH_MISMATCH');
  }
  process.stdout.write('finance immutable extension hash verified\n');
}
