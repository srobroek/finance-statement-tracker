from __future__ import annotations

from copy import deepcopy
import base64
import json
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import re
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, ValidationError


ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOWS = N8N / "workflows"
ERROR_WORKFLOW_ID = "10000000-0000-4000-8000-000000000016"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def git_canonical_sha256(path: Path) -> str:
    """Hash the LF bytes that a normal Git checkout exposes on Linux."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_bootstrap_generator():
    generator_path = N8N / "generate_platform_bootstrap.py"
    spec = importlib.util.spec_from_file_location("finance_bootstrap_generator", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load generator: {generator_path}")
    sys.path.insert(0, str(N8N))
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def load_fixture_generator():
    generator_path = N8N / "disposable" / "generate_fixture_workflows.py"
    spec = importlib.util.spec_from_file_location("finance_fixture_generator", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load generator: {generator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_fixture_against_schema(schema: dict, value: object, path: str = "$") -> None:
    """Execute the JSON-schema subset used by the browser capture contract."""
    expected_type = schema.get("type")
    if expected_type:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        matches = any(
            kind == "object" and isinstance(value, dict)
            or kind == "array" and isinstance(value, list)
            or kind == "string" and isinstance(value, str)
            or kind == "boolean" and isinstance(value, bool)
            or kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)
            for kind in types
        )
        if not matches:
            raise AssertionError(f"{path}: expected {expected_type}")
    if "const" in schema and value != schema["const"]:
        raise AssertionError(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise AssertionError(f"{path}: enum mismatch")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise AssertionError(f"{path}: minLength")
        if "pattern" in schema:
            if not re.search(schema["pattern"], value):
                raise AssertionError(f"{path}: pattern")
        if schema.get("format") == "date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as error:
                raise AssertionError(f"{path}: date") from error
        elif schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise AssertionError(f"{path}: date-time") from error
        elif schema.get("format") == "uri":
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise AssertionError(f"{path}: uri")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise AssertionError(f"{path}: missing {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise AssertionError(f"{path}: forbidden {sorted(unknown)}")
        for key, child in value.items():
            if key in properties:
                validate_fixture_against_schema(properties[key], child, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            validate_fixture_against_schema(schema["items"], child, f"{path}[{index}]")


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

    def run_exported_node(
        self,
        node_name: str,
        json_input: dict,
        binary: dict,
        references: dict[str, dict],
    ) -> dict:
        code = self.nodes("11-interactive-artifact-handoff.json")[node_name]["parameters"]["jsCode"]
        script = f"""
const code = {json.dumps(code)};
const jsonInput = {json.dumps(json_input)};
const binary = {json.dumps(binary)};
const references = {json.dumps(references)};
const lookup = name => ({{ first: () => references[name] }});
try {{
  const output = new Function('$json', '$binary', '$', 'require', code)(jsonInput, binary, lookup, require);
  process.stdout.write(JSON.stringify({{ ok: true, output }}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ ok: false, error: String(error.message || error) }}));
}}
"""
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for exported W11 contract execution")
        environment = os.environ.copy()
        # W11 executes the same Ajv-backed validator as the exported n8n code.
        # Prefer the reviewed package dependency installed by CI, retaining
        # the workstation cache as a compatibility fallback.
        ajv_module_roots = [
            ROOT / "packages/n8n-nodes-finance/node_modules",
            Path("/home/sjors/.cache/typescript/5.9/node_modules"),
        ]
        available_roots = [str(path) for path in ajv_module_roots if (path / "ajv").is_dir()]
        if available_roots:
            environment["NODE_PATH"] = os.pathsep.join(available_roots)
        with tempfile.TemporaryDirectory() as temporary:
            script_path = Path(temporary) / "exported-node.cjs"
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [node, str(script_path)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def run_exported_node_without_dynamic_code(
        self,
        node_name: str,
        json_input: dict,
        binary: dict,
        references: dict[str, dict],
    ) -> dict:
        """Run exported Code-node source under the production JS runner flags."""
        code = self.nodes("11-interactive-artifact-handoff.json")[node_name]["parameters"]["jsCode"]
        script = f"""
function executeNode($json, $binary, $) {{
{code}
}}
const jsonInput = {json.dumps(json_input)};
const binary = {json.dumps(binary)};
const references = {json.dumps(references)};
const lookup = name => ({{ first: () => references[name] }});
try {{
  const output = executeNode(jsonInput, binary, lookup);
  process.stdout.write(JSON.stringify({{ ok: true, output }}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ ok: false, error: String(error.message || error) }}));
}}
"""
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for exported W11 contract execution")
        with tempfile.TemporaryDirectory() as temporary:
            script_path = Path(temporary) / "restricted-exported-node.cjs"
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [node, "--disallow-code-generation-from-strings", "--disable-proto=delete", str(script_path)],
                cwd=ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def run_exported_workflow_node(
        self,
        workflow_filename: str,
        node_name: str,
        json_input: dict,
        references: dict[str, dict],
    ) -> dict:
        code = self.nodes(workflow_filename)[node_name]["parameters"]["jsCode"]
        script = f"""
const code = {json.dumps(code)};
const jsonInput = {json.dumps(json_input)};
const references = {json.dumps(references)};
const lookup = name => ({{ first: () => references[name] }});
try {{
  const output = new Function('$json', '$', 'require', code)(jsonInput, lookup, require);
  process.stdout.write(JSON.stringify({{ ok: true, output }}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ ok: false, error: String(error.message || error) }}));
}}
"""
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for exported workflow contract execution")
        result = subprocess.run(
            [node, "-e", script],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def run_failure_receipt_lifecycle(
        self,
        json_input: dict,
        *,
        drop_terminal_marker: bool = False,
        key_prefix: str | None = None,
        extra_field: bool = False,
    ) -> dict:
        nodes = self.nodes("16-operations-error-handler.json")
        codes = {
            name: nodes[name]["parameters"]["jsCode"]
            for name in (
                "Upsert Durable Failure Receipt",
                "Read Back Failure Receipt",
                "Mark Failure Readback Verified",
                "Read Back Verified Failure Receipt",
            )
        }
        if key_prefix is not None:
            codes = {
                name: code.replace("finance_failure_receipt_v1_", key_prefix)
                for name, code in codes.items()
            }
        if extra_field:
            codes["Upsert Durable Failure Receipt"] = codes["Upsert Durable Failure Receipt"].replace(
                "'readback_verified'];",
                "'readback_verified', 'extra'];",
            )
        script = f"""
const codes = {json.dumps(codes)};
const jsonInput = {json.dumps(json_input)};
const values = new Map();
const execution = {{
  customData: {{
    set: (key, value) => {{
      if (typeof key !== 'string' || key.length > 50)
        throw new Error('CUSTOM_DATA_KEY_LIMIT');
      if (values.size >= 10 && !values.has(key))
        throw new Error('CUSTOM_DATA_KEY_COUNT_LIMIT');
      if (typeof value !== 'string' || value.length > 255)
        throw new Error('CUSTOM_DATA_VALUE_LIMIT');
      values.set(key, value);
    }},
    get: key => values.get(key),
  }},
}};
const refs = {{
  'Redact and Classify Failure': {{ json: jsonInput }},
  'Mark Failure Readback Verified': {{ json: null }},
}};
const lookup = name => ({{ first: () => refs[name] }});
const run = (name, input) => {{
  const output = new Function('$json', '$execution', '$', 'require', codes[name])(
    input,
    execution,
    lookup,
    require,
  );
  return output[0].json;
}};
try {{
  const persisted = run('Upsert Durable Failure Receipt', jsonInput);
  const read = run('Read Back Failure Receipt', persisted);
  const marked = run('Mark Failure Readback Verified', read);
  refs['Mark Failure Readback Verified'] = {{ json: marked }};
  if ({str(drop_terminal_marker).lower()})
    values.delete('finance_failure_receipt_v1_readback_verified');
  const terminal = run('Read Back Verified Failure Receipt', marked);
  process.stdout.write(JSON.stringify({{ ok: true, terminal, values: Object.fromEntries(values) }}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ ok: false, error: String(error.message || error), values: Object.fromEntries(values) }}));
}}
"""
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for exported workflow contract execution")
        result = subprocess.run(
            [node, "-e", script],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def run_exported_workflow_node_with_items(
        self,
        workflow_filename: str,
        node_name: str,
        items: list[dict],
        references: dict[str, dict],
    ) -> dict:
        code = self.nodes(workflow_filename)[node_name]["parameters"]["jsCode"]
        script = f"""
const code = {json.dumps(code)};
const items = {json.dumps(items)};
const references = {json.dumps(references)};
const lookup = name => ({{ first: () => references[name] }});
const input = {{ all: () => items.map(json => ({{ json }})) }};
try {{
  const output = new Function('$json', '$input', '$', 'require', code)(items[0] || {{}}, input, lookup, require);
  process.stdout.write(JSON.stringify({{ ok: true, output }}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ ok: false, error: String(error.message || error) }}));
}}
"""
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for exported workflow contract execution")
        result = subprocess.run(
            [node, "-e", script],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def evaluate_exported_expression(
        self,
        expression: str,
        now: str,
        references: dict[str, dict],
    ) -> object:
        body = expression.removeprefix("={{").removesuffix("}}").strip()
        script = f"""
const expression = {json.dumps(body)};
const references = {json.dumps(references)};
const lookup = name => ({{ first: () => references[name] }});
const now = {{ toISO: () => {json.dumps(now)} }};
try {{
  const value = new Function('$now', '$', `return (${{expression}});`)(now, lookup);
  process.stdout.write(JSON.stringify({{ ok: true, value }}));
}} catch (error) {{
  process.stdout.write(JSON.stringify({{ ok: false, error: String(error.message || error) }}));
}}
"""
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for exported expression execution")
        result = subprocess.run(
            [node, "-e", script],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"], payload)
        return payload["value"]

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

    def test_blocked_workflows_project_objective_registry_contracts(self) -> None:
        """Blocker gates are source-owned metadata, not sticky-note prose."""
        catalog = self.registry.get("blocker_registry")
        self.assertIsInstance(catalog, dict)
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["evaluation"], "ALL_REQUIRED")
        definitions = catalog["definitions"]
        self.assertEqual(
            set(definitions),
            {
                "VERIFIED_STATEMENT_FIXTURE_REQUIRED",
                "ACTIVE_ADAPTER_REQUIRED",
                "ACTIVE_EMAIL_SOURCE_REQUIRED",
                "VERIFIED_MESSAGE_FIXTURE_REQUIRED",
                "MCP_FACADE_CREDENTIAL_REQUIRED",
                "MCP_FACADE_DISPOSABLE_PROOF_REQUIRED",
            },
        )
        expected = {
            "15-finance-mcp-facade.json": [
                "MCP_FACADE_CREDENTIAL_REQUIRED",
                "MCP_FACADE_DISPOSABLE_PROOF_REQUIRED",
            ],
        }
        rows = {row["file"]: row for row in self.registry["workflows"]}
        for filename, codes in expected.items():
            with self.subTest(workflow=filename):
                row = rows[filename]
                workflow = self.workflow(filename)
                contract = workflow["meta"]["blockerContract"]
                self.assertEqual(row["blockers"], codes)
                self.assertEqual(row["blocker_policy"], {
                    "evaluation": "ALL_REQUIRED",
                    "state": "BLOCKED",
                    "operator_warning_required": True,
                })
                self.assertTrue(workflow["meta"]["activationBlocked"])
                self.assertEqual(workflow["meta"]["activationBlockers"], codes)
                self.assertEqual(contract["schemaVersion"], catalog["schema_version"])
                self.assertEqual(contract["registryPath"], "integrations/n8n/pipeline-registry.json")
                self.assertEqual(contract["workflowCode"], row["code"])
                self.assertEqual(contract["evaluation"], "ALL_REQUIRED")
                self.assertTrue(contract["activationBlocked"])
                self.assertEqual(contract["blockerCodes"], codes)
                projected = contract["required"]
                self.assertEqual([item["code"] for item in projected], codes)
                for item in projected:
                    with self.subTest(blocker=item["code"]):
                        self.assertTrue(item["required"])
                        self.assertEqual(item["state"], "BLOCKED")
                        self.assertEqual(item, {
                            **definitions[item["code"]],
                            "code": item["code"],
                            "required": True,
                            "state": "BLOCKED",
                        })
                        evidence = item["evidence"]
                        self.assertTrue(evidence["artifact"])
                        self.assertTrue(evidence["required_fields"])
                        self.assertTrue(evidence["assertions"])
                        self.assertTrue(item["operator_warning"])

                warning = contract["operatorWarning"]
                self.assertTrue(warning["required"])
                self.assertEqual(warning["retainUntil"], "ALL_REQUIRED_PROVEN")
                sticky_names = {
                    node["name"]
                    for node in workflow["nodes"]
                    if node["type"] == "n8n-nodes-base.stickyNote"
                }
                guard_names = {
                    node["name"]
                    for node in workflow["nodes"]
                    if node["type"] in {
                        "n8n-nodes-base.stopAndError",
                        "@n8n/n8n-nodes-langchain.mcpTrigger",
                    }
                }
                self.assertEqual(set(warning["stickyNoteNames"]), sticky_names)
                self.assertEqual(set(warning["guardNodeNames"]), guard_names)

        self.assertEqual(
            set(definitions),
            {
                "VERIFIED_STATEMENT_FIXTURE_REQUIRED",
                "ACTIVE_ADAPTER_REQUIRED",
                "ACTIVE_EMAIL_SOURCE_REQUIRED",
                "VERIFIED_MESSAGE_FIXTURE_REQUIRED",
                "MCP_FACADE_CREDENTIAL_REQUIRED",
                "MCP_FACADE_DISPOSABLE_PROOF_REQUIRED",
            },
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
            {
                "finance.status.v1",
                "finance.reviewed-artifact.handoff.v1",
                "finance.document.request.v1",
            },
        )
        exposed = [row for row in self.registry["workflows"] if row["mcp_exposed"]]
        self.assertEqual([row["code"] for row in exposed], ["FINANCE_MCP_FACADE"])
        facade = self.workflow("15-finance-mcp-facade.json")
        self.assertEqual(
            {node["type"] for node in facade["nodes"]},
            {
                "@n8n/n8n-nodes-langchain.mcpTrigger",
                "@n8n/n8n-nodes-langchain.toolWorkflow",
                "n8n-nodes-base.stickyNote",
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
            "ADCB_STATEMENT_PASSWORD_PLACEHOLDER", "sjor2908", "actual_password", "cashback_ingest_token",
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

    def test_workflow_nodes_are_postgres_jsonb_compatible(self) -> None:
        for filename, workflow in self.workflows.items():
            with self.subTest(workflow=filename):
                serialized_nodes = json.dumps(workflow["nodes"], ensure_ascii=True)
                self.assertNotIn("\\u0000", serialized_nodes)
                self.assertNotIn("\x00", serialized_nodes)

        shared_code = self.nodes("03-shared-statement-pipeline.json")[
            "Merge Allowed AI Proposals"
        ]["parameters"]["jsCode"]
        proposal_code = self.nodes("09-ai-proposal.json")[
            "Validate Proposal Schema and Policy Boundary"
        ]["parameters"]["jsCode"]
        compact_shared = re.sub(r"\s+", "", shared_code)
        self.assertIn("constpair=JSON.stringify([", compact_shared)
        self.assertIn(
            "String(proposal.transaction_id),String(proposal.field)",
            compact_shared,
        )
        self.assertIn(
            "JSON.stringify([proposal.transaction_id, proposal.field])",
            proposal_code,
        )

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
        nodes = self.workflow("14-local-pdf-extraction.json")["nodes"]
        pdf_ops = [
            node["parameters"]["operation"]
            for node in nodes
            if node["type"] == "n8n-nodes-finance.financePdf"
        ]
        self.assertEqual(pdf_ops[:3], ["validate", "unlock", "profile"])
        shared = self.nodes("03-shared-statement-pipeline.json")
        self.assertEqual(
            shared["Run Isolated PDF Extraction"]["parameters"]["workflowId"]["value"],
            self.workflow("14-local-pdf-extraction.json")["id"],
        )
        self.assertFalse(any(
            node["type"] == "n8n-nodes-base.extractFromFile"
            and node.get("parameters", {}).get("operation") == "pdf"
            for workflow in self.workflows.values()
            for node in workflow["nodes"]
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
        self.assertEqual(set(self.tables["state_policies"]), names)
        for name, policy in self.tables["state_policies"].items():
            with self.subTest(table=name):
                self.assertTrue(policy["retention"])
                self.assertTrue(policy["idempotency"])
                self.assertTrue(policy["concurrency"])
                self.assertTrue(policy["index_semantics"])

    def test_every_declared_data_table_is_referenced_by_a_connected_executable_node(self) -> None:
        referenced: set[str] = set()
        for workflow in self.workflows.values():
            connected = set(workflow["connections"])
            for outputs in workflow["connections"].values():
                for channel in outputs.values():
                    for branch in channel:
                        connected.update(edge["node"] for edge in branch)
            for node in workflow["nodes"]:
                if node["name"] not in connected or node["type"] != "n8n-nodes-base.dataTable":
                    continue
                value = node.get("parameters", {}).get("dataTableId", {}).get("value")
                if value:
                    referenced.add(value)
        self.assertIn("finance_ingestion_state", referenced)
        self.assertIn("finance_actual_batches", referenced)
        self.assertIn("finance_ai_reviews", referenced)
        self.assertNotIn("finance_acquisition_receipts", referenced)
        self.assertNotIn("finance_actual_outbox", referenced)
        self.assertNotIn("finance_execution_failures", referenced)
        self.assertNotIn("finance_ai_policy_contracts", referenced)

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
        compact = re.sub(r"\s+", "", code)
        for term in ("run_upper_bound", "pagination_exhausted:true", "scanned_count", "heartbeat", "received>=start", "received<upper"):
            self.assertIn(term, compact)
        self.assertTrue(workflow["meta"]["aggregateOutputAlwaysOne"])
        self.assertTrue(workflow["meta"]["cursorCommitExactlyOnce"])
        self.assertTrue(workflow["meta"]["cursorAdvanceRequiresDownstreamReceipt"])
        receipt_node = json.dumps(nodes["Upsert ENUMERATED Receipt"])
        self.assertIn("finance_ingestion_state", receipt_node)
        self.assertNotIn("finance_acquisition_receipts", receipt_node)
        receipt_columns = nodes["Upsert ENUMERATED Receipt"]["parameters"]["columns"]["value"]
        self.assertIn("receipt_run_id", receipt_columns)
        self.assertIn("receipt_run_upper_bound", receipt_columns)
        self.assertNotIn("committed_run_id", receipt_columns)
        self.assertNotIn("run_upper_bound", receipt_columns)
        self.assertIn(
            "Project Enumeration Receipt Fields for Sweep",
            nodes,
        )
        self.assertEqual(
            [row["keyName"] for row in nodes["Upsert ENUMERATED Receipt"]["parameters"]["filters"]["conditions"]],
            ["source_code"],
        )
        self.assertEqual(nodes["CAS Update Source Cursor"]["parameters"]["operation"], "update")
        cas_filters = nodes["CAS Update Source Cursor"]["parameters"]["filters"]["conditions"]
        self.assertEqual([row["keyName"] for row in cas_filters], ["source_code", "cursor_version"])
        self.assertIn("SOURCE_CURSOR_VERSION_CONFLICT", nodes["Build Cursor CAS Update"]["parameters"]["jsCode"])

    def test_outlook_enumerate_receipt_projects_before_one_row_cursor_cas(self) -> None:
        receipt = {
            "source_code": "OUTLOOK_FINANCE_ACQUISITION",
            "receipt_run_id": "run-new",
            "receipt_run_upper_bound": "2026-08-31T00:00:00.000Z",
            "last_window_start": "2026-08-01T00:00:00.000Z",
            "last_pages_fetched": 2,
            "last_pagination_exhausted": True,
            "last_heartbeat": False,
            "last_terminal_state": "ENUMERATED",
            "last_receipt_created_at": "2026-08-31T00:01:00.000Z",
            "committed_run_id": "run-old",
            "cursor_value": "2026-07-31T00:00:00.000Z",
            "run_upper_bound": "2026-07-31T00:00:00.000Z",
            "cursor_version": 4,
        }
        projected = self.run_exported_workflow_node(
            "12-outlook-message-sweep.json",
            "Project Enumeration Receipt Fields for Sweep",
            receipt,
            {},
        )
        self.assertTrue(projected["ok"], projected)
        projected_row = projected["output"][0]["json"]
        self.assertEqual(projected_row["run_id"], "run-new")
        self.assertEqual(projected_row["run_upper_bound"], "2026-08-31T00:00:00.000Z")
        self.assertEqual(projected_row["window_start"], "2026-08-01T00:00:00.000Z")
        self.assertEqual(projected_row["pages_fetched"], 2)
        self.assertTrue(projected_row["pagination_exhausted"])
        self.assertFalse(projected_row["heartbeat"])
        self.assertEqual(projected_row["terminal_state"], "ENUMERATED")
        proof = {
            "source_code": "OUTLOOK_FINANCE_ACQUISITION",
            "run_id": "run-new",
            "run_upper_bound": "2026-08-31T00:00:00.000Z",
            "terminal_state": "ARCHIVED",
            "readback_verified": True,
            "expected_cursor_version": 4,
        }
        detected = self.run_exported_workflow_node(
            "12-outlook-message-sweep.json",
            "Determine Existing Cursor Commit",
            receipt,
            {"Verify Downstream Persistence Proof": {"json": proof}},
        )
        self.assertTrue(detected["ok"], detected)
        detected_row = detected["output"][0]["json"]
        self.assertFalse(detected_row["cursor_recovery"])
        self.assertEqual(detected_row["resume_path"], "CAS")
        built = self.run_exported_workflow_node(
            "12-outlook-message-sweep.json",
            "Build Cursor CAS Update",
            detected_row,
            {"Verify Downstream Persistence Proof": {"json": proof}},
        )
        self.assertTrue(built["ok"], built)
        self.assertEqual(built["output"][0]["json"]["prior_cursor_version"], 4)
        self.assertEqual(built["output"][0]["json"]["next_cursor_version"], 5)
        cas_row = {
            **receipt,
            "committed_run_id": "run-new",
            "cursor_value": "2026-08-31T00:00:00.000Z",
            "run_upper_bound": "2026-08-31T00:00:00.000Z",
            "cursor_version": 5,
        }
        compared = self.run_exported_workflow_node(
            "12-outlook-message-sweep.json",
            "Compare CAS Cursor Readback",
            cas_row,
            {"Build Cursor CAS Update": {"json": built["output"][0]["json"]}},
        )
        self.assertTrue(compared["ok"], compared)

    def test_outlook_raw_canonical_receipts_project_through_every_readback_route(self) -> None:
        workflow = self.workflow("12-outlook-message-sweep.json")
        schema = {
            column["name"]
            for column in self.nodes("19-platform-data-table-bootstrap.json")[
                "Create or Reuse finance_ingestion_state"
            ]["parameters"]["columns"]["column"]
        }
        raw_row = {
            "source_code": "OUTLOOK_FINANCE_ACQUISITION",
            "receipt_run_id": "run-route",
            "receipt_run_upper_bound": "2026-08-31T00:00:00.000Z",
            "last_window_start": "2026-08-01T00:00:00.000Z",
            "last_pages_fetched": 2,
            "last_pagination_exhausted": True,
            "last_heartbeat": False,
            "last_terminal_state": "ENUMERATED",
            "last_receipt_created_at": "2026-08-31T00:01:00.000Z",
        }
        self.assertTrue(set(raw_row).issubset(schema))
        routes = {
            "Project Enumeration Receipt Fields for Commit Resume": (
                "Read Acquisition Receipt for Commit Resume",
                "Validate Commit Resume State",
            ),
            "Project Enumeration Receipt Fields for Sweep": (
                "Read Back ENUMERATED Receipt",
                "Verify Receipt and Return Sweep",
            ),
            "Project Enumeration Receipt Fields for Terminal Commit": (
                "Read Back Terminal Acquisition Receipt",
                "Verify Terminal Acquisition Receipt",
            ),
            "Project Enumeration Receipt Fields for Replay": (
                "Read Back DOWNSTREAM_VERIFIED Receipt for Replay",
                "Verify Replayed Terminal Acquisition Receipt",
            ),
            "Project Enumeration Receipt Fields for Existing Gate": (
                "Read Existing ENUMERATED Receipt",
                "Existing ENUMERATED Receipt Present",
            ),
            "Project Enumeration Receipt Fields for Archive": (
                "Read Back ARCHIVED Acquisition Receipt",
                "Verify ARCHIVED Acquisition Receipt",
            ),
            "Project Enumeration Receipt Fields for Verified Archive": (
                "Read Back Verified ARCHIVED Receipt",
                "Return Verified ARCHIVED Receipt",
            ),
        }
        for projector, (readback, consumer) in routes.items():
            with self.subTest(projector=projector):
                self.assertEqual(
                    workflow["connections"][readback]["main"][0][0]["node"],
                    projector,
                )
                self.assertEqual(
                    workflow["connections"][projector]["main"][0][0]["node"],
                    consumer,
                )
                projected = self.run_exported_workflow_node(
                    "12-outlook-message-sweep.json", projector, raw_row, {},
                )
                self.assertTrue(projected["ok"], projected)
                output = projected["output"][0]["json"]
                self.assertEqual(output["run_id"], raw_row["receipt_run_id"])
                self.assertEqual(output["run_upper_bound"], raw_row["receipt_run_upper_bound"])
                self.assertEqual(output["window_start"], raw_row["last_window_start"])
                self.assertEqual(output["pages_fetched"], raw_row["last_pages_fetched"])
                self.assertEqual(output["pagination_exhausted"], raw_row["last_pagination_exhausted"])
                self.assertEqual(output["heartbeat"], raw_row["last_heartbeat"])
                self.assertEqual(output["terminal_state"], raw_row["last_terminal_state"])
                self.assertEqual(output["created_at"], raw_row["last_receipt_created_at"])

        missing_state = self.run_exported_workflow_node(
            "12-outlook-message-sweep.json",
            "Project Enumeration Receipt Fields for Sweep",
            {key: value for key, value in raw_row.items() if key != "last_terminal_state"},
            {},
        )
        self.assertFalse(missing_state["ok"])
        self.assertIn("ENUMERATION_RECEIPT_FIELD_MISSING:last_terminal_state", missing_state["error"])

    def test_email_identity_is_derived_after_authoritative_policy_binding(self) -> None:
        workflow = self.workflow("12-outlook-message-sweep.json")
        nodes = self.nodes("12-outlook-message-sweep.json")
        prepare = nodes["Prepare W21 Email Request"]["parameters"]["jsCode"]
        policy = nodes["Build Authoritative W09 Email Job"]["parameters"]["jsCode"]
        request_hash = nodes["SHA-256 W09 Email Request"]
        handoff = nodes["Build Idempotent W09 Email Handoff"]["parameters"]["jsCode"]
        self.assertNotIn("handoff.idempotency_key", prepare)
        self.assertIn("request_canonical", policy)
        self.assertEqual(request_hash["parameters"]["value"], "={{ $json.request_canonical }}")
        self.assertEqual(request_hash["parameters"]["dataPropertyName"], "request_sha256")
        self.assertIn("...request", handoff)
        self.assertIn("job_id: `finance-ai:${requestSha256}`", handoff)
        self.assertIn("idempotency_key: requestSha256", handoff)
        self.assertEqual(
            workflow["connections"]["Build Authoritative W09 Email Job"]["main"][0][0]["node"],
            "SHA-256 W09 Email Request",
        )
        self.assertEqual(
            workflow["connections"]["SHA-256 W09 Email Request"]["main"][0][0]["node"],
            "Build Idempotent W09 Email Handoff",
        )
        # Execute both real W12 code nodes with the same redacted evidence and
        # two authoritative policy rows.  This proves policy rotation cannot
        # reuse an evidence-only identity.
        request = {
            "operation_code": "FINANCE_AI_PROPOSAL",
            "email_evidence": True,
            "policy_id": "classify-unresolved",
            "agent_provider": "CODEX_SUBSCRIPTION",
            "policy_class": "NORMAL",
            "archive_sha256": "e" * 64,
            "evidence_replay_keys": ["replay-1"],
            "archive_identity_keys": ["message-1:INLINE_BODY"],
            "archive_item_ids": ["drive-1"],
            "unresolved": [{
                "transaction_id": "tx-1",
                "allowed_fields": ["category"],
                "redacted_context": {"source_message_id": "message-1", "facts": {"total_minor": 123}},
            }],
        }
        policy_rows = [
            {
                "policy_id": "classify-unresolved", "state": "ACTIVE", "agent_profile": "LUNA_MAX",
                "agent_provider": "CODEX_SUBSCRIPTION", "policy_sha256": "a" * 64,
                "config_sha256": "c" * 64, "output_schema_sha256": "d" * 64,
                "allowed_fields_json": json.dumps(["category"]),
                "allowed_values_json": json.dumps({"category": ["Groceries"]}),
            },
            {
                "policy_id": "classify-unresolved", "state": "ACTIVE", "agent_profile": "LUNA_MAX",
                "agent_provider": "CODEX_SUBSCRIPTION", "policy_sha256": "b" * 64,
                "config_sha256": "c" * 64, "output_schema_sha256": "d" * 64,
                "allowed_fields_json": json.dumps(["category"]),
                "allowed_values_json": json.dumps({"category": ["Household"]}),
            },
        ]
        identities = []
        for policy_row in policy_rows:
            authoritative = self.run_exported_workflow_node_with_items(
                "12-outlook-message-sweep.json",
                "Build Authoritative W09 Email Job",
                [policy_row],
                {"Prepare W21 Email Request": {"json": request}},
            )
            self.assertTrue(authoritative["ok"], authoritative)
            built_request = authoritative["output"][0]["json"]
            request_sha256 = hashlib.sha256(
                built_request["request_canonical"].encode("utf-8")
            ).hexdigest()
            handoff = self.run_exported_workflow_node(
                "12-outlook-message-sweep.json",
                "Build Idempotent W09 Email Handoff",
                {**built_request, "request_sha256": request_sha256},
                {},
            )
            self.assertTrue(handoff["ok"], handoff)
            result = handoff["output"][0]["json"]
            identities.append((request_sha256, result["job_id"], result["idempotency_key"]))
        self.assertNotEqual(identities[0][0], identities[1][0])
        self.assertNotEqual(identities[0][1], identities[1][1])
        self.assertNotEqual(identities[0][2], identities[1][2])

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
            "EI_MONTHLY_STATEMENT", "WIO_MONTHLY_STATEMENT",
        }}
        self.assertEqual(len(rows), 2)
        for row in rows.values():
            self.assertTrue(row["schedule"].startswith("FREQ=DAILY;"))
            self.assertGreater(row["cycle_poll"]["cycle_day"], 0)
            self.assertGreater(row["cycle_poll"]["deadline_days"], 0)
        shared_names = set(self.nodes("22-shared-monthly-statement-cycle.json"))
        for name in ("Upsert Waiting or Deadline Receipt", "Read Back Waiting or Deadline Receipt"):
            self.assertIn(name, shared_names)
        for filename in ("04-ei-monthly-statement.json", "05-wio-monthly-statement.json"):
            names = set(self.nodes(filename))
            self.assertEqual(
                {name for name in names if not name.startswith("Stage ")},
                {"Daily 20:40 Cycle Poll", "Open Configured Cycle Window", "Run Shared Monthly Statement Cycle"},
            )
            self.assertIn("Run Shared Monthly Statement Cycle", names)
        for name in (
            "Monthly Cycle Context",
            "Load Trusted Source Contract",
            "Acquire Archive and Read Back",
            "Initialize Source Cursor via W12",
            "Run Shared Statement Pipeline",
            "Commit Source Cursor via W12",
        ):
            self.assertIn(name, shared_names)

    def test_monthly_cycle_native_trigger_and_imported_caller_schema(self) -> None:
        """Check the source-level schema that n8n imports into each caller."""
        shared = self.workflow("22-shared-monthly-statement-cycle.json")
        trigger = self.nodes("22-shared-monthly-statement-cycle.json")["Monthly Cycle Context"]
        trigger_inputs = trigger["parameters"]["workflowInputs"]["values"]
        expected_inputs = [
            {"name": "cycle_context", "type": "object"},
            {"name": "deadline_policy", "type": "object"},
            {"name": "execution_id", "type": "string"},
        ]
        self.assertEqual(trigger_inputs, expected_inputs)
        self.assertNotIn("inputSource", trigger["parameters"])
        for filename in ("04-ei-monthly-statement.json", "05-wio-monthly-statement.json"):
            caller = self.nodes(filename)["Run Shared Monthly Statement Cycle"]
            inputs = caller["parameters"]["workflowInputs"]
            imported_schema = inputs["schema"]
            self.assertEqual(
                imported_schema,
                [
                    {
                        "id": field["name"],
                        "displayName": field["name"],
                        "type": field["type"],
                        "display": True,
                    }
                    for field in expected_inputs
                ],
            )
            self.assertEqual(set(inputs["value"]), {field["name"] for field in expected_inputs})
            self.assertEqual(
                {row["id"]: row["type"] for row in imported_schema},
                {field["name"]: field["type"] for field in expected_inputs},
            )
            self.assertEqual(caller["parameters"]["workflowId"]["value"], shared["id"])

    def test_monthly_cycle_object_contract_rejects_untrusted_shapes(self) -> None:
        contract = self.workflow("22-shared-monthly-statement-cycle.json")["meta"]["workflowInputContract"]
        valid = {
            "cycle_context": {
                "run_id": "fixture:EI_AMAZON:2026-08",
                "source_code": "EI_AMAZON",
                "window_start": "2026-07-01T00:00:00.000Z",
                "run_upper_bound": "2026-08-03T00:00:00.000Z",
                "cycle_day": 1,
                "period_key": "2026-08",
                "trigger_kind": "SCHEDULE",
            },
            "deadline_policy": {
                "deadline_at": "2026-08-06T23:59:59.000Z",
                "deadline_days": 5,
            },
            "execution_id": "fixture-execution",
        }
        self.assertEqual(list(Draft202012Validator(contract).iter_errors(valid)), [])
        invalid = deepcopy(valid)
        invalid["cycle_context"]["untrusted_field"] = "reject"
        self.assertTrue(list(Draft202012Validator(contract).iter_errors(invalid)))
        invalid = deepcopy(valid)
        invalid["deadline_policy"] = "2026-08-06T23:59:59.000Z"
        self.assertTrue(list(Draft202012Validator(contract).iter_errors(invalid)))

    def test_shared_pipeline_archives_delta_before_prepared_and_reads_every_state(self) -> None:
        names = [node["name"] for node in self.workflow("03-shared-statement-pipeline.json")["nodes"]]
        self.assertLess(names.index("Verify Durable Canonical Delta"), names.index("Upsert PREPARED Actual Outbox"))
        self.assertIn("Apply Prepared Outbox Safely", names)
        writer = set(self.nodes("20-actual-outbox-apply.json"))
        for name in (
            "Download Immutable Delta Artifact", "SHA-256 Recovered Delta",
            "Verify Recovery Contract", "Acquire Recovery Writer Fence",
            "Assert Recovery Fence Before Import", "Release Recovery Writer Fence",
            "Upsert Exact Actual Verification Receipt",
            "Read Back Exact Actual Verification Receipt",
            "Compare Exact Actual Verification Receipt",
            "Read Back Released Recovery Writer Fence",
            "Route Recovery State", "Read Back COMMITTED Recovery Replay",
            "Read Back Exact Actual Verification Receipt Replay",
            "Return Verified Commit Receipt Replay",
            "Assert Recovery Fence After Import",
            "Validate Stored Verification Receipt for Commit",
            "Assert Recovery Fence Before Commit",
        ):
            self.assertIn(name, writer)
        code = self.nodes("20-actual-outbox-apply.json")["Verify Recovery Contract"]["parameters"]["jsCode"]
        self.assertIn("expected_transactions", code)
        self.assertIn("expected_account_balance", code)
        self.assertIn("card_code", code)
        self.assertNotIn("imported_ids:", code)
        writer_nodes = self.nodes("20-actual-outbox-apply.json")
        self.assertIn(
            "card_code",
            writer_nodes["Upsert Exact Actual Verification Receipt"]["parameters"]["columns"]["value"],
        )
        self.assertEqual(
            [row["keyName"] for row in writer_nodes["Upsert Exact Actual Verification Receipt"]["parameters"]["filters"]["conditions"]],
            ["idempotency_key"],
        )
        self.assertIn(
            "idempotency_key",
            writer_nodes["Upsert Exact Actual Verification Receipt"]["parameters"]["columns"]["value"],
        )
        self.assertNotIn(
            "receipt.card_code || receipt.account_id",
            writer_nodes["Return Verified Commit Receipt"]["parameters"]["jsCode"],
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Route Recovery State"]["main"][3][0]["node"],
            "Read Back COMMITTED Recovery Replay",
        )
        self.assertIn(
            "replay_readback_only",
            writer_nodes["Return Verified Commit Receipt Replay"]["parameters"]["jsCode"],
        )
        replay_code = writer_nodes["Return Verified Commit Receipt Replay"]["parameters"]["jsCode"]
        self.assertIn("Verify Recovery Contract", replay_code)
        self.assertNotIn("Build Recovery Fence Release", replay_code)
        committed_values = writer_nodes["Upsert COMMITTED Recovery"]["parameters"]["columns"]["value"]
        self.assertEqual(
            {"lease_owner", "lease_fence"},
            {"lease_owner", "lease_fence"} & committed_values.keys(),
        )
        self.assertIn(
            "ACTUAL_WRITER_LEASE_RELEASE_NOT_READ_BACK",
            writer_nodes["Return Verified Commit Receipt"]["parameters"]["jsCode"],
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Release Recovery Writer Fence"]["main"][0][0]["node"],
            "Read Back Released Recovery Writer Fence",
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Read Back Exact Actual Verification Receipt Replay"]["main"][0][0]["node"],
            "Return Verified Commit Receipt Replay",
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Recovery Import PREPARED"]["main"][0][0]["node"],
            "Build Post-Import Fence Assert",
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Read Back VERIFIED Recovery"]["main"][0][0]["node"],
            "Read Verification Receipt for Commit",
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Assert Recovery Fence Before Commit"]["main"][0][0]["node"],
            "Build Recovery Fence Release",
        )
        self.assertEqual(
            self.workflow("20-actual-outbox-apply.json")["connections"]["Read Back Released Recovery Writer Fence"]["main"][0][0]["node"],
            "Upsert COMMITTED Recovery",
        )
        observed_values = writer_nodes["Upsert ACTUAL OBSERVED Recovery"]["parameters"]["columns"]["value"]
        self.assertIn("actual.actual_result.added", observed_values["actual_transaction_id"])
        self.assertIn("outbox_row.actual_transaction_id", observed_values["actual_transaction_id"])

    def test_shared_pipeline_reuses_existing_outbox_before_prepared_upsert(self) -> None:
        workflow = self.workflow("03-shared-statement-pipeline.json")
        nodes = self.nodes("03-shared-statement-pipeline.json")
        connections = workflow["connections"]
        prepare_code = nodes["Prepare Outbox Intent"]["parameters"]["jsCode"]
        self.assertIn("idempotency_key = `statement:${source.document_sha256}`", prepare_code)
        self.assertNotIn("idempotency_key = `${source.run_id}:${source.document_sha256}`", prepare_code)
        self.assertTrue(nodes["Read Back Existing Actual Outbox"]["alwaysOutputData"])
        self.assertEqual(
            connections["Prepare Outbox Intent"]["main"][0][0]["node"],
            "Read Back Existing Actual Outbox",
        )
        self.assertEqual(
            connections["Select Existing Outbox Or Prepare"]["main"][0][0]["node"],
            "Route Existing Outbox State",
        )
        self.assertEqual(
            connections["Route Existing Outbox State"]["main"][0][0]["node"],
            "Apply Prepared Outbox Safely",
        )
        self.assertEqual(
            connections["Route Existing Outbox State"]["main"][1][0]["node"],
            "Upsert PREPARED Actual Outbox",
        )
        digest = "a" * 64
        draft = {
            "batch_id": "retry-run:new-outbox",
            "idempotency_key": "statement:payload",
            "actual_file_id": "actual-file:replay",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
            "delta_sha256": digest,
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "state": "PREPARED",
        }
        existing = {
            **draft,
            "batch_id": "statement:stable-existing",
            "state": "COMMITTED",
            "lease_owner": "n8n:recovery:statement:stable-existing",
            "lease_fence": 9,
        }
        selected = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Select Existing Outbox Or Prepare",
            existing,
            {"Prepare Outbox Intent": {"json": draft}},
        )
        self.assertTrue(selected["ok"], selected)
        selected_row = selected["output"][0]["json"]
        self.assertTrue(selected_row["existing_outbox_replay"])
        self.assertEqual(selected_row["state"], "COMMITTED")
        self.assertEqual(selected_row["batch_id"], "statement:stable-existing")
        self.assertEqual(selected_row["lease_fence"], 9)
        fresh = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Select Existing Outbox Or Prepare",
            {},
            {"Prepare Outbox Intent": {"json": draft}},
        )
        self.assertTrue(fresh["ok"], fresh)
        self.assertFalse(fresh["output"][0]["json"]["existing_outbox_replay"])
        stale = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Select Existing Outbox Or Prepare",
            {**existing, "delta_sha256": "b" * 64},
            {"Prepare Outbox Intent": {"json": draft}},
        )
        self.assertFalse(stale["ok"])

        receipt = {
            "batch_id": "statement:stable-existing",
            "actual_file_id": "actual-file:replay",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
            "verification_version": 1,
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": digest,
            "observed_payload_sha256": digest,
            "expected_count": 1,
            "observed_count": 1,
            "expected_amount_sum_minor": -100,
            "observed_amount_sum_minor": -100,
            "expected_account_balance": -100,
            "observed_account_balance": -100,
            "invariants_passed": True,
        }
        replay = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt Replay",
            receipt,
            {
                "Verify Recovery Contract": {"json": {
                    "outbox_row": selected_row,
                    "manifest": {
                        "actual_file_id": "actual-file:replay",
                        "account_id": "actual-account:EI_AMAZON",
                        "card_code": "EI_AMAZON",
                        "period_start": "2026-08-01",
                        "period_end": "2026-08-31",
                        "expected_statement_balance_minor": -100,
                    },
                }},
                "Read Back COMMITTED Recovery Replay": {"json": selected_row},
                "Read Back Exact Actual Verification Receipt Replay": {"json": receipt},
                "Read Back Released Recovery Writer Fence Replay": {"json": {
                    "resource_key": "actual:actual-file:replay",
                    "lease_owner": "n8n:recovery:statement:stable-existing",
                    "fencing_token": 9,
                    "released": True,
                }},
            },
        )
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["output"][0]["json"]["replay_readback_only"])

    def test_actual_writer_uses_import_delta_evidence_and_branch_safe_receipts(self) -> None:
        digest = "a" * 64
        root = {
            "outbox_row": {"idempotency_key": "statement:one", "batch_id": "statement:one"},
            "manifest": {
                "actual_file_id": "actual-file", "account_id": "account-1",
                "card_code": "ADCB_CASHBACK", "period_start": "2024-02-01",
                "period_end": "2024-02-29", "expected_statement_balance_minor": 999999,
                "historical_import": True,
            },
            "verification": {
                "account_id": "account-1", "card_code": "ADCB_CASHBACK",
                "expected_transactions": [{"imported_id": "statement:one"}],
                "expected_account_balance": 999999,
            },
        }
        observed_state = {"expected_account_balance": 12500, "observed_account_balance": 12500}
        contract = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json", "Build Recovery Verification Contract",
            observed_state,
            {
                "Verify Recovery Contract": {"json": root},
                "Read Back ACTUAL OBSERVED Recovery": {"json": observed_state},
            },
        )
        self.assertTrue(contract["ok"], contract)
        projected = contract["output"][0]["json"]
        self.assertNotIn("expected_account_balance", projected["verification"])
        self.assertEqual(projected["balance_evidence"], {"expected": 12500, "observed": 12500})
        missing_delta = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json", "Build Recovery Verification Contract",
            {"expected_account_balance": None, "observed_account_balance": None},
            {
                "Verify Recovery Contract": {"json": root},
                "Read Back ACTUAL OBSERVED Recovery": {"json": {
                    "expected_account_balance": None, "observed_account_balance": None,
                }},
            },
        )
        self.assertFalse(missing_delta["ok"])

        receipt = {
            "idempotency_key": "statement:one", "batch_id": "statement:one",
            "actual_file_id": "actual-file", "account_id": "account-1",
            "card_code": "ADCB_CASHBACK", "period_start": "2024-02-01",
            "period_end": "2024-02-29", "expected_payload_sha256": digest,
            "observed_payload_sha256": digest, "expected_count": 1, "observed_count": 1,
            "expected_amount_sum_minor": -100, "observed_amount_sum_minor": -100,
            "expected_account_balance": 12500, "observed_account_balance": 12500,
            "invariants_passed": True,
        }
        validated = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json", "Validate Stored Verification Receipt for Commit",
            receipt, {"Verify Recovery Contract": {"json": root}},
        )
        self.assertTrue(validated["ok"], validated)
        bad_balance = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json", "Validate Stored Verification Receipt for Commit",
            {**receipt, "observed_account_balance": 12499},
            {"Verify Recovery Contract": {"json": root}},
        )
        self.assertFalse(bad_balance["ok"])
        for invalid in (
            {**receipt, "expected_account_balance": None, "observed_account_balance": None},
            {**receipt, "expected_count": None, "observed_count": None},
            {**receipt, "expected_amount_sum_minor": None, "observed_amount_sum_minor": None},
        ):
            rejected = self.run_exported_workflow_node(
                "20-actual-outbox-apply.json", "Validate Stored Verification Receipt for Commit",
                invalid, {"Verify Recovery Contract": {"json": root}},
            )
            self.assertFalse(rejected["ok"])

        committed = {
            "batch_id": "statement:one", "state": "COMMITTED",
            "lease_owner": "n8n:recovery:statement:one", "lease_fence": 4,
        }
        final = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json", "Return Verified Commit Receipt",
            {},
            {
                "Read Back COMMITTED Recovery": {"json": committed},
                "Validate Stored Verification Receipt for Commit": {"json": receipt},
                "Read Back Released Recovery Writer Fence": {"json": {
                    "resource_key": "actual:actual-file", "lease_id": "11111111-1111-4111-8111-111111111111",
                    "lease_owner": committed["lease_owner"], "fencing_token": 4, "released": True,
                }},
                "Build Recovery Fence Release": {"json": {
                    "resource_key": "actual:actual-file", "lease_id": "11111111-1111-4111-8111-111111111111",
                    "lease_owner": committed["lease_owner"], "fencing_token": 4,
                }},
            },
        )
        self.assertTrue(final["ok"], final)

    def test_recovery_rehydrates_artifact_and_preserves_all_outbox_transitions(self) -> None:
        recovery = set(self.nodes("17-actual-outbox-recovery.json"))
        self.assertIn("Apply Nonterminal Outbox Safely", recovery)
        names = set(self.nodes("20-actual-outbox-apply.json"))
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

    def test_actual_writer_routes_canonical_delta_artifact_fields_to_download(self) -> None:
        workflow = self.workflow("20-actual-outbox-apply.json")
        nodes = self.nodes("20-actual-outbox-apply.json")
        self.assertEqual(
            workflow["connections"]["Prepared Outbox Input"]["main"][0][0]["node"],
            "Actual Writer Parameters",
        )
        self.assertEqual(
            workflow["connections"]["Actual Writer Parameters"]["main"][0][0]["node"],
            "Download Immutable Delta Artifact",
        )
        parameters = nodes["Actual Writer Parameters"]["parameters"]
        self.assertFalse(parameters["includeOtherFields"])
        assignments = {
            row["name"]: row["value"]
            for row in parameters["assignments"]["assignments"]
        }
        self.assertEqual(
            assignments["delta_artifact_item_id"],
            "={{ $json.delta_artifact_item_id }}",
        )
        self.assertEqual(
            assignments["delta_artifact_etag"],
            "={{ $json.delta_artifact_etag }}",
        )
        self.assertNotIn("artifact_item_id", assignments)
        self.assertNotIn("artifact_etag", assignments)
        self.assertEqual(
            nodes["Download Immutable Delta Artifact"]["parameters"]["fileId"],
            "={{ $json.delta_artifact_item_id }}",
        )

    def test_committed_actual_replay_is_readback_only_and_rejects_stale_receipts(self) -> None:
        digest = "a" * 64
        committed = {
            "batch_id": "outbox:replay-1",
            "idempotency_key": "outbox:replay-1",
            "actual_file_id": "actual-file:replay-1",
            "state": "COMMITTED",
            "lease_owner": "n8n:recovery:outbox:replay-1",
            "lease_fence": 7,
        }
        actual_schema = {
            column["name"]
            for column in self.nodes("19-platform-data-table-bootstrap.json")[
                "Create or Reuse finance_actual_batches"
            ]["parameters"]["columns"]["column"]
        }
        self.assertTrue(set(committed).issubset(actual_schema))
        receipt = {
            "batch_id": "outbox:replay-1",
            "idempotency_key": "outbox:replay-1",
            "actual_file_id": "actual-file:replay-1",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
            "verification_version": 1,
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": digest,
            "observed_payload_sha256": digest,
            "expected_count": 1,
            "observed_count": 1,
            "expected_amount_sum_minor": 100,
            "observed_amount_sum_minor": 100,
            "expected_account_balance": -100,
            "observed_account_balance": -100,
            "invariants_passed": True,
            "verified_at": "2026-08-20T00:00:00+00:00",
        }
        references = {
            "Verify Recovery Contract": {
                "json": {
                    "outbox_row": committed,
                    "manifest": {
                        "actual_file_id": "actual-file:replay-1",
                        "account_id": "actual-account:EI_AMAZON",
                        "card_code": "EI_AMAZON",
                        "period_start": "2026-08-01",
                        "period_end": "2026-08-31",
                        "expected_statement_balance_minor": -100,
                    },
                }
            },
            "Read Back COMMITTED Recovery Replay": {"json": committed},
            "Read Back Exact Actual Verification Receipt Replay": {"json": receipt},
        }
        connections = self.workflow("20-actual-outbox-apply.json")["connections"]
        replay_route = ["Verify Recovery Contract", "Route Recovery State"]
        next_node = connections["Route Recovery State"]["main"][3][0]["node"]
        while next_node in connections:
            replay_route.append(next_node)
            outputs = connections[next_node]["main"]
            self.assertEqual(len(outputs), 1)
            self.assertEqual(len(outputs[0]), 1)
            next_node = outputs[0][0]["node"]
        replay_route.append(next_node)
        self.assertEqual(
            replay_route,
            [
                "Verify Recovery Contract",
                "Route Recovery State",
                "Read Back COMMITTED Recovery Replay",
                "Read Back Exact Actual Verification Receipt Replay",
                "Return Verified Commit Receipt Replay",
            ],
        )
        self.assertEqual(
            set(references),
            set(replay_route) - {"Route Recovery State", "Return Verified Commit Receipt Replay"},
        )
        replay = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt Replay",
            receipt,
            references,
        )
        self.assertTrue(replay["ok"], replay)
        self.assertTrue(replay["output"][0]["json"]["replay_readback_only"])
        self.assertEqual(replay["output"][0]["json"]["state"], "COMMITTED")
        self.assertTrue(replay["output"][0]["json"]["writer_release_verified"])
        fresh = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Compare Exact Actual Verification Receipt",
            receipt,
            {
                "Verify Recovery Contract": {"json": {
                    "outbox_row": committed,
                    "manifest": {"expected_statement_balance_minor": -100},
                }},
                "Recovery Verify Actual": {"json": {"actual": {
                    "expected_sha256": digest,
                    "observed_sha256": digest,
                    "transaction_count": 1,
                    "amount_sum": 100,
                    "account_balance": -100,
                }}},
            },
        )
        self.assertTrue(fresh["ok"], fresh)
        bad_balance = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Compare Exact Actual Verification Receipt",
            {**receipt, "observed_account_balance": -101},
            {
                "Verify Recovery Contract": {"json": {
                    "outbox_row": committed,
                    "manifest": {"expected_statement_balance_minor": -100},
                }},
                "Recovery Verify Actual": {"json": {"actual": {
                    "expected_sha256": digest,
                    "observed_sha256": digest,
                    "transaction_count": 1,
                    "amount_sum": 100,
                    "account_balance": -100,
                }}},
            },
        )
        self.assertFalse(bad_balance["ok"])
        normal = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt",
            receipt,
            {
                "Read Back COMMITTED Recovery": {"json": committed},
                "Validate Stored Verification Receipt for Commit": {"json": receipt},
                "Build Recovery Fence Release": {"json": {
                    "resource_key": "actual:actual-file:replay",
                    "lease_id": "00000000-0000-4000-8000-000000000007",
                    "lease_owner": "n8n:recovery:outbox:replay-1",
                    "fencing_token": 7,
                }},
                "Read Back Released Recovery Writer Fence": {"json": {
                    "resource_key": "actual:actual-file:replay",
                    "lease_id": "00000000-0000-4000-8000-000000000007",
                    "lease_owner": "n8n:recovery:outbox:replay-1",
                    "fencing_token": 7,
                    "released": True,
                }},
            },
        )
        self.assertTrue(normal["ok"], normal)
        self.assertTrue(normal["output"][0]["json"]["writer_release_verified"])
        unreleased_normal = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt",
            receipt,
            {
                "Read Back COMMITTED Recovery": {"json": committed},
                "Validate Stored Verification Receipt for Commit": {"json": receipt},
                "Build Recovery Fence Release": {"json": {
                    "resource_key": "actual:actual-file:replay",
                    "lease_id": "00000000-0000-4000-8000-000000000007",
                    "lease_owner": "n8n:recovery:outbox:replay-1",
                    "fencing_token": 7,
                }},
                "Read Back Released Recovery Writer Fence": {"json": {"released": False}},
            },
        )
        self.assertFalse(unreleased_normal["ok"])
        invalid_cases = (
            ({**committed, "state": "ACTUAL_OBSERVED"}, receipt),
            (committed, {**receipt, "card_code": "RAK_WORLD"}),
            (committed, {**receipt, "observed_payload_sha256": "b" * 64}),
            (committed, {**receipt, "observed_account_balance": -101}),
        )
        for invalid_committed, invalid_receipt in invalid_cases:
            rejected = self.run_exported_workflow_node(
                "20-actual-outbox-apply.json",
                "Return Verified Commit Receipt Replay",
                invalid_receipt,
                {
                    **references,
                    "Read Back COMMITTED Recovery Replay": {"json": invalid_committed},
                    "Read Back Exact Actual Verification Receipt Replay": {"json": invalid_receipt},
                },
            )
            self.assertFalse(rejected["ok"])
        later_lease_state_is_irrelevant = self.run_exported_workflow_node(
            "20-actual-outbox-apply.json",
            "Return Verified Commit Receipt Replay",
            receipt,
            {
                **references,
                "Read Back Released Recovery Writer Fence Replay": {"json": {
                    "resource_key": "actual:actual-file:replay-1",
                    "lease_owner": "n8n:recovery:outbox:replay-1",
                    "fencing_token": 7,
                    "released": False,
                }},
            },
        )
        self.assertTrue(later_lease_state_is_irrelevant["ok"], later_lease_state_is_irrelevant)

    def test_writer_lease_uses_only_fixed_parameterized_postgres_functions(self) -> None:
        workflow = self.workflow("18-finance-writer-lease.json")
        validator = next(node for node in workflow["nodes"] if node["name"] == "Validate Fixed Lease Operation")
        self.assertNotIn("crypto.randomUUID", validator["parameters"]["jsCode"])
        postgres = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.postgres"]
        self.assertEqual(len(postgres), 3)
        queries = "\n".join(node["parameters"]["query"] for node in postgres)
        for function in ("finance_ops.acquire_writer_lease", "finance_ops.assert_writer_lease", "finance_ops.release_writer_lease"):
            self.assertIn(function, queries)
        acquire = next(node for node in postgres if "acquire_writer_lease" in node["parameters"]["query"])
        self.assertEqual(acquire["parameters"]["query"].count("$"), 3)
        self.assertNotIn("$json.lease_id", acquire["parameters"]["options"]["queryReplacement"])
        self.assertNotIn("={{", queries)
        self.assertTrue(all("$1" in node["parameters"]["query"] for node in postgres))
        migration = (N8N / "postgres" / "001-finance-writer-lease.sql").read_text(encoding="utf-8")
        signature = migration[migration.index("CREATE OR REPLACE FUNCTION finance_ops.acquire_writer_lease"):migration.index(") RETURNS TABLE", migration.index("CREATE OR REPLACE FUNCTION finance_ops.acquire_writer_lease"))]
        self.assertEqual(signature.count("text"), 2)
        self.assertNotIn("p_lease_id", signature)
        self.assertIn("gen_random_uuid()", migration)
        self.assertIn("ON CONFLICT ON CONSTRAINT writer_leases_pkey DO UPDATE", migration)
        self.assertIn("DROP FUNCTION IF EXISTS finance_ops.acquire_writer_lease(text, uuid, text, integer);", migration)
        for term in ("ON CONFLICT ON CONSTRAINT writer_leases_pkey DO UPDATE", "current.fencing_token + 1", "current.expires_at <= clock_timestamp()", "assert_writer_lease", "release_writer_lease"):
            self.assertIn(term, migration)
        release_body = migration[migration.index("CREATE OR REPLACE FUNCTION finance_ops.release_writer_lease"):]
        self.assertIn("IF changed = 1 THEN", release_body)
        self.assertIn("AND released_at IS NOT NULL", release_body)

    def test_error_workflow_redacts_then_upserts_reads_compares_and_marks(self) -> None:
        workflow = self.workflow("16-operations-error-handler.json")
        names = [
            node["name"] for node in workflow["nodes"]
            if node["type"] != "n8n-nodes-base.stickyNote"
        ]
        self.assertEqual(names[:7], [
            "Finance Workflow Failed", "Redact and Classify Failure",
            "Upsert Durable Failure Receipt", "Read Back Failure Receipt",
            "Compare Failure Receipt Readback", "Mark Failure Readback Verified",
            "Read Back Verified Failure Receipt",
        ])
        self.assertEqual(names[7:], [
            "Route Retryable Provider Failure", "Read Provider Circuit after Failure",
            "Build OPEN Provider Circuit", "Upsert OPEN Provider Circuit",
            "Read Back OPEN Provider Circuit", "Verify OPEN Circuit Readback",
        ])
        code = self.nodes("16-operations-error-handler.json")["Redact and Classify Failure"]["parameters"]["jsCode"]
        self.assertIn("[REDACTED]", code)
        self.assertIn("provider_code", code)
        sink = self.nodes("16-operations-error-handler.json")["Upsert Durable Failure Receipt"]["parameters"]["jsCode"]
        readback = self.nodes("16-operations-error-handler.json")["Read Back Failure Receipt"]["parameters"]["jsCode"]
        self.assertIn("customData", sink)
        self.assertIn("customData", readback)
        self.assertIn("finance_failure_receipt_v1_", sink)
        self.assertIn("KEY_COUNT_TOO_LARGE", sink)
        self.assertIn("KEY_TOO_LARGE", sink)
        self.assertIn("length > 255", sink)
        self.assertIn("finance_failure_receipt_v1_readback_verified", self.nodes("16-operations-error-handler.json")["Mark Failure Readback Verified"]["parameters"]["jsCode"])
        self.assertIn("FAILURE_RECEIPT_EXECUTION_LOG_READBACK_MISMATCH", readback)
        self.assertIn("PROVIDER_CIRCUIT_READBACK_MISMATCH", self.nodes("16-operations-error-handler.json")["Verify OPEN Circuit Readback"]["parameters"]["jsCode"])
        receipt = {
            "execution_id": "exec-1",
            "workflow_id": "workflow-1",
            "workflow_name": "Finance Failure Handler",
            "workflow_code": "OPERATIONS_ERROR_HANDLER",
            "run_id": "exec-1",
            "provider_code": "N8N_API",
            "error_class": "TERMINAL",
            "error_message_redacted": "safe failure [REDACTED]",
            "first_seen_at": "2026-08-23T00:00:00.000Z",
        }
        lifecycle = self.run_failure_receipt_lifecycle(receipt)
        self.assertTrue(lifecycle["ok"], lifecycle)
        self.assertTrue(lifecycle["terminal"]["readback_verified"])
        self.assertEqual(lifecycle["values"]["finance_failure_receipt_v1_readback_verified"], "true")
        self.assertEqual(len(lifecycle["values"]), 10)
        self.assertTrue(all(len(key) <= 50 for key in lifecycle["values"]))
        self.assertTrue(all(len(value) <= 255 for value in lifecycle["values"].values()))
        at_value_boundary = self.run_failure_receipt_lifecycle({**receipt, "workflow_name": "x" * 255})
        self.assertTrue(at_value_boundary["ok"], at_value_boundary)
        missing_marker = self.run_failure_receipt_lifecycle(receipt, drop_terminal_marker=True)
        self.assertFalse(missing_marker["ok"])
        oversized = self.run_failure_receipt_lifecycle({**receipt, "workflow_name": "x" * 256})
        self.assertFalse(oversized["ok"])
        self.assertIn("FIELD_TOO_LARGE:workflow_name", oversized["error"])
        key_overflow = self.run_failure_receipt_lifecycle(receipt, key_prefix="k" * 51)
        self.assertFalse(key_overflow["ok"])
        self.assertIn("KEY_TOO_LARGE", key_overflow["error"])
        key_count_overflow = self.run_failure_receipt_lifecycle(receipt, extra_field=True)
        self.assertFalse(key_count_overflow["ok"])
        self.assertIn("KEY_COUNT_TOO_LARGE", key_count_overflow["error"])

    def test_ai_contract_uses_subscription_runner_and_value_domains(self) -> None:
        handoff = load_json(N8N / "contracts" / "subscription-agent-handoff-v1.schema.json")
        proposal = load_json(N8N / "contracts" / "ai-proposal-v1.schema.json")
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema:
            jsonschema.validators.validator_for(handoff).check_schema(handoff)
            jsonschema.validators.validator_for(proposal).check_schema(proposal)
            base = {
                "schema_version": 1,
                "job_id": f"finance-ai:{'a' * 64}",
                "idempotency_key": "a" * 64,
                "agent_provider": "CODEX_SUBSCRIPTION",
                "policy_id": "classify-unresolved",
                "policy_class": "NORMAL",
                "policy_sha256": "b" * 64,
                "config_sha256": "c" * 64,
                "output_schema_sha256": "d" * 64,
                "runner_receipt_id": "receipt-1",
                "runner_model": "gpt-5.6-luna",
                "runner_reasoning_effort": "max",
                "auth_mode": "CHATGPT_SUBSCRIPTION",
            }
            typed_values = {
                "vendor": "Carrefour",
                "tags": ["grocery", "in_store"],
                "review_required": False,
                "category_recommendation": {
                    "name": "Groceries", "group": "Living", "reason": "Exact merchant",
                },
                "rule_recommendation": {"enabled": False, "evidence_count": 3},
            }
            for field, value in typed_values.items():
                result = {
                    **base,
                    "proposals": [{
                        "transaction_id": "actual:fixture:1",
                        "field": field,
                        "value": value,
                        "confidence": 0.95,
                        "reason_code": "FIXTURE_MATCH",
                    }],
                }
                jsonschema.validate(result, proposal)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({
                    **base,
                    "proposals": [{
                        "transaction_id": "actual:fixture:1",
                        "field": "vendor",
                        "value": False,
                        "confidence": 0.95,
                        "reason_code": "TYPE_MISMATCH",
                    }],
                }, proposal)
        proposal_item = proposal["properties"]["proposals"]["items"]
        self.assertIn("value", proposal_item["required"])
        self.assertNotIn("value_json", json.dumps(proposal_item))
        self.assertEqual(len(proposal_item["oneOf"]), 5)
        unresolved = handoff["$defs"]["unresolved"]
        self.assertIn("allowed_values", unresolved["required"])
        self.assertEqual(unresolved["properties"]["allowed_values"]["maxProperties"], 10)
        self.assertEqual(proposal["properties"]["auth_mode"]["const"], "CHATGPT_SUBSCRIPTION")
        self.assertEqual(handoff["properties"]["agent_provider"]["const"], "CODEX_SUBSCRIPTION")

    def test_ai_workflow_derives_profile_enforces_domains_and_omits_internal_hash(self) -> None:
        nodes = self.nodes("09-ai-proposal.json")
        untrusted = nodes["Validate Untrusted Proposal Request"]["parameters"]["jsCode"]
        validation = nodes["Build Authoritative Redacted Proposal Job"]["parameters"]["jsCode"]
        response = nodes["Validate Proposal Schema and Policy Boundary"]["parameters"]["jsCode"]
        for forbidden in ("agent_profile", "policy_sha256", "config_sha256", "output_schema_sha256"):
            self.assertIn(f"'{forbidden}'", untrusted)
        policy_node = json.dumps(nodes["Read Active Server AI Policy Contract"])
        self.assertIn("application-contract-bundle.json", policy_node)
        self.assertNotIn("finance_ai_policy_contracts", policy_node)
        compact_validation = re.sub(r"\s+", "", validation)
        self.assertIn("LUNA_MAX:'NORMAL'", compact_validation)
        self.assertIn("SOL_MEDIUM:'EXCEPTION'", compact_validation)
        self.assertIn("agent_provider", validation)
        self.assertIn("Missing bounded server value domain", validation)
        self.assertIn("request_canonical", validation)
        self.assertIn("Proposal outside configured domain", response)
        self.assertIn("Duplicate proposal field", response)
        self.assertIn("Agent proposal envelope mismatch", response)
        self.assertNotIn("CLAUDE_SUBSCRIPTION", response)
        self.assertNotIn("claude-sonnet-4-6", response)
        handoff_code = nodes["Build Idempotent Agent Handoff"]["parameters"]["jsCode"]
        self.assertIn("agent_provider: request.agent_provider", handoff_code)
        adapter = nodes["Invoke Subscription Agent Adapter"]
        self.assertEqual(adapter["type"], "n8n-nodes-base.executeWorkflow")
        self.assertEqual(
            adapter["parameters"]["workflowId"]["value"],
            self.workflow("21-subscription-agent-adapter.json")["id"],
        )

    def test_ai_submitted_readback_matches_the_canonical_table_schema(self) -> None:
        workflow = self.workflow("09-ai-proposal.json")
        digest = "a" * 64
        expected = {
            "job_id": f"finance-ai:{digest}",
            "idempotency_key": digest,
            "policy_id": "classify-unresolved",
            "policy_sha256": "b" * 64,
            "config_sha256": "c" * 64,
            "output_schema_sha256": "d" * 64,
            "request_sha256": digest,
        }
        # finance_ai_reviews intentionally has no job_id column. The transport
        # job id is deterministically derived from the persisted idempotency key.
        observed = {key: value for key, value in expected.items() if key != "job_id"}
        result = self.run_exported_workflow_node(
            "09-ai-proposal.json",
            "Compare SUBMITTED Agent Job Readback",
            observed,
            {"Build Idempotent Agent Handoff": {"json": expected}},
        )
        self.assertTrue(result["ok"], result)

    def test_ai_redacted_context_is_small_useful_and_excludes_identifiers(self) -> None:
        workflow = self.workflow("09-ai-proposal.json")
        result = self.run_exported_workflow_node(
            "09-ai-proposal.json",
            "Validate Untrusted Proposal Request",
            {
                "policy_id": "classify-unresolved",
                "unresolved": [{
                    "transaction_id": "actual:fixture:1",
                    "allowed_fields": ["category"],
                    "redacted_context": {
                        "merchant": "  Grocery Store  ",
                        "currency": "AED",
                        "source_message_id": "mailbox-secret",
                        "email": "person@example.test",
                        "description": "card 4111 1111 1111 1111",
                    },
                }],
            },
            {},
        )
        self.assertTrue(result["ok"], result)
        context = result["output"][0]["json"]["unresolved"][0]["redacted_context"]
        self.assertEqual(context, {"merchant": "Grocery Store", "currency": "AED"})

        adapter = self.nodes("21-subscription-agent-adapter.json")[
            "Validate and Build Fixed Provider Invocation"
        ]["parameters"]["jsCode"]
        request_source = adapter[adapter.index("const request = {"):adapter.index("const prompt = [")]
        for private_field in ("archive_sha256", "evidence_replay_keys", "archive_identity_keys", "archive_item_ids"):
            self.assertNotIn(f"{private_field}:", request_source)

    def test_ai_policy_targets_are_complete_and_profile_owned(self) -> None:
        policies = load_json(ROOT / "config" / "ai-policies.json")["policies"]
        target_contract = load_json(N8N / "ai-policy-targets.json")
        configured = {target for policy in policies for target in policy["target_fields"]}
        self.assertEqual(configured, set(target_contract["target_fields"]))
        schema = load_json(N8N / "contracts" / "subscription-agent-handoff-v1.schema.json")
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
            [sys.executable, str(N8N / "generate_platform_bootstrap.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = load_json(N8N / "generated" / "platform-bootstrap-manifest.json")
        self.assertEqual(manifest["contract_status"], "SPEC_ONLY")
        self.assertEqual(manifest["n8n_version"], "2.36.2")
        evidence = manifest["execution_evidence"]
        self.assertTrue(evidence["exact_image_import_tested"])
        self.assertTrue(evidence["disposable_create_reuse_tested"])
        self.assertTrue(evidence["table_list_readback_tested"])
        self.assertTrue(evidence["seed_write_result_tested"])
        self.assertFalse(evidence["source_workflow_unmodified_import_tested"])
        self.assertFalse(evidence["seed_independent_readback_tested"])
        self.assertFalse(evidence["production_validated"])
        self.assertRegex(evidence["github_actions_run"], r"^https://github\.com/.+/actions/runs/\d+/job/\d+$")
        self.assertIn("SOURCE_MIGRATION_GATE_REQUIRED", manifest["activation_blockers"])
        self.assertIn("LEGACY_SOURCE_ROWS_RESTORE_REQUIRED", manifest["activation_blockers"])
        self.assertIn(
            "SOURCE_MIGRATION_GATE_REQUIRED",
            self.workflow("19-platform-data-table-bootstrap.json")["meta"]["activationBlockers"],
        )
        self.assertIn(
            "LEGACY_SOURCE_ROWS_RESTORE_REQUIRED",
            self.workflow("19-platform-data-table-bootstrap.json")["meta"]["activationBlockers"],
        )
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
        self.assertEqual(
            manifest["sources"]["config_version_seed_sha256"],
            git_canonical_sha256(N8N / "generated" / "config-versions.seed.json"),
        )
        self.assertEqual(
            manifest["sources"]["application_contract_bundle_sha256"],
            git_canonical_sha256(N8N / "generated" / "application-contract-bundle.json"),
        )
        self.assertEqual(
            manifest["sources"]["application_contract_bundle_schema_sha256"],
            git_canonical_sha256(N8N / "generated" / "application-contract-bundle.schema.json"),
        )
        result = subprocess.run(
            [sys.executable, str(N8N / "compile_config_versions.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_platform_bootstrap_reuses_migration_generator_schema_digest(self) -> None:
        result = subprocess.run(
            [sys.executable, str(N8N / "generate_data_table_migration.py"), "--schema-digest"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        canonical_digest = result.stdout.strip()
        self.assertRegex(canonical_digest, r"^[0-9a-f]{64}$")

        manifest = load_json(N8N / "generated" / "platform-bootstrap-manifest.json")
        workflow = self.workflow("19-platform-data-table-bootstrap.json")
        nodes = self.nodes("19-platform-data-table-bootstrap.json")
        self.assertEqual(manifest["target_schema_contract"]["digest"], canonical_digest)
        self.assertEqual(workflow["meta"]["targetSchemaDigest"], canonical_digest)
        self.assertIn(canonical_digest, nodes["Verify Four-Table Target Contract"]["parameters"]["jsCode"])
        self.assertIn(canonical_digest, nodes["Emit Redacted Bootstrap Receipt"]["parameters"]["jsCode"])

    def test_retained_four_table_readback_receipt_schema_accepts_redacted_fixture(self) -> None:
        schema = load_json(N8N / "schemas" / "finance-data-table-readback-receipt-v1.schema.json")
        Draft202012Validator.check_schema(schema)
        raw = load_json(ROOT / "tests" / "fixtures" / "n8n-2.36.2-data-table-digest-output.json")["raw_stdout"]
        prefix = "finance data table digest verified:"
        payload = json.loads(next(line[len(prefix):] for line in raw.splitlines() if line.startswith(prefix)))
        Draft202012Validator(schema).validate(payload)
        self.assertEqual([table["row_count"] for table in payload["tables"]], [4, 3, 5, 5])
        self.assertFalse(payload["migration_receipt"]["bound"])
        self.assertFalse(payload["forward_gate"]["command_executed"])
        self.assertFalse(payload["rollback_gate"]["command_executed"])

    def test_platform_bootstrap_check_detects_stale_artifact(self) -> None:
        generator = load_bootstrap_generator()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = tuple(root / name for name in ("bundle", "schema", "manifest", "workflow"))
            expected = ("bundle\n", "schema\n", "manifest\n", "workflow\n")
            for path, text in zip(paths, expected):
                path.write_text(text, encoding="utf-8")
            with mock.patch.object(generator, "BUNDLE_PATH", paths[0]), \
                mock.patch.object(generator, "BUNDLE_SCHEMA_PATH", paths[1]), \
                mock.patch.object(generator, "MANIFEST_PATH", paths[2]), \
                mock.patch.object(generator, "WORKFLOW_PATH", paths[3]), \
                mock.patch.object(generator, "render", return_value=expected):
                self.assertEqual(generator.main(["--check"]), 0)
                paths[0].write_text("stale\n", encoding="utf-8")
                self.assertEqual(generator.main(["--check"]), 1)
                with self.assertRaises(SystemExit) as error:
                    generator.main(["--check", "--write"])
                self.assertEqual(error.exception.code, 2)

    def test_application_contract_bundle_is_schema_valid_ordered_and_self_hashed(self) -> None:
        bundle_path = N8N / "generated" / "application-contract-bundle.json"
        schema_path = N8N / "generated" / "application-contract-bundle.schema.json"
        bundle = load_json(bundle_path)
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(bundle)
        self.assertEqual(bundle["schema_version"], 1)
        self.assertEqual(bundle["contract_status"], "SPEC_ONLY")
        self.assertEqual(bundle["schema_sha256"], git_canonical_sha256(schema_path))
        self.assertEqual(bundle["resolver_order"], ["W02", "W04", "W05", "W09"])
        self.assertEqual(
            [resolver["workflow_code"] for resolver in bundle["resolver_maps"]],
            bundle["resolver_order"],
        )
        payload = dict(bundle)
        payload.pop("bundle_content_sha256")
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        self.assertEqual(
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            bundle["bundle_content_sha256"],
        )
        expected_sources = {
            "config/statement-sources.json",
            "config/transaction-email-sources.json",
            "config/ai-policies.json",
        }
        self.assertEqual({source["path"] for source in bundle["source_documents"]}, expected_sources)
        for source in bundle["source_documents"]:
            self.assertEqual(source["sha256"], git_canonical_sha256(ROOT / source["path"]))
        self.assertEqual(
            [resolver["sources"][0]["path"] for resolver in bundle["resolver_maps"]],
            [
                "config/transaction-email-sources.json",
                "config/statement-sources.json",
                "config/statement-sources.json",
                "config/ai-policies.json",
            ],
        )
        workflow = self.workflow("19-platform-data-table-bootstrap.json")
        nodes = self.nodes("19-platform-data-table-bootstrap.json")
        self.assertEqual(
            nodes["Load and Validate Application Contract Bundle"]["type"],
            "n8n-nodes-base.code",
        )
        self.assertEqual(
            nodes["SHA-256 Application Contract Bundle"]["parameters"]["type"],
            "SHA256",
        )
        verify = nodes["Verify Application Contract Bundle Digest and Maps"]["parameters"]["jsCode"]
        self.assertIn("APPLICATION_CONTRACT_BUNDLE_DIGEST_MISMATCH", verify)
        self.assertIn("APPLICATION_CONTRACT_BUNDLE_RESOLVER_MAP_INVALID", verify)
        self.assertEqual(
            workflow["connections"]["Manual Platform Bootstrap Only"]["main"][0][0]["node"],
            "Load and Validate Application Contract Bundle",
        )
        duplicate_key = deepcopy(bundle)
        duplicate_key["resolver_maps"][0]["entries"][1]["key"] = duplicate_key["resolver_maps"][0]["entries"][0]["key"]
        with self.assertRaises(ValidationError):
            validator.validate(duplicate_key)
        arbitrary_source_contract = deepcopy(bundle)
        arbitrary_source_contract["source_contracts"][0]["contract"]["unapproved"] = True
        with self.assertRaises(ValidationError):
            validator.validate(arbitrary_source_contract)
        arbitrary_policy_contract = deepcopy(bundle)
        arbitrary_policy_contract["ai_policy_contracts"][0]["contract"]["unapproved"] = True
        with self.assertRaises(ValidationError):
            validator.validate(arbitrary_policy_contract)

    def test_application_contract_bundle_preserves_transaction_semantics_and_unique_ids(self) -> None:
        transaction_sources = load_json(ROOT / "config" / "transaction-email-sources.json")["sources"]
        expected_codes = [row["code"] for row in transaction_sources]
        self.assertEqual(expected_codes, [
            "RAKBANK_CARD_TRANSACTION",
            "STANDARD_CHARTERED_CARD_TRANSACTION",
        ])
        bundle = load_json(N8N / "generated" / "application-contract-bundle.json")
        transaction_contracts = [
            row for row in bundle["source_contracts"]
            if row["source_path"] == "config/transaction-email-sources.json"
        ]
        self.assertEqual([row["source_code"] for row in transaction_contracts], expected_codes)
        self.assertEqual(
            [row["key"] for row in bundle["resolver_maps"][0]["entries"]],
            expected_codes,
        )
        self.assertEqual(len({row["source_code"] for row in transaction_contracts}), len(expected_codes))

        generator = load_bootstrap_generator()
        documents = {
            path: load_json(ROOT / path)
            for path in generator.APPLICATION_CONFIG_PATHS
        }
        documents["config/transaction-email-sources.json"]["sources"][1]["code"] = expected_codes[0]
        with self.assertRaisesRegex(ValueError, "duplicate code identities"):
            generator.build_source_contracts(documents, bundle["source_documents"])

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
                "n8n-nodes-base.crypto",
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
        matrix = load_json(N8N / "data-table-migration-matrix.json")
        expected = {
            target: [
                {"name": field, "type": definition["type"]}
                for field, definition in schema["columns"].items()
            ]
            for target, schema in matrix["target_schemas"].items()
        }
        referenced_legacy = {
            table["source_table"]
            for table in matrix["tables"]
            if table.get("node_references")
        }
        expected.update({
            table["name"]: [
                {"name": field, "type": column_type}
                for field, column_type in table["columns"].items()
            ]
            for table in self.tables["tables"]
            if table["name"] in referenced_legacy
        })
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
            self.assertEqual(parameters["columns"]["column"], expected[name])
        self.assertEqual(
            [node["parameters"]["tableName"] for node in creates],
            [
                table["name"]
                for table in load_bootstrap_generator().compatibility_table_rows(self.tables, matrix)
            ] + matrix["targets"],
        )
        row_nodes = [
            node for node in workflow["nodes"]
            if node["type"] == "n8n-nodes-base.dataTable"
            and node["parameters"].get("resource") == "row"
        ]
        self.assertEqual([node["name"] for node in row_nodes], ["Upsert Disabled Source Contract Templates"])
        self.assertEqual(workflow["meta"]["targetTables"], matrix["targets"])
        self.assertFalse(workflow["meta"]["legacyTableCreationForbidden"])
        self.assertEqual(
            set(workflow["meta"]["legacyCompatibilityTables"]),
            referenced_legacy,
        )
        self.assertEqual(
            set(workflow["meta"]["legacyTablesNotProvisioned"]),
            {table["name"] for table in self.tables["tables"]} - referenced_legacy,
        )
        self.assertTrue(workflow["meta"]["existingTableDeletionForbidden"])
        self.assertFalse(workflow["meta"]["seedWritesForbidden"])
        self.assertTrue(workflow["meta"]["sourceContractTemplatesSeededDisabled"])

    def test_platform_bootstrap_seeds_only_disabled_legacy_templates(self) -> None:
        nodes = self.nodes("19-platform-data-table-bootstrap.json")
        data_nodes = [
            node for node in nodes.values()
            if node["type"] == "n8n-nodes-base.dataTable"
        ]
        self.assertTrue(data_nodes)
        row_nodes = [node for node in data_nodes if node["parameters"].get("resource") == "row"]
        self.assertEqual([node["name"] for node in row_nodes], ["Upsert Disabled Source Contract Templates"])
        self.assertEqual(row_nodes[0]["parameters"]["dataTableId"]["value"], "finance_source_contracts")
        # Declared-but-unreferenced legacy schemas are preserved when already
        # present, but a fresh compatibility bootstrap does not create them.
        self.assertFalse(any(node["parameters"].get("tableName") == "finance_ai_policy_contracts" for node in data_nodes))
        self.assertFalse(any(node["parameters"].get("tableName") == "finance_config_versions" for node in data_nodes))
        template_code = nodes["Emit Disabled Source Contract Templates"]["parameters"]["jsCode"]
        self.assertIn("enabled: false", template_code)
        self.assertIn("template:", template_code)
        self.assertEqual(
            nodes["Verify Application Contract Bundle Digest and Maps"]["type"],
            "n8n-nodes-base.code",
        )
        target_guard = nodes["Verify Four-Table Target Contract"]["parameters"]["jsCode"]
        self.assertIn("TARGET_TABLE_SET_MISMATCH", target_guard)
        self.assertIn("TARGET_SCHEMA_TYPE_UNSUPPORTED", target_guard)
        receipt = nodes["Emit Redacted Bootstrap Receipt"]["parameters"]["jsCode"]
        compact_receipt = re.sub(r"\s+", "", receipt)
        for marker in ("runtime_cutover:false", "deletion_authorized:false", "second_run_noop:true", "mode:'0600'"):
            self.assertIn(marker, compact_receipt)

    def test_platform_bootstrap_readback_rejects_partial_extra_type_and_id_drift(self) -> None:
        workflow = self.workflow("19-platform-data-table-bootstrap.json")
        nodes = self.nodes("19-platform-data-table-bootstrap.json")
        verifier = nodes["Verify Four Target Table Readback"]["parameters"]["jsCode"]
        matrix = load_json(N8N / "data-table-migration-matrix.json")
        contract = {
            "target_tables": matrix["targets"],
            # The W19 guard emits the canonical n8n Data Table column-array
            # shape; keep the executable fixture aligned with that runtime
            # payload rather than only exercising the matrix's map shape.
            "target_schemas": {
                target: {
                    **schema,
                    "columns": [
                        {"name": field, "type": definition["type"]}
                        for field, definition in schema["columns"].items()
                    ],
                }
                for target, schema in matrix["target_schemas"].items()
            },
            "target_schema_digest": workflow["meta"]["targetSchemaDigest"],
        }
        created = {
            target: {"id": f"table-id-{index}"}
            for index, target in enumerate(matrix["targets"], start=1)
        }
        valid_rows = [
            {
                "name": target,
                "id": created[target]["id"],
                "columns": [
                    {"name": field, "type": definition["type"]}
                    for field, definition in matrix["target_schemas"][target]["columns"].items()
                ],
            }
            for target in matrix["targets"]
        ]
        native_readback_rows = [
            {
                **row,
                "columns": list(reversed(row["columns"])),
            }
            if row["name"] == "finance_ingestion_state" else row
            for row in valid_rows
        ]

        def run(rows: list[dict]) -> subprocess.CompletedProcess[str]:
            harness = f"""
const contract = {json.dumps(contract)};
const created = {json.dumps(created)};
const inputRows = {json.dumps(rows)};
function $(name) {{
  if (name === 'Verify Four-Table Target Contract') return {{ first: () => ({{ json: contract }}) }};
  return {{ first: () => ({{ json: created[name.replace('Create or Reuse ', '')] || {{}} }}) }};
}}
const $input = {{ all: () => inputRows.map(json => ({{ json }})) }};
const execute = () => {{ {verifier} }};
try {{ console.log(JSON.stringify(execute())); }} catch (error) {{ console.error(String(error.message)); process.exit(1); }}
"""
            return subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=False)

        valid = run(valid_rows)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("TARGET_SCHEMA_READBACK_VERIFIED", valid.stdout)
        self.assertEqual(run(valid_rows).stdout, valid.stdout, "second readback must be a deterministic no-op")
        native = run(native_readback_rows)
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertEqual(native.stdout, valid.stdout, "native column order must be canonicalized")

        cases = [
            (valid_rows[:-1], "TARGET_TABLE_MISSING"),
            ([{**row, "columns": [*row["columns"], {"name": "unexpected", "type": "string"}]} if row["name"] == matrix["targets"][0] else row for row in valid_rows], "TARGET_SCHEMA_MISMATCH"),
            ([{**row, "columns": [{**column, "type": "number"} if index == 0 and row["name"] == matrix["targets"][0] else column for index, column in enumerate(row["columns"])]} for row in valid_rows], "TARGET_SCHEMA_MISMATCH"),
            ([{**row, "id": "different-id"} if row["name"] == matrix["targets"][0] else row for row in valid_rows], "TARGET_TABLE_ID_MISMATCH"),
            ([*valid_rows, {"name": "finance_documents_extra", "id": "extra", "columns": []}], "TARGET_TABLE_EXTRA"),
        ]
        for rows, marker in cases:
            result = run(rows)
            self.assertNotEqual(result.returncode, 0, marker)
            self.assertIn(marker, result.stderr, result.stderr)

    def test_mcp_facade_dispatch_is_durably_audited_and_read_back(self) -> None:
        facade = self.nodes("15-finance-mcp-facade.json")
        for name in (
            "finance.status.v1",
            "finance.reviewed-artifact.handoff.v1",
            "finance.document.request.v1",
        ):
            params = facade[name]["parameters"]
            self.assertEqual(params["workflowId"]["value"], "10000000-0000-4000-8000-000000000010")
            self.assertIn("_mcp_request_id", params["workflowInputs"]["value"])
        artifact_inputs = facade["finance.reviewed-artifact.handoff.v1"]["parameters"]["workflowInputs"]["value"]
        self.assertEqual(set(artifact_inputs), {"_mcp_request_id", "operation_code", "artifact_id"})
        self.assertNotIn("expected_sha256", artifact_inputs)
        self.assertIn("server-owned", facade["finance.reviewed-artifact.handoff.v1"]["parameters"]["description"])
        nodes = self.nodes("10-finance-operations-status.json")
        for name in (
            "Upsert ACCEPTED MCP Request", "Read Back ACCEPTED MCP Request",
            "Upsert Terminal MCP Request", "Read Back Terminal MCP Request",
            "Mark MCP Receipt Verified", "Read Verified MCP Receipt",
        ):
            self.assertIn("finance_mcp_requests", json.dumps(nodes[name]))
        terminal = nodes["Build Redacted MCP Terminal Receipt"]["parameters"]["jsCode"]
        self.assertIn("[REDACTED]", terminal)
        self.assertIn("FAILED", terminal)
        dispatch_validation = nodes["Validate Bounded MCP Dispatch"]["parameters"]["jsCode"]
        self.assertIn("'artifact.submit_reviewed': ['artifact_id']", dispatch_validation)
        self.assertNotIn("'artifact.submit_reviewed': ['artifact_id', 'expected_sha256']", dispatch_validation)
        dispatch_inputs = nodes["Dispatch Reviewed Artifact"]["parameters"]["workflowInputs"]["value"]
        self.assertEqual(set(dispatch_inputs), {"operation_code", "artifact_id"})
        self.assertNotIn("expected_sha256", dispatch_inputs)

    def test_ai_proposal_is_archived_hash_verified_and_left_pending_review(self) -> None:
        nodes = self.nodes("09-ai-proposal.json")
        self.assertEqual(nodes["Convert Proposal Artifact to File"]["type"], "n8n-nodes-base.convertToFile")
        self.assertEqual(nodes["Archive Proposal Artifact in OneDrive"]["type"], "n8n-nodes-base.microsoftOneDrive")
        self.assertEqual(nodes["Read Back Proposal Artifact"]["parameters"]["operation"], "download")
        self.assertIn("AGENT_PROPOSAL_ARTIFACT_HASH_MISMATCH", nodes["Verify Proposal Artifact Readback"]["parameters"]["jsCode"])
        values = nodes["Upsert SUCCEEDED Agent Job"]["parameters"]["columns"]["value"]
        self.assertEqual(values["review_state"], "PENDING")
        self.assertTrue({"proposal_artifact_item_id", "proposal_artifact_etag", "proposal_artifact_schema"}.issubset(values))

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


    def test_disposable_execute_workflows_are_recursively_inline_and_allowlisted(self) -> None:
        generator = load_fixture_generator()
        generated = N8N / "disposable" / "generated"
        observed_edges = set()

        def inspect(workflow: dict, ancestors: tuple[str, ...] = ()) -> None:
            self.assertNotIn(workflow["id"], ancestors)
            for node in workflow["nodes"]:
                if node["type"] != "n8n-nodes-base.executeWorkflow":
                    continue
                parameters = node["parameters"]
                self.assertEqual(parameters["source"], "parameter")
                self.assertNotIn("workflowId", parameters)
                child = json.loads(parameters["workflowJson"])
                self.assertEqual(parameters["workflowJson"], generator.canonical(child))
                observed_edges.add((workflow["id"], child["id"]))
                inspect(child, (*ancestors, workflow["id"]))

        for path in sorted(generated.glob("*.json")):
            workflow = load_json(path)
            generator.validate_inline_workflow(workflow)
            inspect(workflow)

        expected_edges = {
            (parent_id, child_id)
            for parent_id, child_ids in generator.ALLOWED_INLINE_EDGES.items()
            for child_id in child_ids
        }
        self.assertEqual(observed_edges, expected_edges)

    def test_disposable_inline_generator_rejects_invalid_graphs_and_payloads(self) -> None:
        generator = load_fixture_generator()

        def workflow(workflow_id: str, target_id: str | None = None) -> dict:
            nodes = [] if target_id is None else [
                generator.execute_node("call", "Call Child", target_id, [0, 0])
            ]
            return {
                "id": workflow_id,
                "name": workflow_id,
                "active": False,
                "nodes": nodes,
                "connections": {},
                "settings": {},
            }

        parent = workflow("parent", "child")
        child = workflow("child")
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            generator.inline_execute_workflows(parent, {"parent": parent, "child": child}, {})
        with self.assertRaisesRegex(ValueError, "unknown"):
            generator.inline_execute_workflows(
                parent, {"parent": parent}, {"parent": frozenset({"child"})}
            )

        cyclic_child = workflow("child", "parent")
        with self.assertRaisesRegex(ValueError, "cycle"):
            generator.inline_execute_workflows(
                parent,
                {"parent": parent, "child": cyclic_child},
                {
                    "parent": frozenset({"child"}),
                    "child": frozenset({"parent"}),
                },
            )

        with self.assertRaisesRegex(ValueError, "residual database-ID"):
            generator.validate_inline_workflow(parent, {"parent": frozenset({"child"})})

        inlined = generator.inline_execute_workflows(
            parent,
            {"parent": parent, "child": child},
            {"parent": frozenset({"child"})},
        )
        inlined["nodes"][0]["parameters"]["workflowJson"] = "{"
        with self.assertRaisesRegex(ValueError, "malformed"):
            generator.validate_inline_workflow(inlined, {"parent": frozenset({"child"})})

        malformed_nodes = {
            "non-object": "not-a-node",
            "missing-type": {},
            "empty-type": {"type": ""},
            "non-string-type": {"type": None},
        }
        for case, malformed_node in malformed_nodes.items():
            with self.subTest(case=case, location="top-level"):
                malformed_parent = workflow("malformed-parent")
                malformed_parent["nodes"].append(deepcopy(malformed_node))
                with self.assertRaisesRegex(TypeError, "malformed node"):
                    generator.inline_execute_workflows(
                        malformed_parent,
                        {"malformed-parent": malformed_parent},
                    )
                with self.assertRaisesRegex(TypeError, "malformed node"):
                    generator.validate_inline_workflow(malformed_parent)

            with self.subTest(case=case, location="nested"):
                malformed_child = workflow("child")
                malformed_child["nodes"].append(deepcopy(malformed_node))
                with self.assertRaisesRegex(TypeError, "malformed node"):
                    generator.inline_execute_workflows(
                        parent,
                        {"parent": parent, "child": malformed_child},
                        {"parent": frozenset({"child"})},
                    )

                nested_inlined = generator.inline_execute_workflows(
                    parent,
                    {"parent": parent, "child": child},
                    {"parent": frozenset({"child"})},
                )
                nested_parameters = nested_inlined["nodes"][0]["parameters"]
                nested_child = json.loads(nested_parameters["workflowJson"])
                nested_child["nodes"].append(deepcopy(malformed_node))
                nested_parameters["workflowJson"] = generator.canonical(nested_child)
                with self.assertRaisesRegex(TypeError, "malformed node"):
                    generator.validate_inline_workflow(
                        nested_inlined,
                        {"parent": frozenset({"child"})},
                    )

    def test_r11_disposable_create_publish_payload_is_flat_and_named(self) -> None:
        payload = load_json(N8N / "disposable" / "create-publish-payload.json")
        create = payload["create"]
        self.assertIsInstance(create["name"], str)
        self.assertTrue(create["name"].strip())
        self.assertNotIn("workflow", create)
        self.assertEqual(payload["publish"], {"id": payload["create_response"]["id"]})

        module_path = N8N / "disposable" / "runtime_payload.py"
        spec = importlib.util.spec_from_file_location("n8n_disposable_runtime_payload", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        workflow = load_json(WORKFLOWS / "12-outlook-message-sweep.json")
        create_response = {"id": "created-workflow-id"}
        created = module.build_runtime_payload(workflow, create_response)
        self.assertEqual(created["create"]["name"], workflow["name"])
        self.assertEqual(created["publish"], create_response)
        self.assertNotEqual(created["publish"]["id"], workflow["id"])
        with self.assertRaisesRegex(ValueError, "N8N_WORKFLOW_NAME_REQUIRED"):
            module.build_runtime_payload({**workflow, "name": None}, create_response)
        with self.assertRaisesRegex(ValueError, "N8N_WORKFLOW_CREATED_ID_REQUIRED"):
            module.build_runtime_payload(workflow, {})

    def test_disposable_fixtures_are_inactive_manual_and_external_write_free(self) -> None:
        generated = N8N / "disposable" / "generated"
        fixtures = [load_json(path) for path in sorted(generated.glob("*.json"))]
        production_ids = {workflow["id"] for workflow in self.workflows.values()}
        registry_files = {row["file"] for row in self.registry["workflows"]}
        forbidden = {
            "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.webhook",
            "@n8n/n8n-nodes-langchain.mcpTrigger",
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
                self.assertFalse(any(
                    node["type"] == "n8n-nodes-base.microsoftOutlook"
                    and node.get("parameters", {}).get("operation") != "getAll"
                    for node in workflow["nodes"]
                ))
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
        self.assertEqual(scenarios["sweep_one_no_attachments"]["expected"]["scanned_count"], 1)
        self.assertEqual(scenarios["sweep_one_no_attachments"]["expected"]["matched_count"], 1)
        self.assertEqual(scenarios["sweep_one_no_attachments"]["expected"]["attachment_identity_keys"], [])
        fixture_ids = {row["id"] for row in manifest["workflows"]}
        scenario_ids = {
            workflow_id
            for scenario in scenarios.values()
            for workflow_id in (
                ([scenario["workflow_id"]] if "workflow_id" in scenario else [])
                + scenario.get("workflow_ids", [])
            )
        }
        self.assertTrue(scenario_ids <= fixture_ids)
        self.assertEqual(scenarios["sweep_101"]["expected"]["attachment_identity_keys"], [])
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
        self.assertEqual(sol["expected_reasoning_effort"], "medium")
        self.assertEqual(sol["execution_gate"], "DISPOSABLE_ALLOW_SOL_MEDIUM")
        self.assertTrue(sol["default_execution_forbidden"])
        self.assertEqual(scenarios["outbox_recovery"]["expected_state"], "COMMITTED")
        self.assertEqual(scenarios["outbox_recovery"]["finance_writes"], 0)
        self.assertEqual(scenarios["error_redaction"]["receipt_sink"], "n8n_execution_history")
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
        self.assertEqual(sol["meta"]["executionGate"], "DISPOSABLE_ALLOW_SOL_MEDIUM")
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

    def test_workflow_ui_renderer_is_current_readable_and_idempotent(self) -> None:
        renderer = N8N / "refactor_workflow_ui.py"
        before = {path.name: path.read_bytes() for path in WORKFLOWS.glob("*.json")}
        result = subprocess.run(
            [sys.executable, str(renderer), "--check"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = {path.name: path.read_bytes() for path in WORKFLOWS.glob("*.json")}
        self.assertEqual(before, after)
        operator_warning_codes = {
            "FINANCE_MCP_FACADE",
        }
        operator_warning_ids = {
            "10000000-0000-4000-8000-000000000015-generated-note-1",
        }
        for filename, workflow in self.workflows.items():
            self.assertNotIn("SPEC ONLY", workflow["name"].upper())
            self.assertNotIn("SETUP REQUIRED", workflow["name"].upper())
            self.assertNotIn("PAUSED", workflow["name"].upper())
            self.assertTrue(workflow["name"].strip(), filename)
            notes = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.stickyNote"]
            code = workflow["meta"]["financeWorkflowCode"]
            if code in operator_warning_codes:
                self.assertEqual(len(notes), 1, filename)
                self.assertIn(notes[0]["id"], operator_warning_ids)
            else:
                self.assertEqual(notes, [], filename)
            for note in notes:
                content = note["parameters"]["content"]
                self.assertIn("**Input:**", content)
                self.assertIn("**Output:**", content)
                self.assertIn("failure", content.casefold())
            for node in workflow["nodes"]:
                if node["type"] != "n8n-nodes-base.code":
                    continue
                code = node["parameters"]["jsCode"]
                self.assertEqual(code.count("// Purpose:"), 1, f"{filename}::{node['name']}")
                self.assertGreaterEqual(len(code.splitlines()), 2, f"{filename}::{node['name']}")
                self.assertLessEqual(max(map(len, code.splitlines())), 600, f"{filename}::{node['name']}")

    def test_canvas_groups_are_native_valid_and_exclude_triggers(self) -> None:
        trigger_types = {
            "n8n-nodes-base.manualTrigger", "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.executeWorkflowTrigger", "n8n-nodes-base.errorTrigger",
            "@n8n/n8n-nodes-langchain.mcpTrigger",
        }
        grouped_workflows = 0
        for filename, workflow in self.workflows.items():
            by_id = {node["id"]: node for node in workflow["nodes"]}
            groups = workflow.get("nodeGroups", [])
            grouped_workflows += bool(groups)
            seen: set[str] = set()
            for group in groups:
                self.assertEqual(set(group), {"name", "nodeIds", "description"})
                self.assertGreaterEqual(len(group["nodeIds"]), 2)
                self.assertTrue(group["description"].startswith("Finance stage"))
                for node_id in group["nodeIds"]:
                    self.assertIn(node_id, by_id, filename)
                    self.assertNotIn(by_id[node_id]["type"], trigger_types)
                    self.assertNotIn(node_id, seen, f"duplicate canvas group membership {filename}")
                    seen.add(node_id)
        self.assertGreaterEqual(grouped_workflows, 15)

    def test_workflow_folder_manifest_is_complete_and_post_import_guarded(self) -> None:
        contract = load_json(N8N / "workflow-folders.json")
        self.assertEqual(contract["n8n_version"], "2.36.2")
        self.assertEqual(len(contract["folders"]), 6)
        workflow_rows = contract["workflows"]
        self.assertEqual(len(workflow_rows), len({row["code"] for row in workflow_rows}))
        self.assertEqual(
            {row["code"] for row in workflow_rows},
            {row["code"] for row in self.registry["workflows"]},
        )
        by_code = {row["code"]: row["folder_id"] for row in workflow_rows}
        tag_by_name = {tag["name"]: tag["id"] for tag in contract["tag_definitions"]}
        for workflow in self.workflows.values():
            code = workflow["meta"]["financeWorkflowCode"]
            self.assertEqual(workflow["meta"]["workflowFolder"]["id"], by_code[code])
            self.assertEqual(workflow["meta"]["workflowTags"], contract["workflow_tags"])
            self.assertEqual([tag["name"] for tag in workflow["tags"]], contract["workflow_tags"])
            self.assertEqual(
                workflow["tags"],
                [{"id": tag_by_name[name], "name": name} for name in contract["workflow_tags"]],
            )
            self.assertEqual(len({tag["id"] for tag in workflow["tags"]}), len(contract["workflow_tags"]))
            self.assertNotIn("parentFolderId", workflow)
        sql = (N8N / "workflow-folder-placement.sql").read_text(encoding="utf-8")
        for marker in (
            "application_project_id", "WORKFLOW_ACTIVATION_VERSION_CHANGED", "shared_workflow",
            "WORKFLOW_FOLDER_MAP_COUNT_MISMATCH", "WORKFLOW_FOLDER_READBACK_MISMATCH",
        ):
            self.assertIn(marker, sql)
        self.assertNotIn("finance_project_id", sql)
        self.assertNotIn("finance_commit", sql)

    def test_execute_subworkflow_references_use_from_list(self) -> None:
        workflow_names = {workflow["id"]: workflow["name"] for workflow in self.workflows.values()}
        count = 0
        for filename, workflow in self.workflows.items():
            for node in workflow["nodes"]:
                if node["type"] not in {
                    "n8n-nodes-base.executeWorkflow",
                    "@n8n/n8n-nodes-langchain.toolWorkflow",
                }:
                    continue
                count += 1
                reference = node["parameters"]["workflowId"]
                self.assertEqual(reference["mode"], "list", f"{filename}::{node['name']}")
                self.assertEqual(reference["cachedResultName"], workflow_names[reference["value"]])
        self.assertGreaterEqual(count, 20)

    def test_outlook_and_onedrive_nodes_use_exact_binary_and_server_filter_contracts(self) -> None:
        for filename in ("01-outlook-finance-acquisition.json", "12-outlook-message-sweep.json"):
            outlook_nodes = [
                node for node in self.workflow(filename)["nodes"]
                if node["type"] == "n8n-nodes-base.microsoftOutlook"
                and node.get("parameters", {}).get("resource") == "folderMessage"
                and node.get("parameters", {}).get("operation") == "getAll"
            ]
            expected_count = 2 if filename == "12-outlook-message-sweep.json" else 0
            self.assertEqual(len(outlook_nodes), expected_count)
            if not outlook_nodes:
                continue
            for node in outlook_nodes:
                params = node["parameters"]
                self.assertEqual(params["output"], "raw")
                self.assertTrue(params["returnAll"])
                values = params["filtersUI"]["values"]
                self.assertEqual(values["filterBy"], "filters")
                filters = values["filters"]
                self.assertIn("receivedAfter", filters)
                self.assertIn("receivedBefore", filters)
                self.assertIn("custom", filters)
                self.assertNotIn("filters", params)
        uploads = []
        for workflow in self.workflows.values():
            uploads.extend(
                node for node in workflow["nodes"]
                if node["type"] == "n8n-nodes-base.microsoftOneDrive"
                and node.get("parameters", {}).get("operation") == "upload"
            )
        self.assertTrue(uploads)
        for node in uploads:
            self.assertEqual(node["typeVersion"], 1.1)
            self.assertTrue(node["parameters"]["binaryData"])
            self.assertEqual(node["parameters"]["binaryPropertyName"], "data")

        acquisition = self.workflow("01-outlook-finance-acquisition.json")
        self.assertNotIn("Get Messages from Configured Folder", acquisition["connections"])
        self.assertNotIn(
            "Exact Sender Subject and Window Filter",
            {node["name"] for node in acquisition["nodes"]},
        )
        sweep_connections = self.workflow("12-outlook-message-sweep.json")["connections"]
        self.assertEqual(
            sweep_connections["Exhaust Outlook Pagination"]["main"][0][0]["node"],
            "Aggregate Exact Window Heartbeat",
        )

    def test_interactive_browser_handoff_validates_before_archive_and_is_idempotent(self) -> None:
        table = next(
            row for row in self.tables["tables"]
            if row["name"] == "finance_document_operations"
        )
        nodes = self.nodes("11-interactive-artifact-handoff.json")
        self.assertTrue({"source_code", "config_version", "actual_file_id", "account_id", "period_key"}.issubset(table["columns"]))
        connections = self.workflow("11-interactive-artifact-handoff.json")["connections"]
        self.assertEqual(
            connections["Validate Browser Capture Schema"]["main"][0][0]["node"],
            "Load Existing Browser Archive Receipt",
        )
        self.assertEqual(
            connections["Check Existing Browser Artifact"]["main"][0][0]["node"],
            "New Browser Artifact?",
        )
        self.assertEqual(
            connections["New Browser Artifact?"]["main"][0][0]["node"],
            "Archive Browser Capture in OneDrive",
        )
        self.assertEqual(
            nodes["New Browser Artifact?"]["parameters"]["conditions"]["conditions"][0]["leftValue"],
            "={{ $json.idempotency_action }}",
        )
        idempotency = nodes["Check Existing Browser Artifact"]["parameters"]["jsCode"]
        self.assertIn("BROWSER_ARTIFACT_ID_HASH_CONFLICT", idempotency)
        self.assertIn("existing.output_sha256", idempotency)
        self.assertIn("idempotency_action: 'NOOP'", idempotency)
        verify = nodes["Verify Browser Archive Receipt"]["parameters"]["jsCode"]
        self.assertIn("BROWSER_ARCHIVE_HASH_INVALID", verify)
        self.assertTrue(nodes["Parse Browser Capture JSON Before Archive"]["type"] == "n8n-nodes-base.code")
        validate = nodes["Validate Browser Capture Schema"]["parameters"]["jsCode"]
        self.assertIn("BROWSER_CAPTURE_BINARY_HASH_MISMATCH", validate)
        self.assertIn("expected_source_sha256", validate)
        self.assertIn("expected_capture_sha256", validate)
        self.assertTrue(self.workflow("11-interactive-artifact-handoff.json")["meta"]["reuploadForbidden"])
        self.assertEqual(
            self.workflow("11-interactive-artifact-handoff.json")["meta"]["artifactIdHashConflict"],
            "BROWSER_ARTIFACT_ID_HASH_CONFLICT",
        )
        self.assertEqual(
            nodes["Dispatch Browser Capture to Headless Pipeline"]["parameters"]["workflowId"]["mode"],
            "list",
        )

        self.assertIn("MCP Reviewed Artifact?", nodes)
        self.assertIn("Load MCP Reviewed Document Record", nodes)
        self.assertIn("Validate MCP Durable Document Reference", nodes)
        self.assertIn("Download MCP Reviewed Capture", nodes)
        self.assertIn("Resolve Capture Hash Contract", nodes)
        reference = nodes["Validate Reviewed Artifact Reference"]["parameters"]["jsCode"]
        self.assertIn("MCP_REVIEWED_BINARY_FORBIDDEN", reference)
        self.assertIn("MCP_REVIEWED_HASHES_MUST_BE_SERVER_DERIVED", reference)
        self.assertIn("MCP_REVIEWED_FIELDS_FORBIDDEN", reference)
        self.assertIn("expected_source_sha256", reference)
        self.assertIn("expected_capture_sha256", reference)
        durable = nodes["Validate MCP Durable Document Reference"]["parameters"]["jsCode"]
        self.assertIn("MCP_REVIEWED_DOCUMENT_NOT_FOUND", durable)
        self.assertIn("server_source_sha256", durable)
        self.assertEqual(
            connections["Validate Reviewed Artifact Reference"]["main"][0][0]["node"],
            "MCP Reviewed Artifact?",
        )
        self.assertEqual(
            connections["MCP Reviewed Artifact?"]["main"][0][0]["node"],
            "Load MCP Reviewed Document Record",
        )
        self.assertEqual(
            connections["MCP Reviewed Artifact?"]["main"][1][0]["node"],
            "SHA-256 Browser Capture Input",
        )
        self.assertEqual(
            connections["Validate MCP Durable Document Reference"]["main"][0][0]["node"],
            "Download MCP Reviewed Capture",
        )
        self.assertEqual(
            connections["SHA-256 Browser Capture Input"]["main"][0][0]["node"],
            "Resolve Capture Hash Contract",
        )
        self.assertEqual(
            set(self.workflow("11-interactive-artifact-handoff.json")["meta"]["browserHandoff"]["handoff_modes"]),
            {"HEADED_CAPTURE", "MCP_REVIEWED"},
        )
        self.assertEqual(
            self.workflow("11-interactive-artifact-handoff.json")["meta"]["browserHandoff"]["mcp_reviewed_contract"],
            ["artifact_id"],
        )

    def test_browser_capture_fixtures_execute_against_embedded_canonical_schema(self) -> None:
        schema = load_json(ROOT / "config" / "browser-capture-schema-v1.json")
        code = self.nodes("11-interactive-artifact-handoff.json")["Validate Browser Capture Schema"]["parameters"]["jsCode"]
        embedded, _ = json.JSONDecoder().raw_decode(code.split("const schema = ", 1)[1])
        self.assertEqual(embedded, schema)

        valid = load_json(ROOT / "tests" / "fixtures" / "browser-captures" / "valid-transaction-rows.json")
        validate_fixture_against_schema(embedded, valid)
        capture_binary = json.dumps(
            valid,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        capture_binary_sha256 = hashlib.sha256(capture_binary).hexdigest()
        source_content_sha256 = valid["artifact"]["source_content_sha256"]
        self.assertNotEqual(capture_binary_sha256, source_content_sha256)
        self.assertEqual(valid, json.loads(capture_binary))

        invalid = load_json(ROOT / "tests" / "fixtures" / "browser-captures" / "invalid-forbidden-field.json")
        durable_storage: list[dict] = []

        def archive_if_valid(capture: dict, expected_source: str, expected_binary: str) -> None:
            validate_fixture_against_schema(embedded, capture)
            self.assertEqual(capture["artifact"]["source_content_sha256"], expected_source)
            actual_binary = hashlib.sha256(json.dumps(
                capture,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            self.assertEqual(actual_binary, expected_binary)
            durable_storage.append(capture)

        with self.assertRaises(AssertionError):
            archive_if_valid(invalid, source_content_sha256, capture_binary_sha256)
        self.assertEqual(durable_storage, [])

        archive_if_valid(valid, source_content_sha256, capture_binary_sha256)
        self.assertEqual([row["capture_id"] for row in durable_storage], [valid["capture_id"]])

        receipt = {
            "document_id": valid["capture_id"],
            "source_sha256": source_content_sha256,
            "output_sha256": capture_binary_sha256,
        }

        def replay(source_hash: str, binary_hash: str) -> str:
            if receipt["source_sha256"] != source_hash or receipt["output_sha256"] != binary_hash:
                raise ValueError("BROWSER_ARTIFACT_ID_HASH_CONFLICT")
            return "NOOP"

        self.assertEqual(replay(source_content_sha256, capture_binary_sha256), "NOOP")
        with self.assertRaisesRegex(ValueError, "BROWSER_ARTIFACT_ID_HASH_CONFLICT"):
            replay(source_content_sha256, "c" * 64)
        with self.assertRaisesRegex(ValueError, "BROWSER_ARTIFACT_ID_HASH_CONFLICT"):
            replay("d" * 64, capture_binary_sha256)

    def test_exported_w11_validator_and_idempotency_execute_real_fixture_bytes(self) -> None:
        valid = load_json(ROOT / "tests" / "fixtures" / "browser-captures" / "valid-transaction-rows.json")
        capture_binary = json.dumps(
            valid,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        capture_binary_sha256 = hashlib.sha256(capture_binary).hexdigest()
        source_content_sha256 = valid["artifact"]["source_content_sha256"]
        # n8n binary data is base64; keep the bytes exact while making this
        # harness independent of n8n's runtime item wrappers.
        binary = {"data": {"data": base64.b64encode(capture_binary).decode("ascii")}}
        contract = {
            "handoff_mode": "HEADED_CAPTURE",
            "artifact_id": valid["capture_id"],
            "expected_source_sha256": source_content_sha256,
            "expected_capture_sha256": capture_binary_sha256,
        }
        mcp_request = {
            "_mcp_request_id": "mcp-fixture-1",
            "operation_code": "artifact.submit_reviewed",
            "artifact_id": valid["capture_id"],
        }
        result = self.run_exported_node("Validate Reviewed Artifact Reference", mcp_request, {}, {})
        self.assertEqual(result["output"][0]["json"]["handoff_mode"], "MCP_REVIEWED")
        result = self.run_exported_node("Validate Reviewed Artifact Reference", mcp_request, binary, {})
        self.assertEqual(result, {"ok": False, "error": "MCP_REVIEWED_BINARY_FORBIDDEN"})
        result = self.run_exported_node(
            "Validate Reviewed Artifact Reference",
            {**mcp_request, "expected_capture_sha256": capture_binary_sha256},
            {},
            {},
        )
        self.assertEqual(result, {"ok": False, "error": "MCP_REVIEWED_HASHES_MUST_BE_SERVER_DERIVED"})
        result = self.run_exported_node(
            "Validate Reviewed Artifact Reference",
            {**mcp_request, "url": "https://client-controlled.example.test"},
            {},
            {},
        )
        self.assertEqual(result, {"ok": False, "error": "Artifact metadata must be resolved from durable server state"})
        result = self.run_exported_node(
            "Validate Reviewed Artifact Reference",
            {"artifact_id": valid["capture_id"], "expected_sha256": source_content_sha256,
             "expected_source_sha256": source_content_sha256, "expected_capture_sha256": capture_binary_sha256},
            binary,
            {},
        )
        self.assertEqual(result, {"ok": False, "error": "EXPECTED_SHA256_LEGACY_FORBIDDEN"})
        references = {
            "SHA-256 Browser Capture Input": {"json": {"input_sha256": capture_binary_sha256}},
            "Resolve Capture Hash Contract": {"json": contract, "binary": binary},
        }
        result = self.run_exported_node("Validate Browser Capture Schema", valid, binary, references)
        self.assertTrue(result["ok"], result)
        validator_code = self.nodes("11-interactive-artifact-handoff.json")["Validate Browser Capture Schema"]["parameters"]["jsCode"]
        for forbidden_runtime_primitive in ("require(", "eval(", "new Function", "WebAssembly.compile"):
            self.assertNotIn(forbidden_runtime_primitive, validator_code)
        restricted_result = self.run_exported_node_without_dynamic_code(
            "Validate Browser Capture Schema", valid, binary, references,
        )
        self.assertTrue(restricted_result["ok"], restricted_result)

        invalid = load_json(ROOT / "tests" / "fixtures" / "browser-captures" / "invalid-forbidden-field.json")
        invalid_bytes = json.dumps(
            invalid,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        invalid_binary = {"data": {"data": base64.b64encode(invalid_bytes).decode("ascii")}}
        invalid_hash = hashlib.sha256(invalid_bytes).hexdigest()
        invalid_refs = {
            "SHA-256 Browser Capture Input": {"json": {"input_sha256": invalid_hash}},
            "Resolve Capture Hash Contract": {"json": {
                **contract,
                "artifact_id": invalid["capture_id"],
                "expected_source_sha256": invalid["artifact"]["source_content_sha256"],
                "expected_capture_sha256": invalid_hash,
            }, "binary": invalid_binary},
        }
        result = self.run_exported_node("Validate Browser Capture Schema", invalid, invalid_binary, invalid_refs)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_CAPTURE_FORBIDDEN_FIELD:capture.password"})
        restricted_invalid = self.run_exported_node_without_dynamic_code(
            "Validate Browser Capture Schema", invalid, invalid_binary, invalid_refs,
        )
        self.assertEqual(
            restricted_invalid,
            {"ok": False, "error": "BROWSER_CAPTURE_FORBIDDEN_FIELD:capture.password"},
        )
        durable_storage: list[dict] = []
        if result["ok"]:
            durable_storage.append(invalid)
        self.assertEqual(durable_storage, [])

        source_mismatch = {**contract, "expected_source_sha256": "d" * 64}
        mismatch_refs = {
            **references,
            "Resolve Capture Hash Contract": {"json": source_mismatch, "binary": binary},
        }
        result = self.run_exported_node("Validate Browser Capture Schema", valid, binary, mismatch_refs)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_CAPTURE_PROVENANCE_MISMATCH"})

        binary_mismatch = {**contract, "expected_capture_sha256": "c" * 64}
        mismatch_refs["Resolve Capture Hash Contract"] = {"json": binary_mismatch, "binary": binary}
        result = self.run_exported_node("Validate Browser Capture Schema", valid, binary, mismatch_refs)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_CAPTURE_BINARY_HASH_MISMATCH"})

        existing = {
            "document_id": valid["capture_id"],
            "source_sha256": source_content_sha256,
            "output_sha256": capture_binary_sha256,
            "onedrive_item_id": "one-drive-item",
            "document_profile": "BROWSER_CAPTURE_V1",
        }
        idempotency_references = {
            "Resolve Capture Hash Contract": {"json": contract, "binary": binary},
            "Validate Browser Capture Schema": {"json": valid, "binary": binary},
            "SHA-256 Browser Capture Input": {"json": {"input_sha256": capture_binary_sha256}},
        }
        result = self.run_exported_node("Check Existing Browser Artifact", existing, binary, idempotency_references)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"][0]["json"]["idempotency_action"], "NOOP")

        source_conflict = {**existing, "source_sha256": "d" * 64}
        result = self.run_exported_node("Check Existing Browser Artifact", source_conflict, binary, idempotency_references)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_ARTIFACT_ID_HASH_CONFLICT"})

        binary_conflict = {**existing, "output_sha256": "c" * 64}
        result = self.run_exported_node("Check Existing Browser Artifact", binary_conflict, binary, idempotency_references)
        self.assertEqual(result, {"ok": False, "error": "BROWSER_ARTIFACT_ID_HASH_CONFLICT"})

    def test_browser_capture_pipeline_is_write_disabled_and_skips_pdf_and_cashback(self) -> None:
        workflow = self.workflow("03-shared-statement-pipeline.json")
        nodes = self.nodes("03-shared-statement-pipeline.json")
        connections = workflow["connections"]

        self.assertEqual(
            connections["Browser Capture?"]["main"][0][0]["node"],
            "Parse Browser Capture Adapter",
        )
        self.assertEqual(
            connections["Browser Capture Write?"]["main"][0][0]["node"],
            "Complete Browser Capture Headless Receipt",
        )
        self.assertFalse(any(node["type"] == "n8n-nodes-finance.actualBudget" for node in workflow["nodes"]))
        terminal = nodes["Complete Browser Capture Headless Receipt"]["parameters"]["jsCode"]
        self.assertIn("direct_actual_writer", terminal)
        self.assertIn("direct_cashback_writer", terminal)

    def test_shared_pipeline_binds_cashback_close_to_post_actual_receipt(self) -> None:
        workflow = self.workflow("03-shared-statement-pipeline.json")
        nodes = self.nodes("03-shared-statement-pipeline.json")
        self.assertEqual(
            workflow["connections"]["Apply Prepared Outbox Safely"]["main"][0][0]["node"],
            "Build Trusted Cashback Finalization",
        )
        self.assertEqual(
            workflow["connections"]["Build Trusted Cashback Finalization"]["main"][0][0]["node"],
            "Convert Trusted Actual Receipt to File",
        )
        self.assertEqual(
            workflow["connections"]["Convert Trusted Actual Receipt to File"]["main"][0][0]["node"],
            "SHA-256 Trusted Actual Receipt",
        )
        self.assertEqual(
            workflow["connections"]["SHA-256 Trusted Actual Receipt"]["main"][0][0]["node"],
            "Finalize Trusted Cashback Payload",
        )
        self.assertEqual(
            workflow["connections"]["Finalize Trusted Cashback Payload"]["main"][0][0]["node"],
            "Build Cashback Reconciliation Request",
        )
        self.assertEqual(
            workflow["connections"]["Build Cashback Reconciliation Request"]["main"][0][0]["node"],
            "Reconcile Cashback Statement",
        )
        self.assertEqual(
            workflow["connections"]["Reconcile Cashback Statement"]["main"][0][0]["node"],
            "Cashback Close Required",
        )
        self.assertEqual(
            workflow["connections"]["Finalize Eligible Cashback Period"]["main"][0][0]["node"],
            "Validate Cashback Finalization Response",
        )
        self.assertEqual(
            workflow["connections"]["Validate Cashback Finalization Response"]["main"][0][0]["node"],
            "Upsert Reconciliation Receipt",
        )
        self.assertEqual(
            nodes["Convert Trusted Actual Receipt to File"]["type"],
            "n8n-nodes-base.convertToFile",
        )
        self.assertEqual(
            nodes["SHA-256 Trusted Actual Receipt"]["type"],
            "n8n-nodes-base.crypto",
        )
        self.assertEqual(
            nodes["SHA-256 Trusted Actual Receipt"]["parameters"]["dataPropertyName"],
            "actual_import_receipt_sha256",
        )
        self.assertTrue(
            nodes["Reconcile Cashback Statement"]["parameters"]["url"].endswith("/api/reconcile")
        )
        self.assertIn(
            "Build Cashback Reconciliation Request",
            nodes["Reconcile Cashback Statement"]["parameters"]["jsonBody"],
        )
        self.assertIn("CASHBACK_FINALIZE_RESPONSE_BINDING_MISMATCH", nodes["Validate Cashback Finalization Response"]["parameters"]["jsCode"])
        self.assertIn("close_id", nodes["Upsert Reconciliation Receipt"]["parameters"]["columns"]["value"]["cashback_close_id"])
        body = nodes["Finalize Eligible Cashback Period"]["parameters"]["jsonBody"]
        self.assertIn("Finalize Trusted Cashback Payload", body)
        self.assertNotIn("cashback_finalization", nodes["Validate Statement Reconciliation and IDs"]["parameters"]["jsCode"])
        trusted_builder = nodes["Build Trusted Cashback Finalization"]["parameters"]["jsCode"]
        self.assertNotIn("source.card_code || source.account_id", trusted_builder)
        self.assertNotIn("actual.card_code || actual.account_id", trusted_builder)

        digest = "a" * 64
        source = {
            "source_code": "EI_AMAZON",
            "document_sha256": "b" * 64,
            "onedrive_item_id": "drive-statement-1",
            "actual_file_id": "actual-file-1",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
        }
        statement = {
            "statement_reference": "EI-2026-08",
            "statement_sha256": "c" * 64,
            "transactions": [{
                "transaction_id": "statement-transaction-1",
                "transaction_date": "2026-08-15",
                "description": "Synthetic Merchant",
                "amount_aed": "8.00",
                "currency_original": "AED",
                "transaction_type": "PURCHASE",
                "purchase_type": "GROCERY",
            }],
        }
        manifest = {"period_start": "2026-08-01", "period_end": "2026-08-31", "card_code": "EI_AMAZON"}
        actual = {
            "batch_id": "outbox:ei-2026-08",
            "actual_file_id": "actual-file-1",
            "account_id": "actual-account:EI_AMAZON",
            "card_code": "EI_AMAZON",
            "state": "COMMITTED",
            "writer_release_verified": True,
            "verification_version": 1,
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "expected_payload_sha256": digest,
            "observed_payload_sha256": digest,
            "expected_count": 2,
            "observed_count": 2,
            "expected_amount_sum_minor": 800,
            "observed_amount_sum_minor": 800,
            "expected_account_balance": -800,
            "observed_account_balance": -800,
            "invariants_passed": True,
            "verified_at": "2026-08-20T00:00:00+00:00",
        }
        references = {
            "Verify Archive and Execution Context": {"json": source},
            "Validate Statement Reconciliation and IDs": {"json": statement},
            "Build Canonical Delta Artifact": {"json": {"manifest": manifest}},
        }
        result = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Trusted Cashback Finalization",
            actual,
            references,
        )
        self.assertTrue(result["ok"], result)
        close = result["output"][0]["json"]["cashback_finalization"]
        self.assertEqual(close["actual_import_receipt"]["state"], "COMMITTED")
        self.assertEqual(close["actual_import_receipt"]["account_id"], "actual-account:EI_AMAZON")
        self.assertEqual(close["actual_import_receipt"]["card_code"], "EI_AMAZON")
        self.assertEqual(close["actual_import_receipt"]["period_end"], "2026-08-31")
        self.assertEqual(close["actual_import_receipt"]["expected_payload_sha256"], digest)
        receipt_digest = hashlib.sha256(
            json.dumps(
                close["actual_import_receipt"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        finalized = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Finalize Trusted Cashback Payload",
            {"actual_import_receipt_sha256": receipt_digest},
            {**references, "Build Trusted Cashback Finalization": result["output"][0]},
        )
        self.assertTrue(finalized["ok"], finalized)
        self.assertEqual(
            finalized["output"][0]["json"]["actual_import_receipt_sha256"],
            receipt_digest,
        )
        self.assertEqual(
            finalized["output"][0]["json"]["cashback_finalization"]["actual_import_receipt_sha256"],
            receipt_digest,
        )
        reconciled = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Cashback Reconciliation Request",
            finalized["output"][0]["json"],
            references,
        )
        self.assertTrue(reconciled["ok"], reconciled)
        reconcile_request = reconciled["output"][0]["json"]["cashback_reconcile"]
        self.assertEqual(reconcile_request["card_code"], "EI_AMAZON")
        self.assertEqual(reconcile_request["period_start"], "2026-08-01")
        self.assertEqual(
            reconcile_request["transactions"][0]["statement_transaction_id"],
            "statement-transaction-1",
        )
        self.assertEqual(reconcile_request["transactions"][0]["event_type"], "PURCHASE")
        self.assertEqual(reconcile_request["transactions"][0]["purchase_type"], "GROCERY")
        close_id = "cashback-close:EI_AMAZON:2026-08-01:2026-08-31"
        finalize_response = {
            "period": {
                "close_id": close_id,
                "card_code": "EI_AMAZON",
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "statement_reference": "EI-2026-08",
                "statement_sha256": "c" * 64,
                "actual_import_receipt_sha256": receipt_digest,
                "actual_verification_sha256": receipt_digest,
                "status": "FINALIZED",
                "idempotent_replay": False,
            }
        }
        validated_close = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Validate Cashback Finalization Response",
            finalize_response,
            {
                **references,
                "Build Cashback Reconciliation Request": reconciled["output"][0],
                "Finalize Trusted Cashback Payload": finalized["output"][0],
            },
        )
        self.assertTrue(validated_close["ok"], validated_close)
        self.assertEqual(validated_close["output"][0]["json"]["close_id"], close_id)
        for invalid_response in (
            {"period": {key: value for key, value in finalize_response["period"].items() if key != "close_id"}},
            {"period": {**finalize_response["period"], "status": "COMMITTED"}},
            {"period": {**finalize_response["period"], "actual_verification_sha256": "d" * 64}},
        ):
            rejected_response = self.run_exported_workflow_node(
                "03-shared-statement-pipeline.json",
                "Validate Cashback Finalization Response",
                invalid_response,
                {
                    **references,
                    "Build Cashback Reconciliation Request": reconciled["output"][0],
                    "Finalize Trusted Cashback Payload": finalized["output"][0],
                },
            )
            self.assertFalse(rejected_response["ok"])
        empty_statement_references = {
            **references,
            "Validate Statement Reconciliation and IDs": {"json": {**statement, "transactions": []}},
        }
        empty_reconcile = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Cashback Reconciliation Request",
            finalized["output"][0]["json"],
            empty_statement_references,
        )
        self.assertFalse(empty_reconcile["ok"])
        missing_card_references = {
            **references,
            "Verify Archive and Execution Context": {"json": {key: value for key, value in source.items() if key != "card_code"}},
        }
        missing_card_reconcile = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Cashback Reconciliation Request",
            finalized["output"][0]["json"],
            missing_card_references,
        )
        self.assertFalse(missing_card_reconcile["ok"])
        replay = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Build Trusted Cashback Finalization",
            actual,
            references,
        )
        replay_finalized = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Finalize Trusted Cashback Payload",
            {"actual_import_receipt_sha256": receipt_digest},
            {**references, "Build Trusted Cashback Finalization": replay["output"][0]},
        )
        self.assertEqual(
            replay_finalized["output"][0]["json"]["actual_import_receipt_sha256"],
            finalized["output"][0]["json"]["actual_import_receipt_sha256"],
        )

        for label, invalid in {
            "missing": {key: value for key, value in actual.items() if key != "observed_payload_sha256"},
            "stale": {**actual, "state": "ACTUAL_OBSERVED"},
            "cross-account": {**actual, "account_id": "RAK_WORLD"},
            "missing-card": {key: value for key, value in actual.items() if key != "card_code"},
            "cross-card": {**actual, "card_code": "RAK_WORLD"},
            "cross-period": {**actual, "period_end": "2026-09-01"},
            "digest-mismatch": {**actual, "observed_payload_sha256": "d" * 64},
        }.items():
            rejected = self.run_exported_workflow_node(
                "03-shared-statement-pipeline.json",
                "Build Trusted Cashback Finalization",
                invalid,
                references,
            )
            self.assertFalse(rejected["ok"], label)

    def test_reconciliation_readback_rejects_stale_version_close_and_digest(self) -> None:
        nodes = self.nodes("03-shared-statement-pipeline.json")
        self.assertIn(
            "RECONCILIATION_READBACK_BINDING_MISMATCH",
            nodes["Validate Reconciliation Readback"]["parameters"]["jsCode"],
        )
        source = {
            "source_code": "EI_AMAZON",
            "period_key": "2026-08",
            "cashback_close_required": True,
        }
        request = {"statement_sha256": "a" * 64}
        actual = {"observed_payload_sha256": "b" * 64}
        row = {
            "source_code": "EI_AMAZON",
            "period_key": "2026-08",
            "reconciliation_version": 1,
            "statement_sha256": "a" * 64,
            "actual_verification_sha256": "b" * 64,
            "cashback_close_id": "cashback-close:EI_AMAZON:2026-08-01:2026-08-31",
            "state": "COMMITTED",
        }
        refs = {
            "Verify Archive and Execution Context": {"json": source},
            "Apply Prepared Outbox Safely": {"json": actual},
            "Build Cashback Reconciliation Request": {"json": {"cashback_reconcile": request}},
            "Validate Cashback Finalization Response": {"json": {"close_id": row["cashback_close_id"]}},
        }
        valid = self.run_exported_workflow_node(
            "03-shared-statement-pipeline.json",
            "Validate Reconciliation Readback",
            row,
            refs,
        )
        self.assertTrue(valid["ok"], valid)
        for invalid in (
            {**row, "reconciliation_version": 2},
            {**row, "cashback_close_id": "cashback-close:RAK_WORLD:2026-08-01:2026-08-31"},
            {**row, "actual_verification_sha256": "c" * 64},
            {**row, "statement_sha256": "d" * 64},
        ):
            rejected = self.run_exported_workflow_node(
                "03-shared-statement-pipeline.json",
                "Validate Reconciliation Readback",
                invalid,
                refs,
            )
            self.assertFalse(rejected["ok"])

    def test_subscription_adapter_uses_pinned_community_nodes_and_server_owned_controls(self) -> None:
        lock = load_json(N8N / "community-node-lock.json")
        self.assertEqual(
            {(row["package"], row["version"]) for row in lock["packages"]},
            {
                ("n8n-nodes-prodex", "0.5.1"),
            },
        )
        nodes = self.nodes("21-subscription-agent-adapter.json")
        codex = nodes["Run Codex Subscription Provider"]
        self.assertEqual((codex["type"], codex["typeVersion"]), ("n8n-nodes-prodex.prodex", 2))
        self.assertEqual(
            set(codex["parameters"]),
            {
                "operation", "useN8nCredentials", "systemPrompt", "skills", "prompt",
                "model", "reasoningEffort", "personality", "threadMode", "sandbox",
                "workingDirectory", "options",
            },
        )
        self.assertEqual(codex["parameters"]["sandbox"], "read_only")
        self.assertEqual(codex["parameters"]["threadMode"], "new")
        self.assertEqual(codex["parameters"]["workingDirectory"], "/tmp/finance-ai")
        self.assertEqual(
            set(codex["parameters"]["options"]),
            {"outputSchema", "streamProgress", "timeoutSeconds"},
        )
        self.assertTrue(codex["parameters"]["options"]["outputSchema"])
        self.assertNotIn("Run Claude Subscription Provider", nodes)
        proposal_schema = load_json(N8N / "contracts" / "ai-proposal-v1.schema.json")
        self.assertEqual(
            json.loads(codex["parameters"]["options"]["outputSchema"]),
            proposal_schema,
        )
        assignments = {
            row["name"]: row["value"]
            for row in nodes["Subscription Provider Parameters"]["parameters"]["assignments"]["assignments"]
        }
        self.assertEqual(json.loads(assignments["proposal_output_schema"]), proposal_schema)
        build = nodes["Validate and Build Fixed Provider Invocation"]["parameters"]["jsCode"]
        self.assertIn("provider_prompt", build)
        for expected in (
            "CODEX_SUBSCRIPTION", "provider_model", "provider_reasoning_effort",
            "provider_auth_mode", "Output JSON Schema",
        ):
            self.assertIn(expected, build)
        validator_name = "Validate ProDex Proposal Schema and Normalize Provider Output"
        normalizer = nodes[validator_name]["parameters"]["jsCode"]
        self.assertIn("PRODEX_AUTH_REQUIRED", normalizer)
        adapter_json = json.dumps(self.workflow("21-subscription-agent-adapter.json"))
        self.assertNotIn("Run Claude Subscription Provider", adapter_json)
        self.assertEqual(
            self.workflow("21-subscription-agent-adapter.json")["meta"]["supportedProviders"],
            ["CODEX_SUBSCRIPTION"],
        )
        self.assertNotIn("providerBranchesEnabled", self.workflow("21-subscription-agent-adapter.json")["meta"])
        self.assertNotIn("Provider Route", self.workflow("21-subscription-agent-adapter.json")["connections"])
        self.assertIn("gpt-5.6-luna", json.dumps(nodes["Subscription Provider Parameters"]))

    def test_subscription_adapter_generator_is_direct_only(self) -> None:
        generator = (N8N / "refactor_workflow_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("CLAUDE_SUBSCRIPTION", generator)
        self.assertNotIn("@ggomez91npm/n8n-nodes-claude-code", generator)

    def test_custom_node_registry_uses_exact_full_types_and_versions(self) -> None:
        contract = self.registry["custom_nodes"]
        used = {
            node["type"]: node["typeVersion"]
            for workflow in self.workflows.values()
            for node in workflow["nodes"]
            if node["type"].startswith("n8n-nodes-finance.")
        }
        self.assertEqual(set(used), set(contract["full_node_types"].values()))
        self.assertEqual(used, contract["type_versions"])

    def test_readme_does_not_claim_exports_are_executable(self) -> None:
        readme = (N8N / "README.md").read_text(encoding="utf-8")
        self.assertIn("SPEC_ONLY", readme)
        self.assertIn("have not yet passed exact-image import", readme)
        self.assertIn("The policy forbids API-key fallback", readme)
        self.assertIn("native n8n OpenAI credential is not used", readme)


if __name__ == "__main__":
    unittest.main()
