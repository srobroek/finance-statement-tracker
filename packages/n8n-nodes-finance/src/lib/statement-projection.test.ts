import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { tmpdir } from 'node:os';
import type { IExecuteFunctions, INodeExecutionData } from 'n8n-workflow';
import { FinanceStatement } from '../nodes/FinanceStatement/FinanceStatement.node';
import { FinanceRules } from '../nodes/FinanceRules/FinanceRules.node';
import { ActualSession, type ActualApi } from './actual-session';
import { projectStatementToActual, type ActualClassificationReadback, type NormalizedStatement } from './statements';

const text = `Statement of Card Account
From: 1st Jul 2026
31st Jul 2026
To:
OPENING BALANCE 0.00
PRIMARY CARD NO:5424XXXXXXXX0082
10 JUL 09 JUL AMAZON.AE DUBAI ARE 93.42
Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges (AED) Current Balance (AED)
50,000.00 49,906.58 93.42 25/08/26 93.42 0.00 93.42`;
const workflow = JSON.parse(readFileSync(resolve(process.cwd(), '../../integrations/n8n/workflows/03-shared-statement-pipeline.json'), 'utf8'));
const node = (name: string) => workflow.nodes.find((entry: {name: string}) => entry.name === name);
const context = (items: INodeExecutionData[], operation: string) => ({ getInputData: () => items, getNodeParameter: () => operation }) as unknown as IExecuteFunctions;
const credentials = {serverUrl:'http://actual:5006',password:'fixture',syncId:'budget',mutationEnabled:true};

test('monthly parser/rules project canonical classification through trusted Actual IDs and verified import', async () => {
  const [parsed] = await new FinanceStatement().execute.call(context([{json:{extracted_text:text, card_code:'EI_AMAZON',actual_file_id:'budget',account_id:'account'}}], 'parse'));
  const [normalized] = await new FinanceRules().execute.call(context(parsed, 'normalize'));
  const [classified] = await new FinanceRules().execute.call(context(normalized, 'applyNonRepresentableRules'));
  const statement = classified[0].json as unknown as NormalizedStatement;
  const source = statement.transactions[0];
  assert.equal(source.vendor, 'Amazon');
  // Preserve an existing classification just as the manual-field rules require.
  source.category = 'Online Shopping';
  source.tags = [...(source.tags || []), 'shared', 'online'];
  const stored: Array<Record<string, unknown>> = [];
  const api = {
    async init(){},async downloadBudget(){},async sync(){},async shutdown(){},async getServerVersion(){return {};},
    async getAccounts(){return [{id:'account',name:'Card',closed:false}];},
    async getCategories(){return [{id:'category-uuid',name:'Online Shopping'}];},
    async getPayees(){return [{id:'payee-uuid',name:'Amazon'}];},
    async getAccountBalance(){return stored.reduce((sum,row)=>sum+Number(row.amount),0);},
    async getTransactions(){return stored;},
    async importTransactions(_account: string,rows: Array<Record<string, unknown>>){
      stored.push(...rows.map((row,index)=>({...row,id:'tx-'+index})));
      return {errors:[],added:stored.map(row=>row.id),updated:[]};
    },
  } as ActualApi;
  const session = new ActualSession(api, resolve(tmpdir(),'monthly-projection-test'));
  const actual = await session.read(credentials,{shape:'categories'});
  classified[0].json.actual = actual;
  const [projected] = await new FinanceRules().execute.call(context(classified,'projectActualImport'));
  const rows = projected[0].json.actual_transactions as ReturnType<typeof projectStatementToActual>;
  assert.equal(rows[0].category,'category-uuid');assert.equal(rows[0].payee,'payee-uuid');
  assert.equal(rows[0].imported_payee,'AMAZON.AE DUBAI ARE');assert.equal(rows[0].amount,-9342);
  assert.ok(rows[0].notes?.includes('#shared'));assert.ok(rows[0].notes?.includes('#online'));
  assert.throws(()=>projectStatementToActual(statement,{...actual,actual_file_id:'wrong'} as never,'budget'),/READBACK_BINDING/);
  assert.throws(()=>projectStatementToActual(statement,{...actual,rows:[{id:'a',name:'Online Shopping'},{id:'b',name:'Online Shopping'}]} as never,'budget'),/NAME_NOT_UNIQUE/);
  const sourceContext = {source_code:'EI_AMAZON',actual_file_id:'budget',account_id:'account',card_code:'EI_AMAZON',document_sha256:'a'.repeat(64),config_version:'fixture'};
  const build = new Function('$json','$',node('Build Canonical Delta Artifact').parameters.jsCode);
  const manifest = build(projected[0].json,()=>({first:()=>({json:sourceContext})}))[0].json.manifest;
  assert.equal(manifest.expected_statement_balance_minor,-9342);
  assert.equal(manifest.transactions[0].category,'category-uuid');
  assert.throws(()=>build({...projected[0].json,closing_balance_aed:null},()=>({first:()=>({json:sourceContext})})),/CLOSING_BALANCE_REQUIRED/);
  const outbox = {schema_version:1,outbox_id:'fixture',state:'PREPARED',account_id:'account',transactions:rows,
    execution_context:{trigger:'SCHEDULE',manual:false,mcp:false},writer_lease:{lease_id:'lease',fencing_token:1,expires_at:new Date(Date.now()+60000).toISOString()}};
  await session.preflight(credentials,outbox);await session.import(credentials,outbox);
  const verification = {account_id:'account',expected_transactions:rows,start_date:'2026-07-01',end_date:'2026-07-31',expected_account_balance:manifest.expected_statement_balance_minor};
  assert.equal((await session.verify(credentials,verification)).status,'VERIFIED');
  stored[0].payee='wrong-payee';await assert.rejects(session.verify(credentials,verification),/field mismatch/);
  // Replays preserve user-owned payee/category/notes instead of overwriting them.
  await session.import(credentials,outbox);assert.equal(stored.length,1);assert.equal(stored[0].payee,'wrong-payee');
  assert.equal((await session.verify(credentials,{...verification,preserve_manual_fields_for_ids:[rows[0].imported_id]})).status,'VERIFIED');
});

test('Wio bypasses Cashback entirely and validates its common durable reconciliation receipt',()=>{
  const branches=workflow.connections['Statement Cashback Required'].main;
  assert.equal(branches[1][0].node,'Upsert Reconciliation Receipt');
  assert.equal(branches[0][0].node,'Build Cashback Reconciliation Request');
  const source={source_code:'WIO_CREDIT',period_key:'2026-07',cashback_close_required:false,document_sha256:'a'.repeat(64)};
  const actual={observed_payload_sha256:'b'.repeat(64)};
  const row={...source,reconciliation_version:1,state:'COMMITTED',statement_sha256:source.document_sha256,actual_verification_sha256:actual.observed_payload_sha256,cashback_close_id:''};
  const lookup=(name:string)=>{if(name==='Verify Archive and Execution Context')return {first:()=>({json:source})};if(name==='Apply Prepared Outbox Safely')return {first:()=>({json:actual})};throw new Error('unexecuted:'+name);};
  const validate=new Function('$json','$',node('Validate Reconciliation Readback').parameters.jsCode);
  assert.equal(validate(row,lookup)[0].json.reconciliation_readback_verified,true);
  assert.throws(()=>validate({...row,cashback_close_id:'unexpected'},lookup),/UNEXPECTED_CLOSE/);
  for(const entry of workflow.nodes.filter((entry:{type:string,parameters:{operation:string}})=>entry.type==='n8n-nodes-base.dataTable'&&entry.parameters.operation==='get'))assert.equal(entry.alwaysOutputData,true);
});

test('historical verification uses the inclusive statement end and keeps import delta evidence separate',async()=>{
  let cutoffSeen: Date | undefined;
  const row={id:'old',account:'account',imported_id:'statement:old',date:'2026-07-09',amount:-9342,imported_payee:'Merchant',cleared:true};
  const api={async init(){},async downloadBudget(){},async sync(){},async shutdown(){},
    async getTransactions(){return [row];},async getAccountBalance(_id:string,cutoff?:Date){cutoffSeen=cutoff;return cutoff?-9342:-15000;},
  } as unknown as ActualApi;
  const session=new ActualSession(api,resolve(tmpdir(),'statement-asof-test'));
  const verification={account_id:'account',expected_transactions:[row],start_date:'2026-07-01',end_date:'2026-07-31',expected_account_balance:-9342};
  const result=await session.verify(credentials,verification);
  assert.equal(result.account_balance,-9342);assert.equal(cutoffSeen?.toISOString().slice(0,10),'2026-07-31');
  await assert.rejects(session.verify(credentials,{...verification,expected_account_balance:-15000}),/balance mismatch/);
  const writer=JSON.parse(readFileSync(resolve(process.cwd(),'../../integrations/n8n/workflows/20-actual-outbox-apply.json'),'utf8'));
  const build=writer.nodes.find((n:{name:string})=>n.name==='Build Recovery Verification Contract').parameters.jsCode;
  const root={manifest:{expected_statement_balance_minor:-9342},verification:{...verification,card_code:'EI_AMAZON'}};
  const output=new Function('$',build)((name:string)=>({first:()=>({json:name==='Verify Recovery Contract'?root:name==='Read Back ACTUAL OBSERVED Recovery'?{expected_account_balance:-15000,observed_account_balance:-15000}:{actual:{}}})}))[0].json;
  assert.equal(output.verification.expected_account_balance,-9342);
  assert.deepEqual(output.balance_evidence,{expected:-15000,observed:-15000});
  const values=writer.nodes.find((n:{name:string})=>n.name==='Upsert Exact Actual Verification Receipt').parameters.columns.value;
  assert.equal(values.expected_payload_sha256,'={{ $json.actual.expected_sha256 }}');
  assert.equal(values.observed_account_balance,'={{ $json.actual.account_balance }}');
});

test('cross-month posting projects inside the statement while preserving original date and identity', async () => {
  const cross = text.replace('10 JUL 09 JUL', '01 JUL 30 JUN');
  const [parsed] = await new FinanceStatement().execute.call(context([{json:{extracted_text:cross}}], 'parse'));
  const statement = parsed[0].json as unknown as NormalizedStatement;
  assert.equal(statement.transactions[0].transaction_date,'2026-06-30');
  assert.equal(statement.transactions[0].post_date,'2026-07-01');
  const row = projectStatementToActual(statement)[0];
  assert.equal(row.date,'2026-07-01');
  assert.match(row.notes || '', /Memo: Transaction date 2026-06-30; posted 2026-07-01/);
  assert.equal(row.imported_id,`statement:emirates_islamic_v1:${statement.transactions[0].transaction_id}`);
  const api = {async init(){},async downloadBudget(){},async sync(){},async shutdown(){},
    async getTransactions(_account:string,start:string,end:string){return [{...row,id:"posted-row",account:"account"}].filter(r=>r.date>=start&&r.date<=end);},
    async getAccountBalance(_account:string,cutoff:Date){return row.date<=cutoff.toISOString().slice(0,10)?-9342:0;},
  } as unknown as ActualApi;
  const session = new ActualSession(api,resolve(tmpdir(),'statement-posting-date-test'));
  assert.equal((await session.verify(credentials,{account_id:'account',expected_transactions:[row],start_date:'2026-07-01',end_date:'2026-07-31',expected_account_balance:-9342})).status,'VERIFIED');
  assert.throws(()=>projectStatementToActual({...statement,transactions:[{...statement.transactions[0],post_date:'2026-08-01'}]}),/outside the statement period/);
  assert.throws(()=>projectStatementToActual({...statement,transactions:[{...statement.transactions[0],post_date:'2026-06-29'}]}),/precedes transaction date/);
});

test('Wio preserves prior-period printed dates without inventing posting or opening entries', async () => {
  const source=`CREDIT STATEMENT
FROM 01/08/2026 TO 01/09/2026
Wio Bank PAYMENT DUE DATE MIN. PAYMENT DUE TOTAL TO PAY
01/09/2026 0.00 0.00
ACCOUNT NUMBER 1234565009
Balance From Last Statement -2.00
Closing balance (Total to pay) 0.00
31/07/2026 P100000001 Example Merchant ****4113 -2.00
12/08/2026 P100000002 Example Merchant ****4113 -6.00
01/09/2026 P100000003 Credit Repayment +6.00`;
  const [parsed] = await new FinanceStatement().execute.call(context([{json:{extracted_text:source}}], 'parse'));
  const statement = parsed[0].json as unknown as NormalizedStatement;
  assert.equal(statement.balance_tied,true);
  assert.equal(statement.period_start,'2026-08-01');assert.equal(statement.period_end,'2026-09-01');
  const rows=projectStatementToActual(statement);
  assert.equal(rows.length,3);assert.equal(rows[0].date,'2026-07-31');
  assert.equal(statement.transactions[0].post_date,null);assert.match(rows[0].notes || '', /posting date not provided/);
  assert.equal(rows.reduce((sum,row)=>sum+row.amount,0),-200);
  const stored=rows.map((row,index)=>({...row,id:'wio-'+index,account:'account'}));
  let readStart='';let cutoff='';
  const api={async init(){},async downloadBudget(){},async sync(){},async shutdown(){},
    async getTransactions(_account:string,start:string,end:string){readStart=start;return stored.filter(row=>row.date>=start&&row.date<=end);},
    async getAccountBalance(_account:string,date?:Date){cutoff=date?.toISOString().slice(0,10)||'';return 200+stored.reduce((sum,row)=>sum+row.amount,0);},
  } as unknown as ActualApi;
  const session=new ActualSession(api,resolve(tmpdir(),'wio-prior-date-test'));
  const result=await session.verify(credentials,{account_id:'account',expected_transactions:rows,start_date:statement.period_start,end_date:statement.period_end,expected_account_balance:0});
  assert.equal(result.status,'VERIFIED');assert.equal(readStart,'2026-07-31');assert.equal(cutoff,'2026-09-01');
  assert.equal(stored.length,3); // The existing opening credit is never manufactured as an import.
  assert.throws(()=>projectStatementToActual({...statement,balance_tied:false}),/outside the statement period/);
  assert.throws(()=>projectStatementToActual({...statement,transactions:[{...statement.transactions[0],post_date:'2026-07-31'}]}),/outside the statement period/);
});


async function classificationFixture(): Promise<NormalizedStatement> {
  const [parsed] = await new FinanceStatement().execute.call(context([{json:{extracted_text:text, card_code:'EI_AMAZON',actual_file_id:'budget',account_id:'account'}}], 'parse'));
  const [normalized] = await new FinanceRules().execute.call(context(parsed, 'normalize'));
  const [classified] = await new FinanceRules().execute.call(context(normalized, 'applyNonRepresentableRules'));
  const statement = classified[0].json as unknown as NormalizedStatement;
  statement.transactions[0].category = 'Online Shopping';
  statement.transactions[0].subcategory = 'Online Shopping';
  return statement;
}
const classificationReadback = (): ActualClassificationReadback => ({shape:'categories',actual_file_id:'budget',
  rows:[{id:'existing-category',name:' online SHOPPING '}],payees:[{id:'existing-payee',name:' AMAZON '}]});

test('classification identity follows bootstrap normalization and preserves existing IDs and names', async () => {
  const statement = await classificationFixture(), readback = classificationReadback();
  const before = structuredClone(readback);
  const [row] = projectStatementToActual(statement, readback, 'budget');
  assert.equal(row.payee, 'existing-payee');assert.equal(row.category, 'existing-category');
  assert.deepEqual(readback, before);
  assert.throws(() => projectStatementToActual(statement, {...readback,actual_file_id:'other-budget'}, 'budget'), /READBACK_BINDING_REQUIRED/);
});

test('normalized classification ambiguities fail even when one resource name matches exactly', async () => {
  const statement = await classificationFixture();
  for (const kind of ['payees','rows'] as const) {
    const readback = classificationReadback();
    readback[kind].push({id:'duplicate-resource',name:kind === 'payees' ? 'Amazon' : 'Online Shopping'});
    assert.throws(() => projectStatementToActual(statement, readback, 'budget'), /CLASSIFICATION_NAME_NOT_UNIQUE/);
  }
});

test('tombstoned classification resources cannot satisfy or make an active identity ambiguous', async () => {
  const statement = await classificationFixture();
  for (const kind of ['payees','rows'] as const) {
    const readback = classificationReadback();
    readback[kind].push({id:'deleted-resource',name:kind === 'payees' ? 'Amazon' : 'Online Shopping',tombstone:true});
    const [row] = projectStatementToActual(statement, readback, 'budget');
    assert.equal(row.payee,'existing-payee');assert.equal(row.category,'existing-category');
    readback[kind] = readback[kind].filter(resource => resource.tombstone === true);
    assert.throws(() => projectStatementToActual(statement, readback, 'budget'), /CLASSIFICATION_NAME_NOT_UNIQUE/);
  }
});
