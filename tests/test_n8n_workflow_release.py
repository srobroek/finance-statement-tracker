import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('release', ROOT / 'scripts/stage-n8n-workflow-release.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class RuntimeInputTests(unittest.TestCase):
    def test_workflow_change_reuses_runtime_but_parser_change_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.check_output(['git','-C',str(root),*args], text=True).strip()
            git('init','-q')
            git('config','user.name','Fixture')
            git('config','user.email','fixture@example.invalid')
            for relative in ('integrations/n8n/workflows/example.json','finance_tracker/parser.py','packages/n8n-nodes-finance/src/node.ts','services/pdf-utility/worker.py'):
                path=root/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('original')
            git('add','.'); git('commit','-qm','Initial')
            original=git('rev-parse','HEAD')
            fingerprints={role:M.git_tree_digest(root,original,paths) for role,paths in M.RUNTIME_INPUTS.items()}
            (root/'integrations/n8n/workflows/example.json').write_text('new graph')
            git('add','.'); git('commit','-qm','Workflow only')
            workflow=git('rev-parse','HEAD')
            self.assertEqual(fingerprints,{role:M.git_tree_digest(root,workflow,paths) for role,paths in M.RUNTIME_INPUTS.items()})
            (root/'finance_tracker/parser.py').write_text('new parser')
            git('add','.'); git('commit','-qm','Parser runtime')
            parser=git('rev-parse','HEAD')
            self.assertNotEqual(fingerprints['task_runners'],M.git_tree_digest(root,parser,M.RUNTIME_INPUTS['task_runners']))
            self.assertEqual(fingerprints['n8n'],M.git_tree_digest(root,parser,M.RUNTIME_INPUTS['n8n']))

    def test_stage_rejects_wrong_source_and_dirty_checkout_before_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError,'exact checked-out'):
                M.stage(ROOT,Path(temporary)/'release','0'*40)

    def test_stage_preserves_application_manifest_and_hashes_all_external_files(self):
        import hashlib
        import json
        with tempfile.TemporaryDirectory() as temporary:
            destination=Path(temporary)/'release'
            commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
            release=M.stage(ROOT,destination,commit)
            manifest=json.loads((destination/release['manifest']).read_text())
            self.assertEqual(manifest['application']['source_commit'],commit)
            self.assertEqual(manifest['application']['id'],release['application_id'])
            self.assertTrue(manifest['workflows']['inactive'])
            self.assertFalse(manifest['workflows']['published'])
            for path,expected in release['files'].items():
                self.assertEqual(hashlib.sha256((destination/path).read_bytes()).hexdigest(),expected)
