from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "integrations/n8n/schemas/prodex-promotion-restart-receipt-v1.schema.json"
DOCKERFILE_PATH = ROOT / "packages/n8n-nodes-finance/Dockerfile.n8n"
WORKFLOW_PATH = ROOT / ".github/workflows/phase1-finance-artifacts.yml"


def schema_errors(document: dict, schema: dict) -> list:
    return sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=str,
    )


def cross_field_errors(document: dict) -> list[str]:
    errors = []
    source_commit = document["source"]["finance_commit"]
    image = document["image"]
    identities = {name: image[name] for name in ("candidate", "before", "after")}
    for name, identity in identities.items():
        reference_digest = identity["reference"].rsplit("@", 1)[-1]
        if identity["digest"] != reference_digest:
            errors.append(f"{name}: reference digest mismatch")
    if identities["candidate"] != identities["after"]:
        errors.append("candidate and after image identities differ")
    if identities["after"]["source_commit"] != source_commit:
        errors.append("after image source commit mismatch")
    protected = document["protected_state"]
    if protected["equal"] and protected["before_sha256"] != protected["after_sha256"]:
        errors.append("protected state fingerprint mismatch")
    return errors


def verified_receipt() -> dict:
    digest = "b" * 64
    commit = "a" * 40

    def image() -> dict:
        return {
            "reference": f"ghcr.io/srobroek/finance-n8n@sha256:{digest}",
            "digest": f"sha256:{digest}",
            "source_commit": commit,
        }

    return {
        "schema_version": 1,
        "status": "VERIFIED",
        "scope": "PRODEX_IMAGE_PROMOTION_RESTART",
        "redacted": True,
        "recorded_at_utc": "2026-08-26T00:00:00Z",
        "source": {
            "finance_commit": commit,
            "dockerfile_sha256": "c" * 64,
            "community_lock_sha256": "d" * 64,
        },
        "image": {
            "candidate": image(),
            "before": image(),
            "after": image(),
            "digest_matches_reference": True,
        },
        "auth": {
            "mode": "CHATGPT_SUBSCRIPTION",
            "mount_path": "/home/node/.n8n/codex",
            "auth_file": {"present": True, "mode": "0600", "uid": 1000, "gid": 1000},
        },
        "process_context": {
            "cwd": "/home/node",
            "sdk_package": "@openai/codex-sdk",
            "sdk_version": "0.142.1",
            "prodex_version": "0.5.1",
            "import_verified": True,
        },
        "protected_state": {
            "scope": "N8N_PROTECTED_STATE",
            "before_sha256": "e" * 64,
            "after_sha256": "e" * 64,
            "equal": True,
        },
        "restart": {
            "only_n8n_restarted": True,
            "n8n_healthy_after_restart": True,
            "auth_reused": True,
            "process_context_import_verified_after_restart": True,
        },
        "topology": {
            "direct_node_execution": True,
            "separate_runner_required": False,
            "separate_listener_required": False,
            "api_key_required": False,
            "per_run_auth_required": False,
        },
        "redaction": {
            "secret_values_recorded": False,
            "token_values_recorded": False,
            "api_key_values_recorded": False,
            "auth_contents_recorded": False,
        },
    }


class N8nProdexRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_verified_promotion_restart_receipt_is_schema_valid(self) -> None:
        receipt = verified_receipt()
        errors = schema_errors(receipt, self.schema)
        self.assertEqual(errors, [], "; ".join(error.message for error in errors))
        self.assertEqual(cross_field_errors(receipt), [])

    def test_verified_receipt_binds_image_identities_and_protected_fingerprints(self) -> None:
        for mutate, expected in (
            (lambda receipt: receipt["image"]["candidate"].update(digest="sha256:" + "0" * 64), "reference digest mismatch"),
            (lambda receipt: receipt["image"]["after"].update(reference="ghcr.io/srobroek/finance-n8n@sha256:" + "0" * 64), "reference digest mismatch"),
            (lambda receipt: receipt["image"]["after"].update(source_commit="f" * 40), "source commit mismatch"),
            (lambda receipt: receipt["protected_state"].update(after_sha256="f" * 64), "fingerprint mismatch"),
        ):
            invalid = copy.deepcopy(verified_receipt())
            mutate(invalid)
            self.assertEqual(schema_errors(invalid, self.schema), [], expected)
            self.assertIn(expected, " ".join(cross_field_errors(invalid)))

    def test_verified_receipt_requires_import_restart_and_protected_state_proof(self) -> None:
        for path in (
            ("process_context", "import_verified"),
            ("restart", "only_n8n_restarted"),
            ("restart", "auth_reused"),
            ("restart", "process_context_import_verified_after_restart"),
            ("protected_state", "equal"),
        ):
            invalid = copy.deepcopy(verified_receipt())
            invalid[path[0]][path[1]] = False
            self.assertTrue(schema_errors(invalid, self.schema), path)

    def test_receipt_rejects_api_key_or_auth_material_and_non_direct_topology(self) -> None:
        invalid = copy.deepcopy(verified_receipt())
        invalid["topology"]["api_key_required"] = True
        self.assertTrue(schema_errors(invalid, self.schema))

        invalid = copy.deepcopy(verified_receipt())
        invalid["auth"]["auth_file"]["access_token"] = "not-recorded"
        self.assertTrue(schema_errors(invalid, self.schema))

    def test_image_uses_n8n_process_cwd_and_existing_immutable_sdk_tree(self) -> None:
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "ln -s /opt/finance-n8n/community-extensions/node_modules /home/node/node_modules",
            dockerfile,
        )
        self.assertIn("WORKDIR /home/node\nUSER node", dockerfile)
        self.assertNotIn("/home/node/.n8n/nodes/node_modules/@openai", dockerfile)

        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("-v \"$state_dir:/home/node/.n8n\"", workflow)
        self.assertIn("process.cwd() !== '/home/node'", workflow)
        self.assertIn("import { Codex } from '@openai/codex-sdk'", workflow)


if __name__ == "__main__":
    unittest.main()
