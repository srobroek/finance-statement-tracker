import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { assertPreparedOutbox } from './contracts';
import { ISSUER_PROFILES, detectIssuerProfile, parseStatement, projectStatementToActual } from './statements';

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

test('RAKBANK preserves explicit dates, FX facts, bank-posted AED, and summary ties', () => {
  const text = `RAKBANK CREDIT CARD STATEMENT
DATE ISSUED : 31/07/2026 :
STATEMENT PERIOD : 01/07/2026 TO 31/07/2026 :
CARD NUMBER : 1234 XXXX 5678 :
PREVIOUS BALANCE AED 100.00
RETAIL TRANSACTIONS AED 28.25 +
PAYMENTS AND CREDITS AED 23.25 -
CURRENT BALANCE AED 105.00
CREDIT CARD LIMIT
AED 1,000.00
MINIMUM PAYMENT DUE
AED 10.00
TOTAL AMOUNT DUE (TO AVOID INTEREST)
AED 105.00
PAYMENT DUE DATE
15/08/2026
DATE TRANSACTION
DESCRIPTION
TRANSACTION CURRENCY
TRANSACTION AMOUNT
TOTAL AMOUNT (AED)
01/07/2026 LOCAL SHOP, DUBAI, AED 10.00 - 10.00
02/07/2026 FOREIGN MERCHANT, EUROPE, EUR 5.00 18.25
03/07/2026 PAYMENT RECEIVED - AED 23.25 CR - 23.25 CR`;

  assert.equal(detectIssuerProfile(text), 'rakbank_v1');
  const statement = parseStatement(text, 'rakbank_v1', 'rakbank.pdf');
  assert.equal(statement.statement_date, '2026-07-31');
  assert.equal(statement.period_start, '2026-07-01');
  assert.equal(statement.payment_due_date, '2026-08-15');
  assert.deepEqual(statement.card_last4s, ['5678']);
  assert.equal(statement.transactions.length, 3);
  assert.equal(statement.transactions[1].amount_original, '5.00');
  assert.equal(statement.transactions[1].currency_original, 'EUR');
  assert.equal(statement.transactions[1].amount_aed, '18.25');
  assert.equal(statement.transactions[1].exchange_rate, null);
  assert.equal(statement.transactions[2].transaction_type, 'PAYMENT');
  assert.equal(statement.balance_tied, true);
  assert.deepEqual(projectStatementToActual(statement).map(row => row.amount), [-1000, -1825, 2325]);

  assert.throws(
    () => parseStatement(text.replace('CURRENT BALANCE AED 105.00', 'CURRENT BALANCE AED 106.00'), 'rakbank_v1'),
    /printed summary does not reconcile/,
  );
});

test('EI credits preserve provisional CREDIT while payment and reward topics stay explicit', () => {
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
  assert.equal(statement.transactions[2].transaction_type, 'CREDIT');
  assert.equal(statement.balance_tied, true);
  const explicitRefund = parseStatement(`Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
13 JUL 12 JUL REFUND FROM MERCHANT 3.55CR
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,966.84 100.00 25/08/26 0.00 0.00 96.45`, 'emirates_islamic_v1');
  assert.equal(explicitRefund.transactions[0].transaction_type, 'REFUND');
});

test('EI resolves December and January row years within statement bounds', () => {
  const statement = parseStatement(`Statement of Card Account
From: 15th Dec 2025
14th Jan 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
31 DEC 02 JAN CROSS-YEAR PURCHASE 10.00
02 JAN 03 JAN PAYMENT RECEIVED THANK YOU 5.00CR
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,966.84 100.00 25/02/26 5.00 0.00 5.00`, 'emirates_islamic_v1');
  assert.equal(statement.period_start, '2025-12-15');
  assert.equal(statement.period_end, '2026-01-14');
  assert.equal(statement.transactions[0].transaction_date, '2026-01-02');
  assert.equal(statement.transactions[0].post_date, '2025-12-31');
  assert.equal(statement.transactions[0].description, 'CROSS-YEAR PURCHASE');
  assert.equal(statement.transactions[0].amount_aed, '10.00');
  assert.equal(statement.transactions[0].card_last4, '0082');
  assert.equal(statement.transactions[1].transaction_date, '2026-01-03');
  assert.equal(statement.transactions[1].post_date, '2026-01-02');
});

test('EI rejects rows whose date cannot be resolved from authoritative bounds', () => {
  const base = `Statement of Card Account
From: 15th Dec 2025
14th Jan 2026
To:
OPENING BALANCE 100.00
PRIMARY CARD NO:5424XXXXXXXX0082
ROW
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,966.84 100.00 25/02/26 100.00 0.00 100.00`;
  assert.throws(() => parseStatement(base.replace('ROW', '31 FEB 02 JAN INVALID DATE 10.00'), 'emirates_islamic_v1'), /Ambiguous or out-of-period statement date/);
  assert.throws(() => parseStatement(base.replace('From: 15th Dec 2025', 'From: 15th Dec 2024').replace('ROW', '01 JAN 02 JAN OUT OF PERIOD 10.00'), 'emirates_islamic_v1'), /Ambiguous or out-of-period statement date/);
  assert.throws(() => parseStatement(base.replace('From: 15th Dec 2025', 'From: missing').replace('ROW', '31 DEC 02 JAN MISSING BOUNDS 10.00'), 'emirates_islamic_v1'), /without authoritative statement bounds/);
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
