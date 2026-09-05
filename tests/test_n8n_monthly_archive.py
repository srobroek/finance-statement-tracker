"""Execute the exported monthly acquisition boundary without financial writes."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MonthlyArchiveTests(unittest.TestCase):
    def test_verified_pdf_selection_and_cursor_receipt_boundary(self):
        script = r"""
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const root = process.argv[1];
const workflows = ['01-outlook-finance-acquisition.json','12-outlook-message-sweep.json','22-shared-monthly-statement-cycle.json'].map(f=>JSON.parse(fs.readFileSync(path.join(root,'integrations/n8n/workflows',f))));
const run=(w,name,rows,refs={},binary={data:{id:'verified-binary'}})=>{
 const code=w.nodes.find(n=>n.name===name).parameters.jsCode;
 const lookup=key=>{if(!(key in refs))throw Error('unexecuted:'+key);return {first:()=>({json:refs[key]}),all:()=>[{json:refs[key]}],item:{json:refs[key]}}};
 return new Function('$json','$input','$','$binary',code)(rows[0],{all:()=>rows.map(json=>({json}))},lookup,binary).map(i=>i.json);
};
const [w1,w12,w22]=workflows;
const h='a'.repeat(64), archiveHash='b'.repeat(64), pipelineHash='c'.repeat(64);
const base={run_id:'cycle-1',source_code:'EI_AMAZON',window_start:'2026-08-01T00:00:00Z',run_upper_bound:'2026-09-01T00:00:00Z',archive_ready:true,receipt_readback_verified:true,attachment_verification_barrier:'VERIFIED',attachment_ids_verified:true,email_evidence_receipt_barrier:'VERIFIED',pagination_exhausted:true,cursor_commit_eligible:false,downstream_receipt_sha256:archiveHash};
const proof=(message,id,extra={})=>({source_message_id:message,source_attachment_id:id,attachment_identity:message+':'+id,document_sha256:h,onedrive_item_id:'drive-'+message+'-'+id,is_pdf:true,is_inline:false,...extra});
const pdf=proof('m1','statement'), copy=proof('m2','copy'), inline=proof('m1','inline',{is_inline:true,document_sha256:'d'.repeat(64)}), text=proof('m2','text',{is_pdf:false,document_sha256:'e'.repeat(64)});
const proofs=[pdf,inline,copy,text];
const envelope={...base,attachment_archive_proof:proofs,attachment_identity_keys:proofs.map(p=>p.attachment_identity)};
const chosen=run(w22,'Select Verified Monthly Statement',[envelope])[0];
assert.equal(chosen.monthly_statement.document_sha256,h);
assert.deepEqual(chosen.monthly_statement_source_identities,['m1:statement','m2:copy']);
assert.equal(chosen.onedrive_item_id,pdf.onedrive_item_id);
assert.equal(run(w22,'Select Verified Monthly Statement',[{...envelope,attachment_archive_proof:[inline,text],attachment_identity_keys:[inline.attachment_identity,text.attachment_identity]}])[0].monthly_statement,null);
const different=proof('m3','different',{document_sha256:'f'.repeat(64)});
assert.throws(()=>run(w22,'Select Verified Monthly Statement',[{...envelope,attachment_archive_proof:[...proofs,different],attachment_identity_keys:[...envelope.attachment_identity_keys,different.attachment_identity]}]),/AMBIGUOUS_DISTINCT_PDFS/);
assert.throws(()=>run(w22,'Select Verified Monthly Statement',[{...envelope,attachment_archive_proof:proofs.slice(1)}]),/COVERAGE/);
assert.throws(()=>run(w22,'Select Verified Monthly Statement',[{...envelope,receipt_readback_verified:false}]),/READBACK/);
const trusted={...base,period_key:'2026-08',account_id:'real-account',actual_file_id:'real-budget'};
const inputRefs={'Assemble Trusted Acquisition Contract':trusted,'Select Verified Monthly Statement':chosen};
const input=run(w22,'Assemble Immutable Pipeline Input',[{monthly_download_sha256:h}],inputRefs)[0];
assert.equal(input.message_id,'m1');assert.equal(input.attachment_id,'statement');assert.equal(input.document_sha256,h);assert.equal(input.account_id,'real-account');
assert.throws(()=>run(w22,'Assemble Immutable Pipeline Input',[{monthly_download_sha256:'0'.repeat(64)}],inputRefs),/HASH_MISMATCH/);
const pipeline={run_id:base.run_id,source_code:base.source_code,state:'SUCCEEDED',terminal_readback_verified:true,receipt_sha256:pipelineHash};
const refs={'Assemble Trusted Acquisition Contract':trusted,'Acquire Archive and Read Back':envelope,'Run Shared Statement Pipeline':pipeline};
const cursor={source_code:base.source_code,cursor_version:0};
const commit=run(w22,'Build W12 COMMIT Request',[cursor],refs)[0];
assert.equal(commit.downstream_receipt_sha256,archiveHash);assert.equal(commit.pipeline_receipt_sha256,pipelineHash);
for(const bad of [{...pipeline,state:'FAILED'},{...pipeline,terminal_readback_verified:false},{...pipeline,run_id:'other-run'},{...pipeline,source_code:'WIO_CREDIT'}])
 assert.throws(()=>run(w22,'Build W12 COMMIT Request',[cursor],{...refs,'Run Shared Statement Pipeline':bad}),/TERMINAL_RECEIPT/);
const edge=(name)=>w22.connections[name].main[0].map(e=>e.node);
assert.deepEqual(edge('Acquire Archive and Read Back'),['Select Verified Monthly Statement']);
assert.deepEqual(edge('Select Verified Monthly Statement'),['Statement Found']);
assert.deepEqual(edge('Download Archived Source'),['SHA-256 Monthly Statement Readback']);
assert.deepEqual(edge('SHA-256 Monthly Statement Readback'),['Assemble Immutable Pipeline Input']);
assert.deepEqual(edge('Run Shared Statement Pipeline'),['Read Source Cursor Before Commit']);
// Replayed receipt rows contain source_sha256, while attachment typing comes
// from the real immutable metadata expansion and must survive the readback.
const expanded = run(w1,'Expand Enumerated Attachment Items',[{id:'m1',source_code:base.source_code,attachment_inventory:[{id:'statement',name:'statement.PDF',isInline:false}]}])[0];
const replayed = run(w1,'Verify Existing Enumerated Archive Receipt',[{...expanded,existing_archive_receipt:{source_message_id:'m1',source_attachment_id:'statement',source_sha256:h,archive_state:'HASH_VERIFIED',onedrive_item_id:pdf.onedrive_item_id}}])[0];
assert.equal(replayed.is_pdf,true);assert.equal(replayed.is_inline,false);assert.equal(replayed.source_sha256,h);
// Actual W01/W12 outputs must retain the typed proofs; fixture validates the
// exported aggregation code rather than assuming a singular legacy item ID.
const msg={message_id:'m1',message:{id:'m1'},attachment_inventory:[{id:'statement'}]};
const request={...base,messages:[msg]};
const email={source_message_id:'m1',email_evidence_identity:'m1:INLINE_BODY',email_evidence_receipt_verified:true,email_evidence_sha256:'e'.repeat(64),onedrive_item_id:'email'};
const result=run(w1,'Attachment Verification Barrier',[replayed],{'Validate Bounded Source Request':request,'Verify Durable Email Evidence Receipt':email})[0];
assert.deepEqual(result.attachment_archive_proof,[pdf]);
const contract={...base,messages:[msg],matched_count:1,attachment_identity_keys:['m1:statement']};
const aggregate=run(w12,'Aggregate Verified Message Archives',[result],{'Attach Immutable Inventory to Sweep':contract})[0];
assert.deepEqual(aggregate.attachment_archive_proof,[pdf]);
const archived={...aggregate,terminal_state:'ARCHIVED',readback_verified:true,downstream_receipt_sha256:archiveHash};
const returned=run(w12,'Return Verified ARCHIVED Receipt',[archived],{'Verify ARCHIVED Acquisition Receipt':archived,'Attach Immutable Inventory to Sweep':contract,'Aggregate Verified Message Archives':aggregate})[0];
assert.deepEqual(returned.attachment_archive_proof,[pdf]);
assert.equal(run(w22,'Select Verified Monthly Statement',[returned])[0].onedrive_item_id,pdf.onedrive_item_id);
"""
        result = subprocess.run(["node", "-e", script, str(ROOT)], text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_contract_reapplication_is_idempotent(self):
        import importlib.util
        from copy import deepcopy
        location = ROOT / "integrations/n8n/monthly_archive_contract.py"
        spec = importlib.util.spec_from_file_location("monthly_contract", location)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        workflows = [json.loads(p.read_text()) for p in (ROOT / "integrations/n8n/workflows").glob("*.json")]
        module.ensure_monthly_archive_contract(workflows)
        first = deepcopy(workflows)
        module.ensure_monthly_archive_contract(workflows)
        self.assertEqual(workflows, first)
