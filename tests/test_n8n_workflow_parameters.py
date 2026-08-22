from __future__ import annotations

import json
import unittest
from copy import deepcopy

from jsonschema import Draft202012Validator

from integrations.n8n.validate_workflow_parameters import (
    CONTRACT_PATH,
    ROOT,
    SCHEMA_PATH,
    load_json,
    scan,
)


def set_workflow(
    name: str,
    field: str,
    value: object,
    *,
    field_type: str = "string",
    node_name: str = "Workflow Parameters",
) -> tuple[str, dict]:
    return name, {
        "nodes": [
            {
                "name": node_name,
                "type": "n8n-nodes-base.set",
                "parameters": {
                    "includeOtherFields": False,
                    "assignments": {
                        "assignments": [
                            {"name": field, "type": field_type, "value": value}
                        ]
                    }
                },
            }
        ]
    }


class N8nWorkflowParameterTests(unittest.TestCase):
    def test_contract_is_schema_valid_and_corpus_inventory_is_current(self) -> None:
        contract = load_json(CONTRACT_PATH)
        schema = load_json(SCHEMA_PATH)
        errors = sorted(Draft202012Validator(schema).iter_errors(contract), key=str)
        self.assertEqual(errors, [])

        report = scan(contract=contract)
        self.assertEqual(report["counts"]["parameter_nodes"], 8)
        self.assertEqual(
            {(row["workflow"], row["node"]) for row in report["parameter_nodes"]},
            {
                ("01-outlook-finance-acquisition.json", "Acquisition Parameters"),
                ("03-shared-statement-pipeline.json", "Statement Pipeline Parameters"),
                ("09-ai-proposal.json", "Agent Proposal Parameters"),
                ("14-local-pdf-extraction.json", "PDF Extraction Parameters"),
                ("14-local-pdf-extraction.json", "Ready for Deterministic Parser"),
                ("20-actual-outbox-apply.json", "Actual Writer Parameters"),
                ("21-subscription-agent-adapter.json", "Subscription Provider Parameters"),
                ("22-onedrive-finance-evidence-root-setup.json", "Setup Parameters"),
            },
        )
        self.assertEqual(report["status"], "PASS")
        proposal_spec = contract["workflows"]["21-subscription-agent-adapter.json"]["nodes"]["Subscription Provider Parameters"]["fields"]["proposal_output_schema"]
        self.assertEqual(proposal_spec["category"], "global_generated_contract")
        self.assertEqual(proposal_spec["source"]["selector"], "$")
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["duplicate_literals"], [])

    def test_parameter_sets_are_validated_caller_json_merges(self) -> None:
        """Execute the Set merge contract for every owned node and key consumer."""

        contract = load_json(CONTRACT_PATH)
        workflow_paths = {
            **{
                name: ROOT / "integrations" / "n8n" / "workflows" / name
                for name in contract["workflows"]
                if not name.startswith("22-")
            },
            "22-onedrive-finance-evidence-root-setup.json": ROOT
            / "integrations"
            / "n8n"
            / "setup-workflows"
            / "22-onedrive-finance-evidence-root-setup.json",
        }
        for workflow_name, workflow_spec in contract["workflows"].items():
            workflow = load_json(workflow_paths[workflow_name])
            by_name = {node["name"]: node for node in workflow["nodes"]}
            for node_name, node_spec in workflow_spec["nodes"].items():
                with self.subTest(workflow=workflow_name, node=node_name):
                    node = by_name[node_name]
                    parameters = node["parameters"]
                    self.assertIs(parameters["includeOtherFields"], False)
                    caller = {
                        field: {"caller": field}
                        for field in node_spec["caller_fields"]
                    }
                    caller.update({
                        "password": "must-not-forward",
                        "credential": "must-not-forward",
                        "arbitrary_extra": "must-not-forward",
                    })
                    merged = {}
                    assignments = parameters["assignments"]["assignments"]
                    caller_assignments = {
                        assignment["name"]: assignment
                        for assignment in assignments
                        if assignment["name"] in node_spec["caller_fields"]
                    }
                    self.assertEqual(
                        set(caller_assignments), set(node_spec["caller_fields"]),
                        f"{workflow_name}::{node_name} must project the exact caller allowlist",
                    )
                    for field, assignment in caller_assignments.items():
                        self.assertEqual(assignment["type"], node_spec["caller_fields"][field])
                        self.assertEqual(assignment["value"], "={{ $json." + field + " }}")
                    for assignment in assignments:
                        value = assignment["value"]
                        if isinstance(value, str) and value.startswith("={{ $json."):
                            source_field = value.removeprefix("={{ $json.").split(" ", 1)[0].rstrip("}")
                            merged[assignment["name"]] = caller.get(source_field)
                        else:
                            merged[assignment["name"]] = value
                    self.assertTrue(
                        set(node_spec["caller_fields"]).issubset(merged),
                        f"{workflow_name}::{node_name} dropped caller JSON",
                    )
                    self.assertNotIn("password", merged)
                    self.assertNotIn("credential", merged)
                    self.assertNotIn("arbitrary_extra", merged)
                    self.assertLessEqual(
                        set(merged),
                        set(node_spec["caller_fields"]) | set(node_spec["fields"]),
                    )

        # These are the high-risk boundaries called out by the workflow
        # contracts. Their immediate consumers must visibly reference the
        # preserved caller fields, not merely appear in an inventory.
        checks = {
            (
                "03-shared-statement-pipeline.json",
                "Statement Pipeline Parameters",
                "Verify Archive and Execution Context",
            ): ("run_id", "source_attachment_id", "attachment_id"),
            (
                "20-actual-outbox-apply.json",
                "Actual Writer Parameters",
                "Download Immutable Delta Artifact",
            ): ("artifact_item_id",),
            (
                "21-subscription-agent-adapter.json",
                "Subscription Provider Parameters",
                "Validate and Build Fixed Provider Invocation",
            ): ("agent_provider", "policy_class"),
        }
        for (workflow_name, parameter_name, consumer_name), fields in checks.items():
            workflow = load_json(workflow_paths[workflow_name])
            consumer = next(node for node in workflow["nodes"] if node["name"] == consumer_name)
            consumer_text = json.dumps(consumer, sort_keys=True)
            for field in fields:
                self.assertIn(field, consumer_text, f"{workflow_name}::{parameter_name}->{consumer_name}")

        explicit_projectors = {
            "01-outlook-finance-acquisition.json": "Validate Bounded Source Request",
            "03-shared-statement-pipeline.json": "Verify Archive and Execution Context",
            "09-ai-proposal.json": "Validate Untrusted Proposal Request",
            "21-subscription-agent-adapter.json": "Validate and Build Fixed Provider Invocation",
        }
        for workflow_name, node_name in explicit_projectors.items():
            workflow = load_json(workflow_paths[workflow_name])
            consumer = next(node for node in workflow["nodes"] if node["name"] == node_name)
            consumer_text = consumer["parameters"].get("jsCode", "")
            self.assertNotIn("...r", consumer_text, workflow_name)
            self.assertNotIn("...request", consumer_text, workflow_name)
            self.assertNotIn("Object.fromEntries", consumer_text, workflow_name)
            if workflow_name == "21-subscription-agent-adapter.json":
                self.assertNotIn("Object.entries(job)", consumer_text)

    def test_model_and_reasoning_values_are_source_selected_not_literal_allowlisted(self) -> None:
        contract = load_json(CONTRACT_PATH)
        fields = contract["workflows"]["21-subscription-agent-adapter.json"]["nodes"]["Subscription Provider Parameters"]["fields"]
        selected = {
            name: spec
            for name, spec in fields.items()
            if "model" in name or "reasoning_effort" in name or name.endswith("package")
        }
        self.assertEqual(len(selected), 2)
        for name, spec in selected.items():
            if spec["category"] != "global_generated_contract":
                continue
            self.assertIn("source", spec, name)
            self.assertNotIn("gpt-5.6", json.dumps(spec), name)
            self.assertNotIn("xhigh", json.dumps(spec).casefold(), name)

    def test_credential_fields_fail_closed(self) -> None:
        contract = deepcopy(load_json(CONTRACT_PATH))
        contract["workflows"] = {
            "synthetic.json": {
                "nodes": {
                    "Workflow Parameters": {
                        "fields": {
                            "credential_id": {
                                "category": "workflow_local_input",
                                "type": "string",
                            }
                        }
                    }
                }
            }
        }
        report = scan(
            contract=contract,
            documents=[set_workflow("synthetic.json", "credential_id", "oauth2")],
        )
        self.assertIn("CREDENTIAL_OR_SECRET_FIELD", {row["code"] for row in report["findings"]})

    def test_caller_controlled_protected_fields_fail_closed(self) -> None:
        contract = deepcopy(load_json(CONTRACT_PATH))
        contract["workflows"] = {
            "synthetic.json": {
                "nodes": {
                    "Workflow Parameters": {
                        "fields": {
                            "account_id": {
                                "category": "workflow_local_input",
                                "type": "string",
                                "expression_allowed": True,
                            }
                        }
                    }
                }
            }
        }
        report = scan(
            contract=contract,
            documents=[set_workflow("synthetic.json", "account_id", "={{ $json.account_id }}")],
        )
        self.assertIn("PROTECTED_CALLER_INPUT", {row["code"] for row in report["findings"]})

    def test_protected_local_inputs_reject_dynamic_webhook_and_n8n_forms(self) -> None:
        contract = deepcopy(load_json(CONTRACT_PATH))
        contract["workflows"] = {
            "synthetic.json": {
                "nodes": {
                    "Workflow Parameters": {
                        "fields": {
                            "account_id": {
                                "category": "workflow_local_input",
                                "type": "string",
                                "expression_allowed": True,
                            }
                        }
                    }
                }
            }
        }
        forms = (
            "={{ $('Webhook').first().json.account_id }}",
            '={{ $("Webhook").item.json["account_id"] }}',
            '={{ $node["Webhook"].json.account_id }}',
            '={{ $items("Webhook")[0].json.account_id }}',
            '={{ $item(0).$node["Webhook"].json.account_id }}',
            "={{ $('Webhook').all()[0].json.account_id }}",
            "={{ $input.item.json.account_id }}",
        )
        for expression in forms:
            with self.subTest(expression=expression):
                report = scan(
                    contract=contract,
                    documents=[set_workflow("synthetic.json", "account_id", expression)],
                )
                self.assertIn("PROTECTED_CALLER_INPUT", {row["code"] for row in report["findings"]})

    def test_named_node_protected_inputs_fail_for_supported_n8n_forms(self) -> None:
        contract = deepcopy(load_json(CONTRACT_PATH))
        contract["workflows"] = {
            "synthetic.json": {
                "nodes": {
                    "Workflow Parameters": {
                        "fields": {
                            "account_id": {"category": "workflow_local_input", "type": "string"},
                        }
                    },
                    "Consumer Parameters": {
                        "fields": {
                            "neutral_ref": {
                                "category": "workflow_local_input",
                                "type": "string",
                                "expression_allowed": True,
                            },
                        }
                    },
                }
            }
        }
        forms = (
            "={{ $('Workflow Parameters').first().json.account_id }}",
            '={{ $node["Workflow Parameters"].json["account_id"] }}',
            '={{ $items("Workflow Parameters")[0].json.account_id }}',
            '={{ $item(0).$node["Workflow Parameters"].json.account_id }}',
        )
        for expression in forms:
            with self.subTest(expression=expression):
                parameter_document = set_workflow("synthetic.json", "account_id", "safe")
                parameter_document[1]["nodes"].append(
                    set_workflow("synthetic.json", "neutral_ref", expression, node_name="Consumer Parameters")[1]["nodes"][0]
                )
                report = scan(contract=contract, documents=[parameter_document])
                self.assertIn("PROTECTED_NAMED_NODE_INPUT", {row["code"] for row in report["findings"]})

    def test_canonical_credential_identifiers_fail_under_neutral_field_names(self) -> None:
        contract = deepcopy(load_json(CONTRACT_PATH))
        contract["workflows"] = {
            "synthetic.json": {
                "nodes": {
                    "Workflow Parameters": {
                        "fields": {
                            "neutral_label": {"category": "workflow_local_constant", "type": "string"},
                        }
                    }
                }
            }
        }
        report = scan(
            contract=contract,
            documents=[set_workflow("synthetic.json", "neutral_label", "financeStatementPassword")],
        )
        self.assertIn("CREDENTIAL_IDENTIFIER_IN_PARAMETER", {row["code"] for row in report["findings"]})

    def test_full_document_global_contract_rejects_drift(self) -> None:
        contract = load_json(CONTRACT_PATH)
        document = deepcopy(load_json(ROOT / "integrations" / "n8n" / "workflows" / "21-subscription-agent-adapter.json"))
        for node in document["nodes"]:
            if node.get("name") != "Subscription Provider Parameters":
                continue
            for assignment in node["parameters"]["assignments"]["assignments"]:
                if assignment.get("name") == "proposal_output_schema":
                    assignment["value"] = json.dumps({"type": "object"})
        report = scan(contract=contract, documents=[("21-subscription-agent-adapter.json", document)])
        self.assertIn("GLOBAL_DOCUMENT_MISMATCH", {row["code"] for row in report["findings"]})

    def test_unallowlisted_parameter_node_and_field_fail_closed(self) -> None:
        contract = deepcopy(load_json(CONTRACT_PATH))
        contract["workflows"] = {
            "synthetic.json": {
                "nodes": {
                    "Workflow Parameters": {
                        "fields": {
                            "known": {"category": "workflow_local_constant", "type": "string"},
                        }
                    }
                }
            }
        }
        known = set_workflow("synthetic.json", "known", "ok")[1]["nodes"][0]
        known["parameters"]["assignments"]["assignments"].append(
            {"name": "unexpected", "type": "string", "value": "bad"}
        )
        unowned = set_workflow("synthetic.json", "known", "ok", node_name="Unexpected Parameters")[1]["nodes"][0]
        report = scan(
            contract=contract,
            documents=[("synthetic.json", {"nodes": [known, unowned]})],
        )
        codes = {row["code"] for row in report["findings"]}
        self.assertIn("PARAMETER_FIELD_UNALLOWLISTED", codes)
        self.assertIn("PARAMETER_NODE_UNALLOWLISTED", codes)

    def test_unrestricted_projector_and_sensitive_extras_fail_closed(self) -> None:
        contract = deepcopy(load_json(CONTRACT_PATH))
        contract["workflows"] = {
            "synthetic.json": {
                "nodes": {
                    "Workflow Parameters": {
                        "caller_fields": {"safe": "string"},
                        "fields": {
                            "local": {"category": "workflow_local_constant", "type": "string"},
                            "account_id": {
                                "category": "workflow_local_input",
                                "type": "string",
                                "expression_allowed": True,
                            },
                        },
                    }
                }
            }
        }
        document = set_workflow("synthetic.json", "safe", "={{ $json.safe }}")[1]
        node = document["nodes"][0]
        node["parameters"]["includeOtherFields"] = True
        node["parameters"]["assignments"]["assignments"].extend([
            {"name": "password", "type": "string", "value": "={{ $json.password }}"},
            {"name": "arbitrary_extra", "type": "string", "value": "={{ $json.arbitrary_extra }}"},
            {"name": "account_id", "type": "string", "value": "={{ $json.account_id }}"},
        ])
        report = scan(contract=contract, documents=[("synthetic.json", document)])
        codes = {row["code"] for row in report["findings"]}
        self.assertIn("CALLER_PROJECTOR_UNRESTRICTED", codes)
        self.assertIn("PARAMETER_FIELD_UNALLOWLISTED", codes)
        self.assertIn("CREDENTIAL_OR_SECRET_FIELD", codes)
        self.assertIn("PROTECTED_CALLER_INPUT", codes)

    def test_repeated_global_literal_in_local_nodes_is_reported(self) -> None:
        contract = deepcopy(load_json(CONTRACT_PATH))
        contract["workflows"] = {
            filename: {
                "nodes": {
                    "Workflow Parameters": {
                        "fields": {
                            "model_alias": {
                                "category": "workflow_local_constant",
                                "type": "string",
                            }
                        }
                    }
                }
            }
            for filename in ("synthetic-a.json", "synthetic-b.json")
        }
        model = load_json(ROOT / "config" / "agent-providers.json")["providers"]["CODEX_SUBSCRIPTION"]["normal_model"]
        report = scan(
            contract=contract,
            documents=[
                set_workflow("synthetic-a.json", "model_alias", model),
                set_workflow("synthetic-b.json", "model_alias", model),
            ],
        )
        self.assertIn("SHARED_LITERAL_COPIED", {row["code"] for row in report["findings"]})


if __name__ == "__main__":
    unittest.main()
