from __future__ import annotations
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from finance_tracker.cashback import load_program_configuration
from finance_tracker.cashback_events import CashbackEventStore, prepare_statement_reconciliation
from test_cashback_events import actual_receipt, actual_receipt_digest

ROOT = Path(__file__).resolve().parents[1]

class StatementCashbackContractTests(unittest.TestCase):
    def build(self, rows):
        workflow = json.loads((ROOT / 'integrations/n8n/workflows/03-shared-statement-pipeline.json').read_text())
        code = next(n['parameters']['jsCode'] for n in workflow['nodes'] if n['name']=='Build Cashback Reconciliation Request')
        refs = {
            'Validate Statement Reconciliation and IDs': {'transactions': rows},
            'Verify Archive and Execution Context': {'card_code':'EI_AMAZON'},
            'Build Canonical Delta Artifact': {'manifest':{'period_start':'2026-07-01','period_end':'2026-07-31'}},
        }
        draft={'cashback_finalization':{'statement_reference':'fixture-july','statement_sha256':'a'*64}}
        script="const d=JSON.parse(process.argv[1]);const result=new Function('$json','$',d.code)(d.draft,n=>({first:()=>({json:d.refs[n]})}));process.stdout.write(JSON.stringify(result[0].json.cashback_reconcile));"
        result=subprocess.run(['node','-e',script,json.dumps({'code':code,'draft':draft,'refs':refs})],capture_output=True,text=True,check=True)
        return json.loads(result.stdout)

    def test_foreign_source_keeps_settlement_amount_bucket_and_original_facts(self):
        request=self.build([{'transaction_id':'foreign-1','transaction_type':'PURCHASE','transaction_date':'2026-07-10','amount_aed':'36.70','amount_original':'10.00','currency_original':'USD','exchange_rate':'3.670','description':'AMAZON.COM','category':'Online Shopping','reward_bucket':'EI_AMAZON','channel':'ONLINE','tags':['online','amazon']}])
        payload=prepare_statement_reconciliation(request,load_program_configuration(as_of=date(2026,7,31)))
        row=payload['transactions'][0]
        self.assertEqual((row['amount_aed'],row['currency'],row['purchase_type'],row['bucket_code']),('36.70','AED','AMAZON','EI_AMAZON'))
        with tempfile.TemporaryDirectory() as directory:
            store=CashbackEventStore(Path(directory)/'events.sqlite3')
            first=store.reconcile_statement(payload)
            replay=store.reconcile_statement(payload)
            self.assertEqual(first['statement_only'],1)
            self.assertTrue(replay['idempotent_replay'])
            import sqlite3
            with sqlite3.connect(store.path) as connection:
                stored=connection.execute('SELECT amount_aed_minor,currency,bucket_code,decision_trace_json FROM cashback_events').fetchone()
            self.assertEqual(stored[:3],(3670,'AED','EI_AMAZON'))
            trace=json.loads(stored[3])[-1]
            self.assertEqual((trace['original_currency'],trace['original_amount']),('USD','10.00'))

    def test_payments_only_statement_reconciles_and_closes_without_fake_purchase(self):
        request=self.build([{'transaction_id':'payment-1','transaction_type':'PAYMENT','transaction_date':'2026-07-10','amount_aed':'100.00','description':'PAYMENT RECEIVED'}])
        self.assertEqual(request['transactions'],[])
        with tempfile.TemporaryDirectory() as directory:
            store=CashbackEventStore(Path(directory)/'events.sqlite3')
            result=store.reconcile_statement(prepare_statement_reconciliation(request,load_program_configuration(as_of=date(2026,7,31))))
            self.assertEqual((result['matched'],result['statement_only'],result['notification_only']),(0,0,0))
            receipt=actual_receipt('fixture-july','2026-07-01','2026-07-31')
            receipt.update(expected_count=1,observed_count=1,expected_amount_sum_minor=10000,observed_amount_sum_minor=10000,expected_account_balance=0,observed_account_balance=0)
            finalized=store.finalize_period({'statement_reference':'fixture-july','statement_sha256':'a'*64,'statement_evidence_reference':'onedrive:fixture','statement_document_url':'onedrive-item:fixture','actual_import_receipt':receipt,'actual_import_receipt_sha256':actual_receipt_digest(receipt)})
            self.assertEqual(finalized['status'],'FINALIZED')

    def test_cross_month_posting_matches_original_notification_and_counts_only_in_posted_month(self):
        row={'transaction_id':'late-post','transaction_type':'PURCHASE','transaction_date':'2026-06-30','post_date':'2026-07-01','amount_aed':'36.70','description':'AMAZON.COM','reward_bucket':'EI_AMAZON','channel':'ONLINE'}
        request=self.build([row])
        self.assertEqual(request['transactions'][0]['post_date'],'2026-07-01')
        notification={'source_event_id':'mail:late','occurred_at':'2026-06-30T10:00:00+04:00','card_code':'EI_AMAZON','amount_aed':'36.70','currency':'AED','purchase_type':'AMAZON','channel':'ONLINE','merchant':'AMAZON.COM','bucket_code':'EI_AMAZON','source':'outlook'}
        unrelated={**notification,'source_event_id':'mail:unrelated','merchant':'OTHER','amount_aed':'12.00'}
        with tempfile.TemporaryDirectory() as directory:
            store=CashbackEventStore(Path(directory)/'events.sqlite3')
            store.upsert([notification,unrelated])
            # The prior monthly close may already have marked the delayed
            # notification as a variance; the later posted statement resolves it.
            import sqlite3
            with sqlite3.connect(store.path) as connection:
                connection.execute("UPDATE cashback_events SET status='IGNORED', reconciliation_status='VARIANCE' WHERE source_event_id='mail:late'")
            payload=prepare_statement_reconciliation(request,load_program_configuration(as_of=date(2026,7,31)))
            first=store.reconcile_statement(payload)
            self.assertEqual((first['matched'],first['statement_only'],first['notification_only']),(1,0,0))
            july=[r for r in store.rows(date(2026,7,1),date(2026,7,31)) if r['status']=='ACTIVE']
            june=[r for r in store.rows(date(2026,6,1),date(2026,6,30)) if r['status']=='ACTIVE']
            self.assertEqual(len(july),1)
            self.assertEqual(july[0]['occurred_at'][:10],'2026-07-01')
            self.assertEqual(july[0]['amount_aed_minor'],3670)
            self.assertEqual([r['source_event_id'] for r in june],['mail:unrelated'])
            trace=json.loads(july[0]['decision_trace_json'])[-1]
            self.assertEqual(trace['transaction_occurred_at'][:10],'2026-06-30')
            self.assertTrue(store.reconcile_statement(payload)['idempotent_replay'])
            self.assertEqual(store.upsert([notification])['unchanged'],1)
            self.assertEqual(len([r for r in store.rows(date(2026,7,1),date(2026,7,31)) if r['status']=='ACTIVE']),1)
            with self.assertRaisesRegex(ValueError,'outside the statement period'):
                bad={**payload,'statement_reference':'invalid-post','transactions':[{**payload['transactions'][0],'post_date':'2026-08-01'}]}
                store.reconcile_statement(bad)
