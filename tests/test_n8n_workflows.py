from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOWS = N8N / "workflows"
ERROR_WORKFLOW_ID = "10000000-0000-4000-8000-000000000016"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def git_canonical_sha256(path: Path) -> str:
    """Hash the LF bytes that a normal Git checkout exposes on Linux."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class N8nWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_json(N8N / "pipeline-registry.json")
        cls.workflows = {
            path.name: load_json(path) for path in sorted(WORKFLOWS.glob("*.json"))
        }
        cls.tables = load_json(N8N / "data-tables.json")
        cls.fixtures = load_json(N8N / "resilience-fixtures.json")

    def workflow(self, filename: str) -> dict:
        return self.workflows[filename]

    def nodes(self, filename: str) -> dict[str, dict]:
        return {node["name"]: node for node in self.workflow(filename)["nodes"]}

    def test_registry_maps_every_existing_codex_automation(self) -> None:
        automations = load_json(ROOT / "config" / "codex-automations.json")
        mapped = {
            row["replaces_codex_automation"]
            for row in self.registry["workflows"]
            if row.get("replaces_codex_automation")
        }
        self.assertEqual(mapped, {row["id"] for row in automations["automations"]})

    def test_registry_is_postgres_spec_only_without_execution_claims(self) -> None:
        self.assertEqual(self.registry["schema_version"], 2)
        self.assertEqual(self.registry["n8n_version"], "2.36.2")
        self.assertEqual(
            self.registry["deployment_mode"], "regular-postgres-external-runners"
        )
        self.assertEqual(self.registry["contract_status"], "SPEC_ONLY")
        self.assertEqual(set(self.registry["execution_evidence"].values()), {False})
        self.assertTrue(
            all(row["status"] == "SPEC_ONLY" for row in self.registry["workflows"])
        )

    def test_registry_and_workflow_exports_are_bijective(self) -> None:
        expected = {row["file"] for row in self.registry["workflows"]}
        self.assertEqual(expected, set(self.workflows))
        codes = {row["code"] for row in self.registry["workflows"]}
        exported = {
            workflow["meta"]["financeWorkflowCode"]
            for workflow in self.workflows.values()
        }
        self.assertEqual(codes, exported)

    def test_mcp_is_disabled_instance_wide_and_facade_is_bounded(self) -> None:
        mcp = self.registry["mcp"]
        self.assertFalse(mcp["instance_mcp_enabled"])
        self.assertEqual(mcp["facade_workflow_code"], "FINANCE_MCP_FACADE")
        self.assertEqual(
            set(mcp["allowed_operation_codes"]),
            {"finance.status", "artifact.submit_reviewed", "document.request"},
        )
        exposed = [row for row in self.registry["workflows"] if row["mcp_exposed"]]
        self.assertEqual([row["code"] for row in exposed], ["FINANCE_MCP_FACADE"])
        facade = self.workflow("15-finance-mcp-facade.json")
        self.assertEqual(
            {node["type"] for node in facade["nodes"]},
            {
                "@n8n/n8n-nodes-langchain.mcpTrigger",
                "@n8n/n8n-nodes-langchain.toolWorkflow",
            },
        )
        self.assertEqual(
            set(facade["meta"]["allowedOperationCodes"]),
            set(mcp["allowed_operation_codes"]),
        )
        raw = json.dumps(facade).casefold()
        for field in mcp["caller_forbidden_fields"]:
            self.assertNotIn(f"$fromai('{field}'", raw)

    def test_adcb_has_no_recurring_n8n_pipeline(self) -> None:
        scheduled_sources = {
            row.get("source") for row in self.registry["workflows"] if row["schedule"]
        }
        self.assertNotIn("ADCB_CASHBACK", scheduled_sources)
        self.assertIn(
            "ADCB_CASHBACK",
            {row["source"] for row in self.registry["retired_or_not_migrated"]},
        )

    def test_workflow_exports_are_inactive_sanitized_and_fail_closed(self) -> None:
        forbidden_types = {"n8n-nodes-base.executeCommand", "n8n-nodes-base.ssh"}
        forbidden_markers = (
            "13393666", "sjor2908", "actual_password", "cashback_ingest_token",
            "172.20.10.20", "notion", "$env", "gpt-5-mini", "lmchatopenai",
            "financetransform", "unlockifprotected",
        )
        for filename, workflow in self.workflows.items():
            with self.subTest(workflow=filename):
                self.assertFalse(workflow["active"])
                self.assertTrue(workflow["id"] and workflow["name"])
                self.assertTrue(workflow["nodes"])
                self.assertEqual(workflow["meta"]["migrationStatus"], "SPEC_ONLY")
                settings = workflow["settings"]
                self.assertEqual(settings["timezone"], "Asia/Dubai")
                self.assertEqual(settings["saveDataSuccessExecution"], "none")
                self.assertEqual(settings["saveDataErrorExecution"], "none")
                if filename == "16-operations-error-handler.json":
                    self.assertNotIn("errorWorkflow", settings)
                else:
                    self.assertEqual(settings["errorWorkflow"], ERROR_WORKFLOW_ID)
                types = {node["type"] for node in workflow["nodes"]}
                self.assertFalse(types & forbidden_types)
                raw = json.dumps(workflow).casefold()
                for marker in forbidden_markers:
                    self.assertNotIn(marker, raw)

    def test_custom_node_contract_is_narrow_and_frozen(self) -> None:
        contract = self.registry["custom_nodes"]
        self.assertEqual((contract["package"], contract["version"]), ("n8n-nodes-finance", "0.1.0"))
        expected = contract["node_types"]
        seen: set[tuple[str, str]] = set()
        for workflow in self.workflows.values():
            for node in workflow["nodes"]:
                if not node["type"].startswith("n8n-nodes-finance."):
                    continue
                short = node["type"].rsplit(".", 1)[1]
                params = node.get("parameters", {})
                self.assertIn(short, expected)
                self.assertIn(params["operation"], expected[short])
                allowed = {"operation", "readShape"} if short == "actualBudget" else {"operation"}
                self.assertLessEqual(set(params), allowed)
                if "readShape" in params:
                    self.assertEqual(params["operation"], "read")
                    self.assertIn(params["readShape"], {"accounts", "categories", "transactionsByImportedIds"})
                if short == "actualBudget":
                    self.assertIn("actualBudgetApi", node.get("credentials", {}))
                if short == "financePdf" and params["operation"] == "unlock":
                    self.assertIn("financeStatementPassword", node.get("credentials", {}))
                seen.add((short, params["operation"]))
        for short, operations in expected.items():
            for operation in operations:
                if short == "actualBudget" and operation in {"doctor", "read"}:
                    continue
                self.assertIn((short, operation), seen)

    def test_pdf_text_extraction_stays_inside_fixed_finance_node(self) -> None:
        for filename in ("03-shared-statement-pipeline.json", "14-local-pdf-extraction.json"):
            nodes = self.workflow(filename)["nodes"]
            pdf_ops = [
                node["parameters"]["operation"]
                for node in nodes
                if node["type"] == "n8n-nodes-finance.financePdf"
            ]
            self.assertEqual(pdf_ops[:3], ["validate", "unlock", "profile"])
            self.assertFalse(any(
                node["type"] == "n8n-nodes-base.extractFromFile"
                and node.get("parameters", {}).get("operation") == "pdf"
                for node in nodes
            ))

    def test_data_table_contract_is_postgres_and_schema_valid(self) -> None:
        self.assertEqual(self.tables["storage"], "n8n-data-tables-on-postgres")
        self.assertEqual(self.tables["contract_status"], "SPEC_ONLY")
        schema = load_json(N8N / "data-tables.schema.json")
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema:
            jsonschema.validators.validator_for(schema).check_schema(schema)
            jsonschema.validate(self.tables, schema)
        names = {row["name"] for row in self.tables["tables"]}
        self.assertTrue({
            "finance_source_contracts", "finance_source_cursors",
            "finance_acquisition_receipts", "finance_archive_receipts",
            "finance_document_operations", "finance_pipeline_runs",
            "finance_actual_outbox", "finance_actual_verifications",
            "finance_reconciliations", "finance_config_versions",
            "finance_provider_circuits", "finance_execution_failures",
            "finance_mcp_requests", "finance_agent_jobs", "finance_ai_policy_contracts",
        }.issubset(names))

    def test_outbox_holds_only_pointer_hash_and_state_metadata(self) -> None:
        table = next(row for row in self.tables["tables"] if row["name"] == "finance_actual_outbox")
        columns = set(table["columns"])
        self.assertTrue({"artifact_item_id", "artifact_etag", "payload_sha256", "config_version", "parser_version", "state"}.issubset(columns))
        self.assertFalse({"transactions", "transaction", "payload_json", "statement_rows"} & columns)
        self.assertEqual(table["allowed_states"], ["PREPARED", "ACTUAL_OBSERVED", "VERIFIED", "COMMITTED", "FAILED"])

    def test_document_state_machine_marks_plaintext_ephemeral(self) -> None:
        table = next(row for row in self.tables["tables"] if row["name"] == "finance_document_operations")
        self.assertTrue({
            "RECEIVED", "VALIDATED", "DECRYPTED_EPHEMERAL", "EXTRACTED_EPHEMERAL",
            "SCHEMA_VALIDATED", "READY_FOR_PARSE", "COMMITTED", "QUARANTINED",
            "UNSUPPORTED", "PASSWORD_FAILED",
        }.issubset(set(table["allowed_states"])))
        self.assertEqual(table["idempotency_key"], ["source_sha256", "document_profile", "requested_schema_version"])

    def test_outlook_sweep_freezes_window_exhausts_and_returns_one_heartbeat(self) -> None:
        workflow = self.workflow("12-outlook-message-sweep.json")
        nodes = self.nodes("12-outlook-message-sweep.json")
        outlook = nodes["Exhaust Outlook Pagination"]
        self.assertTrue(outlook["parameters"]["returnAll"])
        self.assertTrue(outlook["alwaysOutputData"])
        code = nodes["Freeze Trusted Cursor Window"]["parameters"]["jsCode"] + nodes["Aggregate Exact Window Heartbeat"]["parameters"]["jsCode"]
        for term in ("run_upper_bound", "pagination_exhausted:true", "scanned_count", "heartbeat", "received>=start&&received<upper"):
            self.assertIn(term, code)
        self.assertTrue(workflow["meta"]["aggregateOutputAlwaysOne"])
        self.assertTrue(workflow["meta"]["cursorMutationForbidden"])

    def test_fixture_matrix_covers_zero_101_late_duplicates_and_failures(self) -> None:
        self.assertEqual(self.fixtures["contract_status"], "SPEC_ONLY")
        cases = {row["id"]: row for row in self.fixtures["mail_sweep_cases"]}
        required = {
            "zero-messages", "one-hundred-one-messages", "pagination-failure",
            "late-out-of-order", "duplicate-message-attachment-hash",
            "failure-before-cursor", "failure-after-cursor",
        }
        self.assertTrue(required.issubset(cases))
        self.assertEqual(cases["zero-messages"]["expected"]["output_items"], 1)
        self.assertEqual(cases["one-hundred-one-messages"]["expected"]["pages_fetched"], 2)
        self.assertEqual(cases["pagination-failure"]["expected"]["cursor_commits"], 0)
        self.assertEqual(cases["failure-before-cursor"]["expected"]["cursor_commits"], 0)

    def test_monthly_workflows_poll_daily_until_deadline(self) -> None:
        rows = {row["code"]: row for row in self.registry["workflows"] if row["code"] in {
            "EI_MONTHLY_STATEMENT", "WIO_MONTHLY_STATEMENT", "RAK_MONTHLY_STATEMENT", "SC_MONTHLY_STATEMENT",
        }}
        self.assertEqual(len(rows), 4)
        for row in rows.values():
            self.assertTrue(row["schedule"].startswith("FREQ=DAILY;"))
            self.assertGreater(row["cycle_poll"]["cycle_day"], 0)
            self.assertGreater(row["cycle_poll"]["deadline_days"], 0)
        for filename in ("04-ei-monthly-statement.json", "05-wio-monthly-statement.json"):
            names = set(self.nodes(filename))
            self.assertIn("Upsert Waiting or Deadline Receipt", names)
            self.assertIn("Read Back Waiting or Deadline Receipt", names)

    def test_shared_pipeline_archives_delta_before_prepared_and_reads_every_state(self) -> None:
        names = [node["name"] for node in self.workflow("03-shared-statement-pipeline.json")["nodes"]]
        self.assertLess(names.index("Verify Durable Canonical Delta"), names.index("Upsert PREPARED Actual Outbox"))
        for state in ("PREPARED", "ACTUAL OBSERVED", "VERIFIED", "COMMITTED"):
            self.assertIn(f"Upsert {state} {'Outbox' if state == 'ACTUAL OBSERVED' else 'Actual Outbox'}", names)
            self.assertIn(f"Read Back {state} {'Outbox' if state == 'ACTUAL OBSERVED' else 'Actual Outbox'}", names)
        for name in (
            "Download PREPARED Delta Artifact", "SHA-256 PREPARED Delta Artifact",
            "Verify Recovered Delta Contract", "Acquire Fenced Writer Lease",
            "Assert Writer Fence Before Import", "Release Exact Writer Fence",
            "Upsert Exact Actual Verification Receipt",
            "Read Back Exact Actual Verification Receipt",
            "Compare Exact Actual Verification Receipt",
            "Compare Terminal Receipt Readback", "Mark Terminal Readback Verified",
            "Read Back Verified Terminal Receipt",
        ):
            self.assertIn(name, names)
        code = self.nodes("03-shared-statement-pipeline.json")["Build Exact Verification Contract"]["parameters"]["jsCode"]
        self.assertIn("expected_transactions", code)
        self.assertIn("expected_account_balance", code)
        self.assertNotIn("imported_ids:", code)

    def test_recovery_rehydrates_artifact_and_preserves_all_outbox_transitions(self) -> None:
        names = set(self.nodes("17-actual-outbox-recovery.json"))
        for name in (
            "Download Immutable Delta Artifact", "SHA-256 Recovered Delta",
            "Verify Recovery Contract", "Acquire Recovery Writer Fence",
            "Assert Recovery Fence Before Import", "Upsert ACTUAL OBSERVED Recovery",
            "Read Back ACTUAL OBSERVED Recovery", "Build Recovery Verification Contract",
            "Upsert VERIFIED Recovery", "Read Back VERIFIED Recovery",
            "Upsert COMMITTED Recovery", "Read Back COMMITTED Recovery",
            "Release Recovery Writer Fence",
        ):
            self.assertIn(name, names)
        cases = {row["id"] for row in self.fixtures["writer_lease_cases"]}
        self.assertTrue({
            "concurrent-acquire", "expired-reacquire", "stale-token-before-import",
            "kill-after-prepared", "kill-after-actual-observed", "kill-after-verified",
        }.issubset(cases))

    def test_writer_lease_uses_only_fixed_parameterized_postgres_functions(self) -> None:
        workflow = self.workflow("18-finance-writer-lease.json")
        postgres = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.postgres"]
        self.assertEqual(len(postgres), 3)
        queries = "\n".join(node["parameters"]["query"] for node in postgres)
        for function in ("finance_ops.acquire_writer_lease", "finance_ops.assert_writer_lease", "finance_ops.release_writer_lease"):
            self.assertIn(function, queries)
        self.assertNotIn("={{", queries)
        self.assertTrue(all("$1" in node["parameters"]["query"] for node in postgres))
        migration = (N8N / "postgres" / "001-finance-writer-lease.sql").read_text(encoding="utf-8")
        for term in ("ON CONFLICT (resource_key) DO UPDATE", "current.fencing_token + 1", "current.expires_at <= clock_timestamp()", "assert_writer_lease", "release_writer_lease"):
            self.assertIn(term, migration)

    def test_error_workflow_redacts_then_upserts_reads_compares_and_marks(self) -> None:
        workflow = self.workflow("16-operations-error-handler.json")
        names = [node["name"] for node in workflow["nodes"]]
        self.assertEqual(names, [
            "Finance Workflow Failed", "Redact and Classify Failure",
            "Upsert Durable Failure Receipt", "Read Back Failure Receipt",
            "Compare Failure Receipt Readback", "Mark Failure Readback Verified",
            "Read Back Verified Failure Receipt",
        ])
        code = self.nodes("16-operations-error-handler.json")["Redact and Classify Failure"]["parameters"]["jsCode"]
        self.assertIn("[REDACTED]", code)

    def test_ai_contract_uses_subscription_runner_and_value_domains(self) -> None:
        contract = load_json(N8N / "codex-agent-handoff.json")
        runner = contract["runner_contract"]
        self.assertEqual(runner["credential"], "CHATGPT_CACHED_LOGIN")
        self.assertEqual(runner["forced_login_method"], "chatgpt")
        self.assertTrue(runner["api_key_fallback_forbidden"])
        self.assertEqual(runner["server_model_policy"], {
            "NORMAL": {"model": "gpt-5.6-luna", "reasoning_effort": "max"},
            "EXCEPTION": {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
        })
        handoff = load_json(N8N / contract["request_schema"])
        proposal = load_json(N8N / contract["output_schema"])
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema:
            jsonschema.validators.validator_for(handoff).check_schema(handoff)
            jsonschema.validators.validator_for(proposal).check_schema(proposal)
        unresolved = handoff["$defs"]["unresolved"]
        self.assertIn("allowed_values", unresolved["required"])
        self.assertEqual(unresolved["properties"]["allowed_values"]["maxProperties"], 10)
        self.assertEqual(proposal["properties"]["auth_mode"]["const"], "CHATGPT_SUBSCRIPTION")

    def test_ai_workflow_derives_profile_enforces_domains_and_omits_internal_hash(self) -> None:
        nodes = self.nodes("09-ai-proposal.json")
        untrusted = nodes["Validate Untrusted Proposal Request"]["parameters"]["jsCode"]
        validation = nodes["Build Authoritative Redacted Proposal Job"]["parameters"]["jsCode"]
        response = nodes["Validate Proposal Schema and Policy Boundary"]["parameters"]["jsCode"]
        for forbidden in ("agent_profile", "policy_sha256", "config_sha256", "output_schema_sha256"):
            self.assertIn(f"'{forbidden}'", untrusted)
        self.assertIn("finance_ai_policy_contracts", json.dumps(nodes["Read Active Server AI Policy Contract"]))
        self.assertIn("LUNA_MAX:'NORMAL'", validation)
        self.assertIn("SOL_XHIGH:'EXCEPTION'", validation)
        self.assertIn("Missing bounded server value domain", validation)
        self.assertIn("request_canonical", validation)
        self.assertIn("Proposal outside configured domain", response)
        self.assertIn("Duplicate proposal field", response)
        self.assertIn("Agent proposal envelope mismatch", response)
        http_body = nodes["Invoke Fixed Codex Agent Runner"]["parameters"]["jsonBody"]
        self.assertIn("key !== 'request_sha256'", http_body)

    def test_ai_policy_targets_are_complete_and_profile_owned(self) -> None:
        policies = load_json(ROOT / "config" / "ai-policies.json")["policies"]
        target_contract = load_json(N8N / "ai-policy-targets.json")
        configured = {target for policy in policies for target in policy["target_fields"]}
        self.assertEqual(configured, set(target_contract["target_fields"]))
        schema = load_json(N8N / "contracts" / "codex-agent-handoff-v1.schema.json")
        schema_targets = set(schema["$defs"]["unresolved"]["properties"]["allowed_fields"]["items"]["enum"])
        self.assertTrue(configured.issubset(schema_targets))
        self.assertEqual(schema_targets - configured, {"review_required"})
        for policy in policies:
            self.assertIn(policy["agent_profile"], target_contract["profile_policy"])
            self.assertRegex(policy["policy_id"], r"^[a-z0-9][a-z0-9:_-]{0,127}$")

    def test_ai_policy_contract_compiler_is_current_and_server_owned(self) -> None:
        result = subprocess.run(
            [sys.executable, str(N8N / "compile_ai_policy_contracts.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = load_json(ROOT / "config" / "ai-policies.json")["policies"]
        seed = load_json(N8N / "generated" / "ai-policy-contracts.seed.json")
        self.assertEqual(seed["contract_status"], "SPEC_ONLY")
        self.assertEqual({row["policy_id"] for row in source}, {row["policy_id"] for row in seed["rows"]})
        for row in seed["rows"]:
            self.assertRegex(row["policy_sha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(row["config_sha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(row["output_schema_sha256"], r"^[a-f0-9]{64}$")
            self.assertTrue(json.loads(row["allowed_fields_json"]))
            self.assertIsInstance(json.loads(row["allowed_values_json"]), dict)

    def test_platform_bootstrap_generator_is_current(self) -> None:
        result = subprocess.run(
            [sys.executable, str(N8N / "generate_platform_bootstrap.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = load_json(N8N / "generated" / "platform-bootstrap-manifest.json")
        self.assertEqual(manifest["contract_status"], "SPEC_ONLY")
        self.assertEqual(manifest["n8n_version"], "2.36.2")
        self.assertEqual(set(manifest["execution_evidence"].values()), {False})
        self.assertEqual(
            manifest["sources"]["data_tables_sha256"],
            git_canonical_sha256(N8N / "data-tables.json"),
        )
        self.assertEqual(
            manifest["sources"]["ai_policy_seed_sha256"],
            git_canonical_sha256(
                N8N / "generated" / "ai-policy-contracts.seed.json"
            ),
        )

    def test_generated_source_hashes_use_git_canonical_lf_bytes(self) -> None:
        seed = load_json(N8N / "generated" / "ai-policy-contracts.seed.json")
        policies_hash = git_canonical_sha256(ROOT / "config" / "ai-policies.json")
        schema_hash = git_canonical_sha256(
            N8N / "contracts" / "ai-proposal-v1.schema.json"
        )
        for row in seed["rows"]:
            self.assertEqual(row["config_sha256"], policies_hash)
            self.assertEqual(row["output_schema_sha256"], schema_hash)

        fixture_manifest = load_json(N8N / "disposable" / "fixture-manifest.json")
        for filename, digest in fixture_manifest["source_workflow_sha256"].items():
            self.assertEqual(digest, git_canonical_sha256(WORKFLOWS / filename))

    def test_platform_bootstrap_is_manual_only_native_and_nonfinancial(self) -> None:
        workflow = self.workflow("19-platform-data-table-bootstrap.json")
        registry_row = next(
            row for row in self.registry["workflows"]
            if row["code"] == "PLATFORM_DATA_TABLE_BOOTSTRAP"
        )
        self.assertTrue(registry_row["manual_only"])
        self.assertIsNone(registry_row["schedule"])
        self.assertFalse(registry_row["mcp_exposed"])
        self.assertEqual(workflow["meta"]["migrationStatus"], "SPEC_ONLY")
        self.assertTrue(workflow["meta"]["manualOnly"])
        self.assertTrue(workflow["meta"]["platformBootstrapOnly"])
        self.assertTrue(workflow["meta"]["financeLedgerMutationForbidden"])
        self.assertTrue(workflow["meta"]["actualMutationForbidden"])
        triggers = [
            node for node in workflow["nodes"]
            if node["type"] == "n8n-nodes-base.manualTrigger"
        ]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(
            {node["type"] for node in workflow["nodes"]},
            {
                "n8n-nodes-base.manualTrigger",
                "n8n-nodes-base.dataTable",
                "n8n-nodes-base.code",
            },
        )
        self.assertFalse(any(
            node["type"].startswith("n8n-nodes-finance.")
            or node["type"] in {
                "n8n-nodes-base.postgres", "n8n-nodes-base.httpRequest",
                "n8n-nodes-base.microsoftOutlook", "n8n-nodes-base.microsoftOneDrive",
            }
            for node in workflow["nodes"]
        ))

    def test_platform_bootstrap_creates_every_declared_table_with_exact_columns(self) -> None:
        workflow = self.workflow("19-platform-data-table-bootstrap.json")
        manifest = load_json(N8N / "generated" / "platform-bootstrap-manifest.json")
        expected = {row["name"]: row["columns"] for row in self.tables["tables"]}
        creates = [
            node for node in workflow["nodes"]
            if node["type"] == "n8n-nodes-base.dataTable"
            and node["parameters"].get("resource") == "table"
            and node["parameters"].get("operation") == "create"
        ]
        self.assertEqual(len(creates), len(expected))
        self.assertEqual({node["parameters"]["tableName"] for node in creates}, set(expected))
        for node in creates:
            parameters = node["parameters"]
            name = parameters["tableName"]
            self.assertEqual(parameters["options"], {"createIfNotExists": True})
            self.assertEqual(
                parameters["columns"]["column"],
                [
                    {"name": column, "type": column_type}
                    for column, column_type in expected[name].items()
                ],
            )
        self.assertEqual(
            [entry["parameters"] for entry in manifest["table_create_operations"]],
            [node["parameters"] for node in creates],
        )
        self.assertEqual(
            creates[0]["parameters"]["tableName"], "finance_execution_failures"
        )

    def test_platform_bootstrap_only_seeds_and_exactly_reads_ai_policy_contracts(self) -> None:
        nodes = self.nodes("19-platform-data-table-bootstrap.json")
        data_nodes = [
            node for node in nodes.values()
            if node["type"] == "n8n-nodes-base.dataTable"
            and node["parameters"].get("resource") == "row"
        ]
        self.assertEqual(
            [(node["parameters"]["operation"], node["parameters"]["dataTableId"]["value"])
             for node in data_nodes],
            [
                ("upsert", "finance_ai_policy_contracts"),
                ("get", "finance_ai_policy_contracts"),
            ],
        )
        upsert = nodes["Upsert AI Policy Contracts"]["parameters"]
        self.assertEqual(
            [(row["keyName"], row["condition"]) for row in upsert["filters"]["conditions"]],
            [("policy_id", "eq"), ("policy_version", "eq")],
        )
        readback = nodes["Read Back All ACTIVE AI Policy Contracts"]
        self.assertTrue(readback["parameters"]["returnAll"])
        self.assertTrue(readback["alwaysOutputData"])
        self.assertEqual(
            readback["parameters"]["filters"]["conditions"],
            [{"keyName": "state", "condition": "eq", "keyValue": "ACTIVE"}],
        )
        compare = nodes["Exact Compare AI Policy Seed Readback"]["parameters"]["jsCode"]
        for marker in (
            "AI_POLICY_ACTIVE_COUNT_MISMATCH",
            "AI_POLICY_DUPLICATE_ACTIVE_VERSION",
            "AI_POLICY_READBACK_MISSING",
            "AI_POLICY_READBACK_MISMATCH",
            "AI_POLICY_UPDATED_AT_INVALID",
            "finance_ledger_writes:false",
            "actual_writes:false",
        ):
            self.assertIn(marker, compare)
        seed = load_json(N8N / "generated" / "ai-policy-contracts.seed.json")
        self.assertIn(
            json.dumps(seed["rows"], ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            nodes["Emit Versioned AI Policy Seed"]["parameters"]["jsCode"],
        )

    def test_disposable_fixture_workflows_are_generated_current_and_hashed(self) -> None:
        disposable = N8N / "disposable"
        generated = disposable / "generated"
        result = subprocess.run(
            [sys.executable, str(disposable / "generate_fixture_workflows.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = load_json(disposable / "fixture-manifest.json")
        self.assertEqual(manifest["contract_status"], "DISPOSABLE_ONLY")
        self.assertTrue(manifest["production_import_forbidden"])
        self.assertEqual(manifest["required_acknowledgement"], "DISPOSABLE_ONLY")
        self.assertEqual(len(manifest["workflows"]), 18)
        for row in manifest["workflows"]:
            path = generated / row["file"]
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["sha256"])

    def test_disposable_fixtures_are_inactive_manual_and_external_write_free(self) -> None:
        generated = N8N / "disposable" / "generated"
        fixtures = [load_json(path) for path in sorted(generated.glob("*.json"))]
        production_ids = {workflow["id"] for workflow in self.workflows.values()}
        registry_files = {row["file"] for row in self.registry["workflows"]}
        forbidden = {
            "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.webhook",
            "@n8n/n8n-nodes-langchain.mcpTrigger",
            "n8n-nodes-base.microsoftOutlook",
            "n8n-nodes-base.microsoftOneDrive",
            "n8n-nodes-finance.actualBudget",
        }
        for workflow in fixtures:
            with self.subTest(workflow=workflow["name"]):
                self.assertFalse(workflow["active"])
                self.assertNotIn(workflow["id"], production_ids)
                self.assertTrue(workflow["meta"]["disposableOnly"])
                self.assertTrue(workflow["meta"]["productionImportForbidden"])
                self.assertFalse({node["type"] for node in workflow["nodes"]} & forbidden)
                self.assertTrue(any(
                    node["type"] in {
                        "n8n-nodes-base.manualTrigger",
                        "n8n-nodes-base.executeWorkflowTrigger",
                    }
                    for node in workflow["nodes"]
                ))
        self.assertFalse(registry_files & {path.name for path in generated.glob("*.json")})

    def test_disposable_fixture_matrix_covers_runtime_requested_boundaries(self) -> None:
        manifest = load_json(N8N / "disposable" / "fixture-manifest.json")
        scenarios = manifest["scenario_contract"]
        self.assertEqual(scenarios["sweep_zero"]["expected"]["scanned_count"], 0)
        self.assertTrue(scenarios["sweep_zero"]["expected"]["heartbeat"])
        self.assertEqual(scenarios["sweep_101"]["expected"]["scanned_count"], 101)
        self.assertEqual(scenarios["sweep_late_order"]["expected_ids"], ["m1", "m2", "m3"])
        self.assertEqual(scenarios["sweep_pagination_failure"]["expected_exit"], "nonzero")
        self.assertTrue(scenarios["lease_concurrency"]["run_concurrently"])
        self.assertEqual(scenarios["lease_concurrency"]["expected_successes"], 1)
        self.assertEqual(scenarios["lease_stale"]["expected_error"], "WRITER_LEASE_STALE")
        self.assertEqual(scenarios["ai_negative"]["runner_calls"], 0)
        self.assertEqual(
            scenarios["ai_positive_luna"],
            {
                "workflow_id": "90000000-0000-4000-8000-000000000911",
                "expected_exit": 0,
                "policy_id": "classify-unresolved",
                "expected_model": "gpt-5.6-luna",
                "expected_reasoning_effort": "max",
                "expected_auth_mode": "CHATGPT_SUBSCRIPTION",
                "finance_writes": 0,
            },
        )
        sol = scenarios["ai_positive_sol_gated"]
        self.assertEqual(sol["workflow_id"], "90000000-0000-4000-8000-000000000912")
        self.assertEqual(sol["expected_model"], "gpt-5.6-sol")
        self.assertEqual(sol["expected_reasoning_effort"], "xhigh")
        self.assertEqual(sol["execution_gate"], "DISPOSABLE_ALLOW_SOL_XHIGH")
        self.assertTrue(sol["default_execution_forbidden"])
        self.assertEqual(scenarios["outbox_recovery"]["expected_state"], "COMMITTED")
        self.assertEqual(scenarios["outbox_recovery"]["finance_writes"], 0)
        self.assertEqual(scenarios["error_redaction"]["receipt_table"], "finance_execution_failures")
        self.assertEqual(
            set(manifest["blocked_runtime_scenarios"]),
            {
                "bounded_mcp_network_negative",
                "real_actual_recovery_write",
            },
        )

    def test_positive_ai_wrappers_are_fixed_redacted_and_model_unselectable(self) -> None:
        generated = N8N / "disposable" / "generated"
        luna = load_json(generated / "106-ai-positive-luna.json")
        sol = load_json(generated / "107-ai-positive-sol-gated.json")
        for workflow, policy_id in (
            (luna, "classify-unresolved"),
            (sol, "recommend-category"),
        ):
            serialized = json.dumps(workflow)
            self.assertIn(policy_id, serialized)
            self.assertIn("10000000-0000-4000-8000-000000000009", serialized)
            for forbidden in (
                '"model"', '"url"', '"credential"', '"prompt"',
                '"policy_sha256"', '"config_sha256"', '"output_schema_sha256"',
                '"amount"', '"source_id"', '"dedupe_key"',
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertTrue(workflow["meta"]["financeWritesImpossible"])
        self.assertEqual(sol["meta"]["executionGate"], "DISPOSABLE_ALLOW_SOL_XHIGH")
        self.assertTrue(sol["meta"]["defaultExecutionForbidden"])

    def test_rule_ownership_compiler_is_current_disjoint_and_complete(self) -> None:
        result = subprocess.run(
            [sys.executable, str(N8N / "compile_rule_ownership.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = load_json(N8N / "generated" / "rule-ownership-manifest.json")
        actual = load_json(N8N / "generated" / "actual-rules.json")
        n8n_rules = load_json(N8N / "generated" / "n8n-runtime-rules.json")
        self.assertEqual(manifest["overlap"], [])
        self.assertEqual(manifest["unowned"], [])
        actual_keys = {(row["rule_id"], scope) for row in actual["rules"] for scope in row["rule_sets"]}
        n8n_keys = {(row["rule_id"], scope) for row in n8n_rules["rules"] for scope in row["rule_sets"]}
        owned_keys = {(row["rule_id"], row["rule_set"]) for row in manifest["ownership"]}
        self.assertFalse(actual_keys & n8n_keys)
        self.assertEqual(actual_keys | n8n_keys, owned_keys)
        self.assertTrue(all(row["execution_owner"] == "ACTUAL" and row["actual_representable"] for row in actual["rules"]))
        self.assertTrue(all(row["execution_owner"] == "N8N_ONLY" and not row["actual_representable"] for row in n8n_rules["rules"]))

    def test_readme_does_not_claim_exports_are_executable(self) -> None:
        readme = (N8N / "README.md").read_text(encoding="utf-8")
        self.assertIn("SPEC_ONLY", readme)
        self.assertIn("have not yet passed exact-image import", readme)
        self.assertIn("API-key fallback is forbidden", readme)
        self.assertIn("native n8n OpenAI credential is not used", readme)


if __name__ == "__main__":
    unittest.main()
