"""Render the explicitly reviewed, inactive ADCB maintenance subworkflow."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'integrations/n8n/setup-workflows/25-reviewed-adcb-maintenance.json'

VALIDATE_BINDINGS = r'''
const b = $json;
for (const key of ['approved_plan_sha256', 'plan_file_sha256', 'backup_receipt_sha256']) {
  if (!/^[a-f0-9]{64}$/.test(String(b[key] || ''))) throw new Error('MAINTENANCE_REVIEW_BINDING_REQUIRED');
}
for (const key of ['plan_item_id', 'receipt_parent_id', 'actual_file_id', 'account_id']) {
  if (!String(b[key] || '').trim() || String(b[key]).startsWith('BIND_')) throw new Error('MAINTENANCE_RESOURCE_BINDING_REQUIRED');
}
if (!/^[A-Za-z0-9_-]{1,128}$/.test(b.actual_file_id)) throw new Error('MAINTENANCE_INVALID_SYNC_BINDING');
if (!Number.isSafeInteger(b.expected_after_count) || b.expected_after_count < 0 || !Number.isSafeInteger(b.expected_after_balance_minor)) throw new Error('MAINTENANCE_EXPECTED_STATE_BINDING_REQUIRED');
return [{json:b}];
'''
VERIFY_PLAN = r'''
const b = $('Validate Reviewed Bindings').first().json;
const p = $json.maintenance_plan;
if (!p || typeof p !== 'object' || Array.isArray(p)) throw new Error('MAINTENANCE_PLAN_OBJECT_REQUIRED');
if ($('Hash Immutable Maintenance Plan').first().json.plan_file_sha256 !== b.plan_file_sha256) throw new Error('MAINTENANCE_PLAN_FILE_HASH_MISMATCH');
if (p.schema_version !== 'actual-adcb-reconstruction-v1' || p.plan_sha256 !== b.approved_plan_sha256 || p.actual_file_id !== b.actual_file_id || p.account_id !== b.account_id) throw new Error('MAINTENANCE_APPROVED_PLAN_BINDING_MISMATCH');
if (!p.after || p.after.count !== b.expected_after_count || p.after.balance !== b.expected_after_balance_minor || p.backup?.receipt_sha256 !== b.backup_receipt_sha256) throw new Error('MAINTENANCE_EXPECTED_PLAN_STATE_MISMATCH');
return [{json:{maintenance_plan:p}}];
'''
ACQUIRE = r'''
const b = $('Validate Reviewed Bindings').first().json;
return [{json:{operation:'ACQUIRE', resource_key:'actual:'+b.actual_file_id, lease_owner:'n8n:maintenance:'+String($execution.id), ttl_seconds:600}}];
'''
ASSERT = r'''
const l = $('Acquire Global Actual Writer Fence').first().json;
const b = $('Validate Reviewed Bindings').first().json;
const expiresAt = Date.parse(l.expires_at);
if (!Number.isFinite(expiresAt) || l.resource_key !== 'actual:'+b.actual_file_id || !l.lease_id || !Number.isSafeInteger(l.fencing_token) || l.fencing_token <= 0 || expiresAt <= Date.now()+60000) throw new Error('MAINTENANCE_WRITER_FENCE_INVALID');
return [{json:{operation:'ASSERT',resource_key:l.resource_key,lease_id:l.lease_id,fencing_token:l.fencing_token}}];
'''
ENVELOPE = r'''
const l = $('Acquire Global Actual Writer Fence').first().json;
if ($json.valid !== true || $json.resource_key !== l.resource_key || $json.lease_id !== l.lease_id || $json.fencing_token !== l.fencing_token) throw new Error('MAINTENANCE_FENCE_ASSERTION_FAILED');
return [{json:{maintenance_plan:$('Verify Approved Plan Bytes and Identity').first().json.maintenance_plan,writer_lease:l}}];
'''
RECEIPT = r'''
const fence = $json, l = $('Acquire Global Actual Writer Fence').first().json;
const b = $('Validate Reviewed Bindings').first().json;
const r = $('Apply Bounded Reviewed Maintenance').first().json.actual;
if (fence.valid !== true || fence.resource_key !== l.resource_key || fence.lease_id !== l.lease_id || fence.fencing_token !== l.fencing_token) throw new Error('MAINTENANCE_POST_WRITE_FENCE_FAILED');
if (!r || r.plan_sha256 !== b.approved_plan_sha256 || r.actual_file_id !== b.actual_file_id || r.account_id !== b.account_id || r.backup_receipt_sha256 !== b.backup_receipt_sha256 || r.writer_fencing_token !== l.fencing_token) throw new Error('MAINTENANCE_RESULT_BINDING_MISMATCH');
for (const k of ['count','remaining_actions','added','updated','deleted']) if (!Number.isSafeInteger(r[k]) || r[k] < 0) throw new Error('MAINTENANCE_RESULT_COUNT_INVALID');
if (r.target_count !== b.expected_after_count || r.target_balance_minor !== b.expected_after_balance_minor) throw new Error('MAINTENANCE_RESULT_TARGET_MISMATCH');
if (r.added+r.updated+r.deleted > 100 || !Number.isSafeInteger(r.balance_minor) || typeof r.replay !== 'boolean') throw new Error('MAINTENANCE_RESULT_BOUND_EXCEEDED');
for (const k of ['before_rows_sha256','after_rows_sha256']) if (!/^[a-f0-9]{64}$/.test(String(r[k] || ''))) throw new Error('MAINTENANCE_RESULT_HASH_REQUIRED');
if (r.state === 'VERIFIED') {
  if (r.status !== 'ACTUAL_MAINTENANCE_VERIFIED' || r.remaining_actions !== 0 || r.count !== b.expected_after_count || r.balance_minor !== b.expected_after_balance_minor) throw new Error('MAINTENANCE_FINAL_READBACK_MISMATCH');
} else if (r.state !== 'PARTIAL' || r.status !== 'ACTUAL_MAINTENANCE_PARTIAL' || r.remaining_actions <= 0) throw new Error('MAINTENANCE_RESULT_STATE_INVALID');
const keys = ['status','state','plan_sha256','account_id','actual_file_id','backup_receipt_sha256','before_rows_sha256','after_rows_sha256','count','balance_minor','remaining_actions','target_count','target_balance_minor','updated','deleted','added','replay','writer_fencing_token'];
const receipt = {schema_version:'actual-maintenance-receipt-v1'};
for (const k of keys) receipt[k] = r[k];
return [{json:{receipt}}];
'''
VERIFY_ARCHIVE = r'''
const before = $('Hash Redacted Maintenance Receipt').first().json.receipt_sha256;
const after = $json.receipt_readback_sha256;
const item = $('Archive Redacted Maintenance Receipt').first().json;
if (!/^[a-f0-9]{64}$/.test(String(before || '')) || before !== after || !item.id) throw new Error('MAINTENANCE_RECEIPT_ARCHIVE_READBACK_FAILED');
return [{json:{receipt_sha256:before,receipt_item_id:item.id,receipt_archive_verified:true}}];
'''
RELEASE = r'''
const l = $('Acquire Global Actual Writer Fence').first().json;
if ($json.receipt_archive_verified !== true) throw new Error('MAINTENANCE_RECEIPT_REQUIRED_BEFORE_RELEASE');
return [{json:{operation:'RELEASE',resource_key:l.resource_key,lease_id:l.lease_id,fencing_token:l.fencing_token}}];
'''
RETURN = r'''
const row = $json, lease = $('Acquire Global Actual Writer Fence').first().json;
const released = $('Release Exact Maintenance Fence').first().json;
if (released.released !== true || row.released !== true || row.resource_key !== lease.resource_key || row.lease_id !== lease.lease_id || row.lease_owner !== lease.lease_owner || Number(row.fencing_token) !== lease.fencing_token) throw new Error('MAINTENANCE_RELEASE_READBACK_FAILED');
const artifact = $('Verify Durable Maintenance Receipt').first().json;
return [{json:{...$('Build Redacted Maintenance Receipt').first().json.receipt,...artifact,writer_release_verified:true,complete:$('Build Redacted Maintenance Receipt').first().json.receipt.state==='VERIFIED'}}];
'''


def build():
    nodes = []
    def add(name, kind, parameters, version=1, credentials=None):
        i = len(nodes)
        node = {'id':str(25000+i), 'name':name, 'type':'n8n-nodes-base.'+kind if '.' not in kind else kind, 'typeVersion':version, 'position':[i*260,0], 'parameters':parameters}
        if credentials: node['credentials'] = credentials
        nodes.append(node)
    def code(name, body): add(name,'code',{'jsCode':'// Purpose: '+name+'. Keep this deterministic and fail closed.\n'+body.strip()+'\n'},2)
    def lease(name): add(name,'executeWorkflow',{'workflowId':{'__rl':True,'value':'10000000-0000-4000-8000-000000000018','mode':'list','cachedResultName':'Finance · Fenced Actual Writer Lease'},'options':{'waitForSubWorkflow':True}},1.3)
    onedrive={'microsoftOneDriveOAuth2Api':{'id':'BIND_ONEDRIVE','name':'Finance OneDrive'}}
    add('Reviewed Integrated Maintenance Request','executeWorkflowTrigger',{'inputSource':'passthrough'},1.1)
    bindings = {k:'BIND_'+k.upper() for k in ['approved_plan_sha256','plan_file_sha256','backup_receipt_sha256','plan_item_id','receipt_parent_id','actual_file_id','account_id']}
    assignments = [{'id':k,'name':k,'type':'string','value':v} for k,v in bindings.items()]
    assignments += [{'id':k,'name':k,'type':'number','value':-1} for k in ['expected_after_count','expected_after_balance_minor']]
    add('Fixed Reviewed Maintenance Bindings','set',{'assignments':{'assignments':assignments},'includeOtherFields':False,'options':{}},3.4)
    code('Validate Reviewed Bindings',VALIDATE_BINDINGS)
    add('Download Immutable Maintenance Plan','microsoftOneDrive',{'resource':'file','operation':'download','fileId':"={{ $json.plan_item_id }}",'binaryPropertyName':'data'},1,onedrive)
    add('Hash Immutable Maintenance Plan','crypto',{'action':'hash','type':'SHA256','binaryData':True,'binaryPropertyName':'data','dataPropertyName':'plan_file_sha256'})
    add('Extract Immutable Maintenance Plan','extractFromFile',{'operation':'fromJson','binaryPropertyName':'data','destinationKey':'maintenance_plan','options':{}},1)
    code('Verify Approved Plan Bytes and Identity',VERIFY_PLAN)
    code('Build Global Maintenance Lease Request',ACQUIRE)
    lease('Acquire Global Actual Writer Fence')
    code('Build Maintenance Fence Assert',ASSERT)
    lease('Assert Global Fence Before Maintenance')
    code('Build Asserted Maintenance Envelope',ENVELOPE)
    add('Apply Bounded Reviewed Maintenance','n8n-nodes-finance.actualBudget',{'operation':'maintenanceApply','approvedPlanSha256':'BIND_APPROVED_PLAN_SHA256'},1,{'actualBudgetApi':{'id':'BIND_ACTUAL','name':'Finance Actual'}})
    code('Build Post-Maintenance Fence Assert',ASSERT)
    lease('Assert Global Fence After Maintenance')
    code('Build Redacted Maintenance Receipt',RECEIPT)
    add('Convert Redacted Maintenance Receipt','convertToFile',{'operation':'toJson','mode':'each','binaryPropertyName':'data','options':{'fileName':'maintenance-receipt.json'}},1.1)
    add('Hash Redacted Maintenance Receipt','crypto',{'action':'hash','type':'SHA256','binaryData':True,'binaryPropertyName':'data','dataPropertyName':'receipt_sha256'})
    add('Archive Redacted Maintenance Receipt','microsoftOneDrive',{'resource':'file','operation':'upload','binaryData':True,'binaryPropertyName':'data','fileName':"={{ $('Validate Reviewed Bindings').first().json.approved_plan_sha256 + '-' + $execution.id + '-' + $json.receipt_sha256 + '.maintenance-receipt.json' }}",'parentId':"={{ $('Validate Reviewed Bindings').first().json.receipt_parent_id }}"},1,onedrive)
    add('Download Maintenance Receipt Readback','microsoftOneDrive',{'resource':'file','operation':'download','fileId':"={{ $json.id }}",'binaryPropertyName':'data'},1,onedrive)
    add('Hash Maintenance Receipt Readback','crypto',{'action':'hash','type':'SHA256','binaryData':True,'binaryPropertyName':'data','dataPropertyName':'receipt_readback_sha256'})
    code('Verify Durable Maintenance Receipt',VERIFY_ARCHIVE)
    code('Build Exact Maintenance Lease Release',RELEASE)
    lease('Release Exact Maintenance Fence')
    add('Read Back Released Maintenance Fence','postgres',{'operation':'executeQuery','query':'SELECT resource_key, lease_id::text AS lease_id, lease_owner, fencing_token, released_at IS NOT NULL AS released FROM finance_ops.writer_leases WHERE resource_key=$1::text AND lease_id=$2::uuid AND fencing_token=$3::bigint;','options':{'queryReplacement':"={{ [$('Acquire Global Actual Writer Fence').first().json.resource_key, $('Acquire Global Actual Writer Fence').first().json.lease_id, $('Acquire Global Actual Writer Fence').first().json.fencing_token] }}"}},2.6,{'postgres':{'id':'BIND_FINANCE_OPS_DB','name':'Finance Operations Postgres'}})
    code('Return Verified Maintenance Chunk Receipt',RETURN)
    connections={a['name']:{'main':[[{'node':b['name'],'type':'main','index':0}]]} for a,b in zip(nodes,nodes[1:])}
    return {'id':'10000000-0000-4000-8000-000000000025','name':'Finance · Reviewed ADCB Maintenance','active':False,'nodes':nodes,'connections':connections,'settings':{'executionOrder':'v1','timezone':'Asia/Dubai','saveDataErrorExecution':'none','saveDataSuccessExecution':'none','saveManualExecutions':False,'errorWorkflow':'10000000-0000-4000-8000-000000000016'},'pinData':{},'meta':{'financeWorkflowCode':'ADCB_REVIEWED_MAINTENANCE','setupOnly':True,'activationForbidden':True,'scheduleForbidden':True,'reviewedIntegratedInvocationRequired':True,'mutationScope':'APPROVED_HASH_BOUND_ADCB_RECONSTRUCTION_MAX_100_ACTIONS','cursorMutationForbidden':True,'cashbackMutationForbidden':True}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true');args=parser.parse_args()
    rendered=json.dumps(build(),indent=2,ensure_ascii=False)+'\n'
    if args.write: OUTPUT.write_text(rendered)
    elif not OUTPUT.exists() or OUTPUT.read_text()!=rendered: raise SystemExit('reviewed maintenance workflow drift')

if __name__=='__main__': main()
