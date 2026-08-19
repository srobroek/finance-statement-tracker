'use strict';

const WORKFLOW_ID = '10000000-0000-4000-8000-000000000023';
const TERMINAL_NODE = 'Emit Redacted OAuth Proof Receipt';
const EXPECTED_KEYS = new Set([
  'schema_version', 'status', 'execution_id', 'outlook_read_succeeded',
  'outlook_items_observed', 'outlook_max_messages', 'outlook_server_filter_applied',
  'outlook_window_start', 'outlook_window_end', 'onedrive_root_read_succeeded',
  'onedrive_root_items_observed', 'provider_writes', 'message_fields_recorded',
  'file_fields_recorded', 'credential_values_recorded', 'token_values_recorded',
  'production_workflows_activated', 'actual_writes', 'cashback_writes', 'verified_at',
]);

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function validTimestamp(value) {
  return typeof value === 'string' && Number.isFinite(Date.parse(value)) && /(Z|[+-]\d\d:\d\d)$/.test(value);
}

function validateIRun(payload) {
  if (!payload || typeof payload !== 'object' || payload.finished !== true || payload.status !== 'success') {
    fail('WF23_EXECUTION_NOT_FINISHED_SUCCESS');
  }
  const resultData = payload.data?.resultData;
  if (resultData?.lastNodeExecuted !== TERMINAL_NODE) fail('WF23_LAST_NODE_MISMATCH');
  const terminal = resultData.runData?.[TERMINAL_NODE];
  if (!Array.isArray(terminal) || terminal.length !== 1 || terminal[0]?.executionStatus !== 'success') {
    fail('WF23_TERMINAL_RUN_INVALID');
  }
  const main = terminal[0]?.data?.main;
  if (!Array.isArray(main) || main.length !== 1 || !Array.isArray(main[0]) || main[0].length !== 1) {
    fail('WF23_EXPECTED_ONE_TERMINAL_ITEM');
  }
  const result = main[0][0]?.json;
  if (!result || typeof result !== 'object' || Array.isArray(result)) fail('WF23_TERMINAL_RESULT_INVALID');
  const keys = Object.keys(result);
  if (keys.length !== EXPECTED_KEYS.size || keys.some((key) => !EXPECTED_KEYS.has(key))) {
    fail('WF23_TERMINAL_RESULT_KEYS_MISMATCH');
  }
  if (result.schema_version !== 'microsoft-oauth-refresh-proof-receipt-v1' || result.status !== 'VERIFIED') {
    fail('WF23_TERMINAL_STATUS_MISMATCH');
  }
  if (typeof result.execution_id !== 'string' || !/^[0-9]+$/.test(result.execution_id)) {
    fail('WF23_EXECUTION_ID_INVALID');
  }
  if (result.outlook_read_succeeded !== true || result.onedrive_root_read_succeeded !== true ||
      result.outlook_server_filter_applied !== true || result.outlook_max_messages !== 1 ||
      !Number.isInteger(result.outlook_items_observed) || result.outlook_items_observed < 0 || result.outlook_items_observed > 1 ||
      !Number.isInteger(result.onedrive_root_items_observed) || result.onedrive_root_items_observed < 0) {
    fail('WF23_PROVIDER_READ_CONTRACT_MISMATCH');
  }
  for (const key of [
    'provider_writes', 'message_fields_recorded', 'file_fields_recorded',
    'credential_values_recorded', 'token_values_recorded', 'production_workflows_activated',
    'actual_writes', 'cashback_writes',
  ]) {
    if (result[key] !== false) fail(`WF23_TERMINAL_RESULT_MISMATCH_${key.toUpperCase()}`);
  }
  if (!validTimestamp(result.outlook_window_start) || !validTimestamp(result.outlook_window_end) ||
      !validTimestamp(result.verified_at) || Date.parse(result.outlook_window_start) > Date.parse(result.outlook_window_end)) {
    fail('WF23_TIMESTAMP_CONTRACT_MISMATCH');
  }
  return result;
}

module.exports = { validateIRun, WORKFLOW_ID, TERMINAL_NODE };

if (require.main === module) {
  if (process.env.FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK !== 'EXECUTE_WF23_REDACTED_ONLY') {
    throw new Error('FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK=EXECUTE_WF23_REDACTED_ONLY is required');
  }
  if (process.env.EXECUTIONS_DATA_SAVE_ON_SUCCESS !== 'none' ||
      process.env.EXECUTIONS_DATA_SAVE_ON_ERROR !== 'none' ||
      process.env.EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS !== 'false') {
    throw new Error('EXECUTION_DATA_SAVING_MUST_BE_DISABLED');
  }
  const fs = require('node:fs');
  process.stdout.write = () => true;
  process.stderr.write = () => true;
  console.log = () => {};
  console.error = () => {};

  let receipt = null;
  let emitted = false;
  const path = require('node:path');
  const { createRequire } = require('node:module');
  const n8nPackageJson = require.resolve('n8n/package.json', { paths: ['/usr/local/lib/node_modules'] });
  const n8nRoot = path.dirname(n8nPackageJson);
  process.env.NODE_CONFIG_DIR ||= path.join(n8nRoot, 'bin', 'config');
  const n8nRequire = createRequire(n8nPackageJson);
  const { BaseCommand } = n8nRequire('./dist/commands/base-command.js');
  const { Execute } = n8nRequire('./dist/commands/execute.js');

  BaseCommand.prototype.log = function captureRawIRunInMemory(message) {
    if (receipt !== null || typeof message !== 'string' || Buffer.byteLength(message, 'utf8') > 16 * 1024 * 1024) {
      fail('WF23_RAW_OUTPUT_INVALID');
    }
    let payload;
    try { payload = JSON.parse(message); } catch { fail('WF23_RAW_OUTPUT_INVALID'); }
    receipt = validateIRun(payload);
    payload = null;
  };
  const originalRun = Execute.prototype.run;
  Execute.prototype.run = async function executeAndEmitRedactedReceipt(...args) {
    const outcome = await originalRun.apply(this, args);
    if (receipt === null || emitted) fail('WF23_REDACTED_RECEIPT_NOT_CAPTURED');
    emitted = true;
    fs.writeSync(1, `transient WF23 execution verified:${JSON.stringify(receipt)}\n`);
    receipt = null;
    return outcome;
  };
  Execute.prototype.catch = async function emitRedactedExecutionFailure() {
    if (!emitted) fs.writeSync(2, 'transient WF23 execution failure:REDACTED_EXECUTION_FAILED\n');
    emitted = true;
    process.exitCode = 1;
  };
  process.once('uncaughtException', () => { process.exitCode = 1; });
  process.once('unhandledRejection', () => { process.exitCode = 1; });
  require(path.join(n8nRoot, 'bin', 'n8n'));
}
