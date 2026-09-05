"""Execute real cycle nodes at delivery, polling and Dubai month boundaries."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class MonthlyDeliveryWindowTests(unittest.TestCase):
    def test_delivery_month_bounds_include_early_wio_and_exclude_prior_statement_month(self):
        script=r'''
const fs=require('node:fs'),assert=require('node:assert/strict');
const base=process.argv[1];
const bodies=Object.fromEntries(['04-ei-monthly-statement.json','05-wio-monthly-statement.json'].map(f=>[f,JSON.parse(fs.readFileSync(base+'/'+f)).nodes.find(n=>n.name==='Open Configured Cycle Window').parameters.jsCode]));
const RealDate=Date;
const run=(f,iso)=>new Function('Date',bodies[f])(class extends RealDate{constructor(...a){super(...(a.length?a:[iso]));}});
const ei='04-ei-monthly-statement.json',wio='05-wio-monthly-statement.json';
for(const f of [ei,wio]){
 const r=run(f,'2026-09-05T16:40:00Z')[0].json;
 assert.equal(r.window_start,'2026-08-31T20:00:00.000Z');assert.equal(r.period_key,'2026-09');
 for(const received of ['2026-09-01T13:05:13Z','2026-09-02T19:38:09Z']) assert.ok(new RealDate(received)>=new RealDate(r.window_start));
 assert.ok(new RealDate('2026-08-01T12:00:00Z')<new RealDate(r.window_start));
 assert.equal(r.run_upper_bound,'2026-09-05T16:40:00.000Z');
}
assert.equal(run(ei,'2026-08-31T19:59:59Z').length,0);
assert.equal(run(ei,'2026-08-31T20:00:00Z')[0].json.period_key,'2026-09');
assert.equal(run(wio,'2026-09-02T19:59:59Z').length,0);
assert.equal(run(wio,'2026-09-02T20:00:00Z')[0].json.window_start,'2026-08-31T20:00:00.000Z');
assert.equal(run(ei,'2026-09-06T19:59:59Z')[0].json.deadline_at,'2026-09-06T19:59:59.000Z');
assert.equal(run(ei,'2026-09-06T20:00:00Z').length,0);
assert.equal(run(wio,'2026-09-08T19:59:59Z')[0].json.deadline_at,'2026-09-08T19:59:59.000Z');
assert.equal(run(wio,'2026-09-08T20:00:00Z').length,0);
const january=run(ei,'2026-12-31T20:01:00Z')[0].json;assert.equal(january.period_key,'2027-01');assert.equal(january.window_start,'2026-12-31T20:00:00.000Z');
const feb=run(wio,'2028-02-03T16:40:00Z')[0].json;assert.equal(feb.window_start,'2028-01-31T20:00:00.000Z');
'''
        result=subprocess.run(['node','-e',script,str(ROOT/'integrations/n8n/workflows')],text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
