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
