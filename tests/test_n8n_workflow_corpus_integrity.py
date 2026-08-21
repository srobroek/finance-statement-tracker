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
CORPUS_SNAPSHOT = {'01-outlook-finance-acquisition.json': {'nodes': 48,
                                         'edges': 46,
                                         'sticky': 6,
                                         'groups': 7,
                                         'node_ids_sha256': 'e5c57fa1afe9759cdfe1c34b39d5c563a9c673091287995cc37c5824cbbd05c7',
                                         'connections_sha256': '7bf20be092faa1e9652f4dcc8c2ed4c462fc3c262d308c0e846a81ba9ba2c58d',
                                         'parameters_sha256': '872536290a3701ea88f99b9910b823c13f3599fb42de24d28bbe265a701ad0d0',
                                         'groups_sha256': '4664caa773a2aab6e3bbcef3039ff003c2db47d29fb990a56e1ceb867b42f43c'},
 '02-rakbank-live-cashback.json': {'nodes': 14,
                                   'edges': 11,
                                   'sticky': 2,
                                   'groups': 2,
                                   'node_ids_sha256': 'e3048a521c89a80e1ba89af3ecdce88e6bc02ad35563b8250171c7db47a495be',
                                   'connections_sha256': '265a9c534d53ffa987bcae7c6ac00ca20bab617b485e1d008630550565fd703e',
                                   'parameters_sha256': '0f78ab575d5527d1a12d6265ad8f5b4f90fe921e9a40c3ad24fc5f540ba4d914',
                                   'groups_sha256': 'bc8a2c2b8d599feca94f286e915e100d51cd23bb37cc44f5be5d9605152b0715'},
 '03-shared-statement-pipeline.json': {'nodes': 54,
                                       'edges': 52,
                                       'sticky': 5,
                                       'groups': 5,
                                       'node_ids_sha256': '0b128ebc84fd8773f4176ff54570b42a71abb1c61f54c3e0b79115b558a479b2',
                                       'connections_sha256': 'd1ae93d27fe550ae7d1ce8db0f03b1a202457c294b4348e3e4fecee6206f5a2d',
                                       'parameters_sha256': '47227ba51f3bcf80274010d6c1f9ec48ce55286e1fb7f5250a3497f00b14809f',
                                       'groups_sha256': '82b1b5b302e05a98f640377b1492843ac6f6408a40de0a58f93b0abe7763879a'},
 '04-ei-monthly-statement.json': {'nodes': 4,
                                  'edges': 2,
                                  'sticky': 1,
                                  'groups': 1,
                                  'node_ids_sha256': '9eaf7f0a7591de3b99a43615cc2e309e0e52675fe2dfada0b8909b8a5d912d02',
                                  'connections_sha256': 'ecb0981ee4ee51c0412063b6578bbe9d34a12486d320660e85927cdfbcd94fff',
                                  'parameters_sha256': 'd49da43f4cc3e5bf47809c76555fd9e10841ae75a817f6d1ac616db04d8261dc',
                                  'groups_sha256': '350935f05b4ebfa132cbc8c34ac64198fa5cb0d8892f409e044f8fc48953e1b3'},
 '05-wio-monthly-statement.json': {'nodes': 4,
                                   'edges': 2,
                                   'sticky': 1,
                                   'groups': 1,
                                   'node_ids_sha256': '7f1e8bbc395740f90a6909ab52517fb2c7e8f87cc481320f1219a488c14ff574',
                                   'connections_sha256': 'ecb0981ee4ee51c0412063b6578bbe9d34a12486d320660e85927cdfbcd94fff',
                                   'parameters_sha256': '7af3177013efdbb824ba125329e816554177a53dc86f1f60132ef568688863ae',
                                   'groups_sha256': '55a439d01325d7f0005ae93ebc9583397ae3e7cb9cd783d5980de1b3417b9bf6'},
 '22-shared-monthly-statement-cycle.json': {'nodes': 17,
                                            'edges': 15,
                                            'sticky': 1,
                                            'groups': 0,
                                            'node_ids_sha256': '1a77e53c7314de832f131a245e2945712087be86e90561a6a6defb3eb67ba20d',
                                            'connections_sha256': 'c64db46a4ea0c005bfc3ae8f094b4bb992ee507b8e73152d39558aaeb047b6e2',
                                            'parameters_sha256': '8a61c8e7a7e29a23a6459517067af3dfe41ae19db38e7d3b7a89a6b482a8516a',
                                            'groups_sha256': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'},
 '06-rak-monthly-statement.json': {'nodes': 3,
                                   'edges': 1,
                                   'sticky': 1,
                                   'groups': 0,
                                   'node_ids_sha256': '641f342dce57c84d7fe8a7f44645254e69beb0b4929a2aeb340cc2a616c380c4',
                                   'connections_sha256': '68149936edb392300084b7ed26b8bfb355d51ae7b8f289fb0a8a9c4fd0c604a8',
                                   'parameters_sha256': '78318a7e16de31394fb97413f9cc0fcefa564b30bc977ae4d922df5f6fbce65d',
                                   'groups_sha256': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'},
 '07-sc-monthly-statement.json': {'nodes': 3,
                                  'edges': 1,
                                  'sticky': 1,
                                  'groups': 0,
                                  'node_ids_sha256': '1743e0dfdb36a7792a76d0ffc55aec64728c7a321cb8e1ff7e604c1ff003dd3b',
                                  'connections_sha256': 'c491278e0f758fff53c4a8b02e92cd925f4e0cf762ab35fc7307832969d2714c',
                                  'parameters_sha256': '53b53ff9838ac93b49b4b7eda164ed6edfe3327b0f3d97d63c94e97df3191110',
                                  'groups_sha256': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'},
 '08-sc-live-cashback.json': {'nodes': 3,
                              'edges': 1,
                              'sticky': 1,
                              'groups': 0,
                              'node_ids_sha256': '75dbf945741aeb3ea806a352a594bfd3af6a4f829ea98527dae0c85e5d94ec33',
                              'connections_sha256': '7aee054de6ffe42194a96f02c24410ea11dc384c490706333b6649518ade56dd',
                              'parameters_sha256': 'b8a2348b6070980403857b743ff168f554c584a11b5950fce6162f7f0f90be1e',
                              'groups_sha256': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'},
 '09-ai-proposal.json': {'nodes': 35,
                         'edges': 30,
                         'sticky': 4,
                         'groups': 4,
                         'node_ids_sha256': '5adc8b6735156f9707654c224992b4222aaf57ce1e04aa75a0febf3b8fa367bf',
                         'connections_sha256': '4ff52193b65ee9e9b3127e162d9237ea98098100e31d7ed51faa53d9d9b1f103',
                         'parameters_sha256': '169894e6b9a3abecedc616eca54739b4bff77328bfa94d37a03358a845bd5e5b',
                         'groups_sha256': '76109032e9b59e76b1b3e2b357b4359123e35e040d34f2ee82d486edf1f532a6'},
 '10-finance-operations-status.json': {'nodes': 25,
                                       'edges': 22,
                                       'sticky': 3,
                                       'groups': 4,
                                       'node_ids_sha256': 'bea4e354aed50eabba5dbed46b94265a34f1a6424309c852e79fc977adb32dd3',
                                       'connections_sha256': '1f21c1b73354ea60791d63b068052f095984d42b9d2088dcdcbd4f379ebe6a26',
                                       'parameters_sha256': 'f8ff7ee091ef249585a08a90a1bb6eb702ce7a2a9046bfe967d60ceef135eef2',
                                       'groups_sha256': '9daf4bf7a5909b9916da9aefa6145ecd2ce605dd8067ea869e165290360eb377'},
 '11-interactive-artifact-handoff.json': {'nodes': 25,
                                          'edges': 23,
                                          'sticky': 3,
                                          'groups': 3,
                                          'node_ids_sha256': 'ae23deba18045dc9118bd8840328965d7a01c82f9a1d6757b07956036bdc600a',
                                          'connections_sha256': '8c1bf63b034e6f0989f80e92d7191549f0294b8590f403abe914e795465c97d6',
                                          'parameters_sha256': '0091d8cb19b958bf31fdf17f1e23ec560bf4909fbb5beb8c29380a27b165169d',
                                          'groups_sha256': '250d8ca38ae228681ae3d3f0601a135bc4437452f9904ce74c46d73a36268007'},
 '12-outlook-message-sweep.json': {'nodes': 71,
                                   'edges': 69,
                                   'sticky': 8,
                                   'groups': 12,
                                   'node_ids_sha256': 'a43e50a0be11bb568a4288d8f93d5a62139c3f1ab823f4b1a02cd2293fae389e',
                                   'connections_sha256': 'bf9b2870be50c311da905ecb88f08f3bec9235d026cc782bda4431636b0be693',
                                   'parameters_sha256': 'c098ecd365b7dd46648f7416d61565927b190db28bd18b5f1e7f4d77ab3ce10e',
                                   'groups_sha256': '6050b96d2ffd9af7ed306c40eb715b9b953649850734ae900e1be4c1b379730d'},
 '13-document-extraction-request.json': {'nodes': 9,
                                         'edges': 7,
                                         'sticky': 1,
                                         'groups': 1,
                                         'node_ids_sha256': '0b65661d8e39c99ce978fcf8f8ef7096a65fbf206782fdee3302fb09b887257b',
                                         'connections_sha256': '41e0c26cff4d0d117034577e1d8df5cc090190a3015e6f82a5a415e30d1ce19a',
                                         'parameters_sha256': 'ed29c4c08a45e8310f39e06db6179b90dc43ef67a5cf7710d54c0e4c131dba73',
                                         'groups_sha256': 'f796452bc5e2ecd93cef391080a2ca74c41d56c9ab85cc9d4ae70fbf0e59f4f8'},
 '14-local-pdf-extraction.json': {'nodes': 11,
                                  'edges': 8,
                                  'sticky': 2,
                                  'groups': 1,
                                  'node_ids_sha256': '70a377c99ac7bff4b607473ffa2b436955e7d06b7529dbd8ca7697ee19920851',
                                  'connections_sha256': '33a911a9a17f40329ee434f5335de58f019757622eddd205b27c3be6a9460104',
                                  'parameters_sha256': '694448c6f9bc0a0b2ca689ce11d2dcfccfd1569d2af627b1325f6f2d913629b3',
                                  'groups_sha256': 'f5302f8b0dad29e0f1da463e0eca2324188d1d799b978e921c5350c12ec971da'},
 '15-finance-mcp-facade.json': {'nodes': 5,
                                'edges': 3,
                                'sticky': 1,
                                'groups': 0,
                                'node_ids_sha256': 'fa000969bfd8185cc920099d7fd8b9b3969d948b4495238ea14257e7424c1602',
                                'connections_sha256': '7c9d5fb02c48b45838ebb3ba1defabf9a133c0a640b98c9d0d122595404059ae',
                                'parameters_sha256': 'a91a264971a3f9c51d0b108e8bcf825af7e36d66c9c52efc880ec9f8990aa729',
                                'groups_sha256': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'},
 '16-operations-error-handler.json': {'nodes': 15,
                                      'edges': 12,
                                      'sticky': 2,
                                      'groups': 2,
                                      'node_ids_sha256': 'ae67b1605b238427e90882eb92c6c2059fff2bec9eea53369dd3fe5decc02148',
                                      'connections_sha256': '150e548b879a420abc5e02cc35e1d9cc21a5c5a8271b78be55a790571a201f13',
                                      'parameters_sha256': '9facaca99ca903a9ff5079209587136552564d0d187e99869ffd5cb25f21a9ce',
                                      'groups_sha256': '394ff0bec1ac1242da0653758158de02a335258d2b0af20cf8ff6d4c6c2824b1'},
 '17-actual-outbox-recovery.json': {'nodes': 4,
                                    'edges': 2,
                                    'sticky': 1,
                                    'groups': 1,
                                    'node_ids_sha256': '4824411842c66b10005061a9249189ea217cdedb1f325533b0795b1e269b9458',
                                    'connections_sha256': '9f430d2b6f8b229f105b447bbfc28cc2c283d7fcdb7c7d2907e090f367cd4713',
                                    'parameters_sha256': '4133be95c6d0f795388a4ef682cd8a9431706f7ef475571fb7306b6a95884a66',
                                    'groups_sha256': '4d1730ef2989dd5c4ab533d48fd4842c063dc6afe9a3404aa64c40278f6e8978'},
 '18-finance-writer-lease.json': {'nodes': 8,
                                  'edges': 8,
                                  'sticky': 1,
                                  'groups': 1,
                                  'node_ids_sha256': 'e94e3e7ff71c9d164e1106f8fd0d51531724a9703388103fa780ebd7b6297301',
                                  'connections_sha256': 'd3527a6da1b9fa920bb87205c341f888c4080e5c12d5ef4caf668506c73fc5d6',
                                  'parameters_sha256': '5746e70cc09d11cc66a4f38902d31fe57898a2605270256ce3e7fa0999dad3e3',
                                  'groups_sha256': '7fc72af75ed9e79cd041ca76520131daad0f1166c1b37c107d47e9ae775d6b7a'},
 '19-platform-data-table-bootstrap.json': {'nodes': 33,
                                           'edges': 28,
                                           'sticky': 4,
                                           'groups': 4,
                                           'node_ids_sha256': 'd8557af41b6831b6afae29cbe0d1a966e5a4cdcae5e54e2386dc300ec9d801da',
                                           'connections_sha256': '2ac3a9f6cc3128bb7e0b0863a9da03a70ec7e90050e9493bc2298b7ebaa0ef35',
                                           'parameters_sha256': '17013758da47d436c3d42d3e64fab3cb0ee01583d48f808df033817aa277fdfa',
                                           'groups_sha256': 'b1eaca6a9e54bd14fb10fb8343f06701304bfc8ab7e944bcdf218baf3aa6a3eb'},
 '20-actual-outbox-apply.json': {'nodes': 39,
                                 'edges': 38,
                                 'sticky': 4,
                                 'groups': 4,
                                 'node_ids_sha256': 'f04ffe0e29c0dac03480a96ee7c7135c0fa85a1219026bd336e37bb3362957e2',
                                 'connections_sha256': '5ab0846f5f7898b587b26fc5e59d7b4fcd77c35988e843326730899c3b1515c8',
                                 'parameters_sha256': '9d16b0a448f04f10dbdd21e34bbfb16fb2a4b85ae063608ec0e1ed83354a0ef8',
                                 'groups_sha256': '9eb816e129431247bad3b0b6acf54c1f800dd7535ace24a900764d50d3ca186c'},
 '21-subscription-agent-adapter.json': {'nodes': 9,
                                        'edges': 8,
                                        'sticky': 1,
                                        'groups': 1,
                                        'node_ids_sha256': '44424a413fe42001fed8cdcc4f2c9af6c47c75bd5b9936ede9676f889896b5cb',
                                        'connections_sha256': 'dc9ef1e63829f6c2ef02d613ca6c9f4d15b7826ec808384e86612985655f646f',
                                        'parameters_sha256': '482366a87719fa4317ffb930e8ef1d42180d1be85d0a75d8c4ae1343be7244ed',
                                        'groups_sha256': 'e1990b4608b5f524f88b0190f3c1be4a625c9297f9610ad76ec8a181ad5fbf90'}}


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
        self.assert_metric("corpus", "regular export count", 22, len(self.workflows))
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
        self.assert_metric("registry", "workflow row count", 22, len(rows))
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

        folder_by_code = {
            code: folder
            for folder in self.folder_manifest["folders"]
            for code in folder["workflow_codes"]
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
        self.assert_metric("folder manifest", "folder count", 8, len(folders))
        folder_ids = [folder["id"] for folder in folders]
        folder_names = [folder["name"] for folder in folders]
        self.assertEqual(len(folder_ids), len(set(folder_ids)), "duplicate folder IDs")
        self.assertEqual(len(folder_names), len(set(folder_names)), "duplicate folder names")
        codes = [code for folder in folders for code in folder["workflow_codes"]]
        self.assertEqual(len(codes), len(set(codes)), "duplicate folder workflow codes")
        self.assertEqual(set(codes), set(registry_codes), "folder/registry workflow-code bijection drift")
        by_code = folder_by_code
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
