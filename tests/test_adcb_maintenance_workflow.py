"""Execute the exported maintenance gates with synthetic identities only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / 'integrations/n8n/setup-workflows/25-reviewed-adcb-maintenance.json'

class MaintenanceWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('N8N_NODE_MODULES'), 'installed pinned n8n nodes require N8N_NODE_MODULES')
    def test_native_receipt_nodes(self):
        result=subprocess.run(['node',str(ROOT/'tests/maintenance-native-nodes.cjs'),os.environ['N8N_NODE_MODULES'],str(FILE)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_export_is_deterministic_inactive_and_uses_global_fence(self):
        spec=importlib.util.spec_from_file_location('maintenance_generator',ROOT/'integrations/n8n/generate_adcb_maintenance_workflow.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        w=json.loads(FILE.read_text());self.assertEqual(w,module.build())
        self.assertIs(w['active'],False);self.assertTrue(w['meta']['publicationRequiresReview']);self.assertTrue(w['meta']['scheduleForbidden']);self.assertNotIn('activationForbidden',w['meta'])
        self.assertTrue(w['meta']['cursorMutationForbidden'])
        manifest=json.loads((FILE.parent/'manifest.json').read_text())
        entry=next(x for x in manifest['workflows'] if x['code']=='ADCB_REVIEWED_MAINTENANCE')
        self.assertTrue(entry['publication_requires_review']);self.assertTrue(entry['schedule_forbidden'])
        self.assertNotIn('activation_forbidden',entry)
        self.assertTrue(all(x.get('activation_forbidden') is True for x in manifest['workflows'] if x['code']!='ADCB_REVIEWED_MAINTENANCE'))
        self.assertFalse(any(n['type'].endswith(('.manualTrigger','.scheduleTrigger','.webhook','.dataTable')) for n in w['nodes']))
        leases=[n for n in w['nodes'] if n['type'].endswith('.executeWorkflow')]
        self.assertEqual(len(leases),4)
        self.assertTrue(all(n['parameters']['workflowId']['value']=='10000000-0000-4000-8000-000000000018' for n in leases))
        mutation=[n for n in w['nodes'] if n['type']=='n8n-nodes-finance.actualBudget']
        self.assertEqual(len(mutation),1)
        self.assertEqual(mutation[0]['parameters'],{'operation':'maintenanceApply','approvedPlanSha256':'BIND_APPROVED_PLAN_SHA256'})
        extract=next(n for n in w['nodes'] if n['name']=='Extract Immutable Maintenance Plan')
        self.assertEqual(extract['parameters']['destinationKey'],'maintenance_plan')
        convert=next(n for n in w['nodes'] if n['name']=='Convert Redacted Maintenance Receipt')
        self.assertEqual(convert['parameters']['mode'],'each')
        self.assertNotIn('sourceProperty',convert['parameters'])
        upload=next(n for n in w['nodes'] if n['name']=='Archive Redacted Maintenance Receipt')
        self.assertEqual(upload['typeVersion'],1.1)  # v1 silently prefers binary filename
        self.assertIn('$execution.id',upload['parameters']['fileName'])
        names=[n['name'] for n in w['nodes']]
        for a,b in zip(names,names[1:]): self.assertEqual(w['connections'][a]['main'],[[{'node':b,'type':'main','index':0}]])
        self.assertLess(names.index('Verify Approved Plan Bytes and Identity'),names.index('Apply Bounded Reviewed Maintenance'))
        self.assertLess(names.index('Verify Durable Maintenance Receipt'),names.index('Release Exact Maintenance Fence'))

    def test_real_code_rejects_wrong_plan_stale_fence_false_completion_and_archive_failure(self):
        script=r'''
const assert=require('node:assert/strict'),fs=require('node:fs');
const w=JSON.parse(fs.readFileSync(process.argv[1]));
const run=(name,input,refs={})=>new Function('$json','$','$execution',w.nodes.find(n=>n.name===name).parameters.jsCode)(input,k=>{if(!(k in refs))throw Error('unexecuted '+k);return {first:()=>({json:refs[k]})}}, {id:'test-25'})[0].json;
const h='a'.repeat(64),filehash='b'.repeat(64),backup='c'.repeat(64);
const b={approved_plan_sha256:h,plan_file_sha256:filehash,backup_receipt_sha256:backup,plan_item_id:'plan',receipt_parent_id:'receipts',actual_file_id:'sync-1',account_id:'account-1',expected_after_count:4,expected_after_balance_minor:123};
assert.deepEqual(run('Validate Reviewed Bindings',b),b);
assert.throws(()=>run('Validate Reviewed Bindings',{...b,plan_item_id:'BIND_PLAN_ITEM_ID'}),/RESOURCE_BINDING/);
const p={schema_version:'actual-adcb-reconstruction-v1',plan_sha256:h,actual_file_id:b.actual_file_id,account_id:b.account_id,after:{count:4,balance:123},backup:{receipt_sha256:backup}};
const refs={'Validate Reviewed Bindings':b,'Hash Immutable Maintenance Plan':{plan_file_sha256:filehash}};
assert.equal(run('Verify Approved Plan Bytes and Identity',{maintenance_plan:p},refs).maintenance_plan,p);
assert.throws(()=>run('Verify Approved Plan Bytes and Identity',{maintenance_plan:{...p,account_id:'wrong'}},refs),/BINDING_MISMATCH/);
assert.throws(()=>run('Verify Approved Plan Bytes and Identity',{maintenance_plan:p},{...refs,'Hash Immutable Maintenance Plan':{plan_file_sha256:backup}}),/FILE_HASH/);
const l={resource_key:'actual:sync-1',lease_id:'12345678-1234-1234-1234-123456789abc',lease_owner:'n8n:maintenance:test-25',fencing_token:7,expires_at:new Date(Date.now()+600000).toISOString()};
const lr={...refs,'Acquire Global Actual Writer Fence':l};
const acquire=run('Build Global Maintenance Lease Request',{},refs);assert.equal(acquire.resource_key,l.resource_key);assert.equal(acquire.ttl_seconds,600);
assert.equal(run('Build Maintenance Fence Assert',{},lr).fencing_token,7);
assert.throws(()=>run('Build Maintenance Fence Assert',{}, {...lr,'Acquire Global Actual Writer Fence':{...l,expires_at:new Date().toISOString()}}),/FENCE_INVALID/);
assert.throws(()=>run('Build Maintenance Fence Assert',{}, {...lr,'Acquire Global Actual Writer Fence':{...l,expires_at:'invalid'}}),/FENCE_INVALID/);
const fence={valid:true,resource_key:l.resource_key,lease_id:l.lease_id,fencing_token:7};
assert.throws(()=>run('Build Asserted Maintenance Envelope',{...fence,fencing_token:8},lr),/ASSERTION_FAILED/);
const r={status:'ACTUAL_MAINTENANCE_PARTIAL',state:'PARTIAL',plan_sha256:h,account_id:b.account_id,actual_file_id:b.actual_file_id,backup_receipt_sha256:backup,before_rows_sha256:h,after_rows_sha256:filehash,count:3,balance_minor:100,remaining_actions:1,target_count:4,target_balance_minor:123,updated:1,deleted:0,added:0,replay:false,writer_fencing_token:7,transactions:[{private:'must not leak'}]};
const rr={...lr,'Apply Bounded Reviewed Maintenance':{actual:r}};
const receipt=run('Build Redacted Maintenance Receipt',fence,rr).receipt;
assert.equal(receipt.state,'PARTIAL');assert.equal(receipt.transactions,undefined);
for(const patch of [{remaining_actions:0},{state:'VERIFIED',status:'ACTUAL_MAINTENANCE_VERIFIED',remaining_actions:0},{writer_fencing_token:8},{added:101},{backup_receipt_sha256:filehash}]) assert.throws(()=>run('Build Redacted Maintenance Receipt',fence,{...rr,'Apply Bounded Reviewed Maintenance':{actual:{...r,...patch}}}));
const final={...r,status:'ACTUAL_MAINTENANCE_VERIFIED',state:'VERIFIED',remaining_actions:0,count:4,balance_minor:123,updated:0,replay:true};
assert.equal(run('Build Redacted Maintenance Receipt',fence,{...rr,'Apply Bounded Reviewed Maintenance':{actual:final}}).receipt.replay,true);
const ar={'Hash Redacted Maintenance Receipt':{receipt_sha256:h},'Archive Redacted Maintenance Receipt':{id:'receipt'}};
assert.throws(()=>run('Verify Durable Maintenance Receipt',{receipt_readback_sha256:filehash},ar),/ARCHIVE_READBACK/);
const artifact=run('Verify Durable Maintenance Receipt',{receipt_readback_sha256:h},ar);
assert.equal(run('Build Exact Maintenance Lease Release',artifact,lr).operation,'RELEASE');
assert.throws(()=>run('Build Exact Maintenance Lease Release',{receipt_archive_verified:false},lr),/RECEIPT_REQUIRED/);
const retrefs={...lr,'Release Exact Maintenance Fence':{released:true},'Verify Durable Maintenance Receipt':artifact,'Build Redacted Maintenance Receipt':{receipt}};
assert.equal(run('Return Verified Maintenance Chunk Receipt',{...l,released:true},retrefs).complete,false);
assert.throws(()=>run('Return Verified Maintenance Chunk Receipt',{...l,released:false},retrefs),/RELEASE_READBACK/);
'''
        result=subprocess.run(['node','-e',script,str(FILE)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__': unittest.main()
