'use strict';

const PROBE_PREFIX = 'WF23 direct transport probe verified:';
const N8N_CONFIG_ENTRYPOINT = './dist/config';

function exactProbeReceipt() {
  return {
    schema_version: 1,
    status: 'VERIFIED',
    scope: 'DIRECT_EXECUTE_INSTANCE_TRANSPORT',
    execute_instance_resolved: true,
    instance_log_override_invoked: true,
    workflow_loaded: false,
    workflow_executed: false,
    provider_calls: false,
    database_initialized: false,
    raw_irun_persisted: false,
    provider_response_logged: false,
    secret_values_recorded: false,
  };
}

function isDirectEntrypoint(mainModule, currentModule, argv = process.argv) {
  if (mainModule != null && currentModule != null && mainModule === currentModule) return true;
  const filename = typeof currentModule?.filename === 'string'
    ? currentModule.filename.replaceAll('\\', '/')
    : '';
  return mainModule == null
    && Array.isArray(argv)
    && argv.length === 2
    && argv[1] === '-'
    && filename.endsWith('/[stdin]');
}

module.exports = { exactProbeReceipt, isDirectEntrypoint, N8N_CONFIG_ENTRYPOINT, PROBE_PREFIX };

if (isDirectEntrypoint(require.main, module)) {
  if (process.env.FINANCE_WF23_TRANSPORT_PROBE_ACK !== 'READ_ONLY_DIRECT_EXECUTE_INSTANCE') {
    throw new Error('FINANCE_WF23_TRANSPORT_PROBE_ACK=READ_ONLY_DIRECT_EXECUTE_INSTANCE is required');
  }
  const fs = require('node:fs');
  process.stdout.write = () => true;
  process.stderr.write = () => true;
  console.log = () => {};
  console.error = () => {};

  const path = require('node:path');
  const { createRequire } = require('node:module');
  const n8nPackageJson = require.resolve('n8n/package.json', { paths: ['/usr/local/lib/node_modules'] });
  const n8nRoot = path.dirname(n8nPackageJson);
  process.env.NODE_CONFIG_DIR ||= path.join(n8nRoot, 'bin', 'config');
  const n8nRequire = createRequire(n8nPackageJson);
  n8nRequire('reflect-metadata');
  n8nRequire(N8N_CONFIG_ENTRYPOINT);
  const { Container } = n8nRequire('@n8n/di');
  const { Execute } = n8nRequire('./dist/commands/execute.js');
  const command = Container.get(Execute);
  if (!(command instanceof Execute)) throw new Error('DIRECT_EXECUTE_INSTANCE_NOT_RESOLVED');
  let invoked = false;
  command.log = () => { invoked = true; };
  command.log();
  if (!invoked) throw new Error('INSTANCE_LOG_OVERRIDE_NOT_INVOKED');
  fs.writeSync(1, `${PROBE_PREFIX}${JSON.stringify(exactProbeReceipt())}\n`);
}
