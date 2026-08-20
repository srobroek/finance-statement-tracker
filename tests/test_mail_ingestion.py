import unittest
from datetime import datetime
import json
from pathlib import Path
import subprocess

from finance_tracker.mail_ingestion import (
    build_ingest_commit_payload,
    build_outlook_envelope,
    plan_outlook_scan,
)


class MailIngestionTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def workflow(self, name):
        return json.loads(
            (self.ROOT / "integrations" / "n8n" / "workflows" / name).read_text(
                encoding="utf-8"
            )
        )

    def config(self):
        return {
            "outlook": {
                "cursor_source": "outlook",
                "scan_overlap_hours": 2,
                "initial_lookback_hours": 24,
            }
        }

    def execute_code_node(self, workflow, name, json_value=None, input_items=None, refs=None):
        """Execute a Code node with the small n8n context used by these contracts."""
        node = next(node for node in workflow["nodes"] if node["name"] == name)
        runner = r'''
const fs = require('fs');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const refs = payload.refs || {};
const rows = value => Array.isArray(value) ? value : [value];
const item = json => ({json});
const $ = name => {
  if (!(name in refs)) throw new Error(`UNEXECUTED:${name}`);
  const values = rows(refs[name]);
  return {first: () => item(values[0]), all: () => values.map(item), item: item(values[0])};
};
const input = (payload.input_items || []).map(item);
const $input = {all: () => input, first: () => input[0], item: input[0]};
const $binary = payload.binary || {};
const $now = {toISO: () => '2026-08-20T00:00:00.000Z'};
try {
  const output = new Function('$json', '$input', '$', '$binary', '$now', nodeCode)(payload.json, $input, $, $binary, $now);
  process.stdout.write(JSON.stringify({ok: true, output}));
} catch (error) {
  process.stdout.write(JSON.stringify({ok: false, error: String(error.message || error)}));
}
'''.replace("nodeCode", json.dumps(node["parameters"]["jsCode"]))
        completed = subprocess.run(
            ["node", "-e", runner],
            input=json.dumps({
                "json": json_value or {},
                "input_items": input_items or [],
                "refs": refs or {},
            }),
            cwd=self.ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def assert_code_error(self, workflow, name, message, **kwargs):
        result = self.execute_code_node(workflow, name, **kwargs)
        self.assertFalse(result["ok"])
        self.assertIn(message, result["error"])
        return result

    def test_missed_daily_runs_scan_entire_cursor_gap_plus_overlap(self):
        plan = plan_outlook_scan(
            self.config(),
            {"ingest_state": {"cursor": "2026-08-14T23:50:00+04:00"}},
            datetime.fromisoformat("2026-08-17T23:50:00+04:00"),
        )
        self.assertEqual(plan.window_start, "2026-08-14T17:50:00+00:00")
        self.assertEqual(plan.window_end, "2026-08-17T19:50:00+00:00")
        self.assertFalse(plan.initial_scan)

    def test_per_bank_ingest_state_selects_an_independent_cursor_source(self):
        plan = plan_outlook_scan(
            self.config(),
            {
                "ingest_state": {
                    "source": "outlook:rakbank",
                    "cursor": "2026-08-14T23:50:00+04:00",
                }
            },
            datetime.fromisoformat("2026-08-16T23:50:00+04:00"),
        )
        self.assertEqual(plan.source, "outlook:rakbank")

    def test_initial_scan_uses_configured_lookback(self):
        plan = plan_outlook_scan(
            self.config(),
            {"ingest_state": {"cursor": None}},
            datetime.fromisoformat("2026-08-17T23:50:00+04:00"),
        )
        self.assertEqual(plan.window_start, "2026-08-16T19:50:00+00:00")
        self.assertTrue(plan.initial_scan)

    def test_exact_message_envelope_and_commit_are_two_phase(self):
        plan = plan_outlook_scan(
            self.config(),
            {"cursor": "2026-08-16T23:50:00+04:00"},
            datetime.fromisoformat("2026-08-17T23:50:00+04:00"),
        )
        message = {
            "id": "exact-outlook-message",
            "receivedDateTime": "2026-08-17T12:00:00+04:00",
            "subject": "Exact source object",
            "bodyPreview": "Preserved without projection",
        }
        envelope = build_outlook_envelope(plan, [message])
        self.assertEqual(envelope["messages"], [message])
        response = {
            "parse": {"scanned_count": 1, "accepted_count": 0},
            "cursor_candidate": envelope["cursor"],
            "cursor_committed": False,
        }
        commit = build_ingest_commit_payload(envelope, response)
        self.assertEqual(commit["scanned_count"], 1)
        self.assertEqual(commit["accepted_count"], 0)
        self.assertEqual(commit["cursor"], envelope["cursor"])

    def test_partial_service_result_cannot_create_commit_payload(self):
        plan = plan_outlook_scan(
            self.config(),
            {"cursor": "2026-08-16T23:50:00+04:00"},
            datetime.fromisoformat("2026-08-17T23:50:00+04:00"),
        )
        envelope = build_outlook_envelope(
            plan,
            [{"id": "one", "receivedDateTime": "2026-08-17T12:00:00+04:00"}],
        )
        with self.assertRaisesRegex(ValueError, "scanned_count"):
            build_ingest_commit_payload(
                envelope,
                {
                    "parse": {"scanned_count": 0, "accepted_count": 0},
                    "cursor_candidate": envelope["cursor"],
                    "cursor_committed": False,
                },
            )

    def test_w12_enumerates_once_and_hands_immutable_inventory_to_w01(self):
        workflow = self.workflow("12-outlook-message-sweep.json")
        nodes = {node["name"]: node for node in workflow["nodes"]}
        self.assertTrue(workflow["meta"]["enumerateExactlyOnce"])
        self.assertEqual(
            workflow["meta"]["immutableMessageAttachmentIdentity"],
            ["message_id", "attachment_id"],
        )
        self.assertEqual(
            workflow["meta"]["immutableCompositeIdentity"],
            ["source_message_id", "source_attachment_id"],
        )
        self.assertTrue(workflow["meta"]["enumerationReceiptPreRead"])
        self.assertIn(
            "folder_id: sweep.folder_id",
            nodes["Shape Immutable Message Inventory"]["parameters"]["jsCode"],
        )
        self.assertEqual(
            nodes["List Immutable Message Attachments"]["parameters"]["resource"],
            "messageAttachment",
        )
        self.assertIn("IMMUTABLE_MESSAGE_ID_MISSING_OR_DUPLICATE", nodes["Shape Immutable Message Inventory"]["parameters"]["jsCode"])
        self.assertIn("DUPLICATE_MESSAGE_ATTACHMENT_ID", nodes["Aggregate Immutable Archive Inventory"]["parameters"]["jsCode"])
        self.assertIn(
            "immutable_inventory_json",
            nodes["Upsert ENUMERATED Receipt"]["parameters"]["columns"]["value"],
        )
        self.assertEqual(
            workflow["connections"]["Freeze Trusted Cursor Window"]["main"][0][0]["node"],
            "Read Existing ENUMERATED Receipt",
        )
        self.assertEqual(
            workflow["connections"]["Existing ENUMERATED Receipt Present"]["main"][0][0]["node"],
            "Return Existing ENUMERATED Receipt",
        )
        self.assertEqual(
            workflow["connections"]["Verify Receipt and Return Sweep"]["main"][0][0]["node"],
            "Attach Immutable Inventory to Sweep",
        )
        self.assertEqual(
            workflow["connections"]["Attach Immutable Inventory to Sweep"]["main"][0][0]["node"],
            "Archive Enumerated Messages in W01",
        )

    def test_w01_archive_barrier_covers_zero_one_and_many_attachments(self):
        workflow = self.workflow("01-outlook-finance-acquisition.json")
        nodes = {node["name"]: node for node in workflow["nodes"]}
        connections = workflow["connections"]
        self.assertTrue(workflow["meta"]["immutableEnumerationInputRequired"])
        self.assertTrue(workflow["meta"]["immutableEnumerationFailClosed"])
        self.assertFalse(workflow["meta"]["secondGraphEnumeration"])
        self.assertTrue(workflow["meta"]["archiveReceiptPreRead"])
        self.assertEqual(workflow["meta"]["emailEvidenceReceiptBarrier"], "REQUIRED")
        self.assertEqual(
            connections["Has Immutable Enumeration"]["main"][0][0]["node"],
            "Shape Immutable Archive Input",
        )
        self.assertEqual(
            connections["Has Immutable Enumeration"]["main"][1][0]["node"],
            "Reject Missing Immutable Enumeration",
        )
        self.assertNotIn("Get Messages from Configured Folder", nodes)
        self.assertEqual(
            connections["Enumerated Attachment Present"]["main"][0][0]["node"],
            "Read Existing Enumerated Archive Receipt",
        )
        self.assertEqual(
            connections["Existing Email Evidence Receipt Present"]["main"][0][0]["node"],
            "Verify Existing Email Evidence Receipt",
        )
        self.assertIn("attachment_inventory", nodes["Expand Enumerated Attachment Items"]["parameters"]["jsCode"])
        self.assertIn("ARCHIVED_ONLY", nodes["Record Enumerated Attachment Disposition"]["parameters"]["columns"]["value"]["error_class"])
        self.assertIn("source_message_id", nodes["Upsert Enumerated Archive Receipt"]["parameters"]["filters"]["conditions"][1]["keyValue"])
        self.assertIn("replay_noop_key", nodes["Verify Enumerated Archive Receipt"]["parameters"]["jsCode"])
        barrier_code = nodes["Attachment Verification Barrier"]["parameters"]["jsCode"]
        self.assertIn("ATTACHMENT_ARCHIVE_MISSING", barrier_code)
        self.assertIn("ATTACHMENT_ARCHIVE_COUNT_MISMATCH", barrier_code)
        self.assertEqual(
            connections["Record Enumerated Attachment Disposition"]["main"][0][0]["node"],
            "Merge Archive Verification Inputs",
        )
        self.assertEqual(
            connections["Record Email PDF Render Requirement"]["main"][0][0]["node"],
            "Merge Archive Verification Inputs",
        )
        self.assertIn("EMAIL_EVIDENCE_RECEIPT_BARRIER_MISMATCH", barrier_code)
        self.assertIn("source_message_id + ':' + row.source_attachment_id", barrier_code)

    def test_replay_and_failure_barriers_keep_cursor_after_archive_proof(self):
        w12 = self.workflow("12-outlook-message-sweep.json")
        w01 = self.workflow("01-outlook-finance-acquisition.json")
        w12_nodes = {node["name"]: node for node in w12["nodes"]}
        w01_nodes = {node["name"]: node for node in w01["nodes"]}
        self.assertEqual(w12["meta"]["attachmentVerificationBarrier"], "REQUIRED_BEFORE_CURSOR_COMMIT")
        self.assertEqual(
            w12["connections"]["Verify Downstream Persistence Proof"]["main"][0][0]["node"],
            "Require Verified Attachment Barrier",
        )
        self.assertIn("DOWNSTREAM_ARCHIVE_AND_EMAIL_BARRIER_MISSING", w12_nodes["Require Verified Attachment Barrier"]["parameters"]["jsCode"])
        self.assertIn("EMAIL_EVIDENCE_RECEIPT_COUNT_MISMATCH", w12_nodes["Verify Attachment Archive Barrier"]["parameters"]["jsCode"])
        self.assertIn("SOURCE_CURSOR_VERSION_CONFLICT", w12_nodes["Build Cursor CAS Update"]["parameters"]["jsCode"])
        self.assertIn("SOURCE_CURSOR_ALREADY_COMMITTED", w12_nodes["Build Cursor CAS Update"]["parameters"]["jsCode"])
        cas_filters = w12_nodes["CAS Update Source Cursor"]["parameters"]["filters"]["conditions"]
        self.assertEqual([row["keyName"] for row in cas_filters], ["source_code", "cursor_version"])
        self.assertIn("NO_OP_BY_SOURCE_ID_AND_HASH", w01["meta"]["attachmentArchiveReplay"])
        self.assertIn("ARCHIVE_ATTACHMENT_READBACK_HASH_MISMATCH", w01_nodes["Verify Enumerated Attachment Archive"]["parameters"]["jsCode"])
        self.assertIn("ARCHIVE_RECEIPT_REPLAY_NOT_SAFE", w01_nodes["Verify Existing Enumerated Archive Receipt"]["parameters"]["jsCode"])
        self.assertIn("EMAIL_EVIDENCE_RECEIPT_READBACK_MISMATCH", w01_nodes["Verify Durable Email Evidence Receipt"]["parameters"]["jsCode"])
        self.assertIn("Archive Enumerated Attachment in OneDrive", w01["connections"])

    def test_executable_attachment_cardinality_and_composite_identity(self):
        workflow = self.workflow("12-outlook-message-sweep.json")
        sweep = {
            "run_id": "fixture:cardinality",
            "source_code": "FIXTURE",
            "folder_id": "folder",
            "senders": ["sender@example.test"],
            "subjects": ["Fixture"],
            "window_start": "2026-08-19T00:00:00.000Z",
            "run_upper_bound": "2026-08-20T00:00:00.000Z",
            "scanned_count": 1,
            "matched_count": 1,
            "heartbeat": False,
            "pagination_exhausted": True,
            "cursor_commit_eligible": False,
            "messages": [{
                "message_id": "message-1",
                "message": {"id": "message-1", "subject": "Fixture"},
                "attachment_inventory": [],
            }],
        }
        shaped = self.execute_code_node(
            workflow,
            "Shape Immutable Message Inventory",
            refs={"Verify Receipt and Return Sweep": sweep},
        )
        self.assertTrue(shaped["ok"], shaped)
        for count in (0, 1, 101):
            attachments = [
                {"message_id": "message-1", "attachment": {"id": f"attachment-{index:03d}"}}
                for index in range(1, count + 1)
            ]
            result = self.execute_code_node(
                workflow,
                "Aggregate Immutable Archive Inventory",
                input_items=attachments,
                refs={
                    "Verify Receipt and Return Sweep": sweep,
                    "Shape Immutable Message Inventory": [
                        item["json"] for item in shaped["output"]
                    ],
                },
            )
            self.assertTrue(result["ok"], result)
            inventory = result["output"][0]["json"]
            self.assertEqual(len(inventory["messages"]), 1)
            self.assertEqual(len(inventory["messages"][0]["attachment_inventory"]), count)
            self.assertEqual(
                len(inventory["attachment_identity_keys"]), count
            )
            self.assertEqual(
                inventory["attachment_identity_keys"][:1],
                ["message-1:attachment-001"] if count else [],
            )

    def test_executable_restart_rehydrates_exact_inventory_and_resumes_w01(self):
        w12 = self.workflow("12-outlook-message-sweep.json")
        messages = [{
            "message_id": "message-1",
            "message": {"id": "message-1", "subject": "Fixture"},
            "attachment_inventory": [{"id": "attachment-001", "name": "statement.pdf"}],
            "attachment_ids": ["attachment-001"],
            "attachment_identity_keys": ["message-1:attachment-001"],
        }]
        persisted = json.dumps({
            "messages": messages,
            "attachment_identity_keys": ["message-1:attachment-001"],
            "empty_inventory": False,
            "immutable_inventory": True,
            "attachment_ids_verified": True,
        }, separators=(",", ":"))
        receipt = {
            "run_id": "fixture:restart",
            "source_code": "FIXTURE",
            "window_start": "2026-08-19T00:00:00.000Z",
            "run_upper_bound": "2026-08-20T00:00:00.000Z",
            "matched_count": 1,
            "terminal_state": "ENUMERATED",
            "pagination_exhausted": True,
            "cursor_commit_eligible": False,
            "immutable_inventory_json": persisted,
        }
        trusted = {key: receipt[key] for key in ("run_id", "source_code", "window_start", "run_upper_bound")}
        result = self.execute_code_node(
            w12,
            "Return Existing ENUMERATED Receipt",
            json_value=receipt,
            refs={"Freeze Trusted Cursor Window": trusted},
        )
        self.assertTrue(result["ok"], result)
        replay = result["output"][0]["json"]
        self.assertEqual(replay["messages"], messages)
        self.assertEqual(replay["attachment_identity_keys"], ["message-1:attachment-001"])
        self.assertTrue(replay["replay_noop"])
        self.assertEqual(
            w12["connections"]["Return Existing ENUMERATED Receipt"]["main"][0][0]["node"],
            "Archive Enumerated Messages in W01",
        )

    def test_executable_faults_fail_closed_and_cursor_cas_holds(self):
        w12 = self.workflow("12-outlook-message-sweep.json")
        w01 = self.workflow("01-outlook-finance-acquisition.json")
        self.assert_code_error(
            w12,
            "Gate Outlook Circuit",
            "PROVIDER_CIRCUIT_OPEN:MICROSOFT_GRAPH",
            json_value={
                "provider_code": "MICROSOFT_GRAPH",
                "state": "OPEN",
                "retry_after": "2099-01-01T00:00:00.000Z",
            },
        )
        self.assert_code_error(
            w01,
            "Verify Enumerated Attachment Archive",
            "ARCHIVE_ATTACHMENT_READBACK_HASH_MISMATCH",
            json_value={"archive_readback_sha256": "bad"},
            refs={
                "SHA-256 Enumerated Attachment": {
                    "document_sha256": "good",
                    "source_message_id": "message-1",
                    "source_attachment_id": "attachment-001",
                },
                "Archive Enumerated Attachment in OneDrive": {"id": "drive-item"},
            },
        )
        self.assert_code_error(
            w01,
            "Verify Durable Email Evidence Receipt",
            "EMAIL_EVIDENCE_RECEIPT_READBACK_MISMATCH",
            json_value={
                "archive_state": "HASH_VERIFIED",
                "source_message_id": "message-1",
                "source_attachment_id": "INLINE_BODY",
                "source_sha256": "wrong",
                "onedrive_item_id": "drive-item",
            },
            refs={"Verify Email Evidence Readback": {
                "source_message_id": "message-1",
                "email_evidence_sha256": "expected",
            }},
        )
        self.assert_code_error(
            w12,
            "Build Cursor CAS Update",
            "SOURCE_CURSOR_VERSION_CONFLICT",
            json_value={"source_code": "FIXTURE", "cursor_version": 8},
            refs={"Verify Downstream Persistence Proof": {
                "run_id": "fixture:cas",
                "source_code": "FIXTURE",
                "expected_cursor_version": 7,
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
            }},
        )

    def test_executable_mixed_new_and_replay_branches_reach_barrier(self):
        w01 = self.workflow("01-outlook-finance-acquisition.json")
        request = {
            "run_id": "fixture:mixed",
            "source_code": "FIXTURE",
            "folder_id": "folder",
            "senders": ["sender@example.test"],
            "subjects": ["Fixture"],
            "window_start": "2026-08-19T00:00:00.000Z",
            "run_upper_bound": "2026-08-20T00:00:00.000Z",
            "messages": [{"message_id": "message-1", "attachment_inventory": [{"id": "attachment-001"}]}],
        }
        result = self.execute_code_node(
            w01,
            "Attachment Verification Barrier",
            refs={
                "Validate Bounded Source Request": request,
                "Verify Enumerated Attachment Archive": {
                    "attachment_verified": True,
                    "attachment_identity": "message-1:attachment-001",
                    "source_message_id": "message-1",
                    "source_attachment_id": "attachment-001",
                },
                "Verify Existing Email Evidence Receipt": {
                    "email_evidence_receipt_verified": True,
                    "email_evidence_identity": "message-1:INLINE_BODY",
                },
            },
        )
        self.assertTrue(result["ok"], result)
        output = result["output"][0]["json"]
        self.assertEqual(output["attachment_identity_keys"], ["message-1:attachment-001"])
        self.assertEqual(output["email_evidence_receipts_verified"], 1)

    def test_executable_mixed_inline_and_non_pdf_disposition(self):
        w01 = self.workflow("01-outlook-finance-acquisition.json")
        expanded = self.execute_code_node(
            w01,
            "Expand Enumerated Attachment Items",
            json_value={
                "message_id": "message-mixed",
                "source_code": "FIXTURE",
                "onedrive_parent_id": "parent",
                "attachment_inventory": [
                    {"id": "inline-image", "name": "inline.png", "isInline": True},
                    {"id": "receipt-csv", "name": "receipt.csv", "isInline": False},
                ],
            },
        )
        self.assertTrue(expanded["ok"], expanded)
        rows = [item["json"] for item in expanded["output"]]
        self.assertEqual(
            [(row["source_attachment_id"], row["is_inline"], row["is_pdf"]) for row in rows],
            [("inline-image", True, False), ("receipt-csv", False, False)],
        )
        self.assertEqual(
            ["INLINE_ATTACHMENT_ARCHIVED_ONLY", "NON_PDF_ARCHIVED_ONLY"],
            [
                "INLINE_ATTACHMENT_ARCHIVED_ONLY" if row["is_inline"]
                else "NON_PDF_ARCHIVED_ONLY"
                for row in rows
            ],
        )

        request = {
            "run_id": "fixture:mixed-disposition",
            "source_code": "FIXTURE",
            "folder_id": "folder",
            "senders": ["sender@example.test"],
            "subjects": ["Fixture"],
            "onedrive_parent_id": "parent",
            "window_start": "2026-08-19T00:00:00.000Z",
            "run_upper_bound": "2026-08-20T00:00:00.000Z",
            "messages": [{
                "message_id": "message-mixed",
                "attachment_inventory": [
                    {"id": "inline-image"},
                    {"id": "receipt-csv"},
                ],
            }],
        }
        barrier = self.execute_code_node(
            w01,
            "Attachment Verification Barrier",
            refs={
                "Validate Bounded Source Request": request,
                "Verify Enumerated Attachment Archive": [
                    {
                        "attachment_verified": True,
                        "attachment_identity": f"message-mixed:{attachment_id}",
                        "source_message_id": "message-mixed",
                        "source_attachment_id": attachment_id,
                    }
                    for attachment_id in ("inline-image", "receipt-csv")
                ],
                "Verify Durable Email Evidence Receipt": {
                    "email_evidence_receipt_verified": True,
                    "email_evidence_identity": "message-mixed:INLINE_BODY",
                },
            },
        )
        self.assertTrue(barrier["ok"], barrier)
        self.assertEqual(barrier["output"][0]["json"]["attachments_verified"], 2)

    def test_executable_101_attachment_barrier_all_new_then_all_replay(self):
        w01 = self.workflow("01-outlook-finance-acquisition.json")
        messages = [
            {
                "message_id": f"message-{index:03d}",
                "attachment_inventory": [{"id": f"attachment-{index:03d}"}],
            }
            for index in range(1, 102)
        ]
        identities = [
            f"message-{index:03d}:attachment-{index:03d}"
            for index in range(1, 102)
        ]
        archive_receipts = {}
        email_receipts = {}
        provider_calls = []

        for mode in ("all-new", "all-replay"):
            attachment_rows = []
            email_rows = []
            for index, identity in enumerate(identities, start=1):
                message_id, attachment_id = identity.split(":")
                attachment_sha256 = f"{index:064x}"
                email_sha256 = f"{index + 1000:064x}"
                if mode == "all-new":
                    provider_calls.append(identity)
                    archive_receipts[identity] = {
                        "archive_state": "HASH_VERIFIED",
                        "source_message_id": message_id,
                        "source_attachment_id": attachment_id,
                        "source_sha256": attachment_sha256,
                    }
                    email_receipts[message_id] = {
                        "archive_state": "HASH_VERIFIED",
                        "source_message_id": message_id,
                        "source_attachment_id": "INLINE_BODY",
                        "source_sha256": email_sha256,
                    }
                    if index == 1:
                        new_attachment = self.execute_code_node(
                            w01,
                            "Verify Enumerated Attachment Archive",
                            json_value={"archive_readback_sha256": attachment_sha256},
                            refs={
                                "SHA-256 Enumerated Attachment": {
                                    "document_sha256": attachment_sha256,
                                    "source_message_id": message_id,
                                    "source_attachment_id": attachment_id,
                                },
                                "Archive Enumerated Attachment in OneDrive": {"id": "drive-item"},
                            },
                        )
                        self.assertTrue(new_attachment["ok"], new_attachment)
                        attachment_rows.append(new_attachment["output"][0]["json"])
                        new_email = self.execute_code_node(
                            w01,
                            "Verify Durable Email Evidence Receipt",
                            json_value=email_receipts[message_id] | {
                                "onedrive_item_id": "drive-email",
                            },
                            refs={
                                "Verify Email Evidence Readback": {
                                    "source_message_id": message_id,
                                    "email_evidence_sha256": email_sha256,
                                },
                            },
                        )
                        self.assertTrue(new_email["ok"], new_email)
                        email_rows.append(new_email["output"][0]["json"])
                    else:
                        attachment_rows.append({
                            "attachment_verified": True,
                            "attachment_identity": identity,
                            "source_message_id": message_id,
                            "source_attachment_id": attachment_id,
                        })
                        email_rows.append({
                            "email_evidence_receipt_verified": True,
                            "email_evidence_identity": f"{message_id}:INLINE_BODY",
                        })
                else:
                    archive = archive_receipts[identity]
                    email = email_receipts[message_id]
                    attachment_replay = self.execute_code_node(
                        w01,
                        "Verify Existing Enumerated Archive Receipt",
                        json_value={
                            "source_message_id": message_id,
                            "source_attachment_id": attachment_id,
                            "existing_archive_receipt": archive,
                        },
                    )
                    self.assertTrue(attachment_replay["ok"], attachment_replay)
                    email_replay = self.execute_code_node(
                        w01,
                        "Verify Existing Email Evidence Receipt",
                        json_value={
                            "source_message_id": message_id,
                            "email_evidence_sha256": email["source_sha256"],
                            "existing_email_receipt": email,
                        },
                    )
                    self.assertTrue(email_replay["ok"], email_replay)
                    attachment_rows.append(attachment_replay["output"][0]["json"])
                    email_rows.append(email_replay["output"][0]["json"])

            request = {
                "run_id": f"fixture:{mode}",
                "source_code": "FIXTURE",
                "folder_id": "folder",
                "senders": ["sender@example.test"],
                "subjects": ["Fixture"],
                "onedrive_parent_id": "parent",
                "window_start": "2026-08-19T00:00:00.000Z",
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
                "messages": messages,
            }
            barrier = self.execute_code_node(
                w01,
                "Attachment Verification Barrier",
                refs={
                    "Validate Bounded Source Request": request,
                    "Verify Enumerated Attachment Archive": (
                        attachment_rows if mode == "all-new" else []
                    ),
                    "Verify Existing Enumerated Archive Receipt": (
                        attachment_rows if mode == "all-replay" else []
                    ),
                    "Verify Durable Email Evidence Receipt": (
                        email_rows if mode == "all-new" else []
                    ),
                    "Verify Existing Email Evidence Receipt": (
                        email_rows if mode == "all-replay" else []
                    ),
                },
            )
            self.assertTrue(barrier["ok"], barrier)
            output = barrier["output"][0]["json"]
            self.assertEqual(output["attachments_verified"], 101)
            self.assertEqual(output["email_evidence_receipts_verified"], 101)
            self.assertEqual(output["attachment_identity_keys"], identities)

        self.assertEqual(provider_calls, identities)
        self.assertEqual(len(archive_receipts), 101)
        self.assertEqual(len(email_receipts), 101)


if __name__ == "__main__":
    unittest.main()
