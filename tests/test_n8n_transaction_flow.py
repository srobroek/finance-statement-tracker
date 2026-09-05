import json
from pathlib import Path
import unittest

from finance_tracker.n8n_notifications import normalize_archived_mailbox
import tests.test_mail_ingestion as mail

ROOT = Path(__file__).resolve().parents[1]


class TransactionFlowTests(unittest.TestCase):
    def envelope(self, messages):
        return {"source": "outlook:rakbank", "source_code": "RAKBANK_CARD_TRANSACTION", "run_id": "fixture",
                "completed_at": "2026-08-18T00:00:00Z", "cursor": "2026-08-18T00:00:00Z",
                "window_start": "2026-08-16T00:00:00Z", "matched_count": len(messages), "messages": messages}

    def test_canonical_normalization_emits_compact_events_and_exhaustive_dispositions(self):
        message = json.loads((ROOT / 'tests/fixtures/rakbank-card-transaction-unknown-channel.json').read_text())
        message['body'] = {'content': message['bodyPreview'] + (' unused email markup' * 10000)}
        ignored = {**message, 'id': 'ignored', 'subject': 'Unrelated email'}
        review = {**message, 'id': 'review', 'bodyPreview': 'You spent', 'body': {'content': 'Incomplete notification'}}
        result = normalize_archived_mailbox(self.envelope([message, ignored, review]))
        self.assertEqual((result['scanned_count'], result['accepted_count'], result['ignored_count'], result['review_count']), (3, 1, 1, 1))
        self.assertEqual(result['events'][0]['source_event_id'], message['id'] + ':0')
        self.assertLess(len(json.dumps(result)), 10000)
        self.assertNotIn('unused email markup', json.dumps(result))
        self.assertEqual(normalize_archived_mailbox(self.envelope([message, ignored, review])), result)
        with self.assertRaisesRegex(ValueError, 'IDENTITIES_INVALID'):
            normalize_archived_mailbox(self.envelope([message, message]))

    def test_exported_python_and_single_event_graph_verify_complete_scan_and_empty_heartbeat(self):
        workflow = json.loads((ROOT / 'integrations/n8n/workflows/02-rakbank-live-cashback.json').read_text())
        nodes = {node['name']: node for node in workflow['nodes']}
        code = nodes['Normalize Archived Notifications']['parameters']['pythonCode']
        self.assertEqual(nodes['Normalize Archived Notifications']['parameters']['language'], 'pythonNative')
        namespace = {}
        exec('def normalize(_items):\n' + '\n'.join('    ' + line for line in code.splitlines()), namespace)
        runner = mail.MailIngestionTests()
        def execute(name, value=None, rows=None, refs=None):
            result = runner.execute_code_node(workflow, name, json_value=value, input_items=rows, refs=refs)
            self.assertTrue(result['ok'], result)
            return result['output'][0]['json']
        message = json.loads((ROOT / 'tests/fixtures/rakbank-card-transaction-unknown-channel.json').read_text())
        for messages in ([], [message], [{**message, 'id': str(i)} for i in range(3)]):
            with self.subTest(count=len(messages)):
                batch = namespace['normalize']([{'json': self.envelope(messages)}])[0]['json']
                split = runner.execute_code_node(workflow, 'Split Compact Cashback Transactions', json_value=batch)
                self.assertTrue(split['ok'], split)
                children = []
                for index, item in enumerate(split['output']):
                    request = item['json']
                    self.assertNotIn('messages', request)
                    if request.get('empty_scan'):
                        children.append(request)
                        continue
                    receipt = {key: request[key] for key in ('source', 'completed_at', 'cursor')}
                    receipt.update(receipt_kind='TRANSACTION', receipt_id=str(index), receipt_sha256='a'*64,
                                   scanned_count=1, accepted_count=1, event_ids=[request['event']['source_event_id']])
                    response = {'cursor_candidate': request['cursor'], 'cursor_committed': False, 'service_receipt': receipt}
                    children.append(execute('Verify Individual Transaction Receipt', response,
                                            refs={'Ingest One Transaction at a Time': request}))
                manifest = execute('Assemble Complete Scan Receipt', rows=children,
                                   refs={'Normalize Archived Notifications': batch})
                self.assertNotIn('events', manifest)
                self.assertEqual(len(manifest['receipts']), len(messages))
                receipt = {key: manifest[key] for key in ('source', 'completed_at', 'cursor', 'scanned_count', 'accepted_count')}
                receipt.update(receipt_id='aggregate', receipt_sha256='b'*64,
                               scan_dispositions={key: manifest[key] for key in ('ignored_count', 'review_count', 'message_dispositions')})
                final = execute('Verify Service Receipt Before Cursor', {'cursor_committed': False,
                    'cursor_candidate': manifest['cursor'], 'service_receipt': receipt},
                    refs={'Assemble Complete Scan Receipt': manifest})
                self.assertEqual(final['scanned_count'], len(messages))
                self.assertEqual(final['cursor'], self.envelope([])['cursor'])
                if messages:
                    missing = runner.execute_code_node(workflow, 'Assemble Complete Scan Receipt', input_items=children[:-1],
                        refs={'Normalize Archived Notifications': batch})
                    self.assertFalse(missing['ok'])
        self.assertEqual(nodes['Upsert One Cashback Transaction']['parameters']['url'], 'http://cashback:5010/api/ingest/transaction')
        self.assertNotIn('/api/outlook/messages', json.dumps(workflow))
        self.assertEqual(nodes['Ingest One Transaction at a Time']['parameters']['batchSize'], 1)
