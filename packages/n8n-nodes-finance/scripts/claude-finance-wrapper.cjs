#!/usr/bin/env node
'use strict';

const { spawn } = require('node:child_process');
const fs = require('node:fs');

const realCli = '/opt/finance-n8n/community-extensions/node_modules/.bin/claude';
const input = process.argv.slice(2);
if (input[0] !== '-p' || input[1] !== '--output-format' || input[2] !== 'json') {
  throw new Error('FINANCE_CLAUDE_ARGUMENT_CONTRACT_REQUIRED');
}
for (const forbidden of ['--add-dir', '--resume', '--continue', '--dangerously-skip-permissions']) {
  if (input.includes(forbidden)) throw new Error(`FINANCE_CLAUDE_ARGUMENT_BLOCKED:${forbidden}`);
}
fs.mkdirSync('/tmp/finance-ai', { recursive: true, mode: 0o700 });
process.chdir('/tmp/finance-ai');

const args = [
  ...input.slice(0, 3),
  '--no-session-persistence',
  '--permission-mode', 'plan',
  '--tools', '',
  '--disallowedTools', '*',
  '--safe-mode',
  ...input.slice(3),
];
const env = {
  HOME: '/home/node',
  PATH: '/opt/finance-n8n/community-bin:/usr/local/bin:/usr/bin:/bin',
  CLAUDE_CONFIG_DIR: '/home/node/.claude',
};
for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS', 'TZ', 'LANG', 'LC_ALL']) {
  if (process.env[key] !== undefined) env[key] = process.env[key];
}

const child = spawn(realCli, args, { env, stdio: 'inherit', shell: false });
child.on('error', (error) => {
  process.stderr.write(`FINANCE_CLAUDE_SPAWN_FAILED:${error.code || 'UNKNOWN'}\n`);
  process.exit(127);
});
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  process.exit(code ?? 1);
});
