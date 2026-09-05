'use strict';
// Runs inside the isolated no-network container. Only its loopback is used.
const assert = require('node:assert/strict');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const base = 'http://127.0.0.1:5678';
const forbidden = ['DontLeak', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890', '4111111111111111'];
async function post(route, authenticate = true) {
  let response;
  for (let attempt=0;attempt<120;attempt++) {
  response = await fetch(`${base}/webhook/${route}`, {method: 'POST', headers: {'content-type': 'application/json', ...(authenticate ? {'x-disposable-token': 'disposable-error-test-no-production-authority'} : {})}, body: '{}', signal: AbortSignal.timeout(30000)});
  if(response.status!==404) break;
  await pause(500);
  }
  const raw = await response.text();
  let body; try {body = JSON.parse(raw);} catch {body = {};}
  return {status: response.status, body: Array.isArray(body) ? body[0] : body};
}
function verify(result) {
  assert.equal(result.status, 200);
  const row = result.body;
  assert.equal(row.readback_verified, true);
  assert.equal(row.terminal_receipt_sink, 'finance_execution_failures');
  assert.ok(row.execution_id && row.workflow_id);
  for (const secret of forbidden) assert.ok(!JSON.stringify(row).includes(secret));
  return row;
}
(async () => {
  let ready = false;
  for (let i=0;i<180;i++) {
    try { const r=await fetch(`${base}/healthz/readiness`, {signal: AbortSignal.timeout(2000)}); if(r.ok && (await r.json()).status==='ok'){ready=true;break;} } catch {}
    await pause(500);
  }
  assert.ok(ready, 'n8n readiness timeout');
  if (process.argv[2] === 'before') {
    const denied = await post('disposable-error-write', false);
    assert.ok([401,403].includes(denied.status), 'fixture webhook must require its test credential');
    const first = verify(await post('disposable-error-write'));
    const second = verify(await post('disposable-error-write'));
    for(const key of ['execution_id','workflow_id','error_class','error_message_redacted']) assert.equal(first[key],second[key]);
    const triggered = await post('disposable-error-throw');
    assert.ok(triggered.status >= 400, 'real error route must fail');
    let receipt;
    for(let i=0;i<30;i++) { const result = await post('disposable-real-error-read'); if(result.status===200){receipt=verify(result);break;} await pause(500); }
    assert.ok(receipt, 'real Error Trigger did not persist its redacted receipt');
  } else if (process.argv[2] !== 'after') throw Error('phase must be before or after');
  const synthetic = verify(await post('disposable-error-read'));
  const real = verify(await post('disposable-real-error-read'));
  assert.equal(synthetic.execution_id, 'fixture-error-redaction');
  assert.equal(real.workflow_id, '90000000-0000-4000-8000-000000001017');
  console.log(`W16 ${process.argv[2]} restart: redaction, idempotency, protected webhook, real Error Trigger and durable readback verified`);
})().catch(error => { console.error(error.message);process.exitCode=1; });
