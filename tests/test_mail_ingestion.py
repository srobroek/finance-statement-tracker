import json
import subprocess
import unittest
from datetime import datetime
from pathlib import Path

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

    def test_statement_cycles_delegate_one_immutable_inventory_to_w01(self):
        """Cycle callers preserve source policy while W12 owns the only listing."""
        w12 = self.workflow("12-outlook-message-sweep.json")
        w01 = self.workflow("01-outlook-finance-acquisition.json")
        cases = (
            (
                "04-ei-monthly-statement.json",
                {
                    "source_code": "EI_AMAZON",
                    "senders": ["estatement@emiratesislamic.ae"],
                    "subjects": ["Statement of your Emirates Islamic Credit Card"],
                    "cycle_day": 1,
                    "deadline_days": 5,
                },
            ),
            (
                "05-wio-monthly-statement.json",
                {
                    "source_code": "WIO_CREDIT",
                    "senders": [
                        "communications@email.wio.io",
                        "communications@mail.wio.io",
                    ],
                    "subjects": ["Your Wio Credit statement for this month"],
                    "cycle_day": 3,
                    "deadline_days": 5,
                },
            ),
        )
        for filename, source in cases:
            with self.subTest(workflow=filename):
                workflow = self.workflow(filename)
                nodes = {node["name"]: node for node in workflow["nodes"]}
                acquire = nodes["Acquire Archive and Read Back"]
                target = acquire["parameters"]["workflowId"]
                self.assertEqual(
                    target["value"], w12["id"],
                    "cycle must delegate enumeration to W12, never call W01 directly",
                )
                self.assertEqual(
                    target["cachedResultName"], w12["name"]
                )
                self.assertTrue(acquire["parameters"]["options"]["waitForSubWorkflow"])
                for node_name in (
                    "Read Source Cursor Before Commit",
                    "Build W12 COMMIT Request",
                    "Commit Source Cursor via W12",
                    "Verify W12 COMMIT Terminal Readback",
                ):
                    self.assertIn(node_name, nodes)
                self.assertEqual(
                    workflow["connections"]["Run Shared Statement Pipeline"]["main"][0][0]["node"],
                    "Read Source Cursor Before Commit",
                )
                self.assertEqual(
                    workflow["connections"]["Read Source Cursor Before Commit"]["main"][0][0]["node"],
                    "Build W12 COMMIT Request",
                )
                self.assertEqual(
                    workflow["connections"]["Build W12 COMMIT Request"]["main"][0][0]["node"],
                    "Commit Source Cursor via W12",
                )
                self.assertEqual(
                    workflow["connections"]["Commit Source Cursor via W12"]["main"][0][0]["node"],
                    "Verify W12 COMMIT Terminal Readback",
                )
                commit = nodes["Commit Source Cursor via W12"]
                self.assertEqual(commit["parameters"]["workflowId"]["value"], w12["id"])
                commit_inputs = commit["parameters"]["workflowInputs"]["value"]
                for field in (
                    "operation",
                    "downstream_receipt_sha256",
                    "expected_cursor_version",
                    "attachment_verification_barrier",
                    "email_evidence_receipt_barrier",
                    "receipt_readback_verified",
                ):
                    self.assertIn(field, commit_inputs)
                self.assertIn("DOWNSTREAM_TERMINAL_RECEIPT_REQUIRED", nodes["Build W12 COMMIT Request"]["parameters"]["jsCode"])
                self.assertIn("ARCHIVE_BARRIER_REQUIRED_BEFORE_CURSOR_COMMIT", nodes["Build W12 COMMIT Request"]["parameters"]["jsCode"])
                self.assertIn("SOURCE_CURSOR_COMMIT_TERMINAL_READBACK_MISMATCH", nodes["Verify W12 COMMIT Terminal Readback"]["parameters"]["jsCode"])
                self.assertEqual(
                    workflow["settings"]["errorWorkflow"],
                    "10000000-0000-4000-8000-000000000016",
                )
                self.assertEqual(
                    nodes["Download Archived Source"]["credentials"][
                        "microsoftOneDriveOAuth2Api"
                    ]["id"],
                    "BIND_ONEDRIVE",
                )
                self.assertEqual(
                    sum(
                        node["type"] == "n8n-nodes-base.executeWorkflow"
                        and node.get("parameters", {}).get("workflowId", {}).get("value")
                        == w01["id"]
                        for node in workflow["nodes"]
                    ),
                    0,
                )
                self.assertFalse(
                    any(node["type"] == "n8n-nodes-base.microsoftOutlook" for node in workflow["nodes"])
                )

                run = {
                    "run_id": f"fixture:{source['source_code']}:cycle",
                    "source_code": source["source_code"],
                    "folder_id": f"folder:{source['source_code']}",
                    "window_start": "2026-08-19T00:00:00.000Z",
                    "run_upper_bound": "2026-08-20T00:00:00.000Z",
                    "period_key": "2026-08",
                    "cycle_day": source["cycle_day"],
                    "deadline_days": source["deadline_days"],
                    "deadline_at": "2026-08-25T23:59:59.000Z",
                    "trigger_kind": "SCHEDULE",
                }
                contract_row = {
                    **source,
                    "config_version": "fixture-v1",
                    "folder_id": run["folder_id"],
                    "senders_json": json.dumps(source["senders"]),
                    "subjects_json": json.dumps(source["subjects"]),
                    "onedrive_parent_id": f"drive:{source['source_code']}",
                    "manifest_onedrive_parent_id": f"manifest:{source['source_code']}",
                    "actual_file_id": f"actual:{source['source_code']}",
                    "account_id": f"account:{source['source_code']}",
                    "card_code": source["source_code"],
                    "cashback_close_required": source["source_code"] == "EI_AMAZON",
                    "enabled": True,
                }
                assembled = self.execute_code_node(
                    workflow,
                    "Assemble Trusted Acquisition Contract",
                    json_value=contract_row,
                    refs={"Open Configured Cycle Window": run},
                )
                self.assertTrue(assembled["ok"], assembled)
                request = assembled["output"][0]["json"]
                self.assertEqual(request["operation"], "ENUMERATE")
                self.assertEqual(request["source_code"], source["source_code"])
                self.assertEqual(request["senders"], source["senders"])
                self.assertEqual(request["subjects"], source["subjects"])
                self.assertEqual(request["cycle_day"], source["cycle_day"])
                self.assertEqual(request["deadline_days"], source["deadline_days"])
                self.assertEqual(request["deadline_at"], run["deadline_at"])

                frozen = self.execute_code_node(
                    w12,
                    "Freeze Trusted Cursor Window",
                    json_value=request,
                )
                self.assertTrue(frozen["ok"], frozen)
                frozen_request = frozen["output"][0]["json"]
                self.assertEqual(frozen_request["run_upper_bound"], run["run_upper_bound"])
                for sender in source["senders"]:
                    self.assertIn(
                        "from/emailAddress/address eq '" + sender + "'",
                        frozen_request["server_filter"],
                    )
                self.assertIn(
                    "contains(subject,'" + source["subjects"][0] + "')",
                    frozen_request["server_filter"],
                )

                def message(index, attachments, source=source):
                    return {
                        "id": f"message-{index:03d}",
                        "receivedDateTime": f"2026-08-19T00:{index % 60:02d}:00.000Z",
                        "from": {"emailAddress": {"address": source["senders"][0]}},
                        "subject": source["subjects"][0],
                        "attachment_inventory": attachments,
                    }

                cardinalities = {
                    "zero": [],
                    "one": [message(1, [{"id": "statement-001", "name": "statement.pdf"}])],
                    "one-hundred-one": [
                        message(index, [{"id": f"statement-{index:03d}", "name": "statement.pdf"}])
                        for index in range(1, 102)
                    ],
                    "mixed": [
                        message(
                            1,
                            [
                                {"id": "statement-001", "name": "statement.pdf"},
                                {"id": "inline-001", "name": "logo.png", "isInline": True},
                            ],
                        ),
                        message(2, []),
                    ],
                }
                for cardinality, messages in cardinalities.items():
                    with self.subTest(workflow=filename, cardinality=cardinality):
                        aggregate = self.execute_code_node(
                            w12,
                            "Aggregate Exact Window Heartbeat",
                            input_items=messages,
                            refs={"Freeze Trusted Cursor Window": frozen_request},
                        )
                        self.assertTrue(aggregate["ok"], aggregate)
                        sweep = aggregate["output"][0]["json"]
                        shaped = self.execute_code_node(
                            w12,
                            "Shape Immutable Message Inventory",
                            refs={"Verify Receipt and Return Sweep": sweep},
                        )
                        self.assertTrue(shaped["ok"], shaped)
                        parents = [item["json"] for item in shaped["output"]]
                        attachment_rows = [
                            {
                                "message_id": parent["message_id"],
                                "attachment": attachment,
                            }
                            for parent in parents
                            for attachment in parent.get("attachment_inventory", [])
                        ]
                        inventory = self.execute_code_node(
                            w12,
                            "Aggregate Immutable Archive Inventory",
                            input_items=attachment_rows,
                            refs={
                                "Verify Receipt and Return Sweep": sweep,
                                "Shape Immutable Message Inventory": parents,
                            },
                        )
                        self.assertTrue(inventory["ok"], inventory)
                        aggregate_inventory = inventory["output"][0]["json"]
                        attached = self.execute_code_node(
                            w12,
                            "Attach Immutable Inventory to Sweep",
                            json_value={**sweep, "pagination_exhausted": True},
                            refs={"Aggregate Immutable Archive Inventory": aggregate_inventory},
                        )
                        self.assertTrue(attached["ok"], attached)
                        w01_request = attached["output"][0]["json"]
                        validated = self.execute_code_node(
                            w01,
                            "Validate Bounded Source Request",
                            json_value=w01_request,
                        )
                        self.assertTrue(validated["ok"], validated)
                        archive_input = self.execute_code_node(
                            w01,
                            "Shape Immutable Archive Input",
                            refs={"Validate Bounded Source Request": validated["output"][0]["json"]},
                        )
                        self.assertTrue(archive_input["ok"], archive_input)
                        shaped_archive = [item["json"] for item in archive_input["output"]]
                        self.assertEqual(
                            len(shaped_archive), max(1, len(messages)),
                            "W01 receives every message, including an explicit empty inventory",
                        )
                        if messages:
                            expected_messages = sorted(
                                messages,
                                key=lambda row: (row["receivedDateTime"], row["id"]),
                            )
                            self.assertEqual(
                                [row["message_id"] for row in shaped_archive],
                                [row["id"] for row in expected_messages],
                            )
                            self.assertEqual(
                                [row["attachment_inventory"] for row in shaped_archive],
                                [
                                    sorted(row["attachment_inventory"], key=lambda item: item["id"])
                                    for row in expected_messages
                                ],
                            )
                        else:
                            self.assertTrue(shaped_archive[0]["empty_inventory"])

                replay_messages = cardinalities["one-hundred-one"]
                replay_aggregate = self.execute_code_node(
                    w12,
                    "Aggregate Exact Window Heartbeat",
                    input_items=replay_messages,
                    refs={"Freeze Trusted Cursor Window": frozen_request},
                )
                replay_sweep = replay_aggregate["output"][0]["json"]
                replay_parents = self.execute_code_node(
                    w12,
                    "Shape Immutable Message Inventory",
                    refs={"Verify Receipt and Return Sweep": replay_sweep},
                )
                replay_parent_rows = [item["json"] for item in replay_parents["output"]]
                replay_inventory = {
                    "messages": [
                        {
                            "message_id": row["message_id"],
                            "message": row,
                            "attachment_inventory": row["attachment_inventory"],
                            "attachment_ids": [attachment["id"] for attachment in row["attachment_inventory"]],
                            "attachment_identity_keys": [
                                row["message_id"] + ":" + attachment["id"]
                                for attachment in row["attachment_inventory"]
                            ],
                        }
                        for row in replay_parent_rows
                    ],
                    "attachment_identity_keys": [
                        row["message_id"] + ":" + attachment["id"]
                        for row in replay_parent_rows
                        for attachment in row["attachment_inventory"]
                    ],
                    "empty_inventory": False,
                    "immutable_inventory": True,
                    "attachment_ids_verified": True,
                }
                replay_receipt = {
                    **{
                        key: replay_sweep[key]
                        for key in ("run_id", "source_code", "window_start", "run_upper_bound")
                    },
                    "matched_count": len(replay_messages),
                    "terminal_state": "ENUMERATED",
                    "pagination_exhausted": True,
                    "cursor_commit_eligible": False,
                    "immutable_inventory_json": json.dumps(
                        replay_inventory, separators=(",", ":")
                    ),
                }
                replay = self.execute_code_node(
                    w12,
                    "Return Existing ENUMERATED Receipt",
                    json_value=replay_receipt,
                    refs={"Freeze Trusted Cursor Window": frozen_request},
                )
                self.assertTrue(replay["ok"], replay)
                replay_request = replay["output"][0]["json"]
                self.assertTrue(replay_request["replay_noop"])
                self.assertEqual(len(replay_request["messages"]), 101)
                replay_validated = self.execute_code_node(
                    w01,
                    "Validate Bounded Source Request",
                    json_value=replay_request,
                )
                self.assertTrue(replay_validated["ok"], replay_validated)

        self.assertEqual(
            sum(
                node["type"] == "n8n-nodes-base.microsoftOutlook"
                and node["parameters"].get("resource") == "folderMessage"
                for node in w12["nodes"]
            ),
            1,
            "W12 owns the only message listing",
        )
        self.assertFalse(
            any(
                node["type"] == "n8n-nodes-base.microsoftOutlook"
                and node["parameters"].get("resource") == "folderMessage"
                for node in w01["nodes"]
            )
        )

    def test_statement_cycles_run_w01_barrier_and_w12_commit_cas(self):
        """The caller path archives once, proves downstream state, then owns CAS."""
        w12 = self.workflow("12-outlook-message-sweep.json")
        w01 = self.workflow("01-outlook-finance-acquisition.json")
        cases = (
            (
                "04-ei-monthly-statement.json",
                "EI_AMAZON",
                ["estatement@emiratesislamic.ae"],
                ["Statement of your Emirates Islamic Credit Card"],
                1,
            ),
            (
                "05-wio-monthly-statement.json",
                "WIO_CREDIT",
                ["communications@email.wio.io", "communications@mail.wio.io"],
                ["Your Wio Credit statement for this month"],
                3,
            ),
        )

        def cycle_request(workflow, source_code, senders, subjects, cycle_day):
            run = {
                "run_id": f"fixture:{source_code}:barrier",
                "source_code": source_code,
                "folder_id": f"folder:{source_code}",
                "window_start": "2026-08-19T00:00:00.000Z",
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
                "period_key": "2026-08",
                "cycle_day": cycle_day,
                "deadline_days": 5,
                "deadline_at": "2026-08-25T23:59:59.000Z",
                "trigger_kind": "SCHEDULE",
            }
            source = {
                "source_code": source_code,
                "config_version": "fixture-v1",
                "folder_id": run["folder_id"],
                "senders_json": json.dumps(senders),
                "subjects_json": json.dumps(subjects),
                "onedrive_parent_id": f"drive:{source_code}",
                "manifest_onedrive_parent_id": f"manifest:{source_code}",
                "actual_file_id": f"actual:{source_code}",
                "account_id": f"account:{source_code}",
                "card_code": source_code,
                "cashback_close_required": source_code == "EI_AMAZON",
                "enabled": True,
            }
            assembled = self.execute_code_node(
                workflow,
                "Assemble Trusted Acquisition Contract",
                json_value=source,
                refs={"Open Configured Cycle Window": run},
            )
            self.assertTrue(assembled["ok"], assembled)
            return assembled["output"][0]["json"]

        def message(source, index, attachments):
            return {
                "id": f"message-{index:03d}",
                "receivedDateTime": f"2026-08-19T00:{index % 60:02d}:00.000Z",
                "from": {"emailAddress": {"address": source["senders"][0]}},
                "subject": source["subjects"][0],
                "attachment_inventory": attachments,
            }

        def inventory_for(request, messages):
            frozen = self.execute_code_node(
                w12,
                "Freeze Trusted Cursor Window",
                json_value=request,
            )
            self.assertTrue(frozen["ok"], frozen)
            frozen_request = frozen["output"][0]["json"]
            aggregate = self.execute_code_node(
                w12,
                "Aggregate Exact Window Heartbeat",
                input_items=messages,
                refs={"Freeze Trusted Cursor Window": frozen_request},
            )
            self.assertTrue(aggregate["ok"], aggregate)
            sweep = aggregate["output"][0]["json"]
            shaped = self.execute_code_node(
                w12,
                "Shape Immutable Message Inventory",
                refs={"Verify Receipt and Return Sweep": sweep},
            )
            self.assertTrue(shaped["ok"], shaped)
            parents = [item["json"] for item in shaped["output"]]
            attachment_rows = [
                {"message_id": parent["message_id"], "attachment": attachment}
                for parent in parents
                for attachment in parent.get("attachment_inventory", [])
            ]
            inventory = self.execute_code_node(
                w12,
                "Aggregate Immutable Archive Inventory",
                input_items=attachment_rows,
                refs={
                    "Verify Receipt and Return Sweep": sweep,
                    "Shape Immutable Message Inventory": parents,
                },
            )
            self.assertTrue(inventory["ok"], inventory)
            aggregate_inventory = inventory["output"][0]["json"]
            persisted = {
                "messages": aggregate_inventory["messages"],
                "attachment_identity_keys": aggregate_inventory["attachment_identity_keys"],
                "empty_inventory": aggregate_inventory["empty_inventory"],
                "immutable_inventory": True,
                "attachment_ids_verified": True,
            }
            receipt = {
                **{
                    key: sweep[key]
                    for key in (
                        "run_id",
                        "source_code",
                        "window_start",
                        "run_upper_bound",
                        "scanned_count",
                        "matched_count",
                        "heartbeat",
                    )
                },
                "pagination_exhausted": True,
                "cursor_commit_eligible": False,
                "immutable_inventory_json": json.dumps(persisted, separators=(",", ":")),
            }
            verified = self.execute_code_node(
                w12,
                "Verify Receipt and Return Sweep",
                json_value=receipt,
                refs={"Aggregate Immutable Archive Inventory": aggregate_inventory},
            )
            self.assertTrue(verified["ok"], verified)
            attached = self.execute_code_node(
                w12,
                "Attach Immutable Inventory to Sweep",
                json_value=verified["output"][0]["json"],
                refs={"Aggregate Immutable Archive Inventory": aggregate_inventory},
            )
            self.assertTrue(attached["ok"], attached)
            return frozen_request, attached["output"][0]["json"], persisted

        for filename, source_code, senders, subjects, cycle_day in cases:
            with self.subTest(workflow=filename):
                workflow = self.workflow(filename)
                request = cycle_request(workflow, source_code, senders, subjects, cycle_day)
                cardinalities = {
                    "zero": [],
                    "one": [message(request, 1, [{"id": "statement-001", "name": "statement.pdf"}])],
                    "one-hundred-one": [
                        message(request, index, [{"id": f"statement-{index:03d}", "name": "statement.pdf"}])
                        for index in range(1, 102)
                    ],
                    "mixed": [
                        message(
                            request,
                            1,
                            [
                                {"id": "statement-001", "name": "statement.pdf"},
                                {"id": "inline-001", "name": "logo.png", "isInline": True},
                            ],
                        ),
                        message(request, 2, []),
                    ],
                }
                committed = None
                for cardinality, messages in cardinalities.items():
                    with self.subTest(cardinality=cardinality):
                        frozen, w01_request, persisted = inventory_for(request, messages)
                        validated = self.execute_code_node(
                            w01,
                            "Validate Bounded Source Request",
                            json_value=w01_request,
                        )
                        self.assertTrue(validated["ok"], validated)
                        archive_input = self.execute_code_node(
                            w01,
                            "Shape Immutable Archive Input",
                            refs={"Validate Bounded Source Request": validated["output"][0]["json"]},
                        )
                        self.assertTrue(archive_input["ok"], archive_input)
                        archive_rows = [
                            {
                                "attachment_verified": True,
                                "attachment_identity": f"{row['message_id']}:{attachment['id']}",
                                "source_message_id": row["message_id"],
                                "source_attachment_id": attachment["id"],
                                "source_sha256": "a" * 64,
                                "onedrive_item_id": f"drive:{row['message_id']}:{attachment['id']}",
                            }
                            for row in persisted["messages"]
                            for attachment in row["attachment_inventory"]
                        ]
                        email_rows = [
                            {
                                "email_evidence_receipt_verified": True,
                                "email_evidence_identity": f"{row['message_id']}:INLINE_BODY",
                                "source_message_id": row["message_id"],
                                "source_attachment_id": "INLINE_BODY",
                                "source_sha256": "b" * 64,
                                "onedrive_item_id": f"drive-email:{row['message_id']}",
                            }
                            for row in persisted["messages"]
                        ]
                        if archive_input["output"] and messages:
                            verified_archive_rows = []
                            verified_email_rows = []
                            for archive_item in archive_input["output"]:
                                archive_message = archive_item["json"]
                                message_id = archive_message["message_id"]
                                for attachment in archive_message["attachment_inventory"]:
                                    attachment_id = attachment["id"]
                                    attachment_hash = "a" * 64
                                    archive_readback = self.execute_code_node(
                                        w01,
                                        "Verify Enumerated Attachment Archive",
                                        json_value={"archive_readback_sha256": attachment_hash},
                                        refs={
                                            "SHA-256 Enumerated Attachment": {
                                                "document_sha256": attachment_hash,
                                                "source_message_id": message_id,
                                                "source_attachment_id": attachment_id,
                                            },
                                            "Archive Enumerated Attachment in OneDrive": {
                                                "id": f"drive:{message_id}:{attachment_id}",
                                            },
                                        },
                                    )
                                    self.assertTrue(archive_readback["ok"], archive_readback)
                                    archive_receipt = self.execute_code_node(
                                        w01,
                                        "Verify Enumerated Archive Receipt",
                                        json_value={
                                            "archive_state": "HASH_VERIFIED",
                                            "source_message_id": message_id,
                                            "source_attachment_id": attachment_id,
                                            "source_sha256": attachment_hash,
                                            "onedrive_item_id": f"drive:{message_id}:{attachment_id}",
                                        },
                                        refs={
                                            "Verify Enumerated Attachment Archive":
                                                archive_readback["output"][0]["json"],
                                        },
                                    )
                                    self.assertTrue(archive_receipt["ok"], archive_receipt)
                                    verified_archive_rows.append(archive_receipt["output"][0]["json"])

                                built_email = self.execute_code_node(
                                    w01,
                                    "Build Original Email Evidence",
                                    json_value=archive_message,
                                )
                                self.assertTrue(built_email["ok"], built_email)
                                email_hash = "b" * 64
                                email_readback = self.execute_code_node(
                                    w01,
                                    "Verify Email Evidence Readback",
                                    json_value={"email_readback_sha256": email_hash},
                                    refs={
                                        "SHA-256 Email Evidence": {
                                            "email_evidence_sha256": email_hash,
                                            "source_message_id": message_id,
                                        },
                                        "Archive Email Evidence in OneDrive": {
                                            "id": f"drive-email:{message_id}",
                                        },
                                        "Build Original Email Evidence":
                                            built_email["output"][0]["json"],
                                    },
                                )
                                self.assertTrue(email_readback["ok"], email_readback)
                                email_receipt = self.execute_code_node(
                                    w01,
                                    "Verify Durable Email Evidence Receipt",
                                    json_value={
                                        "archive_state": "HASH_VERIFIED",
                                        "source_message_id": message_id,
                                        "source_attachment_id": "INLINE_BODY",
                                        "source_sha256": email_hash,
                                        "onedrive_item_id": f"drive-email:{message_id}",
                                    },
                                    refs={
                                        "Verify Email Evidence Readback":
                                            email_readback["output"][0]["json"],
                                    },
                                )
                                self.assertTrue(email_receipt["ok"], email_receipt)
                                verified_email_rows.append(email_receipt["output"][0]["json"])
                            archive_rows = verified_archive_rows
                            email_rows = verified_email_rows
                        barrier = self.execute_code_node(
                            w01,
                            "Attachment Verification Barrier",
                            refs={
                                "Validate Bounded Source Request": validated["output"][0]["json"],
                                "Verify Enumerated Attachment Archive": archive_rows,
                                "Verify Durable Email Evidence Receipt": email_rows,
                            },
                        )
                        self.assertTrue(barrier["ok"], barrier)
                        downstream = barrier["output"][0]["json"]
                        self.assertEqual(downstream["attachments_verified"], len(archive_rows))
                        self.assertEqual(downstream["email_evidence_receipts_verified"], len(messages))
                        downstream.update(
                            pagination_exhausted=True,
                            scanned_count=len(messages),
                            heartbeat=not messages,
                            archive_ready=True,
                            receipt_readback_verified=True,
                        )
                        pipeline_receipt = {
                            "run_id": frozen["run_id"],
                            "source_code": source_code,
                            "state": "SUCCEEDED",
                            "receipt_sha256": "c" * 64,
                            "terminal_readback_verified": True,
                        }
                        built_commit = self.execute_code_node(
                            workflow,
                            "Build W12 COMMIT Request",
                            json_value={
                                "source_code": source_code,
                                "cursor_version": 0,
                                "cursor_value": "2026-08-18T00:00:00.000Z",
                                "committed_run_id": None,
                            },
                            refs={
                                "Assemble Trusted Acquisition Contract": request,
                                "Acquire Archive and Read Back": downstream,
                                "Run Shared Statement Pipeline": pipeline_receipt,
                            },
                        )
                        self.assertTrue(built_commit["ok"], built_commit)
                        commit_request = built_commit["output"][0]["json"]
                        validated_commit = self.execute_code_node(
                            w12,
                            "Validate Sweep or Commit",
                            json_value=commit_request,
                        )
                        self.assertTrue(validated_commit["ok"], validated_commit)
                        merged_commit = self.execute_code_node(
                            w12,
                            "Merge Commit Evidence Readback",
                            json_value={
                                **commit_request,
                                "terminal_state": "ENUMERATED",
                                "immutable_inventory_json": json.dumps(
                                    persisted, separators=(",", ":")
                                ),
                                "pagination_exhausted": True,
                                "cursor_commit_eligible": False,
                                "matched_count": len(messages),
                            },
                            refs={"Validate Sweep or Commit": commit_request},
                        )
                        self.assertTrue(merged_commit["ok"], merged_commit)
                        proof = self.execute_code_node(
                            w12,
                            "Verify Downstream Persistence Proof",
                            json_value=merged_commit["output"][0]["json"],
                            refs={"Validate Sweep or Commit": validated_commit["output"][0]["json"]},
                        )
                        self.assertTrue(proof["ok"], proof)
                        cas = self.execute_code_node(
                            w12,
                            "Build Cursor CAS Update",
                            json_value={
                                "source_code": source_code,
                                "cursor_version": 0,
                                "cursor_value": "2026-08-18T00:00:00.000Z",
                                "committed_run_id": None,
                            },
                            refs={"Verify Downstream Persistence Proof": proof["output"][0]["json"]},
                        )
                        self.assertTrue(cas["ok"], cas)
                        cas_row = cas["output"][0]["json"]
                        self.assertEqual(cas_row["prior_cursor_version"], 0)
                        self.assertEqual(cas_row["next_cursor_version"], 1)
                        readback = self.execute_code_node(
                            w12,
                            "Compare CAS Cursor Readback",
                            json_value={
                                "cursor_value": frozen["run_upper_bound"],
                                "cursor_version": 1,
                                "committed_run_id": frozen["run_id"],
                            },
                            refs={"Build Cursor CAS Update": cas_row},
                        )
                        self.assertTrue(readback["ok"], readback)
                        terminal = self.execute_code_node(
                            w12,
                            "Return Verified Cursor Commit",
                            json_value={
                                "downstream_receipt_sha256": "c" * 64,
                                "cursor_commit_eligible": True,
                            },
                            refs={"Build Cursor CAS Update": cas_row},
                        )
                        self.assertTrue(terminal["ok"], terminal)
                        self.assertEqual(terminal["output"][0]["json"]["status"], "CURSOR_COMMITTED")
                        if cardinality == "one-hundred-one":
                            committed = (frozen, persisted, archive_rows, email_rows)

                self.assertIsNotNone(committed)
                frozen, persisted, archive_rows, email_rows = committed
                replay_receipt = {
                    **{
                        key: frozen[key]
                        for key in ("run_id", "source_code", "window_start", "run_upper_bound")
                    },
                    "matched_count": 101,
                    "terminal_state": "ENUMERATED",
                    "pagination_exhausted": True,
                    "cursor_commit_eligible": False,
                    "immutable_inventory_json": json.dumps(persisted, separators=(",", ":")),
                }
                replay = self.execute_code_node(
                    w12,
                    "Return Existing ENUMERATED Receipt",
                    json_value=replay_receipt,
                    refs={"Freeze Trusted Cursor Window": frozen},
                )
                self.assertTrue(replay["ok"], replay)
                replay_request = replay["output"][0]["json"]
                replay_validated = self.execute_code_node(
                    w01,
                    "Validate Bounded Source Request",
                    json_value=replay_request,
                )
                self.assertTrue(replay_validated["ok"], replay_validated)
                replay_barrier = self.execute_code_node(
                    w01,
                    "Attachment Verification Barrier",
                    refs={
                        "Validate Bounded Source Request": replay_validated["output"][0]["json"],
                        "Verify Existing Enumerated Archive Receipt": archive_rows,
                        "Verify Existing Email Evidence Receipt": email_rows,
                    },
                )
                self.assertTrue(replay_barrier["ok"], replay_barrier)
                replay_downstream = replay_barrier["output"][0]["json"]
                replay_downstream.update(
                    pagination_exhausted=True,
                    scanned_count=101,
                    heartbeat=False,
                    archive_ready=True,
                    receipt_readback_verified=True,
                )
                replay_built_commit = self.execute_code_node(
                    workflow,
                    "Build W12 COMMIT Request",
                    json_value={
                        "source_code": source_code,
                        "cursor_version": 1,
                        "cursor_value": frozen["run_upper_bound"],
                        "committed_run_id": frozen["run_id"],
                    },
                    refs={
                        "Assemble Trusted Acquisition Contract": request,
                        "Acquire Archive and Read Back": replay_downstream,
                        "Run Shared Statement Pipeline": {
                            "run_id": frozen["run_id"],
                            "source_code": source_code,
                            "state": "SUCCEEDED",
                            "receipt_sha256": "c" * 64,
                            "terminal_readback_verified": True,
                        },
                    },
                )
                self.assertTrue(replay_built_commit["ok"], replay_built_commit)
                replay_commit_request = replay_built_commit["output"][0]["json"]
                replay_commit = self.execute_code_node(
                    w12,
                    "Validate Sweep or Commit",
                    json_value=replay_commit_request,
                )
                self.assertTrue(replay_commit["ok"], replay_commit)
                replay_merged_commit = self.execute_code_node(
                    w12,
                    "Merge Commit Evidence Readback",
                    json_value={
                        **replay_commit_request,
                        "terminal_state": "ENUMERATED",
                        "immutable_inventory_json": json.dumps(
                            persisted, separators=(",", ":")
                        ),
                        "pagination_exhausted": True,
                        "cursor_commit_eligible": False,
                        "matched_count": 101,
                    },
                    refs={"Validate Sweep or Commit": replay_commit_request},
                )
                self.assertTrue(replay_merged_commit["ok"], replay_merged_commit)
                replay_proof = self.execute_code_node(
                    w12,
                    "Verify Downstream Persistence Proof",
                    json_value=replay_merged_commit["output"][0]["json"],
                    refs={"Validate Sweep or Commit": replay_commit["output"][0]["json"]},
                )
                self.assertTrue(replay_proof["ok"], replay_proof)
                self.assert_code_error(
                    w12,
                    "Build Cursor CAS Update",
                    "SOURCE_CURSOR_NON_MONOTONIC",
                    json_value={
                        "source_code": source_code,
                        "cursor_version": 1,
                        "cursor_value": frozen["run_upper_bound"],
                        "committed_run_id": frozen["run_id"],
                    },
                    refs={"Verify Downstream Persistence Proof": replay_proof["output"][0]["json"]},
                )

        self.assertEqual(
            sum(
                node["type"] == "n8n-nodes-base.dataTable"
                and node["parameters"].get("dataTableId", {}).get("value") == "finance_source_cursors"
                and node["parameters"].get("operation") == "update"
                for node in w12["nodes"]
            ),
            1,
            "W12 owns the sole source-cursor CAS update",
        )
        for filename, *_ in cases:
            self.assertEqual(
                sum(
                    node["type"] == "n8n-nodes-base.dataTable"
                    and node["parameters"].get("dataTableId", {}).get("value") == "finance_source_cursors"
                    and node["parameters"].get("operation") == "update"
                    for node in self.workflow(filename)["nodes"]
                ),
                0,
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
        self.assertEqual(
            w12["connections"]["Read ENUMERATED Receipt for Commit"]["main"][0][0]["node"],
            "Merge Commit Evidence Readback",
        )
        self.assertEqual(
            w12["connections"]["Merge Commit Evidence Readback"]["main"][0][0]["node"],
            "Verify Downstream Persistence Proof",
        )
        self.assertIn(
            "COMMIT_ARCHIVE_BARRIER_READBACK_MISMATCH",
            w12_nodes["Merge Commit Evidence Readback"]["parameters"]["jsCode"],
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

    def test_executable_generated_fixtures_preserve_zero_one_and_101_paths(self):
        fixture = json.loads((self.ROOT / "integrations" / "n8n" / "disposable" / "generated" / "90-derived-outlook-sweep-core.json").read_text(encoding="utf-8"))

        def exhaust(case):
            context = {
                "fixture_case": case,
                "run_id": f"fixture:{case}",
                "source_code": "FIXTURE",
                "folder_id": "fixture-folder",
                "senders": ["fixture@example.test"],
                "subjects": ["Fixture transaction"],
                "window_start": "2026-08-19T00:00:00.000Z",
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
            }
            return self.execute_code_node(
                fixture,
                "Exhaust Outlook Pagination",
                refs={"Freeze Trusted Cursor Window": context},
            )

        zero = exhaust("zero")
        self.assertTrue(zero["ok"], zero)
        zero_aggregate = self.execute_code_node(
            fixture,
            "Aggregate Exact Window Heartbeat",
            input_items=[item["json"] for item in zero["output"]],
            refs={"Freeze Trusted Cursor Window": {
                "run_id": "fixture:zero",
                "source_code": "FIXTURE",
                "folder_id": "fixture-folder",
                "senders": ["fixture@example.test"],
                "subjects": ["Fixture transaction"],
                "window_start": "2026-08-19T00:00:00.000Z",
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
                "onedrive_parent_id": "fixture-parent",
                "max_messages": 500,
            }},
        )
        self.assertTrue(zero_aggregate["ok"], zero_aggregate)
        zero_context = zero_aggregate["output"][0]["json"]
        self.assertEqual(zero_context["scanned_count"], 0)
        self.assertTrue(zero_context["heartbeat"])
        self.assertEqual(
            {key: zero_context[key] for key in (
                "folder_id", "senders", "subjects", "onedrive_parent_id",
            )},
            {
                "folder_id": "fixture-folder",
                "senders": ["fixture@example.test"],
                "subjects": ["Fixture transaction"],
                "onedrive_parent_id": "fixture-parent",
            },
        )

        one = exhaust("one-no-attachments")
        self.assertTrue(one["ok"], one)
        one_messages = one["output"]
        self.assertEqual(len(one_messages), 1)
        self.assertEqual(one_messages[0]["json"]["attachment_inventory"], [])
        one_aggregate = self.execute_code_node(
            fixture,
            "Aggregate Exact Window Heartbeat",
            input_items=[item["json"] for item in one_messages],
            refs={"Freeze Trusted Cursor Window": {
                "run_id": "fixture:one-no-attachments",
                "source_code": "FIXTURE",
                "senders": ["fixture@example.test"],
                "subjects": ["Fixture transaction"],
                "window_start": "2026-08-19T00:00:00.000Z",
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
                "max_messages": 500,
            }},
        )
        self.assertTrue(one_aggregate["ok"], one_aggregate)
        self.assertEqual(one_aggregate["output"][0]["json"]["matched_count"], 1)

        hundred_one = exhaust("one-hundred-one")
        self.assertTrue(hundred_one["ok"], hundred_one)
        self.assertEqual(len(hundred_one["output"]), 101)
        aggregate = self.execute_code_node(
            fixture,
            "Aggregate Exact Window Heartbeat",
            input_items=[item["json"] for item in hundred_one["output"]],
            refs={"Freeze Trusted Cursor Window": {
                "run_id": "fixture:one-hundred-one",
                "source_code": "FIXTURE",
                "senders": ["fixture@example.test"],
                "subjects": ["Fixture transaction"],
                "window_start": "2026-08-19T00:00:00.000Z",
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
                "max_messages": 500,
            }},
        )
        self.assertTrue(aggregate["ok"], aggregate)
        sweep = {
            **aggregate["output"][0]["json"],
            "immutable_inventory": True,
        }
        shaped = self.execute_code_node(
            fixture,
            "Shape Immutable Message Inventory",
            refs={"Verify Receipt and Return Sweep": sweep},
        )
        self.assertTrue(shaped["ok"], shaped)
        self.assertEqual(len(shaped["output"]), 101)
        listed = self.execute_code_node(
            fixture,
            "List Immutable Message Attachments",
            input_items=[item["json"] for item in shaped["output"]],
        )
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(len(listed["output"]), 101)
        stamped = []
        for parent, placeholder in zip(shaped["output"], listed["output"], strict=True):
            result = self.execute_code_node(
                fixture,
                "Stamp Immutable Attachment IDs",
                input_items=[placeholder["json"]],
                refs={"Shape Immutable Message Inventory": parent["json"]},
            )
            self.assertTrue(result["ok"], result)
            stamped.extend(result["output"])
        self.assertEqual(len(stamped), 101)
        inventory = self.execute_code_node(
            fixture,
            "Aggregate Immutable Archive Inventory",
            input_items=[item["json"] for item in stamped],
            refs={
                "Verify Receipt and Return Sweep": sweep,
                "Shape Immutable Message Inventory": [
                    item["json"] for item in shaped["output"]
                ],
            },
        )
        self.assertTrue(inventory["ok"], inventory)
        result = inventory["output"][0]["json"]
        self.assertEqual(len(result["messages"]), 101)
        self.assertEqual(result["attachment_identity_keys"], [])
        self.assertFalse(result["empty_inventory"])

    def test_executable_restart_rehydrates_exact_inventory_and_resumes_w01(self):
        w12 = self.workflow("12-outlook-message-sweep.json")
        messages = [
            {
                "message_id": f"message-{index:03d}",
                "message": {"id": f"message-{index:03d}", "subject": "Fixture"},
                "attachment_inventory": [{
                    "id": f"attachment-{index:03d}",
                    "name": "statement.pdf",
                }],
                "attachment_ids": [f"attachment-{index:03d}"],
                "attachment_identity_keys": [
                    f"message-{index:03d}:attachment-{index:03d}"
                ],
            }
            for index in range(1, 102)
        ]
        identities = [
            f"message-{index:03d}:attachment-{index:03d}"
            for index in range(1, 102)
        ]
        persisted = json.dumps({
            "messages": messages,
            "attachment_identity_keys": identities,
            "empty_inventory": False,
            "immutable_inventory": True,
            "attachment_ids_verified": True,
        }, separators=(",", ":"))
        receipt = {
            "run_id": "fixture:restart",
            "source_code": "FIXTURE",
            "window_start": "2026-08-19T00:00:00.000Z",
            "run_upper_bound": "2026-08-20T00:00:00.000Z",
            "matched_count": 101,
            "terminal_state": "ENUMERATED",
            "pagination_exhausted": True,
            "cursor_commit_eligible": False,
            "immutable_inventory_json": persisted,
        }
        trusted = {
            key: receipt[key]
            for key in ("run_id", "source_code", "window_start", "run_upper_bound")
        }
        result = self.execute_code_node(
            w12,
            "Return Existing ENUMERATED Receipt",
            json_value=receipt,
            refs={"Freeze Trusted Cursor Window": trusted},
        )
        self.assertTrue(result["ok"], result)
        replay = result["output"][0]["json"]
        self.assertEqual(replay["messages"], messages)
        self.assertEqual(replay["attachment_identity_keys"], identities)
        self.assertEqual(len(replay["messages"]), 101)
        self.assertTrue(replay["replay_noop"])
        self.assertFalse(replay["cursor_commit_eligible"])
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

    def test_w12_initializes_empty_source_cursor_v0_once_with_collision_readback(self):
        w12 = self.workflow("12-outlook-message-sweep.json")
        request = {
            "operation": "INITIALIZE",
            "run_id": "fixture:init",
            "source_code": "EI_AMAZON",
            "config_version": "1",
            "initial_cursor_value": "2026-07-01T00:00:00.000Z",
            "initial_cursor_source": "statement-sources",
            "overlap_seconds": 7200,
        }
        validated = self.execute_code_node(
            w12, "Validate Sweep or Commit", json_value=request
        )
        self.assertTrue(validated["ok"], validated)
        built = self.execute_code_node(
            w12,
            "Build Cursor v0 Initialization",
            input_items=[],
            refs={"Validate Sweep or Commit": validated["output"][0]["json"]},
        )
        self.assertTrue(built["ok"], built)
        pending = built["output"][0]["json"]
        self.assertTrue(pending["initialized"])
        self.assertEqual(pending["cursor_version"], 0)
        self.assertEqual(pending["cursor_value"], request["initial_cursor_value"])
        self.assertEqual(pending["config_version"], "1")

        row = {
            "source_code": "EI_AMAZON",
            "cursor_value": pending["cursor_value"],
            "committed_run_id": "",
            "run_upper_bound": pending["run_upper_bound"],
            "cursor_version": 0,
            "readback_verified": True,
        }
        readback = self.execute_code_node(
            w12,
            "Verify Cursor v0 Initialization Readback",
            input_items=[row],
            refs={"Build Cursor v0 Initialization": pending},
        )
        self.assertTrue(readback["ok"], readback)
        terminal = self.execute_code_node(
            w12,
            "Return Verified Cursor Initialization",
            json_value=readback["output"][0]["json"],
        )
        self.assertTrue(terminal["ok"], terminal)
        self.assertEqual(terminal["output"][0]["json"]["status"], "CURSOR_INITIALIZED")

        existing = self.execute_code_node(
            w12,
            "Build Cursor v0 Initialization",
            input_items=[row],
            refs={"Validate Sweep or Commit": validated["output"][0]["json"]},
        )
        self.assertTrue(existing["ok"], existing)
        self.assertFalse(existing["output"][0]["json"]["initialized"])
        self.assertEqual(existing["output"][0]["json"]["status"], "CURSOR_ALREADY_INITIALIZED")
        existing_terminal = self.execute_code_node(
            w12,
            "Return Existing Cursor Initialization Readback",
            json_value=existing["output"][0]["json"],
        )
        self.assertTrue(existing_terminal["ok"], existing_terminal)

        self.assert_code_error(
            w12,
            "Build Cursor v0 Initialization",
            "SOURCE_CURSOR_INITIALIZATION_COLLISION",
            input_items=[row, row],
            refs={"Validate Sweep or Commit": validated["output"][0]["json"]},
        )
        self.assert_code_error(
            w12,
            "Verify Cursor v0 Initialization Readback",
            "SOURCE_CURSOR_INITIALIZATION_COLLISION",
            input_items=[
                row,
                {
                    **row,
                    "cursor_value": "2026-08-20T00:00:00.000Z",
                    "run_upper_bound": "2026-08-20T00:00:00.000Z",
                    "cursor_version": 1,
                },
            ],
            refs={"Build Cursor v0 Initialization": pending},
        )

        for filename, source_code in (
            ("04-ei-monthly-statement.json", "EI_AMAZON"),
            ("05-wio-monthly-statement.json", "WIO_CREDIT"),
        ):
            workflow = self.workflow(filename)
            nodes = {node["name"]: node for node in workflow["nodes"]}
            initializer = nodes["Initialize Source Cursor via W12"]
            self.assertEqual(
                initializer["parameters"]["workflowInputs"]["value"]["operation"],
                "INITIALIZE",
            )
            self.assertEqual(
                initializer["parameters"]["workflowInputs"]["value"]["initial_cursor_source"],
                "statement-sources",
            )
            self.assertEqual(
                workflow["connections"]["Assemble Trusted Acquisition Contract"]["main"][0][0]["node"],
                "Initialize Source Cursor via W12",
            )
            self.assertEqual(
                workflow["connections"]["Initialize Source Cursor via W12"]["main"][0][0]["node"],
                "Restore Enumeration Request After Cursor Init",
            )
            restored = self.execute_code_node(
                workflow,
                "Restore Enumeration Request After Cursor Init",
                json_value={
                    "status": "CURSOR_INITIALIZED",
                    "cursor_version": 0,
                    "readback_verified": True,
                },
                refs={
                    "Assemble Trusted Acquisition Contract": {
                        "run_id": "fixture:restore",
                        "source_code": source_code,
                        "folder_id": "Inbox/Statements",
                        "senders": ["sender@example.test"],
                        "subjects": ["Statement"],
                        "window_start": "2026-07-01T00:00:00.000Z",
                        "run_upper_bound": "2026-08-20T00:00:00.000Z",
                    }
                },
            )
            self.assertTrue(restored["ok"], restored)
            restored_request = restored["output"][0]["json"]
            self.assertEqual(restored_request["operation"], "ENUMERATE")
            self.assertEqual(restored_request["folder_id"], "Inbox/Statements")
            self.assertEqual(restored_request["senders"], ["sender@example.test"])
            self.assertEqual(
                initializer["parameters"]["workflowInputs"]["value"]["source_code"],
                "={{ $('Assemble Trusted Acquisition Contract').item.json.source_code }}",
            )
            self.assertIn(source_code, workflow["nodes"][1]["parameters"]["jsCode"])

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
        w12 = self.workflow("12-outlook-message-sweep.json")

        class ReceiptTableStub:
            def __init__(self):
                self.rows = {}
                self.reads = 0
                self.writes = 0

            def read(self, key):
                self.reads += 1
                return dict(self.rows[key])

            def write(self, key, row):
                self.writes += 1
                self.rows[key] = dict(row)

        class OneDriveStub:
            def __init__(self):
                self.attachment_calls = []
                self.email_calls = []

            def archive_attachment(self, message_id, attachment_id, sha256):
                self.attachment_calls.append(f"{message_id}:{attachment_id}")
                return {
                    "archive_state": "HASH_VERIFIED",
                    "source_message_id": message_id,
                    "source_attachment_id": attachment_id,
                    "source_sha256": sha256,
                    "onedrive_item_id": f"drive-{attachment_id}",
                }

            def archive_email(self, message_id, sha256):
                self.email_calls.append(message_id)
                return {
                    "archive_state": "HASH_VERIFIED",
                    "source_message_id": message_id,
                    "source_attachment_id": "INLINE_BODY",
                    "source_sha256": sha256,
                    "onedrive_item_id": f"drive-email-{message_id}",
                }

        class CursorCasStub:
            def __init__(self):
                self.row = {
                    "source_code": "FIXTURE",
                    "cursor_version": 0,
                    "cursor_value": "2026-08-19T00:00:00.000Z",
                    "committed_run_id": None,
                }
                self.writes = 0

            def read(self):
                return dict(self.row)

            def compare_and_swap(self, source_code, expected_version, cursor_value, run_id):
                if self.row["source_code"] != source_code or self.row["cursor_version"] != expected_version:
                    raise AssertionError("CAS stub rejected stale version")
                self.row = {
                    "source_code": source_code,
                    "cursor_version": expected_version + 1,
                    "cursor_value": cursor_value,
                    "committed_run_id": run_id,
                }
                self.writes += 1
                return dict(self.row)

        archive_table = ReceiptTableStub()
        email_table = ReceiptTableStub()
        onedrive = OneDriveStub()
        messages = [
            {
                "message_id": f"message-{index:03d}",
                "source_code": "FIXTURE",
                "folder_id": "folder",
                "senders": ["sender@example.test"],
                "subjects": ["Fixture"],
                "onedrive_parent_id": "parent",
                "window_start": "2026-08-19T00:00:00.000Z",
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
                "receivedDateTime": f"2026-08-19T00:00:{index % 60:02d}.000Z",
                "from": {"emailAddress": {"address": "sender@example.test"}},
                "subject": "Fixture",
                "body": {"contentType": "text", "content": f"Body {index}"},
                "attachment_inventory": [{
                    "id": f"attachment-{index:03d}",
                    "name": "statement.pdf",
                    "isInline": False,
                }],
            }
            for index in range(1, 102)
        ]
        identities = [
            f"message-{index:03d}:attachment-{index:03d}"
            for index in range(1, 102)
        ]
        attachment_hashes = {
            message["message_id"]: f"{index:064x}"
            for index, message in enumerate(messages, start=1)
        }
        email_hashes = {
            message["message_id"]: f"{index + 1000:064x}"
            for index, message in enumerate(messages, start=1)
        }
        cursor_cas = CursorCasStub()
        cursor = cursor_cas.read()
        committed_proof = None

        def request():
            return {
                "run_id": "fixture:101",
                "source_code": "FIXTURE",
                "folder_id": "folder",
                "senders": ["sender@example.test"],
                "subjects": ["Fixture"],
                "onedrive_parent_id": "parent",
                "window_start": "2026-08-19T00:00:00.000Z",
                "run_upper_bound": "2026-08-20T00:00:00.000Z",
                "messages": messages,
            }

        for mode in ("all-new", "all-replay"):
            attachment_rows = []
            email_rows = []
            for message in messages:
                message_id = message["message_id"]
                attachment_id = message["attachment_inventory"][0]["id"]
                identity = f"{message_id}:{attachment_id}"
                expanded = self.execute_code_node(
                    w01,
                    "Expand Enumerated Attachment Items",
                    json_value=message,
                )
                self.assertTrue(expanded["ok"], expanded)
                self.assertEqual(len(expanded["output"]), 1)
                attachment = expanded["output"][0]["json"]
                self.assertEqual(attachment["attachment_identity"], identity)

                built = self.execute_code_node(
                    w01,
                    "Build Original Email Evidence",
                    json_value=message,
                )
                self.assertTrue(built["ok"], built)
                email_expected = {
                    "email_evidence_sha256": email_hashes[message_id],
                    "source_message_id": message_id,
                }

                if mode == "all-new":
                    archive_receipt = onedrive.archive_attachment(
                        message_id, attachment_id, attachment_hashes[message_id],
                    )
                    archive_readback = self.execute_code_node(
                        w01,
                        "Verify Enumerated Attachment Archive",
                        json_value={
                            "archive_readback_sha256": attachment_hashes[message_id],
                        },
                        refs={
                            "SHA-256 Enumerated Attachment": {
                                "document_sha256": attachment_hashes[message_id],
                                "source_message_id": message_id,
                                "source_attachment_id": attachment_id,
                            },
                            "Archive Enumerated Attachment in OneDrive": archive_receipt,
                        },
                    )
                    self.assertTrue(archive_readback["ok"], archive_readback)
                    verified_archive = self.execute_code_node(
                        w01,
                        "Verify Enumerated Archive Receipt",
                        json_value=archive_receipt,
                        refs={
                            "Verify Enumerated Attachment Archive":
                                archive_readback["output"][0]["json"],
                        },
                    )
                    self.assertTrue(verified_archive["ok"], verified_archive)
                    archive_row = verified_archive["output"][0]["json"]
                    archive_table.write(identity, archive_receipt)
                    attachment_rows.append(archive_row)

                    email_receipt = onedrive.archive_email(
                        message_id, email_hashes[message_id],
                    )
                    email_readback = self.execute_code_node(
                        w01,
                        "Verify Email Evidence Readback",
                        json_value={
                            "email_readback_sha256": email_hashes[message_id],
                        },
                        refs={
                            "SHA-256 Email Evidence": email_expected,
                            "Archive Email Evidence in OneDrive": email_receipt,
                            "Build Original Email Evidence":
                                built["output"][0]["json"],
                        },
                    )
                    self.assertTrue(email_readback["ok"], email_readback)
                    verified_email = self.execute_code_node(
                        w01,
                        "Verify Durable Email Evidence Receipt",
                        json_value=email_receipt,
                        refs={
                            "Verify Email Evidence Readback":
                                email_readback["output"][0]["json"],
                        },
                    )
                    self.assertTrue(verified_email["ok"], verified_email)
                    email_table.write(message_id, email_receipt)
                    email_rows.append(verified_email["output"][0]["json"])
                else:
                    archive_receipt = archive_table.read(identity)
                    rehydrated_archive = self.execute_code_node(
                        w01,
                        "Rehydrate Enumerated Attachment Archive Pre-Read",
                        json_value=archive_receipt,
                        refs={
                            "Expand Enumerated Attachment Items": attachment,
                        },
                    )
                    self.assertTrue(rehydrated_archive["ok"], rehydrated_archive)
                    verified_archive = self.execute_code_node(
                        w01,
                        "Verify Existing Enumerated Archive Receipt",
                        json_value=rehydrated_archive["output"][0]["json"],
                    )
                    self.assertTrue(verified_archive["ok"], verified_archive)
                    attachment_rows.append(verified_archive["output"][0]["json"])

                    email_receipt = email_table.read(message_id)
                    rehydrated_email = self.execute_code_node(
                        w01,
                        "Rehydrate Email Evidence Archive Pre-Read",
                        json_value=email_receipt,
                        refs={
                            "SHA-256 Email Evidence": email_expected,
                            "Build Original Email Evidence":
                                built["output"][0]["json"],
                            "Convert Email Evidence to File": {},
                        },
                    )
                    self.assertTrue(rehydrated_email["ok"], rehydrated_email)
                    verified_email = self.execute_code_node(
                        w01,
                        "Verify Existing Email Evidence Receipt",
                        json_value=rehydrated_email["output"][0]["json"],
                    )
                    self.assertTrue(verified_email["ok"], verified_email)
                    email_rows.append(verified_email["output"][0]["json"])

            barrier = self.execute_code_node(
                w01,
                "Attachment Verification Barrier",
                refs={
                    "Validate Bounded Source Request": request(),
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
            barrier_result = barrier["output"][0]["json"]
            self.assertEqual(barrier_result["attachments_verified"], 101)
            self.assertEqual(barrier_result["email_evidence_receipts_verified"], 101)
            self.assertEqual(barrier_result["attachment_identity_keys"], identities)
            self.assertFalse(barrier_result["cursor_commit_eligible"])

            if mode == "all-new":
                inventory = {
                    "messages": [{
                        "message_id": message["message_id"],
                        "message": message,
                        "attachment_inventory": message["attachment_inventory"],
                        "attachment_identity_keys": [(
                            f"{message['message_id']}:{message['attachment_inventory'][0]['id']}"
                        )],
                    } for message in messages],
                    "attachment_identity_keys": identities,
                    "empty_inventory": False,
                }
                attached = self.execute_code_node(
                    w12,
                    "Attach Immutable Inventory to Sweep",
                    json_value={
                        **request(),
                        "pagination_exhausted": True,
                        "scanned_count": 101,
                        "matched_count": 101,
                        "heartbeat": False,
                        "cursor_commit_eligible": False,
                    },
                    refs={"Aggregate Immutable Archive Inventory": inventory},
                )
                self.assertTrue(attached["ok"], attached)
                verified = self.execute_code_node(
                    w12,
                    "Verify Attachment Archive Barrier",
                    json_value=barrier_result,
                    refs={"Attach Immutable Inventory to Sweep":
                          attached["output"][0]["json"]},
                )
                self.assertTrue(verified["ok"], verified)
                validation = {
                    **request(),
                    "operation": "COMMIT",
                    "expected_cursor_version": cursor["cursor_version"],
                    "downstream_receipt_sha256": "a" * 64,
                }
                proof = self.execute_code_node(
                    w12,
                    "Verify Downstream Persistence Proof",
                    json_value=verified["output"][0]["json"],
                    refs={"Validate Sweep or Commit": validation},
                )
                self.assertTrue(proof["ok"], proof)
                committed_proof = proof["output"][0]["json"]
                cas = self.execute_code_node(
                    w12,
                    "Build Cursor CAS Update",
                    json_value={
                        "source_code": "FIXTURE",
                        "cursor_version": cursor["cursor_version"],
                        "cursor_value": cursor["cursor_value"],
                    },
                    refs={"Verify Downstream Persistence Proof": committed_proof},
                )
                self.assertTrue(cas["ok"], cas)
                cas_row = cas["output"][0]["json"]
                self.assertEqual(cas_row["prior_cursor_version"], 0)
                self.assertEqual(cas_row["next_cursor_version"], 1)
                cursor = cursor_cas.compare_and_swap(
                    "FIXTURE",
                    cas_row["prior_cursor_version"],
                    request()["run_upper_bound"],
                    request()["run_id"],
                )
                self.assertEqual(cursor["cursor_version"], cas_row["next_cursor_version"])
                readback = self.execute_code_node(
                    w12,
                    "Compare CAS Cursor Readback",
                    json_value=cursor,
                    refs={"Build Cursor CAS Update": cas_row},
                )
                self.assertTrue(readback["ok"], readback)
                terminal = self.execute_code_node(
                    w12,
                    "Return Verified Cursor Commit",
                    json_value={
                        "downstream_receipt_sha256": "a" * 64,
                        "cursor_commit_eligible": True,
                    },
                    refs={"Build Cursor CAS Update": cas_row},
                )
                self.assertTrue(terminal["ok"], terminal)
                self.assertEqual(terminal["output"][0]["json"]["status"], "CURSOR_COMMITTED")
            else:
                self.assertEqual(cursor["cursor_version"], 1)
                self.assertEqual(cursor["cursor_value"], request()["run_upper_bound"])
                self.assertEqual(len(onedrive.attachment_calls), 101)
                self.assertEqual(len(onedrive.email_calls), 101)
                self.assertEqual(archive_table.writes, 101)
                self.assertEqual(email_table.writes, 101)
                self.assertEqual(cursor_cas.writes, 1)
                self.assertTrue(committed_proof)
                replay_verified = self.execute_code_node(
                    w12,
                    "Verify Attachment Archive Barrier",
                    json_value=barrier_result,
                    refs={"Attach Immutable Inventory to Sweep":
                          attached["output"][0]["json"]},
                )
                self.assertTrue(replay_verified["ok"], replay_verified)
                replay_proof = self.execute_code_node(
                    w12,
                    "Verify Downstream Persistence Proof",
                    json_value=replay_verified["output"][0]["json"],
                    refs={"Validate Sweep or Commit": {
                        **request(),
                        "operation": "COMMIT",
                        "expected_cursor_version": cursor["cursor_version"],
                        "downstream_receipt_sha256": "a" * 64,
                    }},
                )
                self.assertTrue(replay_proof["ok"], replay_proof)
                self.assert_code_error(
                    w12,
                    "Build Cursor CAS Update",
                    "SOURCE_CURSOR_NON_MONOTONIC",
                    json_value={
                        "source_code": "FIXTURE",
                        "cursor_version": cursor["cursor_version"],
                        "cursor_value": cursor["cursor_value"],
                        "committed_run_id": cursor["committed_run_id"],
                    },
                    refs={"Verify Downstream Persistence Proof":
                          replay_proof["output"][0]["json"]},
                )

        self.assertEqual(len(archive_table.rows), 101)
        self.assertEqual(len(email_table.rows), 101)
        self.assertEqual(archive_table.writes, 101)
        self.assertEqual(email_table.writes, 101)
        self.assertEqual(cursor_cas.writes, 1)
        self.assertEqual(len(onedrive.attachment_calls), 101)
        self.assertEqual(len(onedrive.email_calls), 101)


if __name__ == "__main__":
    unittest.main()
