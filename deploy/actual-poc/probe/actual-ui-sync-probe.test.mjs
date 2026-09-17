import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';
import { chromium } from 'playwright';

const probeSource = await readFile(new URL('./actual-ui-sync-probe.mjs', import.meta.url), 'utf8');
const callbackStart = probeSource.indexOf('const registerStateInPage = ') + 'const registerStateInPage = '.length;
const callbackEnd = probeSource.indexOf('\n\nconst registerState =', callbackStart);
assert.ok(callbackStart > 'const registerStateInPage = '.length, 'register-state callback is present');
assert.ok(callbackEnd > callbackStart, 'register-state callback has a bounded source region');
const callbackSource = probeSource.slice(callbackStart, callbackEnd).trim().replace(/;$/, '');
const registerStateInPage = vm.runInNewContext(`(${callbackSource})`);
const orderingStart = probeSource.indexOf('const compareText = ');
const orderingEnd = probeSource.indexOf('\nconst expectedBytes', orderingStart);
assert.ok(orderingStart > 0, 'readback ordering helpers are present');
assert.ok(orderingEnd > orderingStart, 'readback ordering helpers have a bounded source region');
const orderingHelpers = vm.runInNewContext(`(() => { ${probeSource.slice(orderingStart, orderingEnd)}; return { canonicalizeAccounts, canonicalizeTransactions }; })()`);

test('readback ordering is stable across permutations and account-grouped collection', () => {
  const rows = [
    { account_name: 'B', amount_minor: 10, date: '2026-08-02', imported_id: 'b-2', payee: null },
    { account_name: 'A', amount_minor: 20, date: '2026-08-01', imported_id: 'a-1', payee: 'Alpha' },
    { account_name: 'B', amount_minor: -5, date: '2026-08-01', imported_id: 'b-1', payee: 'Beta' },
  ];
  const groupedByAccount = [rows[1], rows[0], rows[2]];
  const permutations = [rows, [...rows].reverse(), groupedByAccount];
  const canonical = permutations.map(value => JSON.parse(JSON.stringify(orderingHelpers.canonicalizeTransactions(value))));
  assert.deepEqual(canonical[0], canonical[1]);
  assert.deepEqual(canonical[0], canonical[2]);
  assert.deepEqual(canonical[0].map(row => row.imported_id), ['a-1', 'b-1', 'b-2']);
  assert.deepEqual(
    JSON.parse(JSON.stringify(orderingHelpers.canonicalizeAccounts([{ name: 'Wio' }, { name: 'ADCB' }]))),
    [{ name: 'ADCB' }, { name: 'Wio' }],
  );
});

test('register-state browser callback evaluates without an outer closure', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <h1 style="width: 120px; height: 24px">Current</h1>
      <div data-testid="transaction-table" style="width: 640px; height: 240px">
        <div data-testid="table" style="width: 640px; height: 200px">
          <div data-focus-key="row-1" style="width: 640px; height: 40px">
            <div data-testid="row" style="width: 640px; height: 40px">2026-08-01 Merchant -10</div>
          </div>
        </div>
      </div>
      <input data-testid="transactions-search" aria-label="Search transactions" style="width: 160px; height: 24px">
    `);
    const state = await page.evaluate(registerStateInPage, { accountName: 'Current' });
    assert.equal(state.account_heading_visible, true);
    assert.equal(state.table_inner_present, true);
    assert.equal(state.row_count, 1);
    assert.equal(state.search_present, true);
  } finally {
    await browser.close();
  }
});
