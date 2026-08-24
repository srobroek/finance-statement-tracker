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
const WATCHDOG_TIMEOUT_MS = 120_000;
const WATCHDOG_FINALIZE_GRACE_MS = 10_000;
const WATCHDOG_STAGES = Object.freeze([
  'CONFIG_LOAD',
  'MODULE_LOAD',
  'COMMAND_INIT',
  'COMMAND_RUN',
  'RAW_CAPTURE',
  'FINALIZE',
]);
const TIMEOUT_FAILURE_CODES = new Set(WATCHDOG_STAGES.map((stage) => `WF23_TIMEOUT_${stage}`));
const SAFE_FAILURE_CODES = new Set([
  'WF23_EXECUTION_NOT_FINISHED_SUCCESS',
  'WF23_LAST_NODE_MISMATCH',
  'WF23_TERMINAL_RUN_INVALID',
  'WF23_EXPECTED_ONE_TERMINAL_ITEM',
  'WF23_TERMINAL_RESULT_INVALID',
  'WF23_TERMINAL_RESULT_KEYS_MISMATCH',
  'WF23_TERMINAL_STATUS_MISMATCH',
  'WF23_EXECUTION_ID_INVALID',
  'WF23_PROVIDER_READ_CONTRACT_MISMATCH',
  'WF23_TERMINAL_RESULT_MISMATCH_PROVIDER_WRITES',
  'WF23_TERMINAL_RESULT_MISMATCH_MESSAGE_FIELDS_RECORDED',
  'WF23_TERMINAL_RESULT_MISMATCH_FILE_FIELDS_RECORDED',
  'WF23_TERMINAL_RESULT_MISMATCH_CREDENTIAL_VALUES_RECORDED',
  'WF23_TERMINAL_RESULT_MISMATCH_TOKEN_VALUES_RECORDED',
  'WF23_TERMINAL_RESULT_MISMATCH_PRODUCTION_WORKFLOWS_ACTIVATED',
  'WF23_TERMINAL_RESULT_MISMATCH_ACTUAL_WRITES',
  'WF23_TERMINAL_RESULT_MISMATCH_CASHBACK_WRITES',
  'WF23_TIMESTAMP_CONTRACT_MISMATCH',
  'WF23_RAW_OUTPUT_INVALID',
  'WF23_MULTIPLE_RAW_OUTPUTS',
  'WF23_REDACTED_RECEIPT_NOT_CAPTURED',
  'WF23_N8N_REQUESTED_EARLY_EXIT',
  'WF23_DIRECT_LIFECYCLE_ORDER_INVALID',
  'WF23_WATCHDOG_STAGE_INVALID',
  ...TIMEOUT_FAILURE_CODES,
]);
const SUCCESS_PREFIX = 'transient WF23 execution verified:';
const FAILURE_PREFIX = 'transient WF23 execution failed:';
const N8N_CONFIG_ENTRYPOINT = './dist/config';
const DIRECT_LIFECYCLE_ORDER = Object.freeze([
  'config-loaded',
  'command-loaded',
  'modules-loaded',
  'execute-resolved',
  'execute-initialized',
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

function safeFailureCode(error) {
  let code;
  try { code = error?.code; } catch { return 'WF23_REDACTED_EXECUTION_FAILED'; }
  return typeof code === 'string' && SAFE_FAILURE_CODES.has(code)
    ? code
    : 'WF23_REDACTED_EXECUTION_FAILED';
}

function terminalLine(error, receipt) {
  if (!error && receipt) return `${SUCCESS_PREFIX}${JSON.stringify(receipt)}\n`;
  return `${FAILURE_PREFIX}${JSON.stringify({
    schema_version: 1,
    status: 'FAILED',
    error_code: safeFailureCode(error),
    provider_response_logged: false,
    secret_values_recorded: false,
  })}\n`;
}

function directLifecycleGate() {
  let index = 0;
  return function advance(stage) {
    if (stage !== DIRECT_LIFECYCLE_ORDER[index]) fail('WF23_DIRECT_LIFECYCLE_ORDER_INVALID');
    index += 1;
    return index;
  };
}

function createStageWatchdog({
  timeoutMs = WATCHDOG_TIMEOUT_MS,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  onTimeout,
} = {}) {
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || typeof setTimer !== 'function' ||
      typeof clearTimer !== 'function' || typeof onTimeout !== 'function') {
    fail('WF23_WATCHDOG_STAGE_INVALID');
  }
  let active = true;
  let generation = 0;
  let timer = null;

  function arm(stage) {
    if (!WATCHDOG_STAGES.includes(stage) || !active) fail('WF23_WATCHDOG_STAGE_INVALID');
    generation += 1;
    const armedGeneration = generation;
    if (timer !== null) clearTimer(timer);
    timer = setTimer(() => {
      if (!active || armedGeneration !== generation) return;
      active = false;
      timer = null;
      onTimeout(`WF23_TIMEOUT_${stage}`);
    }, timeoutMs);
    return stage;
  }

  function cancel() {
    if (!active) return false;
    active = false;
    generation += 1;
    if (timer !== null) clearTimer(timer);
    timer = null;
    return true;
  }

  return { arm, cancel };
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

module.exports = {
  validateIRun, safeFailureCode, terminalLine, directLifecycleGate, createStageWatchdog,
  isDirectEntrypoint, DIRECT_LIFECYCLE_ORDER, N8N_CONFIG_ENTRYPOINT, WATCHDOG_STAGES,
  WATCHDOG_TIMEOUT_MS, WORKFLOW_ID, TERMINAL_NODE,
};

if (isDirectEntrypoint(require.main, module)) {
  if (process.env.FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK !== 'EXECUTE_WF23_REDACTED_ONLY') {
    throw new Error('FINANCE_MICROSOFT_OAUTH_PROOF_EXECUTION_ACK=EXECUTE_WF23_REDACTED_ONLY is required');
  }
  if (process.env.EXECUTIONS_DATA_SAVE_ON_SUCCESS !== 'none' ||
      process.env.EXECUTIONS_DATA_SAVE_ON_ERROR !== 'none' ||
      process.env.EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS !== 'false') {
    throw new Error('EXECUTION_DATA_SAVING_MUST_BE_DISABLED');
  }
  if (process.env.N8N_RUNNERS_MODE !== 'external' ||
      process.env.N8N_RUNNERS_BROKER_LISTEN_ADDRESS !== '0.0.0.0' ||
      !process.env.N8N_RUNNERS_AUTH_TOKEN) {
    throw new Error('WF23_DEPLOYED_TASK_RUNNER_CONTROL_PATH_REQUIRED');
  }
  const fs = require('node:fs');
  process.stdout.write = () => true;
  process.stderr.write = () => true;
  console.log = () => {};
  console.error = () => {};

  let emitted = false;
  const path = require('node:path');
  const { createRequire } = require('node:module');
  const n8nPackageJson = require.resolve('n8n/package.json', { paths: ['/usr/local/lib/node_modules'] });
  const n8nRoot = path.dirname(n8nPackageJson);
  process.env.NODE_CONFIG_DIR ||= path.join(n8nRoot, 'bin', 'config');
  const n8nRequire = createRequire(n8nPackageJson);
  const originalExit = process.exit.bind(process);

  function fixedError(code) {
    return Object.assign(new Error(code), { code });
  }

  async function executeDirectly() {
    let command = null;
    let receipt = null;
    let terminalError = null;
    let terminating = false;
    let watchdog = null;
    const advanceLifecycle = directLifecycleGate();

    function writeTerminalOnce(error, validatedReceipt) {
      if (emitted) return false;
      emitted = true;
      fs.writeSync(1, terminalLine(error, validatedReceipt));
      return true;
    }

    async function terminateOnTimeout(code) {
      if (terminating || emitted || !TIMEOUT_FAILURE_CODES.has(code)) return;
      terminating = true;
      receipt = null;
      watchdog.cancel();
      writeTerminalOnce(fixedError(code), null);

      if (code !== 'WF23_TIMEOUT_FINALIZE' && command && typeof command.finally === 'function') {
        const forcedExit = setTimeout(() => {
          process.exit = originalExit;
          originalExit(1);
        }, WATCHDOG_FINALIZE_GRACE_MS);
        try { await command.finally(fixedError(code)); } catch { /* fixed failure already emitted */ }
        clearTimeout(forcedExit);
      }
      process.exit = originalExit;
      originalExit(1);
    }

    watchdog = createStageWatchdog({
      onTimeout: (code) => { void terminateOnTimeout(code); },
    });
    process.exit = () => { throw fixedError('WF23_N8N_REQUESTED_EARLY_EXIT'); };
    watchdog.arm('CONFIG_LOAD');
    try {
      n8nRequire('source-map-support').install();
      n8nRequire('reflect-metadata');
      if (process.env.E2E_TESTS !== 'true') n8nRequire('dotenv').config({ quiet: true });
      n8nRequire(N8N_CONFIG_ENTRYPOINT);
      advanceLifecycle('config-loaded');
      const { Container } = n8nRequire('@n8n/di');
      const { Execute } = n8nRequire('./dist/commands/execute.js');
      advanceLifecycle('command-loaded');
      const { ModuleRegistry } = n8nRequire('@n8n/backend-common');
      watchdog.arm('MODULE_LOAD');
      await Container.get(ModuleRegistry).loadModules();
      advanceLifecycle('modules-loaded');
      command = Container.get(Execute);
      advanceLifecycle('execute-resolved');
      command.flags = { id: WORKFLOW_ID, rawOutput: true };
      watchdog.arm('COMMAND_INIT');
      await command.init();
      advanceLifecycle('execute-initialized');

      // Install instance-owned sinks only after normal initialization. This
      // prevents provider/error text from reaching n8n's logger and avoids
      // relying on dynamic-import command constructor identity.
      command.logger = new Proxy({}, { get: () => () => undefined });
      command.log = function captureRawIRunInMemory(message) {
        watchdog.arm('RAW_CAPTURE');
        if (receipt !== null) fail('WF23_MULTIPLE_RAW_OUTPUTS');
        if (typeof message !== 'string' || Buffer.byteLength(message, 'utf8') > 16 * 1024 * 1024) {
          fail('WF23_RAW_OUTPUT_INVALID');
        }
        let payload;
        try { payload = JSON.parse(message); } catch { fail('WF23_RAW_OUTPUT_INVALID'); }
        receipt = validateIRun(payload);
        payload = null;
        watchdog.arm('COMMAND_RUN');
      };
      watchdog.arm('COMMAND_RUN');
      await command.run();
      if (receipt === null) throw fixedError('WF23_REDACTED_RECEIPT_NOT_CAPTURED');
    } catch (error) {
      terminalError = error;
    }

    if (terminating) return;

    if (command && typeof command.finally === 'function') {
      const sanitizedError = terminalError ? fixedError(safeFailureCode(terminalError)) : undefined;
      watchdog.arm('FINALIZE');
      try {
        await command.finally(sanitizedError);
      } catch (error) {
        if (!terminalError) terminalError = fixedError(safeFailureCode(error));
      }
    }
    if (terminating) return;
    watchdog.cancel();
    process.exit = originalExit;
    writeTerminalOnce(terminalError, receipt);
    receipt = null;
    originalExit(terminalError ? 1 : 0);
  }

  executeDirectly().catch(() => {
    process.exit = originalExit;
    if (!emitted) fs.writeSync(1, terminalLine(fixedError('WF23_REDACTED_EXECUTION_FAILED'), null));
    originalExit(1);
  });
}
