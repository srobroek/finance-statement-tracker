from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOWS = N8N / "workflows"


# The approved visual rebaseline removes these historical generated stage
# labels.  The current-head extraction removed a few of these nodes before
# this cleanup, but the full allowlist remains a regression guard against any
# reintroduction.  One blocker warning note is intentionally retained and
# are asserted separately below.
_REMOVED_STAGE_NOTE_RANGES = {
    "001": range(1, 7),
    "002": range(1, 3),
    "003": range(1, 6),
    "004": range(1, 4),
    "005": range(1, 4),
    "009": range(1, 5),
    "010": range(1, 4),
    "011": range(1, 4),
    "012": range(1, 9),
    "013": range(1, 2),
    "014": range(1, 3),
    "016": range(1, 3),
    "017": range(1, 2),
    "018": range(1, 2),
    "019": range(1, 5),
    "020": range(1, 5),
    "021": range(1, 2),
}
REMOVED_STAGE_NOTE_IDS = {
    f"10000000-0000-4000-8000-000000000{workflow}-generated-note-{number}"
    for workflow, numbers in _REMOVED_STAGE_NOTE_RANGES.items()
    for number in numbers
}
RETAINED_OPERATOR_WARNING_NOTE_IDS = {
    "10000000-0000-4000-8000-000000000015-generated-note-1",
}


# These are the reviewed regular-export fingerprints.  A deliberate workflow
# edit must update this snapshot in the same review as the export change.
CORPUS_SNAPSHOT = {'01-outlook-finance-acquisition.json': {'nodes': 42,
                                         'edges': 46,
                                         'sticky': 0,
                                         'groups': 7,
                                         'node_ids_sha256': '6a0ef6d52743499bac9c51b27a761b32dee24c0af24d7d69bd147a1458e94915',
                                         'connections_sha256': 'e1effc687d4d0118964ffbb6557329ede32cbb0cdd26820f33f77fd583063540',
                                         'parameters_sha256': '540d57b89c412f7332e05708b6ca302972d53f59cf34fc10b3ae8ad88eeb3857',
                                         'groups_sha256': '4664caa773a2aab6e3bbcef3039ff003c2db47d29fb990a56e1ceb867b42f43c'},
 '02-rakbank-live-cashback.json': {'nodes': 12,
                                   'edges': 11,
                                   'sticky': 0,
                                   'groups': 2,
                                   'node_ids_sha256': '2e195a65a480836bc390a0af0932abd2f34b77c2aa0b7dc2f3a6891130138fa6',
                                   'connections_sha256': '265a9c534d53ffa987bcae7c6ac00ca20bab617b485e1d008630550565fd703e',
                                   'parameters_sha256': 'd0b771b06bc1041c4d6be3473e6a1b98f58329eee34a9c90a8617d9fdc9db39d',
                                   'groups_sha256': 'bc8a2c2b8d599feca94f286e915e100d51cd23bb37cc44f5be5d9605152b0715'},
 '03-shared-statement-pipeline.json': {'nodes': 49,
                                       'edges': 52,
                                       'sticky': 0,
                                       'groups': 5,
                                       'node_ids_sha256': 'd3194e26de68314113b7692c4fd1e2883c373baf3bcb68489f089c2622ad7419',
                                       'connections_sha256': 'd1ae93d27fe550ae7d1ce8db0f03b1a202457c294b4348e3e4fecee6206f5a2d',
                                       'parameters_sha256': '71c5441c950fef1a99d319007a379cbd734be1cdbb8e2654b1bf6ff7b88317d1',
                                       'groups_sha256': '82b1b5b302e05a98f640377b1492843ac6f6408a40de0a58f93b0abe7763879a'},
 '04-ei-monthly-statement.json': {'nodes': 3,
                                  'edges': 2,
                                  'sticky': 0,
                                  'groups': 1,
                                  'node_ids_sha256': 'a940322f4533b277859116892c286226677e74c743e18c34dc8535f7c9842a8a',
                                  'connections_sha256': 'ecb0981ee4ee51c0412063b6578bbe9d34a12486d320660e85927cdfbcd94fff',
                                  'parameters_sha256': '59ed25468fac57062bdff5e25c7fc7b88bfd554f4e6fc7d6c917f2896f90a2c7',
                                  'groups_sha256': '350935f05b4ebfa132cbc8c34ac64198fa5cb0d8892f409e044f8fc48953e1b3'},
 '05-wio-monthly-statement.json': {'nodes': 3,
                                   'edges': 2,
                                   'sticky': 0,
                                   'groups': 1,
                                   'node_ids_sha256': '009da3b20276e97aab46f240718284723748de52371e897056b5f2efcb8e1324',
                                   'connections_sha256': 'ecb0981ee4ee51c0412063b6578bbe9d34a12486d320660e85927cdfbcd94fff',
                                   'parameters_sha256': 'acffbb0c6e3000cb97dcc979cf702587faf8f43724c1559bcfe23eb845f8a6ad',
                                   'groups_sha256': '55a439d01325d7f0005ae93ebc9583397ae3e7cb9cd783d5980de1b3417b9bf6'},
 '09-ai-proposal.json': {'nodes': 27,
                         'edges': 26,
                         'sticky': 0,
                         'groups': 4,
                         'node_ids_sha256': '8400f4d566da50dc7f4ee9a7fb78004b777e8be1269217b893a52dfce610584e',
                         'connections_sha256': '47a2d6f22243d5b6d7d797759ee7009165846e8b8f8a145b51c0e6ccaa470121',
                         'parameters_sha256': 'c7bc4d1dc7fd3f82061a79a02d191586f34abbbfcf1490f2abe52006a4acce45',
                         'groups_sha256': 'eac7f53359623e5596f8bf8d03060139765f5ddcc8f474d485fc4786f1be480a'},
 '10-finance-operations-status.json': {'nodes': 22,
                                       'edges': 22,
                                       'sticky': 0,
                                       'groups': 4,
                                       'node_ids_sha256': 'f1445a8c54733005522f5fcf996dc9b2c33d6698b3dda327e22820876f957dfc',
                                       'connections_sha256': '1f21c1b73354ea60791d63b068052f095984d42b9d2088dcdcbd4f379ebe6a26',
                                       'parameters_sha256': '270f68296287010b036c5fc2089d8eb911b3ab493dd34600d72a6fe9c0e1b4f3',
                                       'groups_sha256': '9daf4bf7a5909b9916da9aefa6145ecd2ce605dd8067ea869e165290360eb377'},
 '11-interactive-artifact-handoff.json': {'nodes': 22,
                                          'edges': 23,
                                          'sticky': 0,
                                          'groups': 3,
                                          'node_ids_sha256': 'd1fadaeaf61dd29d98518ad6aaf6756a800b5279556cee09a8b7f2cf4735cc37',
                                          'connections_sha256': '8c1bf63b034e6f0989f80e92d7191549f0294b8590f403abe914e795465c97d6',
                                          'parameters_sha256': '5e71787de7e78082443b7ffa811ae782ac78e569f00c2d1fa4f0dc1ba9910df2',
                                          'groups_sha256': '250d8ca38ae228681ae3d3f0601a135bc4437452f9904ce74c46d73a36268007'},
 '12-outlook-message-sweep.json': {'nodes': 91,
                                   'edges': 96,
                                   'sticky': 0,
                                   'groups': 19,
                                   'node_ids_sha256': '224f06b5d75af8f5f310ec6af38c7d0b32ae5f59bc39cc24e50e6fab9bb5aff5',
                                   'connections_sha256': '5c676ffec2dd0e3fd3c164560e8a8d0322fa4f705fb55c47bd65a78e988d4048',
                                   'parameters_sha256': '5ed205d328b988e464605193949fed5d00e2521ec61ff4a53b497777599735f8',
                                   'groups_sha256': '946bb8968c5df4f9f4666aa89b25831cc0856d078d40e85e6ca16e407f4113a0'},
 '13-document-extraction-request.json': {'nodes': 8,
                                         'edges': 7,
                                         'sticky': 0,
                                         'groups': 1,
                                         'node_ids_sha256': 'a414f6411a3b668c9b234320bc32481b19bd90297421dd8da00dc075a86746df',
                                         'connections_sha256': '41e0c26cff4d0d117034577e1d8df5cc090190a3015e6f82a5a415e30d1ce19a',
                                         'parameters_sha256': 'a92eee8ba921d443cc132c815447dc26d28fb7d0339da34aef30573a8c597a7b',
                                         'groups_sha256': 'f796452bc5e2ecd93cef391080a2ca74c41d56c9ab85cc9d4ae70fbf0e59f4f8'},
 '14-local-pdf-extraction.json': {'nodes': 9,
                                  'edges': 8,
                                  'sticky': 0,
                                  'groups': 1,
                                  'node_ids_sha256': '577ae11a3b07086294072ce5b6acf452993eaddd6b591140d011285da2ce58ad',
                                  'connections_sha256': '33a911a9a17f40329ee434f5335de58f019757622eddd205b27c3be6a9460104',
                                  'parameters_sha256': '133c2aa1fae8518f6fee1aadc26d0a3701ce55f3c95ff8c0011a81b93e9fbf79',
                                  'groups_sha256': 'f5302f8b0dad29e0f1da463e0eca2324188d1d799b978e921c5350c12ec971da'},
 '15-finance-mcp-facade.json': {'nodes': 5,
                                'edges': 3,
                                'sticky': 1,
                                'groups': 0,
                                'node_ids_sha256': 'fa000969bfd8185cc920099d7fd8b9b3969d948b4495238ea14257e7424c1602',
                                'connections_sha256': '7c9d5fb02c48b45838ebb3ba1defabf9a133c0a640b98c9d0d122595404059ae',
                                'parameters_sha256': 'e8887cd16fe823b9bd28b66ec2fc7e4016a8d68e1d72cfb273bb2b716f50bf1c',
                                'groups_sha256': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'},
 '16-operations-error-handler.json': {'nodes': 8,
 'edges': 7,
 'sticky': 0,
 'groups': 1,
 'node_ids_sha256': '3a08a7c0e2da0410bebb154cdd3d6a5f25edef34c2641a45d94486ea29193de0',
 'connections_sha256': 'e26dfa4789bda48bca0ec534a5e47eb8a0c4af3cf9a02bc170d09735c8a24635',
 'parameters_sha256': 'ae4577afbe5f42b9ff379d13bef66dbe9d13488d91f67583cb4620fc3d050b1c',
 'groups_sha256': '424498dcd5f34f2270742ee5798be41f37c79ed766efc22f894622c48fb911d4'},
 '17-actual-outbox-recovery.json': {'nodes': 3,
                                    'edges': 2,
                                    'sticky': 0,
                                    'groups': 1,
                                    'node_ids_sha256': 'b6b448b0beaa1bf7843621deff6926a433c832583db17c69ab03fc94f57703da',
                                    'connections_sha256': '9f430d2b6f8b229f105b447bbfc28cc2c283d7fcdb7c7d2907e090f367cd4713',
                                    'parameters_sha256': '5a54e27c72f882cfce8ca806e5a61ce5a119ff4a6ffe79f0f1e9c9c6c8070987',
                                    'groups_sha256': '4d1730ef2989dd5c4ab533d48fd4842c063dc6afe9a3404aa64c40278f6e8978'},
 '18-finance-writer-lease.json': {'nodes': 7,
                                  'edges': 8,
                                  'sticky': 0,
                                  'groups': 1,
                                  'node_ids_sha256': '540ec1502e83accc5d6e45648b638799e80cc02cbede1f547087967ea9d158ab',
                                  'connections_sha256': 'd3527a6da1b9fa920bb87205c341f888c4080e5c12d5ef4caf668506c73fc5d6',
                                  'parameters_sha256': 'a79c5984c7e60c20059c46ad4509b1fbad1d11b47c531977a45d11755cc90b47',
                                  'groups_sha256': '7fc72af75ed9e79cd041ca76520131daad0f1166c1b37c107d47e9ae775d6b7a'},
 '19-platform-data-table-bootstrap.json': {'nodes': 23,
 'edges': 22,
 'sticky': 0,
 'groups': 3,
 'node_ids_sha256': 'bd9ab030ce145c1faeb13f91d55f419b66efddfec724a6ebe709829d0ea44f79',
 'connections_sha256': '3841284460c041a4dce73fe06f8f3ead6f3c6d5b69acfbf5fc15b4b62a19ac83',
 'parameters_sha256': 'b615b52eff0b0380304daf9c45cb2d1ed3218ab3622ec1b9345ff1f3fef96721',
 'groups_sha256': '8c59a6de13b48f6062d325ebcc88700a1dd033f142989291cb1d646b516a961a'},
 '20-actual-outbox-apply.json': {'nodes': 42,
                                 'edges': 45,
                                 'sticky': 0,
                                 'groups': 4,
                                 'node_ids_sha256': '7273c5e2848b14ae0492e26dc49f2d3b71329fd6ca09e28126480c2d7ad0fcaf',
                                 'connections_sha256': 'ae7e35f09ea1f8d8d14a6163ee69d58b4ccd5efb757853a3c4379fd861feba57',
                                 'parameters_sha256': '6cc61e2c411137d4e31224a06fcfc572a6abbb50c1aefdfbee943464adb790b4',
                                 'groups_sha256': '9eb816e129431247bad3b0b6acf54c1f800dd7535ace24a900764d50d3ca186c'},
 '21-subscription-agent-adapter.json': {'nodes': 5,
                                        'edges': 4,
                                        'sticky': 0,
                                        'groups': 1,
                                        'node_ids_sha256': '1a110c098db11f745871eed5f147f8d49fc769fc24942fb3147f80146c34c262',
                                        'connections_sha256': '2b1b9065d7e4ee46faf6c409e01f0ee1e4156d5b99c93861054c267922f939bd',
                                        'parameters_sha256': '4fbad26a6324e22efbe34cd8069e75479233a14a9e2735f5bc3eca80452a9550',
                                        'groups_sha256': 'd591ac682902484fecae6ac68da4b601d353bc4a7dd9768cc92d7caaa514d814'},
 '22-shared-monthly-statement-cycle.json': {'nodes': 16,
                                            'edges': 15,
                                            'sticky': 0,
                                            'groups': 0,
                                            'node_ids_sha256': '069c61f8f0803b90027c9f0ddd6fcb4a548d06297017816ead42da0305de2119',
                                            'connections_sha256': 'c64db46a4ea0c005bfc3ae8f094b4bb992ee507b8e73152d39558aaeb047b6e2',
                                            'parameters_sha256': '155cff0b3826af6288c314d8e2e6613f7efced78960b200a101e6466b2316aff',
                                            'groups_sha256': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'}}


VISUAL_CLEANUP_SNAPSHOT: dict[str, dict] = {}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def edge_count(workflow: dict) -> int:
    return sum(
        len(branch)
        for outputs in workflow["connections"].values()
        for branches in outputs.values()
        for branch in branches
    )


def executable_parameter_snapshot(workflow: dict) -> list[dict]:
    rows = [
        {
            "id": node["id"],
            "type": node["type"],
            "typeVersion": node.get("typeVersion"),
            "parameters": node.get("parameters", {}),
        }
        for node in workflow["nodes"]
    ]
    return sorted(rows, key=lambda row: row["id"])


def group_snapshot(workflow: dict) -> list[dict]:
    return [
        {
            "name": group["name"],
            "nodeIds": group["nodeIds"],
            "description": group["description"],
        }
        for group in workflow.get("nodeGroups", [])
    ]


def registry_export_code_mismatches(
    rows: list[dict], workflows: dict[str, dict]
) -> list[str]:
    mismatches: list[str] = []
    for row in rows:
        filename = row.get("file")
        workflow = workflows.get(filename)
        export_code = workflow.get("meta", {}).get("financeWorkflowCode") if workflow else None
        if export_code != row.get("code"):
            mismatches.append(
                f"{filename}: registry code={row.get('code')!r}, export code={export_code!r}"
            )
    return mismatches


class N8nWorkflowCorpusIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_json(N8N / "pipeline-registry.json")
        cls.folder_manifest = load_json(N8N / "workflow-folders.json")
        cls.workflows = {
            path.name: load_json(path)
            for path in sorted(WORKFLOWS.glob("*.json"))
        }

    def assert_metric(self, filename: str, metric: str, expected: object, actual: object) -> None:
        self.assertEqual(
            actual,
            expected,
            f"{filename}: {metric} mismatch; expected {expected!r}, got {actual!r}",
        )

    def test_regular_exports_are_exactly_inactive_and_unpublished(self) -> None:
        self.assert_metric("corpus", "regular export count", 19, len(self.workflows))
        missing = sorted(set(CORPUS_SNAPSHOT) - set(self.workflows))
        extra = sorted(set(self.workflows) - set(CORPUS_SNAPSHOT))
        self.assertEqual(
            (missing, extra),
            ([], []),
            f"regular export filenames drifted; missing={missing}, extra={extra}",
        )
        active = sorted(name for name, workflow in self.workflows.items() if workflow.get("active") is not False)
        self.assertEqual(active, [], f"active regular exports: {active}")
        for filename, workflow in self.workflows.items():
            with self.subTest(workflow=filename):
                self.assertIs(workflow.get("active"), False)
                self.assertEqual(workflow["meta"]["migrationStatus"], "SPEC_ONLY")
                for field in ("published", "isPublished", "activeVersion", "activeVersionId"):
                    self.assertFalse(
                        workflow.get(field),
                        f"{filename}: publication marker {field!r} is set",
                    )

        rows = self.registry["workflows"]
        self.assertEqual(
            {row["status"] for row in rows},
            {"SPEC_ONLY"},
            "registry has a non-SPEC_ONLY workflow status",
        )
        self.assertEqual(
            set(self.registry["execution_evidence"].values()),
            {False},
            "registry contains positive execution/publication evidence",
        )

    def test_registry_and_folder_manifest_are_bijective(self) -> None:
        rows = self.registry["workflows"]
        self.assert_metric("registry", "workflow row count", 19, len(rows))
        registry_files = [row["file"] for row in rows]
        registry_codes = [row["code"] for row in rows]
        self.assertEqual(len(registry_files), len(set(registry_files)), "duplicate registry files")
        self.assertEqual(len(registry_codes), len(set(registry_codes)), "duplicate registry codes")
        self.assertEqual(set(registry_files), set(self.workflows), "registry/export file bijection drift")
        self.assertEqual(
            {workflow["meta"]["financeWorkflowCode"] for workflow in self.workflows.values()},
            set(registry_codes),
            "registry/export workflow-code bijection drift",
        )
        self.assertEqual(
            registry_export_code_mismatches(rows, self.workflows),
            [],
            "registry row is bound to the wrong export: "
            f"{registry_export_code_mismatches(rows, self.workflows)}",
        )

        folders_by_id = {folder["id"]: folder for folder in self.folder_manifest["folders"]}
        folder_by_code = {
            row["code"]: folders_by_id[row["folder_id"]]
            for row in self.folder_manifest["workflows"]
        }
        same_folder_id = folder_by_code[rows[0]["code"]]["id"]
        same_folder_indices = [
            index
            for index, row in enumerate(rows)
            if folder_by_code[row["code"]]["id"] == same_folder_id
        ]
        self.assertGreaterEqual(
            len(same_folder_indices),
            2,
            "negative fixture requires two registry rows in one folder",
        )
        swapped_rows = [dict(row) for row in rows]
        first_index, second_index = same_folder_indices[:2]
        swapped_rows[first_index]["code"], swapped_rows[second_index]["code"] = (
            swapped_rows[second_index]["code"],
            swapped_rows[first_index]["code"],
        )
        self.assertNotEqual(
            registry_export_code_mismatches(swapped_rows, self.workflows),
            [],
            "registry/export guard failed to detect a same-folder code swap",
        )

        folders = self.folder_manifest["folders"]
        self.assert_metric("folder manifest", "folder count", 6, len(folders))
        folder_ids = [folder["id"] for folder in folders]
        self.assertEqual(len(folder_ids), len(set(folder_ids)), "duplicate folder IDs")
        folder_keys = [(folder["parentFolderId"], folder["name"]) for folder in folders]
        self.assertEqual(len(folder_keys), len(set(folder_keys)), "duplicate folder names within a parent")
        codes = [row["code"] for row in self.folder_manifest["workflows"]]
        self.assertEqual(len(codes), len(set(codes)), "duplicate folder workflow codes")
        self.assertEqual(set(codes), set(registry_codes), "folder/registry workflow-code bijection drift")
        self.assertEqual(
            self.folder_manifest["tags"],
            ["finance", "setup-required", "inactive", "active"],
        )
        self.assertEqual(
            self.folder_manifest["workflow_tags"],
            ["finance", "setup-required", "inactive"],
        )
        by_code = folder_by_code
        expected_tags = self.folder_manifest["workflow_tags"]
        for row in rows:
            filename = row["file"]
            workflow = self.workflows[filename]
            code = row["code"]
            folder = by_code[code]
            with self.subTest(workflow=filename):
                self.assertEqual(
                    workflow["meta"]["workflowFolder"]["id"],
                    folder["id"],
                )
                self.assertEqual(
                    workflow["meta"]["workflowFolder"]["name"],
                    folder["name"],
                )
                self.assertEqual(
                    workflow["meta"]["workflowFolder"]["placement"],
                    "POST_IMPORT_REVIEWED_MIGRATION",
                )
                self.assertEqual(workflow["meta"]["workflowTags"], expected_tags)
                self.assertEqual(
                    [tag["name"] for tag in workflow["tags"]],
                    expected_tags,
                )
                self.assertEqual(
                    len({tag["id"] for tag in workflow["tags"]}),
                    len(expected_tags),
                    f"{filename}: workflow tag IDs are not unique",
                )
                self.assertNotIn("parentFolderId", workflow)

    def test_node_ids_are_unique_and_connections_have_no_dangling_endpoints(self) -> None:
        all_node_ids: list[tuple[str, str]] = []
        for filename, workflow in self.workflows.items():
            node_names = [node["name"] for node in workflow["nodes"]]
            node_ids = [node["id"] for node in workflow["nodes"]]
            duplicate_names = sorted(name for name, count in Counter(node_names).items() if count > 1)
            duplicate_ids = sorted(node_id for node_id, count in Counter(node_ids).items() if count > 1)
            with self.subTest(workflow=filename):
                self.assertEqual(duplicate_names, [], f"{filename}: duplicate node names: {duplicate_names}")
                self.assertEqual(duplicate_ids, [], f"{filename}: duplicate node IDs: {duplicate_ids}")
            all_node_ids.extend((filename, node_id) for node_id in node_ids)

            names = set(node_names)
            unknown_sources = sorted(set(workflow["connections"]) - names)
            unknown_targets: list[str] = []
            malformed_edges: list[str] = []
            for source, outputs in workflow["connections"].items():
                if not isinstance(outputs, dict):
                    malformed_edges.append(f"{source}: outputs is not an object")
                    continue
                for output_type, branches in outputs.items():
                    if not isinstance(branches, list):
                        malformed_edges.append(f"{source}/{output_type}: branches is not a list")
                        continue
                    for branch_index, branch in enumerate(branches):
                        if not isinstance(branch, list):
                            malformed_edges.append(f"{source}/{output_type}[{branch_index}]: branch is not a list")
                            continue
                        for edge_index, edge in enumerate(branch):
                            if not isinstance(edge, dict) or not isinstance(edge.get("node"), str):
                                malformed_edges.append(
                                    f"{source}/{output_type}[{branch_index}][{edge_index}]: missing node target"
                                )
                            elif edge["node"] not in names:
                                unknown_targets.append(edge["node"])
            with self.subTest(workflow=filename):
                self.assertEqual(unknown_sources, [], f"{filename}: dangling source nodes: {unknown_sources}")
                self.assertEqual(unknown_targets, [], f"{filename}: dangling target nodes: {unknown_targets}")
                self.assertEqual(malformed_edges, [], f"{filename}: malformed connections: {malformed_edges}")

        duplicate_global_ids = sorted(
            node_id
            for node_id, count in Counter(node_id for _, node_id in all_node_ids).items()
            if count > 1
        )
        self.assertEqual(
            duplicate_global_ids,
            [],
            f"node IDs are not unique across regular exports: {duplicate_global_ids}",
        )

    def test_generated_stage_labels_are_removed_and_blocker_warnings_remain(self) -> None:
        sticky_ids = {
            node["id"]
            for workflow in self.workflows.values()
            for node in workflow["nodes"]
            if node["type"] == "n8n-nodes-base.stickyNote"
        }
        self.assertEqual(
            len(REMOVED_STAGE_NOTE_IDS),
            53,
            "approved stage-label allowlist must remain exact",
        )
        self.assertTrue(
            REMOVED_STAGE_NOTE_IDS.isdisjoint(sticky_ids),
            f"approved generated stage labels remain: {sorted(REMOVED_STAGE_NOTE_IDS & sticky_ids)}",
        )
        self.assertEqual(sticky_ids, RETAINED_OPERATOR_WARNING_NOTE_IDS)

    def test_workflow_counts_and_fingerprints_are_deterministic(self) -> None:
        for filename, workflow in self.workflows.items():
            expected = {
                **CORPUS_SNAPSHOT[filename],
                **VISUAL_CLEANUP_SNAPSHOT.get(filename, {}),
            }
            groups = group_snapshot(workflow)
            actual = {
                "nodes": len(workflow["nodes"]),
                "edges": edge_count(workflow),
                "sticky": sum(
                    node["type"] == "n8n-nodes-base.stickyNote"
                    for node in workflow["nodes"]
                ),
                "groups": len(groups),
                "node_ids_sha256": digest(
                    sorted((node["name"], node["id"]) for node in workflow["nodes"])
                ),
                "connections_sha256": digest(workflow["connections"]),
                "parameters_sha256": digest(executable_parameter_snapshot(workflow)),
                "groups_sha256": digest(groups),
            }
            for metric, expected_value in expected.items():
                with self.subTest(workflow=filename, metric=metric):
                    self.assert_metric(filename, metric, expected_value, actual[metric])

            group_names = [group["name"] for group in groups]
            group_node_ids = [node_id for group in groups for node_id in group["nodeIds"]]
            known_node_ids = {node["id"] for node in workflow["nodes"]}
            with self.subTest(workflow=filename, metric="group names"):
                self.assertEqual(
                    len(group_names), len(set(group_names)),
                    f"{filename}: duplicate Canvas Group names",
                )
            with self.subTest(workflow=filename, metric="group membership"):
                self.assertEqual(
                    len(group_node_ids), len(set(group_node_ids)),
                    f"{filename}: duplicate Canvas Group node membership",
                )
                self.assertTrue(
                    set(group_node_ids) <= known_node_ids,
                    f"{filename}: Canvas Group references unknown node IDs",
                )


if __name__ == "__main__":
    unittest.main()
