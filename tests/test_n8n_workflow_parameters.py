from __future__ import annotations

from copy import deepcopy
import json
import unittest

from jsonschema import Draft202012Validator

from integrations.n8n.validate_workflow_parameters import (
    CONTRACT_PATH,
    ROOT,
    SCHEMA_PATH,
    load_json,
    scan,
)


def set_workflow(name: str, field: str, value: object, *, field_type: str = "string") -> tuple[str, dict]:
    return name, {
        "nodes": [
            {
                "name": "Workflow Parameters",
                "type": "n8n-nodes-base.set",
                "parameters": {
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
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(
            {(row["workflow"], row["node"], row["code"]) for row in report["findings"]},
            {
                ("01-outlook-finance-acquisition.json", "Acquisition Parameters", "PARAMETER_PASSTHROUGH_ENABLED"),
                ("03-shared-statement-pipeline.json", "Statement Pipeline Parameters", "PARAMETER_PASSTHROUGH_ENABLED"),
                ("09-ai-proposal.json", "Agent Proposal Parameters", "PARAMETER_PASSTHROUGH_ENABLED"),
                ("14-local-pdf-extraction.json", "PDF Extraction Parameters", "PARAMETER_PASSTHROUGH_ENABLED"),
                ("14-local-pdf-extraction.json", "Ready for Deterministic Parser", "PARAMETER_PASSTHROUGH_ENABLED"),
                ("20-actual-outbox-apply.json", "Actual Writer Parameters", "PARAMETER_PASSTHROUGH_ENABLED"),
                ("21-subscription-agent-adapter.json", "Subscription Provider Parameters", "PARAMETER_PASSTHROUGH_ENABLED"),
            },
        )
        self.assertEqual(report["duplicate_literals"][0]["literal"], "default")
        self.assertTrue(report["duplicate_literals"][0]["allowed"])

    def test_model_and_reasoning_values_are_source_selected_not_literal_allowlisted(self) -> None:
        contract = load_json(CONTRACT_PATH)
        fields = contract["workflows"]["21-subscription-agent-adapter.json"]["nodes"]["Subscription Provider Parameters"]["fields"]
        selected = {
            name: spec
            for name, spec in fields.items()
            if "model" in name or "reasoning_effort" in name or name.endswith("package")
        }
        self.assertEqual(len(selected), 10)
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
