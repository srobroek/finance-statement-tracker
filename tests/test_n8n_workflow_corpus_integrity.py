from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "integrations" / "n8n"
WORKFLOWS = N8N / "workflows"


# These are the reviewed regular-export fingerprints.  A deliberate workflow
# edit must update this snapshot in the same review as the export change.
CORPUS_SNAPSHOT = {
    "01-outlook-finance-acquisition.json": {
        "nodes": 32, "edges": 27, "sticky": 4, "groups": 5,
        "node_ids_sha256": "7a200d3d7de3f098829e4a790e4260d6f0859be8cec8220b8cd0117f3fc3f93a",
        "connections_sha256": "10ac76d9b15c2b38ebbc3302ba6f935637b2fe8135b172a61219cd2761cfe5f7",
        "parameters_sha256": "e152a8ab80c1fe7b1fc16459d6f1fcaea651b6e8e1e66ec7c17b92fc1ec35351",
        "groups_sha256": "8e17c89f183c13b3f737fee417ff7e6faeb5c83c2a66143b44b04cf2b0c02130",
    },
    "02-rakbank-live-cashback.json": {
        "nodes": 14, "edges": 11, "sticky": 2, "groups": 2,
        "node_ids_sha256": "e3048a521c89a80e1ba89af3ecdce88e6bc02ad35563b8250171c7db47a495be",
        "connections_sha256": "265a9c534d53ffa987bcae7c6ac00ca20bab617b485e1d008630550565fd703e",
        "parameters_sha256": "92c741c4cc76bbcc651ab292c43e4ad7e7db7f25855c613c083cfa0c7854e491",
        "groups_sha256": "bc8a2c2b8d599feca94f286e915e100d51cd23bb37cc44f5be5d9605152b0715",
    },
    "03-shared-statement-pipeline.json": {
        "nodes": 38, "edges": 34, "sticky": 5, "groups": 4,
        "node_ids_sha256": "32009bc5c4e11387eba10f694b870cf51e0a913068aa71ab96219a2e0fc6fda6",
        "connections_sha256": "ec73420f97757715df91a765f331980315a2705d3aab6ae5ab82879027d8c67f",
        "parameters_sha256": "5f2583a9b90b2e7263706ac5c74d9b33c31d342f85681cdc288cac22104e855b",
        "groups_sha256": "6b46936ea631c054301c7bde9acdc3d3792873d1efcf8e4a54cd977320352144",
    },
    "04-ei-monthly-statement.json": {
        "nodes": 13, "edges": 10, "sticky": 2, "groups": 2,
        "node_ids_sha256": "b5a9ad15be4694091a3d2d8c6109b6543b04c4d11a72424f3a40915db23f0ba6",
        "connections_sha256": "0c229f1577dc41cae23482089751a8100b43e0fd1a71df38d98966f962643013",
        "parameters_sha256": "987c5a27e51c9de9b127be0de6849871fb03b4d307daea814e28cfbf8754e496",
        "groups_sha256": "0f8ec15ed7e5cb95e3e39a842f84585c7e78326ad3a256de318177bd8d392fa7",
    },
    "05-wio-monthly-statement.json": {
        "nodes": 13, "edges": 10, "sticky": 2, "groups": 2,
        "node_ids_sha256": "077d15256aea7be46e42222e65ea5d55cd0203b5f2c49d7bfcb60a7650f00688",
        "connections_sha256": "0c229f1577dc41cae23482089751a8100b43e0fd1a71df38d98966f962643013",
        "parameters_sha256": "ec3f7e72ffe9c7599576d2df7ff78876ab99693201527ea645cd7d43d5617f86",
        "groups_sha256": "25b0c6e15bb8ea7f5ad4bdbaf46813319ab2c55876ce128fc70703fb5f4678bf",
    },
    "06-rak-monthly-statement.json": {
        "nodes": 3, "edges": 1, "sticky": 1, "groups": 0,
        "node_ids_sha256": "641f342dce57c84d7fe8a7f44645254e69beb0b4929a2aeb340cc2a616c380c4",
        "connections_sha256": "68149936edb392300084b7ed26b8bfb355d51ae7b8f289fb0a8a9c4fd0c604a8",
        "parameters_sha256": "78318a7e16de31394fb97413f9cc0fcefa564b30bc977ae4d922df5f6fbce65d",
        "groups_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    "07-sc-monthly-statement.json": {
        "nodes": 3, "edges": 1, "sticky": 1, "groups": 0,
        "node_ids_sha256": "1743e0dfdb36a7792a76d0ffc55aec64728c7a321cb8e1ff7e604c1ff003dd3b",
        "connections_sha256": "c491278e0f758fff53c4a8b02e92cd925f4e0cf762ab35fc7307832969d2714c",
        "parameters_sha256": "53b53ff9838ac93b49b4b7eda164ed6edfe3327b0f3d97d63c94e97df3191110",
        "groups_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    "08-sc-live-cashback.json": {
        "nodes": 3, "edges": 1, "sticky": 1, "groups": 0,
        "node_ids_sha256": "75dbf945741aeb3ea806a352a594bfd3af6a4f829ea98527dae0c85e5d94ec33",
        "connections_sha256": "7aee054de6ffe42194a96f02c24410ea11dc384c490706333b6649518ade56dd",
        "parameters_sha256": "b8a2348b6070980403857b743ff168f554c584a11b5950fce6162f7f0f90be1e",
        "groups_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    "09-ai-proposal.json": {
        "nodes": 35, "edges": 30, "sticky": 4, "groups": 4,
        "node_ids_sha256": "5adc8b6735156f9707654c224992b4222aaf57ce1e04aa75a0febf3b8fa367bf",
        "connections_sha256": "4ff52193b65ee9e9b3127e162d9237ea98098100e31d7ed51faa53d9d9b1f103",
        "parameters_sha256": "bbcd0a8a5a32798796372b5996600ca932b69eda97cb71db013d75c51193730a",
        "groups_sha256": "76109032e9b59e76b1b3e2b357b4359123e35e040d34f2ee82d486edf1f532a6",
    },
    "10-finance-operations-status.json": {
        "nodes": 25, "edges": 22, "sticky": 3, "groups": 4,
        "node_ids_sha256": "bea4e354aed50eabba5dbed46b94265a34f1a6424309c852e79fc977adb32dd3",
        "connections_sha256": "1f21c1b73354ea60791d63b068052f095984d42b9d2088dcdcbd4f379ebe6a26",
        "parameters_sha256": "12a7e59dd79991525472b1350300357236727befbb044ce4b65c8e064e4591b8",
        "groups_sha256": "9daf4bf7a5909b9916da9aefa6145ecd2ce605dd8067ea869e165290360eb377",
    },
    "11-interactive-artifact-handoff.json": {
        "nodes": 11, "edges": 8, "sticky": 2, "groups": 1,
        "node_ids_sha256": "c165d9d44593145c4119c297b516e4eb76116915da84351cdc3a06e726479363",
        "connections_sha256": "b2bca7e1601274e6e60c4e80bfdb152e6f7c1a57f0cd7278e3c57ddb23c394f8",
        "parameters_sha256": "242aea53a61ed21e553e5c007aceaaef498cdb6a143df85fd008eb733af83d8a",
        "groups_sha256": "247a17ee5762ad736b9e9f13bda28190a27f70f7e986b51fc782571c67cb28cf",
    },
    "12-outlook-message-sweep.json": {
        "nodes": 26, "edges": 22, "sticky": 3, "groups": 4,
        "node_ids_sha256": "42420dc7fdc792baabfb59ce7bc77591e74c4364bfb6a8195c999c3fee57c874",
        "connections_sha256": "686235393a8d7a9dc973b8059ce7d4fd3b4d8da82a11b3793be02c82431c3065",
        "parameters_sha256": "29201a29d7e0e2792691635bca954ab8db2294a93521bdc9f0cf69188d4b8754",
        "groups_sha256": "a44e32557326fbf1382a2408168b5493b812b3defe0e479bee4cdf37b4763e97",
    },
    "13-document-extraction-request.json": {
        "nodes": 9, "edges": 7, "sticky": 1, "groups": 1,
        "node_ids_sha256": "0b65661d8e39c99ce978fcf8f8ef7096a65fbf206782fdee3302fb09b887257b",
        "connections_sha256": "41e0c26cff4d0d117034577e1d8df5cc090190a3015e6f82a5a415e30d1ce19a",
        "parameters_sha256": "ed29c4c08a45e8310f39e06db6179b90dc43ef67a5cf7710d54c0e4c131dba73",
        "groups_sha256": "f796452bc5e2ecd93cef391080a2ca74c41d56c9ab85cc9d4ae70fbf0e59f4f8",
    },
    "14-local-pdf-extraction.json": {
        "nodes": 11, "edges": 8, "sticky": 2, "groups": 1,
        "node_ids_sha256": "70a377c99ac7bff4b607473ffa2b436955e7d06b7529dbd8ca7697ee19920851",
        "connections_sha256": "33a911a9a17f40329ee434f5335de58f019757622eddd205b27c3be6a9460104",
        "parameters_sha256": "4e7d37fa331fdde338472e8926e0105c2f9a6f1845c7a0fbf75274374f22bf30",
        "groups_sha256": "f5302f8b0dad29e0f1da463e0eca2324188d1d799b978e921c5350c12ec971da",
    },
    "15-finance-mcp-facade.json": {
        "nodes": 5, "edges": 3, "sticky": 1, "groups": 0,
        "node_ids_sha256": "b78e523c514f8bdf9d2e142ccb277a2204cac2223a2c9edbb3ce0e74e352926b",
        "connections_sha256": "e10d827f899ff6149a323e8d1db076459ea2f0110f80840bfdf7bfeb1d986ef4",
        "parameters_sha256": "f4a5d7c2cc55c346f0a2d82b060a8d443de52c918dfd5059d5adc0389a887615",
        "groups_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    "16-operations-error-handler.json": {
        "nodes": 15, "edges": 12, "sticky": 2, "groups": 2,
        "node_ids_sha256": "ae67b1605b238427e90882eb92c6c2059fff2bec9eea53369dd3fe5decc02148",
        "connections_sha256": "150e548b879a420abc5e02cc35e1d9cc21a5c5a8271b78be55a790571a201f13",
        "parameters_sha256": "9facaca99ca903a9ff5079209587136552564d0d187e99869ffd5cb25f21a9ce",
        "groups_sha256": "394ff0bec1ac1242da0653758158de02a335258d2b0af20cf8ff6d4c6c2824b1",
    },
    "17-actual-outbox-recovery.json": {
        "nodes": 4, "edges": 2, "sticky": 1, "groups": 1,
        "node_ids_sha256": "4824411842c66b10005061a9249189ea217cdedb1f325533b0795b1e269b9458",
        "connections_sha256": "9f430d2b6f8b229f105b447bbfc28cc2c283d7fcdb7c7d2907e090f367cd4713",
        "parameters_sha256": "4133be95c6d0f795388a4ef682cd8a9431706f7ef475571fb7306b6a95884a66",
        "groups_sha256": "4d1730ef2989dd5c4ab533d48fd4842c063dc6afe9a3404aa64c40278f6e8978",
    },
    "18-finance-writer-lease.json": {
        "nodes": 8, "edges": 8, "sticky": 1, "groups": 1,
        "node_ids_sha256": "e94e3e7ff71c9d164e1106f8fd0d51531724a9703388103fa780ebd7b6297301",
        "connections_sha256": "d3527a6da1b9fa920bb87205c341f888c4080e5c12d5ef4caf668506c73fc5d6",
        "parameters_sha256": "5746e70cc09d11cc66a4f38902d31fe57898a2605270256ce3e7fa0999dad3e3",
        "groups_sha256": "7fc72af75ed9e79cd041ca76520131daad0f1166c1b37c107d47e9ae775d6b7a",
    },
    "19-platform-data-table-bootstrap.json": {
        "nodes": 33, "edges": 28, "sticky": 4, "groups": 4,
        "node_ids_sha256": "d8557af41b6831b6afae29cbe0d1a966e5a4cdcae5e54e2386dc300ec9d801da",
        "connections_sha256": "2ac3a9f6cc3128bb7e0b0863a9da03a70ec7e90050e9493bc2298b7ebaa0ef35",
        "parameters_sha256": "11b1f9956aee7a8f29b2f66a5b64c6e89036fd7e51f13aae240596da3f131999",
        "groups_sha256": "b1eaca6a9e54bd14fb10fb8343f06701304bfc8ab7e944bcdf218baf3aa6a3eb",
    },
    "20-actual-outbox-apply.json": {
        "nodes": 33, "edges": 30, "sticky": 4, "groups": 4,
        "node_ids_sha256": "dc2a457ee47afc1668b93f5ff1c2e576c16559cd63d9cf0da03906c869cee4e0",
        "connections_sha256": "e49fbb079f9ba6f49db633d98a2d871a15b9a393296693a24ea118883e4eb1c4",
        "parameters_sha256": "4eb78203d8b8635074b63d6217ace3f35bf302c52840098ebf038e9b56b93f9a",
        "groups_sha256": "9eb816e129431247bad3b0b6acf54c1f800dd7535ace24a900764d50d3ca186c",
    },
    "21-subscription-agent-adapter.json": {
        "nodes": 9, "edges": 8, "sticky": 1, "groups": 1,
        "node_ids_sha256": "44424a413fe42001fed8cdcc4f2c9af6c47c75bd5b9936ede9676f889896b5cb",
        "connections_sha256": "dc9ef1e63829f6c2ef02d613ca6c9f4d15b7826ec808384e86612985655f646f",
        "parameters_sha256": "738c9c720190ca4e64419c6ad813a30e8d00903da8be86ca5406ec76121fa726",
        "groups_sha256": "e1990b4608b5f524f88b0190f3c1be4a625c9297f9610ad76ec8a181ad5fbf90",
    },
}


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
        self.assert_metric("corpus", "regular export count", 21, len(self.workflows))
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
        self.assert_metric("registry", "workflow row count", 21, len(rows))
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

        folders = self.folder_manifest["folders"]
        self.assert_metric("folder manifest", "folder count", 8, len(folders))
        folder_ids = [folder["id"] for folder in folders]
        folder_names = [folder["name"] for folder in folders]
        self.assertEqual(len(folder_ids), len(set(folder_ids)), "duplicate folder IDs")
        self.assertEqual(len(folder_names), len(set(folder_names)), "duplicate folder names")
        codes = [code for folder in folders for code in folder["workflow_codes"]]
        self.assertEqual(len(codes), len(set(codes)), "duplicate folder workflow codes")
        self.assertEqual(set(codes), set(registry_codes), "folder/registry workflow-code bijection drift")
        by_code = {
            code: folder
            for folder in folders
            for code in folder["workflow_codes"]
        }
        expected_tags = self.folder_manifest["tags"]
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

    def test_workflow_counts_and_fingerprints_are_deterministic(self) -> None:
        for filename, workflow in self.workflows.items():
            expected = CORPUS_SNAPSHOT[filename]
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
