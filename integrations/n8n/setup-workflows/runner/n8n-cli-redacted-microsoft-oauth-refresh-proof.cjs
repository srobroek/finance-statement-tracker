'use strict';

const crypto = require('node:crypto');

const WORKFLOW_ID = '10000000-0000-4000-8000-000000000023';
const TRIGGER_NODE = 'Run Reviewed OAuth Proof';
const TERMINAL_NODE = 'Emit Redacted OAuth Proof Receipt';
const AUTH_COOKIE = 'n8n-auth';
const LOCAL_ORIGIN = 'http://127.0.0.1:5678';
const LOCAL_REST_ORIGIN = 'http://127.0.0.1:5678';
const WATCHDOG_TIMEOUT_MS = 120_000;
const EXECUTION_RECONCILIATION_TIMEOUT_MS = 5_000;
const EXECUTION_POLL_INTERVAL_MS = 100;
const TERMINAL_EXECUTION_STATUSES = new Set(['success', 'error', 'canceled', 'cancelled', 'crashed', 'failed']);
const EXPECTED_KEYS = new Set([
  'schema_version', 'status', 'execution_id', 'outlook_read_succeeded',
  'outlook_items_observed', 'outlook_max_messages', 'outlook_server_filter_applied',
  'outlook_window_start', 'outlook_window_end', 'onedrive_root_read_succeeded',
  'onedrive_root_items_observed', 'provider_writes', 'message_fields_recorded',
  'file_fields_recorded', 'credential_values_recorded', 'token_values_recorded',
  'production_workflows_activated', 'actual_writes', 'cashback_writes', 'verified_at',
]);
const SAFE_FAILURE_CODES = new Set([
  'WF23_EXECUTION_NOT_FINISHED_SUCCESS', 'WF23_LAST_NODE_MISMATCH',
  'WF23_TERMINAL_RUN_INVALID', 'WF23_EXPECTED_ONE_TERMINAL_ITEM',
  'WF23_TERMINAL_RESULT_INVALID', 'WF23_TERMINAL_RESULT_KEYS_MISMATCH',
  'WF23_TERMINAL_STATUS_MISMATCH', 'WF23_EXECUTION_ID_INVALID',
  'WF23_EXECUTION_ID_MISMATCH', 'WF23_PROVIDER_READ_CONTRACT_MISMATCH',
  'WF23_TERMINAL_RESULT_MISMATCH_PROVIDER_WRITES',
  'WF23_TERMINAL_RESULT_MISMATCH_MESSAGE_FIELDS_RECORDED',
  'WF23_TERMINAL_RESULT_MISMATCH_FILE_FIELDS_RECORDED',
  'WF23_TERMINAL_RESULT_MISMATCH_CREDENTIAL_VALUES_RECORDED',
  'WF23_TERMINAL_RESULT_MISMATCH_TOKEN_VALUES_RECORDED',
  'WF23_TERMINAL_RESULT_MISMATCH_PRODUCTION_WORKFLOWS_ACTIVATED',
  'WF23_TERMINAL_RESULT_MISMATCH_ACTUAL_WRITES',
  'WF23_TERMINAL_RESULT_MISMATCH_CASHBACK_WRITES',
  'WF23_TIMESTAMP_CONTRACT_MISMATCH', 'WF23_AUTH_OWNER_COUNT', 'WF23_AUTH_OWNER_INVALID',
  'WF23_AUTH_SECRET_COUNT', 'WF23_AUTH_MFA_REQUIRED',
  'WF23_AUTH_SECRET_INVALID', 'WF23_AUTH_READ_FAILED',
  'OUTLOOK_AUTH_REQUIRED', 'ONEDRIVE_AUTH_REQUIRED',
  'WF23_REST_RUN_FAILED', 'WF23_REST_RESPONSE_INVALID',
  'WF23_PUSH_CONNECTION_FAILED', 'WF23_PUSH_EXECUTION_FAILED',
  'WF23_TERMINAL_RESULT_NOT_CAPTURED', 'WF23_RAW_OUTPUT_INVALID',
  'WF23_REDACTED_RECEIPT_NOT_CAPTURED', 'WF23_N8N_REQUESTED_EARLY_EXIT',
  'WF23_TIMEOUT_COMMAND_RUN', 'WF23_EXECUTION_NOT_REMOVED',
]);
const SUCCESS_PREFIX = 'transient WF23 execution verified:';
const FAILURE_PREFIX = 'transient WF23 execution failed:';

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

function isDirectEntrypoint(mainModule, currentModule, argv = process.argv) {
  if (mainModule != null && currentModule != null && mainModule === currentModule) return true;
  const filename = typeof currentModule?.filename === 'string'
    ? currentModule.filename.replaceAll('\\', '/')
    : '';
  return mainModule == null && Array.isArray(argv) && argv.length === 2 && argv[1] === '-' && filename.endsWith('/[stdin]');
}

function createJWTHash({ email, password, mfaEnabled, mfaSecret }) {
  const payload = [email, password];
  if (mfaEnabled && mfaSecret) payload.push(mfaSecret.substring(0, 3));
  return crypto.createHash('sha256').update(payload.join(':')).digest('base64').substring(0, 10);
}

function installedModule(name) {
  const { createRequire } = require('node:module');
  const packageJson = require.resolve('n8n/package.json', { paths: ['/usr/local/lib/node_modules'] });
  return createRequire(packageJson)(name);
}

function databaseOptions(env) {
  return {
    host: env.DB_POSTGRESDB_HOST,
    port: Number(env.DB_POSTGRESDB_PORT || 5432),
    database: env.DB_POSTGRESDB_DATABASE,
    user: env.DB_POSTGRESDB_USER,
    password: env.DB_POSTGRESDB_PASSWORD,
    max: 1,
    connectionTimeoutMillis: 5_000,
    idleTimeoutMillis: 1_000,
  };
}

async function readAuthContext({ pgModule, env = process.env }) {
  const pool = new pgModule.Pool(databaseOptions(env));
  try {
    const ownerResult = await pool.query(
      'SELECT id, email, password, "mfaEnabled", "mfaSecret" FROM "user" WHERE "roleSlug" = $1 AND disabled = false',
      ['global:owner'],
    );
    if (!Array.isArray(ownerResult.rows) || ownerResult.rows.length !== 1) fail('WF23_AUTH_OWNER_COUNT');
    const owner = ownerResult.rows[0];
    if (typeof owner.id !== 'string' || typeof owner.email !== 'string' || typeof owner.password !== 'string') {
      fail('WF23_AUTH_OWNER_INVALID');
    }
    if (owner.mfaEnabled === true) fail('WF23_AUTH_MFA_REQUIRED');
    let jwtSecret = env.N8N_USER_MANAGEMENT_JWT_SECRET;
    if (!jwtSecret) {
      const secretResult = await pool.query(
        'SELECT value FROM deployment_key WHERE type = $1 AND status = $2',
        ['signing.jwt', 'active'],
      );
      if (!Array.isArray(secretResult.rows) || secretResult.rows.length !== 1) fail('WF23_AUTH_SECRET_COUNT');
      jwtSecret = secretResult.rows[0]?.value;
    }
    if (typeof jwtSecret !== 'string' || jwtSecret.length === 0) fail('WF23_AUTH_SECRET_INVALID');
    return { id: owner.id, hash: createJWTHash(owner), jwtSecret };
  } catch (error) {
    if (error?.code && SAFE_FAILURE_CODES.has(error.code)) throw error;
    fail('WF23_AUTH_READ_FAILED');
  } finally {
    await pool.end().catch(() => {});
  }
}

function signAuthToken({ id, hash, jwtSecret }, jwtModule) {
  return jwtModule.sign({ id, hash, usedMfa: false }, jwtSecret, { algorithm: 'HS256', expiresIn: 300 });
}

function wrappedExecutionId(body) {
  if (!body || typeof body !== 'object' || !body.data || typeof body.data !== 'object') fail('WF23_REST_RESPONSE_INVALID');
  const value = body.data.executionId;
  if ((typeof value !== 'string' && typeof value !== 'number') || !/^[0-9]+$/.test(String(value))) fail('WF23_REST_RESPONSE_INVALID');
  return String(value);
}

function frameText(value) {
  const data = value?.data ?? value;
  if (typeof data === 'string') return data;
  if (Buffer.isBuffer(data)) return data.toString('utf8');
  if (data instanceof ArrayBuffer) return Buffer.from(data).toString('utf8');
  return null;
}

function framePayload(value) {
  const text = frameText(value);
  if (text === null) return null;
  try {
    const payload = JSON.parse(text);
    return payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : null;
  } catch {
    return null;
  }
}

function terminalRunPayload(task) {
  return {
    finished: true,
    status: 'success',
    data: { resultData: { lastNodeExecuted: TERMINAL_NODE, runData: { [TERMINAL_NODE]: [task] } } },
  };
}

function executionStatus(body) {
  const candidates = [body, body?.data, body?.data?.execution];
  for (const candidate of candidates) {
    if (candidate && typeof candidate.status === 'string') return candidate.status.toLowerCase();
  }
  return null;
}

function executionIsTerminal(body) {
  const status = executionStatus(body);
  if (status && TERMINAL_EXECUTION_STATUSES.has(status)) return true;
  return !status && Boolean(body?.finished === true || body?.data?.finished === true);
}

async function awaitWithin(promise, timeoutMs) {
  if (timeoutMs <= 0) throw new Error('WF23_EXECUTION_RECONCILIATION_TIMEOUT');
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('WF23_EXECUTION_RECONCILIATION_TIMEOUT')), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function readResponseBody(response, timeoutMs) {
  if (typeof response?.json !== 'function') return null;
  try {
    return await awaitWithin(Promise.resolve(response.json()), timeoutMs);
  } catch {
    return null;
  }
}

async function fetchWithin(fetchImpl, url, options, timeoutMs) {
  const controller = new AbortController();
  const request = Promise.resolve(fetchImpl(url, { ...options, signal: controller.signal }));
  try {
    return await awaitWithin(request, timeoutMs);
  } catch (error) {
    controller.abort();
    throw error;
  }
}

async function pollExecution({ fetchImpl, executionUrl, headers, deadline, observe }) {
  while (Date.now() < deadline) {
    try {
      const response = await fetchWithin(
        fetchImpl,
        executionUrl,
        { method: 'GET', headers },
        Math.max(1, deadline - Date.now()),
      );
      if (await observe(response, Math.max(1, deadline - Date.now()))) return true;
    } catch {}
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await new Promise((resolve) => setTimeout(resolve, Math.min(EXECUTION_POLL_INTERVAL_MS, remaining)));
  }
  return false;
}

async function reconcileExecution({ token, executionId, fetchImpl, timeoutMs = EXECUTION_RECONCILIATION_TIMEOUT_MS }) {
  if (typeof fetchImpl !== 'function' || !executionId) return false;
  const headers = { Origin: LOCAL_ORIGIN, Cookie: `${AUTH_COOKIE}=${token}` };
  const stopUrl = `${LOCAL_REST_ORIGIN}/rest/executions/${encodeURIComponent(executionId)}/stop`;
  const executionUrl = `${LOCAL_REST_ORIGIN}/rest/executions/${encodeURIComponent(executionId)}`;
  const deadline = Date.now() + timeoutMs;
  const observeTerminal = async (response, requestTimeoutMs) => {
    return response?.ok && executionIsTerminal(await readResponseBody(response, requestTimeoutMs));
  };
  try {
    const stopResponse = await fetchWithin(fetchImpl, stopUrl, { method: 'POST', headers }, Math.max(1, deadline - Date.now()));
    if (stopResponse?.ok) {
      if (await pollExecution({ fetchImpl, executionUrl, headers, deadline, observe: observeTerminal })) return true;
    }
  } catch {}
  if (await pollExecution({ fetchImpl, executionUrl, headers, deadline, observe: observeTerminal })) return true;
  while (true) {
    try {
      const response = await fetchWithin(fetchImpl, executionUrl, { method: 'GET', headers }, Math.max(1, timeoutMs));
      if (await observeTerminal(response, Math.max(1, timeoutMs))) return true;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, EXECUTION_POLL_INTERVAL_MS));
  }
}

async function awaitExecutionRemoval({ token, executionId, fetchImpl, timeoutMs = EXECUTION_RECONCILIATION_TIMEOUT_MS }) {
  if (typeof fetchImpl !== 'function' || !executionId) return false;
  const headers = { Origin: LOCAL_ORIGIN, Cookie: `${AUTH_COOKIE}=${token}` };
  const executionUrl = `${LOCAL_REST_ORIGIN}/rest/executions/${encodeURIComponent(executionId)}`;
  const deadline = Date.now() + timeoutMs;
  return pollExecution({
    fetchImpl,
    executionUrl,
    headers,
    deadline,
    observe: async (response) => response?.status === 404,
  });
}

async function runLocalWorkflow({ token, wsModule, fetchImpl = globalThis.fetch, random = crypto.randomBytes,
  timeoutMs = WATCHDOG_TIMEOUT_MS, workflowId = WORKFLOW_ID, triggerNode = TRIGGER_NODE,
  reconcileTimeoutMs = EXECUTION_RECONCILIATION_TIMEOUT_MS }) {
  if (typeof fetchImpl !== 'function') fail('WF23_REST_RUN_FAILED');
  const pushRef = random(12).toString('hex');
  const WebSocket = wsModule.WebSocket || wsModule;
  const socket = new WebSocket(
    `ws://127.0.0.1:5678/rest/push?pushRef=${encodeURIComponent(pushRef)}`,
    { headers: { Origin: LOCAL_ORIGIN, Cookie: `${AUTH_COOKIE}=${token}` } },
  );
  let timer;
  let settled = false;
  let executionId;
  let finishedStatus;
  let terminalTask;
  let failureError;
  let runRequest;
  const pendingFinished = new Map();
  const pendingTerminal = new Map();
  let resolveRun;
  let rejectRun;
  const resultPromise = new Promise((resolve, reject) => { resolveRun = resolve; rejectRun = reject; });
  const failRun = (error) => {
    if (!settled) {
      settled = true;
      failureError = error;
      rejectRun(error);
    }
  };
  const maybeFinish = () => {
    if (settled || !executionId || finishedStatus !== 'success' || !terminalTask) return;
    try {
      const receipt = validateIRun(terminalRunPayload(terminalTask));
      if (receipt.execution_id !== executionId) fail('WF23_EXECUTION_ID_MISMATCH');
      settled = true;
      resolveRun(receipt);
    } catch (error) {
      failRun(error);
    }
  };
  const onMessage = (message) => {
    const frame = framePayload(message);
    if (!frame || settled) return;
    const data = frame.data;
    if (!data || typeof data !== 'object') return;
    const frameExecutionId = typeof data.executionId === 'string' || typeof data.executionId === 'number'
      ? String(data.executionId)
      : null;
    if (!frameExecutionId) return;
    if (frame.type === 'nodeExecuteAfterData' && data.nodeName === TERMINAL_NODE &&
        data.data && typeof data.data === 'object') {
      if (executionId && frameExecutionId === executionId) {
        terminalTask = data.data;
        maybeFinish();
      } else if (!executionId) {
        pendingTerminal.set(frameExecutionId, data.data);
      }
      return;
    }
    if (frame.type === 'executionFinished' && (!data.workflowId || data.workflowId === workflowId)) {
      if (data.status !== 'success') {
        if (executionId && frameExecutionId === executionId) {
          failRun(Object.assign(new Error('WF23_EXECUTION_NOT_FINISHED_SUCCESS'), { code: 'WF23_EXECUTION_NOT_FINISHED_SUCCESS' }));
        } else if (!executionId) {
          pendingFinished.set(frameExecutionId, data.status);
        }
        return;
      }
      if (executionId && frameExecutionId === executionId) {
        finishedStatus = data.status;
        maybeFinish();
      } else if (!executionId) {
        pendingFinished.set(frameExecutionId, data.status);
      }
    }
  };
  const onOpen = async () => {
    runRequest = (async () => {
      try {
        const response = await fetchImpl(`${LOCAL_REST_ORIGIN}/rest/workflows/${encodeURIComponent(workflowId)}/run`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json', Origin: LOCAL_ORIGIN,
            Cookie: `${AUTH_COOKIE}=${token}`, 'push-ref': pushRef,
          },
          body: JSON.stringify({ triggerToStartFrom: { name: triggerNode } }),
        });
        if (!response?.ok) fail('WF23_REST_RUN_FAILED');
        executionId = wrappedExecutionId(await response.json());
        terminalTask = pendingTerminal.get(executionId);
        finishedStatus = pendingFinished.get(executionId);
        if (finishedStatus && finishedStatus !== 'success') {
          failRun(Object.assign(new Error('WF23_EXECUTION_NOT_FINISHED_SUCCESS'), { code: 'WF23_EXECUTION_NOT_FINISHED_SUCCESS' }));
          return;
        }
        maybeFinish();
      } catch (error) {
        failRun(error?.code ? error : Object.assign(new Error('WF23_REST_RUN_FAILED'), { code: 'WF23_REST_RUN_FAILED' }));
      }
    })();
    await runRequest;
  };
  const onClose = () => {
    if (!settled) failRun(Object.assign(new Error('WF23_PUSH_CONNECTION_FAILED'), { code: 'WF23_PUSH_CONNECTION_FAILED' }));
  };
  const onError = () => failRun(Object.assign(new Error('WF23_PUSH_CONNECTION_FAILED'), { code: 'WF23_PUSH_CONNECTION_FAILED' }));
  socket.on('message', onMessage);
  socket.on('open', onOpen);
  socket.on('close', onClose);
  socket.on('error', onError);
  timer = setTimeout(() => failRun(Object.assign(new Error('WF23_TIMEOUT_COMMAND_RUN'), { code: 'WF23_TIMEOUT_COMMAND_RUN' })), timeoutMs);
  try {
    const receipt = await resultPromise;
    if (!await awaitExecutionRemoval({ token, executionId, fetchImpl, timeoutMs: reconcileTimeoutMs })) {
      failureError = Object.assign(new Error('WF23_EXECUTION_NOT_REMOVED'), { code: 'WF23_EXECUTION_NOT_REMOVED' });
      throw failureError;
    }
    return receipt;
  } finally {
    clearTimeout(timer);
    try {
      if (failureError && runRequest) {
        try { await awaitWithin(runRequest, reconcileTimeoutMs); } catch {}
        if (executionId) await reconcileExecution({ token, executionId, fetchImpl, timeoutMs: reconcileTimeoutMs });
      }
    } finally {
      if (typeof socket.close === 'function') socket.close();
    }
  }
}

async function executeLocalProof() {
  const pgModule = installedModule('pg');
  const jwtModule = installedModule('jsonwebtoken');
  const wsModule = installedModule('ws');
  const auth = await readAuthContext({ pgModule });
  let token = signAuthToken(auth, jwtModule);
  try {
    return await runLocalWorkflow({ token, wsModule });
  } finally {
    token = null;
  }
}

module.exports = {
  validateIRun, safeFailureCode, terminalLine, isDirectEntrypoint,
  createJWTHash, databaseOptions, readAuthContext, signAuthToken,
  wrappedExecutionId, framePayload, terminalRunPayload, runLocalWorkflow,
  WATCHDOG_TIMEOUT_MS, WORKFLOW_ID, TRIGGER_NODE, TERMINAL_NODE,
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
  executeLocalProof().then((receipt) => {
    fs.writeSync(1, terminalLine(null, receipt));
    process.exit(0);
  }).catch((error) => {
    fs.writeSync(1, terminalLine(error, null));
    process.exit(1);
  });
}
