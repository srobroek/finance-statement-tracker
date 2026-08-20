import copy
import hashlib
import json
import unittest
from pathlib import Path

from finance_tracker.project_backlog import (
    IMPLEMENTATION_PATH,
    TRANSCRIPT_PATH,
    build_backlog,
    derive_n8n_data_table_column_access,
    load_and_build,
    render_markdown,
    validate_backlog,
)


ROOT = Path(__file__).resolve().parents[1]


class ProjectBacklogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.transcript = json.loads(TRANSCRIPT_PATH.read_text(encoding="utf-8"))
        cls.implementation = json.loads(IMPLEMENTATION_PATH.read_text(encoding="utf-8"))
        cls.payload = load_and_build()

    def test_preserves_every_transcript_id_exactly_once(self) -> None:
        source_ids = [row["id"] for row in self.transcript["requirements"]]
        backlog_ids = [row["id"] for row in self.payload["tasks"]]
        self.assertEqual(set(backlog_ids), set(source_ids))
        self.assertEqual(len(backlog_ids), len(set(backlog_ids)))
        self.assertEqual(len(backlog_ids), 77)

    def test_no_task_is_promoted_to_verified_without_fresh_acceptance(self) -> None:
        self.assertEqual(self.payload["summary"]["verified_count"], 0)
        self.assertNotIn("VERIFIED", {row["status"] for row in self.payload["tasks"]})

    def test_latest_n8n_overrides_are_persistent(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        self.assertIn("Tidy Workflow", " ".join(tasks["N8N-002"]["contradictions"]))
        canvas_acceptance = " ".join(tasks["N8N-002"]["acceptance_criteria"])
        self.assertIn("Canvas Group", canvas_acceptance)
        self.assertIn("zero redundant sticky notes", canvas_acceptance)
        self.assertIn("browser acceptance", canvas_acceptance)
        self.assertIn("duplicate-cluster audit", canvas_acceptance)
        self.assertIn("From list", canvas_acceptance)
        self.assertIn("trivial one-node or pass-through wrappers", canvas_acceptance)
        self.assertIn("exactly 42 semantic Canvas Groups", canvas_acceptance)
        self.assertIn("22 triggers, six From-list delegated nodes and four blocked terminals", canvas_acceptance)
        self.assertIn("exactly 21 workflow-overview stickies", canvas_acceptance)
        self.assertIn("exactly two byte-deterministic", canvas_acceptance.lower())
        self.assertIn("Finance/Shared", canvas_acceptance)
        self.assertIn("Global/Shared", canvas_acceptance)
        self.assertIn("exactly one typed Workflow Parameters", canvas_acceptance)
        self.assertIn("without assuming Enterprise Variables", canvas_acceptance)
        self.assertIn("From list", " ".join(tasks["N8N-008"]["contradictions"]))
        agent_text = " ".join(tasks["AGENT-005"]["contradictions"])
        self.assertIn("n8n-nodes-prodex@0.5.1", agent_text)
        self.assertIn("@ggomez91npm/n8n-nodes-claude-code@0.8.0", agent_text)
        self.assertIn("disposable", tasks["AGENT-005"]["next_action"].lower())
        hierarchy = tasks["N8N-010"]
        self.assertIn("Finance", hierarchy["requirement"])
        self.assertIn("Global", hierarchy["requirement"])
        self.assertEqual(hierarchy["priority"], "P3")
        self.assertEqual(hierarchy["dependencies"], ["N8N-006", "AUTO-001"])
        self.assertIn("N8N-011", hierarchy["related_tasks"])
        self.assertIn("N8N-002", hierarchy["related_tasks"])
        self.assertEqual(hierarchy["implementation_state"], "DESIGN_RECORDED_UNIMPLEMENTED")
        self.assertNotIn("MICROSOFT_RESTART_PERSISTENCE_PROOF_REQUIRED", hierarchy["blockers"])
        hierarchy_acceptance = " ".join(hierarchy["acceptance_criteria"])
        self.assertIn("10/3/8/0", hierarchy_acceptance)
        self.assertIn("All 20 Execute Sub-workflow", hierarchy_acceptance)
        self.assertIn("Finance/Shared", hierarchy_acceptance)
        self.assertIn("second hierarchy application", hierarchy_acceptance)
        self.assertIn("Canvas Group coverage", hierarchy_acceptance)
        self.assertIn("browser readback", hierarchy_acceptance)
        minimization = tasks["N8N-011"]
        minimization_acceptance = " ".join(minimization["acceptance_criteria"])
        self.assertIn("all 15 tables and every column", minimization_acceptance)
        self.assertIn("producer", minimization_acceptance)
        self.assertIn("rollback", minimization_acceptance)

    def test_data_table_machine_inventory_is_schema_valid_exact_and_complete(self) -> None:
        import jsonschema

        contract = json.loads(
            (ROOT / "integrations" / "n8n" / "data-tables.json").read_text(encoding="utf-8")
        )
        inventory = json.loads((
            ROOT
            / "docs"
            / "project-audit"
            / "n8n-data-table-column-disposition-2026-08-20.json"
        ).read_text(encoding="utf-8"))
        schema = json.loads((
            ROOT
            / "docs"
            / "project-audit"
            / "n8n-data-table-column-disposition.schema.json"
        ).read_text(encoding="utf-8"))
        jsonschema.validate(inventory, schema)
        self.assertEqual(len(contract["tables"]), 15)
        expected = {
            (table["name"], column)
            for table in contract["tables"]
            for column in table["columns"]
        }
        observed = [(row["table"], row["column"]) for row in inventory["rows"]]
        self.assertEqual(len(observed), len(set(observed)))
        self.assertEqual(set(observed), expected)
        self.assertEqual(inventory["observability_owners"], {
            "generic_execution_and_failure": "n8n",
            "protected_route_access": "Cloudflare",
            "agent_traces": "optional Langfuse sanitized traces only",
        })
        self.assertEqual(inventory["resolver_contract"]["subworkflows"], [
            "Resolve Finance Source and Runtime Config",
            "Resolve AI Policy and Output Contract",
        ])
        self.assertEqual(inventory["resolver_contract"]["node_type"], "n8n-nodes-base.set")
        self.assertTrue(inventory["resolver_contract"]["content_addressed"])
        self.assertFalse(inventory["resolver_contract"]["caller_mutable"])
        self.assertEqual(inventory["resolver_contract"]["placements"], {
            "Resolve Finance Source and Runtime Config": "Finance/Shared",
            "Resolve AI Policy and Output Contract": "Global/Shared",
        })
        local_parameters = inventory["resolver_contract"]["callable_workflow_parameters"]
        self.assertEqual(local_parameters["local_node"], "exactly one Workflow Parameters Edit Fields node")
        self.assertEqual(set(local_parameters["layers"]), {"input", "config", "params"})
        self.assertFalse(local_parameters["generic_parameter_table_allowed"])
        self.assertEqual(
            local_parameters["downstream_expression"],
            "$('Workflow Parameters').first().json.<field>",
        )
        self.assertIn("credential", local_parameters["forbidden_override_fields"])
        self.assertFalse(
            inventory["resolver_contract"]["secret_runtime_owners"]["enterprise_variables_assumed"]
        )
        self.assertFalse(any(
            "finance_operation_receipts" in row["replacement"]
            for row in inventory["rows"]
        ))
        text = json.dumps(inventory)
        self.assertNotIn("finance_period_closes", {row["table"] for row in inventory["rows"]})
        self.assertNotIn("FIXED_POSTGRES_IF_PROVEN", text)
        self.assertNotIn("fixed operational Postgres circuit", text)
        expected_target_counts = {
            "finance_ingestion_state": 9,
            "finance_documents": 20,
            "finance_actual_batches": 18,
            "finance_ai_reviews": 9,
        }
        plan = (
            ROOT / "docs" / "project-audit" / "n8n-data-table-minimization-plan-2026-08-20.md"
        ).read_text(encoding="utf-8")
        current_binding_keys = {
            (
                access["workflow_code"], access["workflow_path"],
                access["data_table_node_id"], access["operation"],
                access["binding_kind"], access["binding_path"], access["binding_sha256"],
            )
            for source_row in inventory["rows"]
            for access in source_row["workflow_access"]
        }
        for target, count in expected_target_counts.items():
            target_rows = inventory["target_schemas"][target]
            self.assertEqual(len(target_rows), count)
            names = [row["name"] for row in target_rows]
            self.assertEqual(len(names), len(set(names)), target)
            for row in target_rows:
                self.assertTrue(row["constraints"], f"{target}.{row['name']}")
                self.assertTrue(row["authoritative_owner"], f"{target}.{row['name']}")
                self.assertTrue(row["source_lineage"], f"{target}.{row['name']}")
                self.assertTrue(row["producers"], f"{target}.{row['name']}")
                self.assertTrue(row["consumers"], f"{target}.{row['name']}")
                self.assertEqual(
                    len(row["producers"]),
                    len({json.dumps(item, sort_keys=True) for item in row["producers"]}),
                )
                self.assertEqual(
                    len(row["consumers"]),
                    len({json.dumps(item, sort_keys=True) for item in row["consumers"]}),
                )
                self.assertTrue(row["rationale"], f"{target}.{row['name']}")
                for binding in row["producers"] + row["consumers"]:
                    self.assertTrue((ROOT / binding["workflow_path"]).exists())
                    if binding["status"] == "current source binding":
                        self.assertIn(
                            (
                                binding["workflow_code"], binding["workflow_path"],
                                binding["data_table_node_id"], binding["operation"],
                                binding["binding_kind"], binding["binding_path"],
                                binding["binding_sha256"],
                            ),
                            current_binding_keys,
                        )
                    else:
                        self.assertEqual(binding["status"], "planned target binding; not implemented")
                        self.assertTrue(binding["data_table_node_id"].startswith("planned-n8n011-"))
                        self.assertEqual(binding["binding_sha256"], "0" * 64)
            self.assertIn(f"`{target}`", plan)
            self.assertIn(f"{target}` ({count})", plan)
        document_names = {row["name"] for row in inventory["target_schemas"]["finance_documents"]}
        self.assertIn("archive_state", document_names)
        self.assertIn("processing_state", document_names)

        actual_columns = {
            row["name"]: row
            for row in inventory["target_schemas"]["finance_actual_batches"]
        }
        expected_lineage = {
            "account_id": {"finance_actual_verifications.account_id"},
            "period_start": {"finance_actual_verifications.period_start"},
            "period_end": {"finance_actual_verifications.period_end"},
            "verification_artifact_item_id": {"workflow:WF20.Read Back Immutable Verification Artifact.id"},
            "verification_artifact_sha256": {
                "finance_actual_verifications.expected_payload_sha256",
                "finance_actual_verifications.observed_payload_sha256",
                "finance_actual_verifications.invariants_passed",
                "workflow:WF20.SHA-256 Immutable Verification Artifact Readback.value",
            },
        }
        for column, lineage in expected_lineage.items():
            self.assertEqual(
                {row["source"] for row in actual_columns[column]["source_lineage"]},
                lineage,
            )
            self.assertTrue(any(
                binding["workflow_code"] == "WF20"
                and binding["status"] == "planned target binding; not implemented"
                for binding in actual_columns[column]["producers"]
            ), column)

    def test_data_table_inventory_matches_exact_current_workflow_access(self) -> None:
        inventory = json.loads((
            ROOT / "docs" / "project-audit" / "n8n-data-table-column-disposition-2026-08-20.json"
        ).read_text(encoding="utf-8"))
        contract = json.loads(
            (ROOT / "integrations" / "n8n" / "data-tables.json").read_text(encoding="utf-8")
        )
        expected_access = derive_n8n_data_table_column_access(
            ROOT / "integrations" / "n8n" / "workflows",
            contract,
        )
        observed_rows = {(row["table"], row["column"]): row for row in inventory["rows"]}
        self.assertEqual(set(observed_rows), set(expected_access))
        workflow_cache = {}
        for key, expected in expected_access.items():
            row = observed_rows[key]
            self.assertEqual(row["workflow_access"], expected, f"{key[0]}.{key[1]}")
            self.assertTrue(row["rationale"], f"{key[0]}.{key[1]}")
            for binding in row["workflow_access"]:
                workflow = workflow_cache.setdefault(
                    binding["workflow_path"],
                    json.loads((ROOT / binding["workflow_path"]).read_text(encoding="utf-8")),
                )
                nodes = {str(node["id"]): node for node in workflow["nodes"]}
                data_node = nodes[binding["data_table_node_id"]]
                binding_node = nodes[binding["binding_node_id"]]
                self.assertEqual(data_node["name"], binding["data_table_node_name"])
                self.assertEqual(binding_node["name"], binding["binding_node_name"])
                self.assertEqual(data_node["parameters"]["operation"], binding["operation"])
                value = binding_node
                for part in binding["binding_path"].split("."):
                    value = value[int(part)] if isinstance(value, list) else value[part]
                digest = hashlib.sha256(
                    json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                self.assertEqual(digest, binding["binding_sha256"])
                if binding["binding_kind"] == "schema_definition":
                    self.assertEqual(value["name"], row["column"])
                elif binding["binding_kind"] == "filter":
                    self.assertEqual(value["keyName"], row["column"])
                elif binding["binding_kind"] == "column_value":
                    self.assertTrue(binding["binding_path"].endswith(f".{row['column']}"))

        all_access = [entry for row in inventory["rows"] for entry in row["workflow_access"]]
        self.assertEqual(sum(row["access_kind"] == "write" for row in all_access), 279)
        for entry in all_access:
            if entry["access_kind"] == "write":
                self.assertEqual(entry["binding_kind"], "column_value")
                self.assertIn("parameters.columns.value.", entry["binding_path"])
            elif entry["binding_kind"] == "filter":
                self.assertEqual(entry["access_kind"], "read")
                self.assertIn("parameters.filters.conditions.", entry["binding_path"])

        expected_workflows = {
            "finance_source_contracts": {"WF02", "WF04", "WF05", "WF09", "WF19"},
            "finance_source_cursors": {"WF12", "WF19"},
            "finance_acquisition_receipts": {"WF12", "WF19"},
            "finance_archive_receipts": {"WF01", "WF19"},
            "finance_document_operations": {"WF01", "WF11", "WF13", "WF19"},
            "finance_pipeline_runs": {"WF03", "WF04", "WF05", "WF19"},
            "finance_actual_outbox": {"WF03", "WF17", "WF19", "WF20"},
            "finance_actual_verifications": {"WF19", "WF20"},
            "finance_reconciliations": {"WF03", "WF19"},
            "finance_config_versions": {"WF19"},
            "finance_provider_circuits": {"WF01", "WF09", "WF12", "WF16", "WF19"},
            "finance_execution_failures": {"WF16", "WF19"},
            "finance_mcp_requests": {"WF10", "WF19"},
            "finance_agent_jobs": {"WF09", "WF19"},
            "finance_ai_policy_contracts": {"WF09", "WF19"},
        }
        for table, expected in expected_workflows.items():
            table_access = [
                access
                for row in inventory["rows"]
                if row["table"] == table
                for access in row["workflow_access"]
            ]
            self.assertEqual({row["workflow_code"] for row in table_access}, expected, table)
        source_wf19 = [
            access
            for row in inventory["rows"]
            if row["table"] == "finance_source_contracts"
            for access in row["workflow_access"]
            if access["workflow_code"] == "WF19"
        ]
        self.assertEqual(
            {(row["operation"], row["access_kind"]) for row in source_wf19},
            {("create", "schema")},
        )
        self.assertEqual(
            {row["disposition"] for row in inventory["rows"] if row["table"] == "finance_provider_circuits"},
            {"REMOVE_TO_N8N_RETRY_POLICY"},
        )
        self.assertTrue(all(
            row["disposition"] == "MOVE_ONEDRIVE_VERIFICATION_ARTIFACT"
            for row in inventory["rows"]
            if row["table"] == "finance_actual_verifications"
        ))

    def test_implementation_audit_is_mapped_to_relevant_requirements(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        self.assertIn("finance_tracker/transaction_semantics.py", tasks["ACTUAL-010"]["evidence_paths"])
        self.assertIn("config/actual-note-contract.json", tasks["ACTUAL-011"]["evidence_paths"])
        self.assertIn("services/codex-agent-runner", tasks["AGENT-003"]["evidence_paths"])
        self.assertNotIn("CURRENT_TEST_FAILURES", tasks["AGENT-003"]["blockers"])
        self.assertIn("CODEX_SUBSCRIPTION_AUTH_COMPATIBILITY_BLOCKED", tasks["AGENT-003"]["blockers"])

    def test_every_open_task_has_owner_validator_and_acceptance_evidence_state(self) -> None:
        for task in self.payload["tasks"]:
            if task["status"] == "SUPERSEDED":
                continue
            self.assertTrue(task["owner_workstream"], task["id"])
            self.assertTrue(task["acceptance_validator"], task["id"])
            self.assertTrue(task["acceptance_criteria"], task["id"])
            self.assertIn("verification_state", task, task["id"])
            self.assertIn("evidence_paths", task, task["id"])
            self.assertIn("tests", task, task["id"])
            self.assertTrue(task["blockers"], task["id"])

    def test_current_snapshot_is_honest_about_runtime_boundaries(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        commits = self.payload["progress_overlay"]["evidence_commits"]
        self.assertEqual(
            self.payload["progress_overlay"]["evidence_commit"],
            "6a4595caf8e9da0970e93aea8c32cc8fa0e4dcda",
        )
        self.assertIn("3484c8261512b92a217d3c8c12ba777b41ac8386", commits)
        self.assertIn("da6b0c128210b2cd44a7a2c5a120b08e942de9ce", commits)
        self.assertIn("3a6acc625abe99d977d2b225eaae63f8ffe02c65", commits)
        self.assertIn("00491aae2ab43c486f3a9b4a62ce3ba5e63032f6", commits)
        self.assertIn("c8d4f7ce984ec0107846b2c7aa398cb1141caf39", commits)
        self.assertIn("From list", render_markdown(self.payload))
        self.assertIn("EMAIL_TO_PDF_RENDERER_REQUIRED", tasks["N8N-003"]["blockers"])
        self.assertIn("ADCB_ISSUER_BALANCE_CONTRADICTION", tasks["ACTUAL-021"]["blockers"])
        self.assertIn("DUPLICATED_FINANCE_EVIDENCE_PATHS", tasks["DOC-001"]["blockers"])
        self.assertNotIn("RUNTIME_COMMIT_DRIFT_RECONCILIATION_REQUIRED", tasks["PLATFORM-006"]["blockers"])
        self.assertIn("WF23_EXACT_CLEANUP_REQUIRED", tasks["N8N-003"]["blockers"])
        self.assertIn(
            "MICROSOFT_RESTART_PERSISTENCE_PROOF_REQUIRED",
            tasks["N8N-003"]["blockers"],
        )
        self.assertNotIn(
            "OAUTH_WORKFLOW_BINDING_AND_READBACK_REQUIRED",
            tasks["N8N-003"]["blockers"],
        )
        self.assertIn(
            "ONEDRIVE_FINANCE_EVIDENCE_ROOT_LIVE_RUN_REQUIRED",
            tasks["DOC-001"]["blockers"],
        )
        self.assertIn("22 workflows", tasks["N8N-006"]["live_readback"])
        self.assertIn("zero active", tasks["N8N-006"]["live_readback"])
        self.assertIn("external", tasks["N8N-003"]["live_readback"].lower())
        self.assertIn("no n8n-only restart", tasks["N8N-003"]["live_readback"])
        self.assertIn(
            "/opt/disposable/finance-n8n/20260819155134/receipts/microsoft-oauth-8149f42f2694-20260820T011501Z-failure.json",
            tasks["N8N-003"]["evidence_paths"],
        )
        self.assertIn(
            "d85a54134152493150b2618beffa25434e79e4160001f1d8ebdf14ce1ca88148",
            " ".join(tasks["N8N-003"]["tests"]),
        )
        self.assertIn("WF23_EXACT_CLEANUP_RECEIPT_REQUIRED", tasks["N8N-012"]["blockers"])
        self.assertIn("later external cleanup receipt", tasks["N8N-012"]["live_readback"])
        self.assertIn("WF20_VERIFICATION_ARTIFACT_ATOMICITY_REQUIRED", tasks["N8N-011"]["blockers"])
        self.assertIn("FOUR_TABLE_TARGET_INDEPENDENT_REVIEW_REQUIRED", tasks["N8N-011"]["blockers"])
        self.assertNotIn(
            "RUNTIME_COMMIT_DRIFT_RECONCILIATION_REQUIRED",
            tasks["ACTUAL-019"]["blockers"],
        )
        self.assertNotIn(
            "OAUTH_WORKFLOW_BINDING_AND_READBACK_REQUIRED",
            tasks["AUTO-001"]["blockers"],
        )
        self.assertEqual(tasks["AGENT-005"]["status"], "PARTIAL")

    def test_cleanup_is_the_hierarchy_and_wf22_critical_path(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        self.assertIn("N8N-012", tasks["N8N-003"]["dependencies"])
        self.assertIn("N8N-003", tasks["DOC-001"]["dependencies"])
        self.assertIn("DOC-001", tasks["N8N-005"]["dependencies"])
        self.assertIn("N8N-005", tasks["N8N-011"]["dependencies"])
        self.assertIn("N8N-011", tasks["N8N-006"]["dependencies"])
        self.assertIn("N8N-006", tasks["AUTO-001"]["dependencies"])
        self.assertIn("AUTO-001", tasks["N8N-010"]["dependencies"])
        self.assertNotIn("N8N-010", tasks["DOC-001"]["dependencies"])
        ranks = {row["id"]: row["rank"] for row in self.payload["ordered_executable_queue"]}
        sequence = ["N8N-012", "N8N-003", "DOC-001", "N8N-005", "N8N-011", "N8N-006", "AUTO-001", "N8N-010"]
        for before, after in zip(sequence, sequence[1:]):
            self.assertLess(ranks[before], ranks[after], f"{before} before {after}")

    def test_every_relative_evidence_path_exists(self) -> None:
        missing = []
        for task in self.payload["tasks"]:
            for evidence_path in task["evidence_paths"]:
                if evidence_path.startswith("/"):
                    continue
                if not (ROOT / evidence_path).exists():
                    missing.append((task["id"], evidence_path))
        self.assertEqual(missing, [])

    def test_stale_baseline_is_historical_and_status_is_current(self) -> None:
        historical_plan = (ROOT / "docs" / "end-to-end-project-plan-2026-08-19.md").read_text(encoding="utf-8")
        current_status = (ROOT / "docs" / "project-audit" / "implementation-status.md").read_text(encoding="utf-8")
        self.assertIn("Historical architecture baseline", historical_plan)
        self.assertIn("non-authoritative for current execution", historical_plan)
        self.assertIn("Historical point-in-time baseline (2026-08-19)", historical_plan)
        self.assertNotIn("Current authoritative baseline", historical_plan)
        self.assertIn("Audited committed baseline", current_status)
        stale = ("f4436d8", "14 passed, 2 failed", "14/16 passing", "35/36 passing")
        for marker in stale:
            self.assertNotIn(marker, current_status)
            self.assertNotIn(marker, json.dumps(self.implementation))

    def test_generator_rejects_superseded_evidence(self) -> None:
        implementation = copy.deepcopy(self.implementation)
        implementation["items"][0]["tests"].append("Codex runner: 14 passed, 2 failed")
        with self.assertRaisesRegex(ValueError, "superseded evidence"):
            build_backlog(self.transcript, implementation)

    def test_superseded_notion_requirement_is_not_queued(self) -> None:
        tasks = {row["id"]: row for row in self.payload["tasks"]}
        self.assertEqual(tasks["DOCS-004"]["status"], "SUPERSEDED")
        queued = {row["id"] for row in self.payload["ordered_executable_queue"]}
        self.assertNotIn("DOCS-004", queued)

    def test_validator_rejects_unknown_dependency(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["tasks"][0]["dependencies"].append("ACTUAL-999")
        errors = validate_backlog(payload, {row["id"] for row in self.transcript["requirements"]})
        self.assertTrue(any("unknown dependencies" in error for error in errors))

    def test_validator_rejects_dependency_cycles_and_related_overlap(self) -> None:
        payload = copy.deepcopy(self.payload)
        tasks = {task["id"]: task for task in payload["tasks"]}
        tasks["N8N-012"]["dependencies"] = ["N8N-010"]
        errors = validate_backlog(payload, {row["id"] for row in self.transcript["requirements"]})
        self.assertTrue(any("dependency cycle" in error for error in errors))

        payload = copy.deepcopy(self.payload)
        task = payload["tasks"][0]
        task["related_tasks"] = list(task["dependencies"])
        errors = validate_backlog(payload, {row["id"] for row in self.transcript["requirements"]})
        self.assertTrue(any("dependencies and related_tasks overlap" in error for error in errors))

    def test_checked_in_dependency_graph_is_acyclic(self) -> None:
        errors = validate_backlog(
            self.payload,
            {row["id"] for row in self.transcript["requirements"]},
        )
        self.assertFalse(any("dependency cycle" in error for error in errors))

    def test_validator_rejects_unproven_verified_status(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["tasks"][0]["status"] = "VERIFIED"
        payload["tasks"][0]["evidence_paths"] = []
        payload["tasks"][0]["live_readback"] = ""
        payload["tasks"][0]["last_verified"] = None
        errors = validate_backlog(payload, {row["id"] for row in self.transcript["requirements"]})
        self.assertTrue(any("VERIFIED requires" in error for error in errors))

    def test_render_is_deterministic(self) -> None:
        first = render_markdown(self.payload)
        second = render_markdown(load_and_build())
        self.assertEqual(first, second)

    def test_checked_in_backlog_matches_generator(self) -> None:
        checked_in = json.loads((ROOT / "config" / "project-backlog.json").read_text(encoding="utf-8"))
        self.assertEqual(checked_in, self.payload)

    def test_generated_payload_passes_internal_validator(self) -> None:
        errors = validate_backlog(
            self.payload,
            {row["id"] for row in self.transcript["requirements"]},
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
