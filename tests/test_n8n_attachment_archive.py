"""Execute exported archive contracts with multi-attachment and replay inputs."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "integrations/n8n/workflows/01-outlook-finance-acquisition.json"


class AttachmentArchiveTests(unittest.TestCase):
    def test_attachment_loop_preserves_all_receipts_and_content_identity(self):
        script = r"""
const assert = require('node:assert/strict');
const w = JSON.parse(require('node:fs').readFileSync(process.argv[1]));
const nodes = Object.fromEntries(w.nodes.map(n => [n.name, n]));
const targets = (name, port = 0) => w.connections[name].main[port].map(e => e.node);
assert.equal(nodes['Archive One Attachment at a Time'].type, 'n8n-nodes-base.splitInBatches');
assert.equal(nodes['Archive One Attachment at a Time'].typeVersion, 3);
assert.equal(nodes['Archive One Attachment at a Time'].parameters.batchSize, 1);
assert.deepEqual(targets('Expand Enumerated Attachment Items'), ['Archive One Attachment at a Time']);
assert.deepEqual(targets('Archive One Attachment at a Time', 1), ['Enumerated Attachment Present']);
assert.deepEqual(targets('Archive One Attachment at a Time', 0), ['Merge Archive Verification Inputs']);
for (const terminal of ['Return Verified Attachment to Loop', 'Verify Existing Enumerated Archive Receipt', 'Empty Enumerated Attachment Verification'])
  assert.deepEqual(targets(terminal), ['Archive One Attachment at a Time']);
assert.deepEqual(targets('Record Enumerated Attachment Disposition'), ['Return Verified Attachment to Loop']);
const mergeInputs = Object.entries(w.connections).flatMap(([from, ports]) => ports.main.flatMap(branch => branch.filter(e => e.node === 'Merge Archive Verification Inputs').map(e => [from, e.index])));
assert.deepEqual(mergeInputs.sort(), [['Archive One Attachment at a Time', 0], ['Record Email PDF Render Requirement', 1]].sort());
const run = (name, rows, refs = {}) => {
  const items = rows.map(json => ({json}));
  const lookup = key => {
    if (!(key in refs)) throw new Error('unexecuted:' + key);
    const records = refs[key].map(json => ({json}));
    return {item: records[0], first: () => records[0], all: () => records};
  };
  return new Function('$json', '$input', '$', nodes[name].parameters.jsCode)(rows[0], {all: () => items}, lookup).map(item => item.json);
};
const hash = 'a'.repeat(64);
const message = {id:'message-1', message_id:'message-1', source_code:'EI_AMAZON', onedrive_parent_id:'folder', attachment_inventory:[{id:'pdf-1',name:'statement.pdf'},{id:'pdf-2',name:'copy.pdf'}]};
const request = {run_id:'run-1', source_code:'EI_AMAZON', window_start:'2026-08-01T00:00:00Z', run_upper_bound:'2026-09-01T00:00:00Z', messages:[{message_id:message.id,message,attachment_inventory:message.attachment_inventory}]};
const expanded = run('Expand Enumerated Attachment Items',[message]);
assert.equal(expanded.length,2);
const ids = [];
const completed = expanded.map((attachment, index) => {
  // First item reuses a durable receipt, second follows first-write/readback.
  const expected = {...attachment,document_sha256:hash,onedrive_item_id:'drive-'+index,attachment_verified:true,archive_state:'HASH_VERIFIED'};
  const receipt = {...expected,source_sha256:hash};
  let verified;
  if (index === 0) verified = run('Verify Existing Enumerated Archive Receipt',[{...attachment,existing_archive_receipt:receipt}])[0];
  else {
    verified = run('Verify Enumerated Archive Receipt',[receipt],{'Verify Enumerated Attachment Archive':[expected]})[0];
    // Native disposition writes replace input JSON. Restore the receipt explicitly.
    verified = run('Return Verified Attachment to Loop',[{document_id:'operation-only'}],{'Verify Enumerated Archive Receipt':[verified]})[0];
  }
  const expression = nodes['Upsert Enumerated Archive Receipt'].parameters.columns.value.archive_receipt_id.slice(3,-2).trim();
  ids.push(new Function('$json','$','return '+expression)(expected, () => ({first:() => ({json:request})})));
  return verified;
});
assert.notEqual(ids[0],ids[1], 'identical bytes from two attachment IDs require separate receipts');
const email = {source_message_id:message.id,email_evidence_identity:message.id+':INLINE_BODY',email_evidence_receipt_verified:true,email_evidence_sha256:'b'.repeat(64),onedrive_item_id:'drive-email'};
const refs = {'Validate Bounded Source Request':[request],'Verify Durable Email Evidence Receipt':[email]};
const barrier = run('Attachment Verification Barrier',[...completed,{document_id:'email-operation'}],refs)[0];
assert.equal(barrier.attachments_verified,2);
assert.deepEqual(barrier.attachment_identity_keys,['message-1:pdf-1','message-1:pdf-2']);
assert.deepEqual(barrier.archive_item_ids,['drive-0','drive-1','drive-email']);
assert.throws(() => run('Attachment Verification Barrier',[completed[0]],refs),/ATTACHMENT_ARCHIVE_COUNT_MISMATCH/);
assert.throws(() => run('Return Verified Attachment to Loop',[{}],{'Verify Enumerated Archive Receipt':[{...completed[1],attachment_verified:false}]}),/ATTACHMENT_VERIFIED_RESULT_REQUIRED/);
const expr = nodes['Upsert Enumerated Archive Receipt'].parameters.columns.value.archive_receipt_id.slice(3,-2).trim();
const otherMessageId = new Function('$json','$','return '+expr)({...expanded[0],source_message_id:'message-2',document_sha256:hash}, () => ({first:() => ({json:request})}));
assert.notEqual(ids[0],otherMessageId,'same bytes on another message must not collide');
const empty = run('Expand Enumerated Attachment Items',[{...message,attachment_inventory:[]}]);
assert.equal(empty.length,1);
assert.equal(empty[0].attachment_empty,true);
console.log('two attachments, mixed replay, identical bytes, missing proof, and no attachments passed');
"""
        node = shutil.which("node")
        self.assertIsNotNone(node)
        result = subprocess.run([node, "-e", script, str(WORKFLOW)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
