'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const root = '/opt/finance-n8n/custom-extensions/n8n-nodes-finance';
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

const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
if (manifest.name !== 'n8n-nodes-finance' || manifest.version !== '0.1.0') {
  throw new Error('FINANCE_EXTENSION_VERSION_MISMATCH');
}
const hash = crypto.createHash('sha256');
updateTree(hash, root);
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
