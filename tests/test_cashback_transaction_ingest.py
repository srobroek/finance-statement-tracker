from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.cashback_events import CashbackEventStore, IngestCursorConflict


class TransactionIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'events.sqlite3'
        self.store = CashbackEventStore(self.path)
        self.fields = dict(source='RAKBANK_CARD_TRANSACTION', completed_at='2026-09-05T12:00:00+00:00', cursor='2026-09-05T12:00:00+00:00')
        self.event = json.loads((Path(__file__).parent / 'fixtures/cashback-event.sample.json').read_text())
        self.event['source_event_id'] = 'sample:0'

    def child(self, event=None, **fields):
        return self.store.ingest_transaction({**self.fields, **fields, 'event': event or self.event})['service_receipt']

    def aggregate(self, children, accepted=None, **fields):
        return self.store.combine_transaction_receipts({
            **self.fields, 'scanned_count': len(children),
            'accepted_count': len(children) if accepted is None else accepted,
            'ignored_count': 0, 'review_count': 0,
            'message_dispositions': [{'message_id': child['event_ids'][0][:-2], 'status': 'ACCEPTED', 'source_event_id': child['event_ids'][0]} for child in children],
            'receipts': [{k: child[k] for k in ('receipt_id', 'receipt_sha256')} for child in children], **fields,
        })['service_receipt']

    def commit(self, receipt):
        return self.store.record_ingest_success({**self.fields, 'scanned_count': receipt['scanned_count'], 'accepted_count': receipt['accepted_count'], 'service_receipt': receipt})

    def test_single_transaction_replay_restart_then_final_commit(self):
        first = self.child()
        self.assertEqual(first, self.child())
        self.store = CashbackEventStore(self.path)
        self.assertEqual(first, self.child())
        self.assertIsNone(self.store.ingest_state(self.fields['source'])['cursor'])
        receipt = self.aggregate([first])
        self.assertEqual(receipt['event_ids'], [self.event['source_event_id']])
        self.assertFalse(self.commit(receipt)['idempotent_replay'])
        self.assertTrue(self.commit(receipt)['idempotent_replay'])

    def test_child_cannot_commit_cursor(self):
        with self.assertRaisesRegex(IngestCursorConflict, 'cannot commit'):
            self.commit(self.child())

    def test_partial_batch_missing_receipt_never_commits(self):
        first = self.child()
        with self.assertRaises(IngestCursorConflict):
            self.aggregate([first], accepted=2, scanned_count=2)
        self.assertIsNone(self.store.ingest_state(self.fields['source'])['cursor'])
        second = self.child({**self.event, 'source_event_id': 'second:0', 'amount_aed': '246.00'})
        final = self.aggregate([first, second])
        self.assertEqual(final['accepted_count'], 2)
        self.commit(final)

    def test_unknown_tampered_duplicate_and_wrong_window_receipts_fail(self):
        first = self.child()
        cases = [([first, first], {}), ([{**first, 'receipt_sha256': '0' * 64}], {}),
                 ([{**first, 'receipt_id': 'missing'}], {}),
                 ([first], {'source': 'other'}),
                 ([first], {'completed_at': '2026-09-06T12:00:00+00:00'}),
                 ([first], {'cursor': 'later'})]
        for children, fields in cases:
            with self.subTest(fields=fields, children=len(children)), self.assertRaises(ValueError):
                self.aggregate(children, **fields)

    def test_zero_event_scan_and_skipped_dispositions(self):
        receipt = self.aggregate([], scanned_count=2, ignored_count=1, review_count=1, message_dispositions=[{'message_id':'ignored', 'status':'IGNORED', 'reason':'outside source contract'}, {'message_id':'review', 'status':'REVIEW', 'reason':'missing exchange rate'}])
        self.assertEqual(receipt['event_ids'], [])
        self.commit(receipt)

    def test_conflicting_event_or_duplicate_identity_has_no_child_receipt(self):
        self.child()
        with self.assertRaises(ValueError):
            self.child({**self.event, 'amount_aed': '1.00'})
        with self.assertRaises(IngestCursorConflict):
            self.child({**self.event, 'source_event_id': 'different-source-same-transaction'})

    def test_batch_and_extra_envelope_fields_rejected(self):
        for event in ([self.event], None):
            with self.assertRaises(ValueError):
                self.store.ingest_transaction({**self.fields, 'event': event})
        with self.assertRaises(ValueError):
            self.store.ingest_transaction({**self.fields, 'event': self.event, 'messages': []})

    def test_zero_scanned_heartbeat_advances_success_time_without_events(self):
        receipt = self.aggregate([])
        self.commit(receipt)
        state = self.store.ingest_state(self.fields['source'])
        self.assertEqual(state['last_success_at'], self.fields['completed_at'])
        self.assertEqual(state['accepted_count'], 0)

    def test_dispositions_cannot_omit_duplicate_or_substitute_messages(self):
        first = self.child()
        for dispositions in ([], [{'message_id':'another', 'status':'ACCEPTED', 'source_event_id':'another:0'}],
                             [{'message_id':'sample', 'status':'REVIEW', 'reason':'unknown'}],
                             [{'message_id':'sample', 'status':[]} ]):
            with self.subTest(dispositions=dispositions), self.assertRaises(ValueError):
                self.aggregate([first], message_dispositions=dispositions)

    def test_changed_persisted_transaction_invalidates_old_child(self):
        first = self.child()
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cashback_events SET bucket_code = 'CHANGED'")
        with self.assertRaisesRegex(IngestCursorConflict, 'missing or changed'):
            self.aggregate([first])

    def test_invalid_window_does_not_persist_transaction(self):
        with self.assertRaises(ValueError):
            self.child(cursor='2026-09-06T12:00:00+00:00')
        self.assertEqual(self.child()['receipt_kind'], 'TRANSACTION')

    def test_http_single_transaction_receipt_auth_limit_and_heartbeat(self):
        import os
        import socket
        import subprocess
        import sys
        import time
        import urllib.error
        import urllib.request
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        root = Path(__file__).resolve().parent.parent
        env = {**os.environ, 'CASHBACK_HOST': '127.0.0.1', 'CASHBACK_PORT': str(port),
               'CASHBACK_DB_PATH': str(Path(self.temp.name) / 'http.sqlite3'),
               'CASHBACK_DASHBOARD_PATH': str(Path(self.temp.name) / 'dashboard.json'),
               'CASHBACK_INGEST_TOKEN': 'test-token', 'CASHBACK_REFRESH_SECONDS': '0', 'CASHBACK_PUBLIC_URL': f'http://127.0.0.1:{port}'}
        process = subprocess.Popen([sys.executable, str(root / 'apps/cashback-control/server.py')], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(process.wait, 5)
        self.addCleanup(process.terminate)
        base = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + '/api/health', timeout=.2):
                    break
            except OSError:
                time.sleep(.05)
        else:
            self.fail('Server did not become ready')

        def post(path, body, token='test-token'):
            request = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={'Content-Type':'application/json', 'Authorization':'Bearer ' + token})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)

        for path in ('/api/ingest/transaction', '/api/ingest/receipt'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                post(path, {}, token='wrong')
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            post('/api/ingest/transaction', {'padding':'x' * 1_000_001})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        child = post('/api/ingest/transaction', {**self.fields, 'event': self.event})
        self.assertEqual(child['persistence']['inserted'], 1)
        self.assertFalse(child['cursor_committed'])
        receipt = child['service_receipt']
        batch = {**self.fields, 'scanned_count':1, 'accepted_count':1, 'ignored_count':0, 'review_count':0,
                 'receipts':[{key:receipt[key] for key in ('receipt_id','receipt_sha256')}],
                 'message_dispositions':[{'message_id':'sample','status':'ACCEPTED','source_event_id':'sample:0'}]}
        aggregate = post('/api/ingest/receipt', batch)['service_receipt']
        final = post('/api/ingest-runs', {**self.fields, 'scanned_count':1, 'accepted_count':1, 'service_receipt':aggregate})
        self.assertEqual(final['ingest']['accepted_count'], 1)
        next_fields = {**self.fields, 'completed_at':'2026-09-06T12:00:00+00:00', 'cursor':'2026-09-06T12:00:00+00:00'}
        empty = post('/api/ingest/receipt', {**next_fields,'scanned_count':0,'accepted_count':0,'ignored_count':0,'review_count':0,'receipts':[],'message_dispositions':[]})['service_receipt']
        heartbeat = post('/api/ingest-runs', {**next_fields,'scanned_count':0,'accepted_count':0,'service_receipt':empty})
        self.assertEqual(heartbeat['ingest']['cursor_version'], 2)
        self.assertEqual(heartbeat['ingest']['last_success_at'], next_fields['completed_at'])
