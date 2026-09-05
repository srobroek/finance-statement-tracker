import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { IExecuteFunctions, INodeExecutionData } from 'n8n-workflow';
import { FinanceStatement } from '../nodes/FinanceStatement/FinanceStatement.node';
import { FinanceRules } from '../nodes/FinanceRules/FinanceRules.node';
import { loadPackagedLedgerRules, PACKAGED_RULE_SOURCE_SHA256 } from './runtime-rules';

const text = `Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 0.00
PRIMARY CARD NO:5424XXXXXXXX0082
10 JUL 09 JUL AMAZON.AE DUBAI ARE 93.42
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,906.58 93.42 25/08/26 93.42 0.00 93.42`;

function context(items: INodeExecutionData[], operation = 'parse'): IExecuteFunctions {
  return { getInputData: () => items, getNodeParameter: () => operation } as unknown as IExecuteFunctions;
}

test('real parser and rules execute nodes load compiled rules and classify canonical statement rows', async () => {
  const [parsed] = await new FinanceStatement().execute.call(context([{ json: {
    extracted_text: text, source_file: 'fixture.pdf', card_code: 'EI_AMAZON', account_id: 'fixture-account',
    run_id: 'fixture-run', balance_tied: false,
  } }]));
  assert.equal(parsed[0].json.balance_tied, true);
  assert.equal(parsed[0].json.run_id, 'fixture-run');
  assert.equal(parsed[0].json.extracted_text, undefined);
  const [normalized] = await new FinanceRules().execute.call(context(parsed, 'normalize'));
  const [classified] = await new FinanceRules().execute.call(context(normalized, 'applyNonRepresentableRules'));
  const rows = classified[0].json.transactions as Array<Record<string, unknown>>;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].merchant_raw, 'AMAZON.AE DUBAI ARE');
  assert.equal(rows[0].vendor, 'Amazon');
  assert.equal(rows[0].card, 'EI_AMAZON');
  assert.equal(rows[0].account, 'fixture-account');
  assert.equal(rows[0].reward_bucket, 'EI_AMAZON');
  assert.equal(rows[0].amount_aed, '93.42');
  assert.equal(rows[0].source_direction, 'DEBIT');
  assert.equal(classified[0].json.rules_source_sha256, PACKAGED_RULE_SOURCE_SHA256);
  assert.ok(loadPackagedLedgerRules().length > 100);
  const workflow = JSON.parse(readFileSync(resolve(process.cwd(), '../../integrations/n8n/workflows/03-shared-statement-pipeline.json'), 'utf8'));
  const code = workflow.nodes.find((node: { name: string }) => node.name === 'Validate Statement Reconciliation and IDs').parameters.jsCode;
  const validate = new Function('$json', code);
  assert.equal(validate(classified[0].json)[0].json.balance_tied, true);
  assert.throws(() => validate({ ...classified[0].json, balance_tied: false, reconciliation: { balanced: true } }), /STATEMENT_RECONCILIATION_FAILED/);

});

test('runtime rules cannot be disabled or replaced by ingestion payload', async () => {
  await assert.rejects(new FinanceRules().execute.call(context([{ json: {
    transactions: [{ merchant_raw: 'Amazon.ae', amount_aed: '10', source_direction: 'DEBIT' }],
    non_representable_rules: [],
  } }], 'applyNonRepresentableRules')), /UNTRUSTED_LEDGER_RULE_OVERRIDE_FORBIDDEN/);
});
