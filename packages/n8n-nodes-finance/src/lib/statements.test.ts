import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { assertPreparedOutbox } from './contracts';
import { ISSUER_PROFILES, parseStatement, projectStatementToActual } from './statements';

test('packaged issuer profiles exactly match ACTIVE repository source contracts', () => {
  const registryPath = path.resolve(process.cwd(), '../../config/statement-sources.json');
  const registry = JSON.parse(readFileSync(registryPath, 'utf8')) as {
    sources: Array<{ adapter_status: string; adapter: string | null; card_code: string }>;
  };
  const active = registry.sources
    .filter(source => source.adapter_status === 'ACTIVE')
    .map(source => source.adapter)
    .filter((adapter): adapter is string => typeof adapter === 'string')
    .sort();
  assert.deepEqual([...ISSUER_PROFILES].sort(), active);
  for (const placeholder of registry.sources.filter(source => source.adapter_status === 'PLACEHOLDER')) {
    assert.equal(placeholder.adapter, null, `${placeholder.card_code} must remain unmatchable without verified evidence`);
  }
});

test('statement placeholders are explicit interim card-scoped gaps', () => {
  const registryPath = path.resolve(process.cwd(), '../../config/statement-sources.json');
  const registry = JSON.parse(readFileSync(registryPath, 'utf8')) as {
    sources: Array<{ adapter_status: string; adapter: string | null; card_code: string; notes: string }>;
  };
  const placeholders = registry.sources.filter(source => source.adapter_status === 'PLACEHOLDER');
  assert.deepEqual(placeholders.map(source => source.card_code).sort(), ['RAK_WORLD', 'SC_PLATINUM_X']);
  for (const source of placeholders) {
    assert.match(source.notes, /interim placeholder/i);
    assert.match(source.notes, /first real statement fixture/i);
    assert.match(source.notes, /ACTIVE source ingestion\/history/i);
  }
  assert.equal(registry.sources.find(source => source.card_code === 'ADCB_CASHBACK')?.adapter_status, 'ACTIVE');

  const acceptancePath = path.resolve(process.cwd(), '../../config/project-acceptance.json');
  const acceptance = JSON.parse(readFileSync(acceptancePath, 'utf8')) as {
    requirements: Array<{ id: string; blockers: string[] }>;
  };
  const orchestration = acceptance.requirements.find(requirement => requirement.id === 'deterministic-n8n-orchestration');
  assert.ok(orchestration, 'deterministic n8n acceptance requirement must exist');
  assert.equal(orchestration.blockers.includes('RAK_SC_STATEMENT_ADAPTERS_REQUIRED'), false,
    'placeholder statement adapters must not globally block active workflows');
});

test('EI credits are typed as payment and refund and statement ties', () => {
  const statement = parseStatement(`Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 1,043.29
PRIMARY CARD NO:5424XXXXXXXX0082
02 JUL 02 JUL TRANSFER PAYMENT RECEIVED THANK YOU 1,100.00CR
10 JUL 09 JUL AMAZON.AE DUBAI ARE 93.42
13 JUL 12 JUL AMAZON.AE DUBAI ARE 3.55CR
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,966.84 100.00 25/08/26 33.16 0.00 33.16`, 'emirates_islamic_v1', 'ei.pdf');
  assert.equal(statement.transactions[0].transaction_type, 'PAYMENT');
  assert.equal(statement.transactions[2].transaction_type, 'REFUND');
  assert.equal(statement.balance_tied, true);
});

test('ADCB preserves foreign facts and reward credit semantics', () => {
  const statement = parseStatement(`15/07/26
09/08/26
PREVIOUS BALANCE OUTSTANDING 100.00
Card No : XXXXXXXXXXXX8833 - TEST USER
14/06/2026 LOCAL SHOP DUBAI ARE 50.00
18/06/2026 PAYMENT RECEIVED, THANK YOU 25.00 CR
23/06/2026 FOREIGN VENDOR USA 10.00 USD 38.25
[1 USD=AED 3.82500]
10/07/2026 1% Cashback-Other Purchase JUN-26 1.00 CR
15/07/2026 NEW BALANCE OUTSTANDING 162.25`, 'adcb_v1');
  assert.equal(statement.transactions[2].currency_original, 'USD');
  assert.equal(statement.transactions[2].amount_original, '10.00');
  assert.equal(statement.transactions[3].transaction_type, 'REWARD_CREDIT');
  assert.equal(statement.balance_tied, true);
});

test('Wio parses signed rows and payment topics', () => {
  const statement = parseStatement(`CREDIT STATEMENT
FROM 01/07/2026 TO 01/08/2026
Wio Bank PAYMENT DUE DATE MIN. PAYMENT DUE TOTAL TO PAY
01/08/2026 0.00 0.00
ACCOUNT NUMBER 3342325009
Balance From Last Statement 0.00
Closing balance (Total to pay) -274.40
01/07/2026 P100000001 Example Merchant ****4113 -100.00
01/08/2026 P100000002 Credit Repayment +374.40`, 'wio_credit_v1');
  assert.equal(statement.transactions[1].transaction_type, 'PAYMENT');
  assert.equal(statement.balance_tied, true);
});

test('placeholder or caller-invented profiles are rejected', () => {
  assert.throws(() => parseStatement('x'.repeat(30), 'rak_world_v1' as never), /Unknown or unverified/);
});

test('EI payment/refund projection uses positive Actual credits and preflights', () => {
  const statement = parseStatement(`Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
02 JUL 02 JUL TRANSFER PAYMENT RECEIVED THANK YOU 80.00CR
03 JUL 03 JUL AMAZON REFUND 20.00CR
04 JUL 04 JUL AMAZON PURCHASE 10.00
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,990.00 10.00 25/08/26 10.00 0.00 10.00`, 'emirates_islamic_v1');
  const rows = projectStatementToActual(statement);
  assert.deepEqual(rows.map(row => row.amount), [8000, 2000, -1000]);
  const checked = assertPreparedOutbox({ schema_version: 1, outbox_id: 'ei', state: 'PREPARED', account_id: 'ei-account', execution_context: { trigger: 'SUBWORKFLOW', manual: false, mcp: false }, writer_lease: { lease_id: 'lease', fencing_token: 1, expires_at: new Date(Date.now() + 60_000).toISOString() }, transactions: rows });
  assert.equal(checked.transactions.length, 3);
});

test('ADCB reward and Wio payment project to positive Actual amounts', () => {
  const adcb = parseStatement(`15/07/26
09/08/26
PREVIOUS BALANCE OUTSTANDING 10.00
Card No : XXXXXXXXXXXX8833 - TEST
10/07/2026 1% Cashback-Other Purchase JUN-26 1.00 CR
15/07/2026 NEW BALANCE OUTSTANDING 9.00`, 'adcb_v1');
  assert.equal(projectStatementToActual(adcb)[0].amount, 100);
  const wio = parseStatement(`CREDIT STATEMENT
FROM 01/07/2026 TO 01/08/2026
Wio Bank PAYMENT DUE DATE MIN. PAYMENT DUE TOTAL TO PAY
01/08/2026 0.00 0.00
ACCOUNT NUMBER 3342325009
Balance From Last Statement 100.00
Closing balance (Total to pay) 0.00
01/08/2026 P100000002 Credit Repayment +100.00`, 'wio_credit_v1');
  assert.equal(projectStatementToActual(wio)[0].amount, 10000);
});
