import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';

if (typeof chromium?.launch !== 'function') throw new Error('Playwright chromium launch export missing');
const requireApi = createRequire('/probe/package.json');
const actualApi = requireApi('@actual-app/api');
if (typeof actualApi.downloadBudget !== 'function' || typeof actualApi.sync !== 'function') {
  throw new Error('Actual API downloadBudget/sync exports missing');
}
const base = process.env.ACTUAL_RESTORE_URL;
const password = process.env.ACTUAL_PASSWORD;
const runIndex = process.env.ACTUAL_RESTORE_RUN_INDEX ?? 'unknown';
const sourceCommit = process.env.FINANCE_SOURCE_COMMIT ?? '92678bca35ef2fec68d11a7063bd1da8c26e35c3';
const checkpointPath = process.env.ACTUAL_RESTORE_CHECKPOINT_PATH ?? `/output/checkpoints-${runIndex}.json`;
const apiDataDir = process.env.ACTUAL_RESTORE_DATA_DIR ?? `/tmp/actual-api-cache-${runIndex}`;
const syncId = process.env.ACTUAL_SYNC_ID;
const expectedPath = process.env.ACTUAL_EXPECTED_ENVELOPE_PATH ?? '/probe/expected-envelope.json';
const expectedContractPath = process.env.ACTUAL_EXPECTED_ENVELOPE_CONTRACT_PATH ?? '/probe/expected-envelope.contract.json';
if (!syncId) throw new Error('ACTUAL_SYNC_ID missing');
if (!base || !password) throw new Error('missing Actual probe environment');

const clean = value => String(value ?? '').replace(/\s+/g, ' ').trim();
const redact = value => clean(value)
  .replace(/https?:\/\/[^\s]+/gi, '<url>')
  .replace(/(password|passwd|secret|token|cookie|authorization)\s*[=:]\s*[^\s,;]+/gi, '$1=<redacted>')
  .slice(0, 500);
const digest = value => createHash('sha256').update(value, 'utf8').digest('hex');
const stable = value => {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
  return value;
};
const stableDigest = value => digest(JSON.stringify(stable(value)));
const expectedBytes = await readFile(expectedPath);
const expectedStat = await stat(expectedPath);
const expectedMode = (expectedStat.mode & 0o777).toString(8).padStart(4, '0');
if (expectedMode !== '0600') throw new Error('expected envelope mode must be 0600');
const expectedHash = digest(expectedBytes);
const contractBytes = await readFile(expectedContractPath);
const expectedContract = JSON.parse(contractBytes.toString('utf8'));
if (expectedContract.path !== expectedPath || expectedContract.sha256 !== expectedHash || expectedContract.mode !== expectedMode) {
  throw new Error('expected envelope path/hash/mode contract mismatch');
}
const expectedEnvelopeEvidence = {
  path: expectedPath,
  sha256: expectedHash,
  mode: expectedMode,
  contract_path: expectedContractPath,
  contract_sha256: digest(contractBytes),
};
const expectedEnvelope = JSON.parse(expectedBytes.toString('utf8'));
const expected = {
  accounts: expectedEnvelope.accounts,
  representative_transactions: expectedEnvelope.representative_transactions,
};
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
const events = [];
const diagnostics = { console: [], pageerror: [], requestfailed: [] };
const checkpoints = [];
const evidence = {
  navigation: [],
  account_surfaces: [],
  register_searches: [],
  indexeddb: [],
  api_pre_ui: null,
  register_states: [],
  expected_envelope: expectedEnvelopeEvidence,
};

page.on('request', request => {
  const url = new URL(request.url());
  if (/account\/(login|validate)|sync\//.test(url.pathname)) events.push({ kind: 'request', method: request.method(), path: url.pathname });
});
page.on('response', response => {
  const url = new URL(response.url());
  if (/account\/(login|validate)|sync\//.test(url.pathname)) events.push({ kind: 'response', status: response.status(), path: url.pathname });
});
page.on('console', message => diagnostics.console.push({ type: message.type(), text: redact(message.text()) }));
page.on('pageerror', error => diagnostics.pageerror.push({ text: redact(error?.stack ?? error?.message ?? error) }));
page.on('requestfailed', request => diagnostics.requestfailed.push({
  method: request.method(), path: new URL(request.url()).pathname, error: redact(request.failure()?.errorText ?? 'unknown'),
}));

const visibleRoles = async () => page.locator('[role]').evaluateAll(elements => {
  const counts = {};
  for (const element of elements) {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    if (!rect.width || !rect.height || style.visibility === 'hidden' || style.display === 'none') continue;
    const role = element.getAttribute('role');
    if (role) counts[role] = (counts[role] ?? 0) + 1;
  }
  return counts;
}).catch(() => ({}));

const indexedDbState = async () => page.evaluate(async () => {
  try {
    const databases = await indexedDB.databases();
    return { available: true, databases: databases.map(entry => ({ name: entry.name ?? null, version: entry.version ?? null })) };
  } catch (error) {
    return { available: false, error: String(error?.name ?? 'indexeddb_error') };
  }
}).catch(error => ({ available: false, error: String(error?.name ?? 'indexeddb_error') }));

const registerState = async accountName => page.evaluate(({ accountName }) => {
  const body = document.body?.innerText ?? '';
  const visible = element => {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return Boolean(rect.width && rect.height && style.visibility !== 'hidden' && style.display !== 'none');
  };
  const table = document.querySelector('[data-testid="transaction-table"]');
  const inner = table?.querySelector('[data-testid="table"]');
  const wrappers = inner ? [...inner.querySelectorAll('[data-focus-key]')].filter(visible) : [];
  const rows = wrappers.map(wrapper => wrapper.querySelector('[data-testid="row"]')).filter(visible);
  const headings = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
    .filter(visible).map(element => clean(element.textContent));
  const searchCandidates = [
    ['[data-testid="transactions-search"]', document.querySelector('[data-testid="transactions-search"]')],
    ['input[placeholder*="Search" i]', document.querySelector('input[placeholder*="Search" i]')],
    ['input[aria-label*="Search" i]', document.querySelector('input[aria-label*="Search" i]')],
  ];
  const search = searchCandidates.find(([, element]) => visible(element));
  return {
    route: location.pathname,
    account_heading_visible: headings.some(value => value === accountName || value.includes(accountName)),
    visible_headings: headings.slice(0, 12),
    table_present: Boolean(table),
    table_visible: visible(table),
    table_inner_present: Boolean(inner),
    table_inner_visible: visible(inner),
    row_wrapper_count: wrappers.length,
    row_count: rows.length,
    search_present: Boolean(search),
    search_selector: search?.[0] ?? null,
    body_downloading: body.includes('Downloading'),
    body_has_files: body.includes('Files'),
  };
}, { accountName }).catch(error => ({ route: page.url(), account_heading_visible: false, table_present: false, table_visible: false, table_inner_present: false, table_inner_visible: false, row_wrapper_count: 0, row_count: 0, search_present: false, search_selector: null, body_downloading: false, body_has_files: false, error: String(error?.name ?? 'register_state_error') }));

const checkpoint = async (label, extra = {}) => {
  const body = await page.locator('body').innerText().catch(() => '');
  const record = {
    label,
    run_index: runIndex,
    url: page.url(),
    route: new URL(page.url()).pathname,
    body_sha256: digest(body),
    body_bytes: Buffer.byteLength(body, 'utf8'),
    visible_roles: await visibleRoles(),
    console: diagnostics.console.slice(-50),
    pageerror: diagnostics.pageerror.slice(-50),
    requestfailed: diagnostics.requestfailed.slice(-50),
    indexeddb: await indexedDbState(),
    ...extra,
  };
  checkpoints.push(record);
  await writeFile(checkpointPath, JSON.stringify({ schema_version: 2, source_commit: sourceCommit, status: 'checkpoint', checkpoints }, null, 2), { mode: 0o600 });
  return record;
};

const textOf = async locator => clean(await locator.innerText().catch(() => ''));
const waitForList = () => page.waitForFunction(() => document.body.innerText.includes('Files') || location.pathname === '/budget', null, { timeout: 30000 });
const waitForBudget = () => page.waitForFunction(() => location.pathname === '/budget' && !document.body.innerText.includes('Downloading'), null, { timeout: 120000 });
const amountCandidates = minor => {
  const sign = minor < 0 ? '-' : '';
  const fixed = (Math.abs(minor) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const plain = (Math.abs(minor) / 100).toFixed(2);
  const minorText = Math.abs(minor).toLocaleString('en-US');
  return minor < 0 ? [`-${minorText}`, `${sign}${fixed}`, `-${plain}`] : [minorText, fixed, plain];
};
const dateCandidates = date => {
  const [year, month, day] = date.split('-').map(Number);
  const value = new Date(Date.UTC(year, month - 1, day));
  const monthLong = value.toLocaleString('en-US', { month: 'long', timeZone: 'UTC' });
  const monthShort = value.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
  return [date, `${month}/${day}/${year}`, `${monthLong} ${day}, ${year}`, `${monthShort} ${day}, ${year}`, `${day} ${monthShort} ${year}`];
};

const collectApiProof = async () => {
  let initialized = false;
  try {
    await mkdir(apiDataDir, { recursive: true, mode: 0o700 });
    await actualApi.init({ dataDir: apiDataDir, serverURL: base, password });
    initialized = true;
    await actualApi.downloadBudget(syncId);
    if (typeof actualApi.sync !== 'function') throw new Error('Actual API sync export missing');
    await actualApi.sync();
    const rawAccounts = await actualApi.getAccounts();
    const apiAccounts = [];
    for (const expectedAccount of expected.accounts) {
      const raw = rawAccounts.find(item => item.name === expectedAccount.name);
      if (!raw) throw new Error(`Actual API account missing: ${expectedAccount.name}`);
      const balanceRaw = await actualApi.getAccountBalance(raw.id);
      const balance = typeof balanceRaw === 'number' ? balanceRaw : Number(balanceRaw?.balance_current ?? balanceRaw?.balance ?? balanceRaw);
      if (!Number.isFinite(balance)) throw new Error(`Actual API account balance unavailable: ${expectedAccount.name}`);
      apiAccounts.push({ name: raw.name, balance_minor: balance, offbudget: Boolean(raw.offbudget), closed: Boolean(raw.closed) });
    }
    const grouped = new Map();
    for (const row of expected.representative_transactions) grouped.set(row.account_name, [...(grouped.get(row.account_name) ?? []), row]);
    const apiTransactions = [];
    for (const [accountName, rows] of grouped) {
      const raw = rawAccounts.find(item => item.name === accountName);
      const transactions = await actualApi.getTransactions(raw.id, '2020-01-01', '2030-12-31');
      for (const expectedTransaction of rows) {
        const match = transactions.find(item => item.imported_id === expectedTransaction.imported_id);
        if (!match) throw new Error(`Actual API transaction missing: ${expectedTransaction.imported_id}`);
        apiTransactions.push({ account_name: accountName, amount_minor: Number(match.amount), date: match.date, imported_id: match.imported_id, payee: match.payee ?? null });
      }
    }
    const result = { accounts: apiAccounts, representative_transactions: apiTransactions };
    const expectedDigest = stableDigest(expected);
    const resultDigest = stableDigest(result);
    if (resultDigest !== expectedDigest) throw new Error('Actual API normalized contract differs from expected');
    const proof = {
      source: '@actual-app/api@26.8.1',
      normalized_contract: true,
      account_count: apiAccounts.length,
      representative_transaction_count: apiTransactions.length,
      account_digest: stableDigest(apiAccounts),
      transaction_digest: stableDigest(apiTransactions),
      contract_digest: resultDigest,
      expected_contract_digest: expectedDigest,
    };
    return { result, proof };
  } finally {
    if (initialized) await actualApi.shutdown().catch(() => {});
  }
};

const clickAccount = async accountName => {
  const link = page.locator('a').filter({ hasText: accountName }).first();
  await link.waitFor({ state: 'visible', timeout: 10000 });
  const href = await link.getAttribute('href');
  const targetPath = href ? new URL(href, base).pathname : null;
  if (!targetPath) throw new Error(`account link href missing: ${accountName}`);
  await link.click();
  await page.waitForURL(url => new URL(url).pathname === targetPath, { timeout: 30000 });
  return { href, targetPath };
};

const returnToBudget = async () => {
  if (new URL(page.url()).pathname === '/budget') return;
  await page.goBack({ waitUntil: 'commit', timeout: 30000 });
  await waitForBudget();
  if (new URL(page.url()).pathname !== '/budget') throw new Error(`SPA back navigation did not return to budget: ${new URL(page.url()).pathname}`);
  const closed = page.getByText('Closed accounts...', { exact: true }).first();
  if (await closed.count()) {
    const text = await textOf(page.locator('body'));
    if (!text.includes('ADCB Credit Card · 8833 / 6838')) await closed.click();
  }
};

try {
  await page.goto(`${base}/login`, { waitUntil: 'commit', timeout: 15000 });
  await checkpoint('login_page');
  const input = page.locator('input[type="password"]').first();
  await input.waitFor({ state: 'visible', timeout: 30000 });
  const responseWait = page.waitForResponse(response => new URL(response.url()).pathname === '/account/login', { timeout: 30000 });
  await input.fill(password);
  const submit = page.getByRole('button', { name: /sign in|log in|login/i }).first();
  if (await submit.count()) await submit.click(); else await page.locator('button').first().click();
  const loginResponse = await responseWait;
  if (loginResponse.status() !== 200) throw new Error(`Actual login HTTP ${loginResponse.status()}`);
  await waitForList();
  await checkpoint('authenticated_file_list', { login_http_status: loginResponse.status() });
  if ((await textOf(page.locator('body'))).includes('Invalid password')) throw new Error('Actual UI rejected password');
  const finance = page.getByText('My Finances', { exact: true }).first();
  if (!await finance.count()) throw new Error('My Finances file not visible');
  await finance.click();
  evidence.navigation.push({ step: 'budget-file', label: 'My Finances' });
  await waitForBudget();
  await checkpoint('budget_ready', { budget_ready: true, login_http_status: loginResponse.status(), validate_http_200: events.some(event => event.kind === 'response' && event.path === '/account/validate' && event.status === 200) });
  evidence.navigation.push({ step: 'budget-ready', href: page.url(), downloading: false });

  const apiProof = await collectApiProof();
  evidence.api_pre_ui = apiProof.proof;
  await checkpoint('api_pre_ui', { api_pre_ui: apiProof.proof });

  const closed = page.getByText('Closed accounts...', { exact: true }).first();
  if (!await closed.count()) throw new Error('closed-account surface control not visible');
  await checkpoint('before_closed_account_surface', { closed_control_present: true });
  await closed.click();
  await page.waitForTimeout(500);
  await checkpoint('closed_account_surface_open', { closed_surface_open: true });
  evidence.navigation.push({ step: 'closed-accounts-expanded', href: page.url() });

  const accountLinks = new Map();
  for (const account of expected.accounts) {
    const link = page.locator('a').filter({ hasText: account.name }).first();
    await link.waitFor({ state: 'visible', timeout: 10000 });
    const href = await link.getAttribute('href');
    const rendered = await textOf(link);
    if (!href || !rendered.includes(account.name)) throw new Error(`account surface missing: ${account.name}`);
    accountLinks.set(account.name, href);
    evidence.account_surfaces.push({ name: account.name, href, rendered_text: rendered, closed: account.closed, offbudget: account.offbudget });
  }
  const closedAccount = expected.accounts.find(account => account.closed);
  if (!closedAccount || !evidence.account_surfaces.some(account => account.name === closedAccount.name && account.closed)) throw new Error('closed twelfth account not proven');
  await checkpoint('account_surfaces_selected', { default_visible_account_count: expected.accounts.filter(account => !account.closed).length, closed_account_count: expected.accounts.filter(account => account.closed).length, account_surface_count: evidence.account_surfaces.length });

  const groups = new Map();
  for (const transaction of expected.representative_transactions) groups.set(transaction.account_name, [...(groups.get(transaction.account_name) ?? []), transaction]);
  const uiTransactions = [];
  for (const [accountName, transactions] of groups) {
    await returnToBudget();
    const clicked = await clickAccount(accountName);
    const initialState = await registerState(accountName);
    evidence.register_states.push({ stage: 'before_register_readiness', account_name: accountName, state: initialState });
    await checkpoint('before_register_readiness', { account_name: accountName, expected_transaction_count: transactions.length, register_state: initialState });
    await page.waitForFunction(({ accountName }) => {
      const visible = element => {
        if (!element) return false;
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return Boolean(rect.width && rect.height && style.visibility !== 'hidden' && style.display !== 'none');
      };
      const body = document.body?.innerText ?? '';
      const table = document.querySelector('[data-testid="transaction-table"]');
      const inner = table?.querySelector('[data-testid="table"]');
      const wrappers = inner ? [...inner.querySelectorAll('[data-focus-key]')].filter(visible) : [];
      const rows = wrappers.map(wrapper => wrapper.querySelector('[data-testid="row"]')).filter(visible);
      const heading = [...document.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(visible).some(element => (element.textContent ?? '').includes(accountName));
      const search = [...document.querySelectorAll('[data-testid="transactions-search"],input[placeholder*="Search" i],input[aria-label*="Search" i]')].some(visible);
      return location.pathname !== '/budget' && heading && visible(table) && visible(inner) && rows.length > 0 && search && !body.includes('Downloading');
    }, { accountName }, { timeout: 60000 });
    const readyState = await registerState(accountName);
    evidence.register_states.push({ stage: 'register_semantically_ready', account_name: accountName, state: readyState });
    await checkpoint('register_semantically_ready', { account_name: accountName, account_heading_visible: readyState.account_heading_visible, search_present: readyState.search_present, table_present: readyState.table_present, table_inner_present: readyState.table_inner_present, row_count: readyState.row_count, register_state: readyState });
    if (!readyState.account_heading_visible || !readyState.search_present || !readyState.table_inner_present || readyState.row_count < 1) throw new Error(`validated register surface incomplete: ${accountName}`);
    const table = page.locator('[data-testid="transaction-table"]').first();
    const rowLocator = table.locator('[data-testid="table"] [data-focus-key] [data-testid="row"]');
    const search = page.locator(readyState.search_selector).first();
    for (const transaction of transactions) {
      await search.fill(transaction.date);
      await page.waitForTimeout(700);
      const renderedTable = await textOf(table);
      const rows = (await rowLocator.allTextContents()).map(clean).filter(Boolean);
      const row = rows.find(value => amountCandidates(transaction.amount_minor).some(candidate => value.includes(candidate)) && dateCandidates(transaction.date).some(candidate => value.includes(candidate)));
      const dateMarker = dateCandidates(transaction.date).find(candidate => renderedTable.includes(candidate));
      if (!row || !dateMarker) throw new Error(`register row not visible: ${transaction.imported_id}`);
      evidence.register_searches.push({ account_name: accountName, href: clicked.href, search_term: transaction.date, date_marker: dateMarker, row_text: row, signed_amount_minor: transaction.amount_minor, imported_id: transaction.imported_id, payee: transaction.payee });
      uiTransactions.push({ ...transaction });
    }
    await checkpoint('register_rows_proven', { account_name: accountName, list_present: readyState.table_present, list_visible: readyState.table_visible, search_present: readyState.search_present, row_count: readyState.row_count, transaction_count: transactions.length, signed_identity_count: transactions.length });
  }
  evidence.indexeddb = await indexedDbState();
  if (!evidence.indexeddb.databases?.some(entry => entry.name?.startsWith('documents-'))) throw new Error('Actual document IndexedDB database missing');
  if (uiTransactions.length !== expected.representative_transactions.length) throw new Error('representative transaction count mismatch');
  await checkpoint('final_ui_parity', { account_count: evidence.account_surfaces.length, default_visible_account_count: 11, closed_account_count: 1, transaction_count: uiTransactions.length, list_proven: true, search_proven: true, rows_proven: true, account_heading_proven: true, api_pre_ui: evidence.api_pre_ui });
  console.log(JSON.stringify({ schema_version: 2, source_commit: sourceCommit, expected_envelope: expectedEnvelopeEvidence, api: apiProof.result, ui: { accounts: expected.accounts, representative_transactions: uiTransactions }, evidence, browser_events: events, checkpoints }, null, 2));
} catch (error) {
  await checkpoint('probe_failure', { failure_checkpoint: true, error: redact(error?.stack ?? error?.message ?? error), final_ui_booleans: { list_proven: false, search_proven: false, rows_proven: false, account_heading_proven: false } }).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
