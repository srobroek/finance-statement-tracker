'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const root = process.argv[2];
if (!root || !path.isAbsolute(root)) throw new Error('COMMUNITY_AI_ROOT_REQUIRED');

const files = {
  prodex: {
    path: 'n8n-nodes-prodex/dist/nodes/ProDex/ProDex.node.js',
    sha256: 'b45f0b0fb73ea9c4f6011faff11c25da8fa90212bec4b906a3dc0ee6732cfb88',
  },
  prodexChat: {
    path: 'n8n-nodes-prodex/dist/nodes/ProDexChatModel/ProDexChatModel.node.js',
    sha256: '9e02ad808db1e5ead546e35bf0a8866fecc5b112137353b615bac776617a64be',
  },
  prodexSetup: {
    path: 'n8n-nodes-prodex/dist/nodes/ProDexSetup/ProDexSetup.node.js',
    sha256: '799fa08275e5a9065eef794b9f0736d3b79c569962187ee20c7d577a897616ae',
  },
  codexEnv: {
    path: 'n8n-nodes-prodex/dist/lib/auth/codexEnv.js',
    sha256: '41eade527cdbc11629db6643300eee7ed12eca7166eb2cdf92441d0a4752ae96',
  },
};

function digest(text) {
  return crypto.createHash('sha256').update(text).digest('hex');
}

function readReviewed(entry) {
  const absolute = path.join(root, entry.path);
  const text = fs.readFileSync(absolute, 'utf8');
  if (digest(text) !== entry.sha256) throw new Error(`COMMUNITY_AI_UPSTREAM_HASH_MISMATCH:${entry.path}`);
  return { absolute, text };
}

function replaceOnce(text, before, after, label) {
  const first = text.indexOf(before);
  if (first < 0 || text.indexOf(before, first + before.length) >= 0) {
    throw new Error(`COMMUNITY_AI_PATCH_CONTRACT_MISMATCH:${label}`);
  }
  return text.slice(0, first) + after + text.slice(first + before.length);
}

function writeReviewed(entry, transform) {
  const { absolute, text } = readReviewed(entry);
  const next = transform(text);
  if (next === text) throw new Error(`COMMUNITY_AI_PATCH_EMPTY:${entry.path}`);
  fs.writeFileSync(absolute, next, { encoding: 'utf8', mode: 0o444 });
}

writeReviewed(files.codexEnv, (source) => {
  const before = `function buildCodexEnv(codexHome) {\n    const env = {};\n    for (const [key, value] of Object.entries(process.env)) {\n        if (value !== undefined) {\n            env[key] = value;\n        }\n    }\n    env.CODEX_HOME = codexHome;\n    delete env.CODEX_ACCESS_TOKEN;\n    return env;\n}`;
  const after = `function buildCodexEnv(codexHome) {\n    const env = {\n        HOME: '/home/node',\n        PATH: '/opt/finance-n8n/community-bin:/usr/local/bin:/usr/bin:/bin',\n        CODEX_HOME: codexHome,\n    };\n    for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS', 'TZ', 'LANG', 'LC_ALL']) {\n        const value = process.env[key];\n        if (value !== undefined) env[key] = value;\n    }\n    return env;\n}`;
  let next = replaceOnce(source, before, after, 'prodex-env-allowlist');
  const sandboxBefore = `function mapSandboxMode(sandbox) {\n    switch (sandbox) {\n        case 'read_only':\n            return 'read-only';\n        case 'workspace_write':\n            return 'workspace-write';\n        case 'full_access':\n            return 'danger-full-access';\n        default:\n            return 'read-only';\n    }\n}`;
  const sandboxAfter = `function mapSandboxMode(_sandbox) {\n    return 'read-only';\n}`;
  next = replaceOnce(next, sandboxBefore, sandboxAfter, 'prodex-read-only-sandbox');
  return next;
});

writeReviewed(files.prodex, (source) => {
  let next = replaceOnce(
    source,
    `                const operation = this.getNodeParameter('operation', itemIndex, 'runAgent');`,
    `                const operation = this.getNodeParameter('operation', itemIndex, 'runAgent');\n                if (operation !== 'runAgent') {\n                    throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'FINANCE_PRODEX_OPERATION_BLOCKED', { itemIndex });\n                }`,
    'prodex-operation',
  );
  next = replaceOnce(
    next,
    `                    const useN8nCredentials = this.getNodeParameter('useN8nCredentials', itemIndex, false);`,
    `                    const useN8nCredentials = this.getNodeParameter('useN8nCredentials', itemIndex, false);\n                    if (useN8nCredentials) {\n                        throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'FINANCE_PRODEX_TOKEN_CREDENTIAL_BLOCKED', { itemIndex });\n                    }`,
    'prodex-disk-auth-only',
  );
  next = replaceOnce(
    next,
    `                    const options = this.getNodeParameter('options', itemIndex, {});`,
    `                    const options = this.getNodeParameter('options', itemIndex, {});\n                    if (selectedSkills.length > 0 || options.dynamicSkills) {\n                        throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'FINANCE_PRODEX_SKILLS_BLOCKED', { itemIndex });\n                    }`,
    'prodex-skills',
  );
  next = replaceOnce(
    next,
    `                    const workingDirectory = this.getNodeParameter('workingDirectory', itemIndex, '');`,
    `                    const workingDirectory = this.getNodeParameter('workingDirectory', itemIndex, '');\n                    if (threadMode !== 'new' || sandbox !== 'read_only' || workingDirectory !== '/tmp/finance-ai') {\n                        throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'FINANCE_PRODEX_EXECUTION_BOUNDARY_REQUIRED', { itemIndex });\n                    }`,
    'prodex-execution-boundary',
  );
  next = replaceOnce(
    next,
    `                    const result = await (0, runAgent_1.runCodexAgent)({`,
    `                    if (!outputSchema || typeof outputSchema !== 'object') {\n                        throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'FINANCE_PRODEX_OUTPUT_SCHEMA_REQUIRED', { itemIndex });\n                    }\n                    const result = await (0, runAgent_1.runCodexAgent)({`,
    'prodex-output-schema',
  );
  return next;
});

writeReviewed(files.prodexChat, (source) => replaceOnce(
  source,
  `    async supplyData(itemIndex) {\n        try {`,
  `    async supplyData(itemIndex) {\n        throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'FINANCE_PRODEX_CHAT_MODEL_BLOCKED_USE_SCHEMA_NODE', { itemIndex });\n        try {`,
  'prodex-chat-model-disabled',
));

writeReviewed(files.prodexSetup, (source) => replaceOnce(
  source,
  `    async execute() {\n        const operation = this.getNodeParameter('operation', 0);`,
  `    async execute() {\n        throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'FINANCE_PRODEX_SETUP_DISABLED_USE_MOUNTED_LOGIN');\n        const operation = this.getNodeParameter('operation', 0);`,
  'prodex-setup-disabled',
));


process.stdout.write('community AI runtime hardening applied\n');
