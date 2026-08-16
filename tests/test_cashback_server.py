from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class CashbackServerTests(unittest.TestCase):
    def test_health_and_ingest_authorization(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env.update({
                "CASHBACK_HOST": "127.0.0.1",
                "CASHBACK_PORT": str(port),
                "CASHBACK_DB_PATH": str(Path(temporary) / "events.sqlite3"),
                "CASHBACK_DASHBOARD_PATH": str(Path(temporary) / "dashboard.json"),
                "CASHBACK_INGEST_TOKEN": "test-token",
            })
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "apps" / "cashback-control" / "server.py")],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                health_url = f"http://127.0.0.1:{port}/api/health"
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(health_url, timeout=0.2) as response:
                            self.assertEqual(response.status, 200)
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.05)
                else:
                    self.fail("Cashback server did not become ready")

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=1
                ) as response:
                    self.assertEqual(response.headers.get("Cache-Control"), "no-cache")

                payload = json.dumps({
                    "source_event_id": "api-test:1",
                    "occurred_at": "2026-08-16T12:00:00+04:00",
                    "card_code": "RAK_WORLD",
                    "amount_aed": "10",
                    "merchant": "Test Merchant",
                }).encode("utf-8")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/events",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                    urllib.request.urlopen(request, timeout=1)
                self.assertEqual(unauthorized.exception.code, 401)

                request.add_header("Authorization", "Bearer test-token")
                with urllib.request.urlopen(request, timeout=1) as response:
                    result = json.loads(response.read())
                self.assertEqual(result["inserted"], 1)

                with urllib.request.urlopen(request, timeout=1) as response:
                    replay = json.loads(response.read())
                self.assertEqual(replay["updated"], 1)

                def post(endpoint: str, value: dict[str, object]) -> dict[str, object]:
                    api_request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/{endpoint}",
                        data=json.dumps(value).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer test-token",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(api_request, timeout=1) as api_response:
                        return json.loads(api_response.read())

                correction = post("corrections", {
                    "correction_id": "api-test-correction:1",
                    "source_event_id": "api-test:1",
                    "changes": {"purchase_type": "GROCERY"},
                    "reason": "synthetic classification correction",
                    "source": "test",
                })
                self.assertEqual(correction["correction"]["source_event_id"], "api-test:1")

                refund = post("events", {
                    "source_event_id": "api-test:refund:1",
                    "occurred_at": "2026-08-17T12:00:00+04:00",
                    "card_code": "RAK_WORLD",
                    "amount_aed": "2",
                    "merchant": "Test Merchant",
                    "purchase_type": "GROCERY",
                    "event_type": "REFUND",
                })
                self.assertEqual(refund["inserted"], 1)

                reconciliation = post("reconcile", {
                    "statement_reference": "synthetic-statement-2026-08",
                    "card_code": "RAK_WORLD",
                    "period_start": "2026-08-01",
                    "period_end": "2026-08-31",
                    "transactions": [
                        {
                            "statement_transaction_id": "purchase-1",
                            "occurred_at": "2026-08-16T12:00:00+04:00",
                            "amount_aed": "10",
                            "currency": "AED",
                            "merchant": "Test Merchant",
                            "purchase_type": "GROCERY",
                            "event_type": "PURCHASE",
                        },
                        {
                            "statement_transaction_id": "refund-1",
                            "occurred_at": "2026-08-17T12:00:00+04:00",
                            "amount_aed": "2",
                            "currency": "AED",
                            "merchant": "Test Merchant",
                            "purchase_type": "GROCERY",
                            "event_type": "REFUND",
                        },
                    ],
                })
                self.assertEqual(reconciliation["reconciliation"]["matched"], 2)
                self.assertEqual(reconciliation["reconciliation"]["notification_only"], 0)

                finalized = post("periods/finalize", {
                    "statement_reference": "synthetic-statement-2026-08",
                    "statement_evidence_reference": "sha256:synthetic",
                    "statement_document_url": "https://evidence.example/statement.pdf",
                    "actual_import_verified": True,
                })
                self.assertEqual(finalized["period"]["status"], "FINALIZED")

                outlook = post("outlook/messages", {
                    "source": "outlook",
                    "completed_at": "2026-08-16T16:20:00+04:00",
                    "cursor": "2026-08-16T16:20:00+04:00",
                    "messages": [
                        {
                            "id": "outlook-api-test-1",
                            "subject": "ADCB Card Transaction OTP generated",
                            "sender": {"emailAddress": {"address": "adcbalert@adcb.com"}},
                            "receivedDateTime": "2026-08-16T10:30:00Z",
                            "bodyPreview": (
                                "OTP for transaction at Mollak for AED 3057.92 on your ADCB "
                                "Credit Card XXX8833 sent to your registered mobile number."
                            ),
                            "web_link": "https://outlook.office.example/outlook-api-test-1",
                        }
                    ],
                })
                self.assertEqual(outlook["parse"]["scanned_count"], 1)
                self.assertEqual(outlook["parse"]["accepted_count"], 1)
                self.assertEqual(outlook["persistence"]["inserted"], 1)
                self.assertFalse(outlook["cursor_committed"])
                self.assertEqual(
                    outlook["parse"]["events"][0]["purchase_type"],
                    "GENERAL",
                )
                review_queue = post("review-queue", {"limit": 20})
                self.assertIn(
                    "outlook-api-test-1:0",
                    {event["source_event_id"] for event in review_queue["events"]},
                )
                heartbeat = post("ingest-runs", {
                    "source": "outlook",
                    "completed_at": "2026-08-16T16:20:00+04:00",
                    "scanned_count": 1,
                    "accepted_count": 1,
                    "cursor": "2026-08-16T16:20:00+04:00",
                })
                self.assertEqual(heartbeat["ingest"]["source"], "outlook")
                self.assertEqual(
                    heartbeat["ingest"]["cursor"],
                    "2026-08-16T16:20:00+04:00",
                )
                ingest_state = post("ingest-state", {"source": "outlook"})
                self.assertEqual(
                    ingest_state["ingest_state"]["cursor"],
                    "2026-08-16T16:20:00+04:00",
                )

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/dashboard", timeout=1
                ) as response:
                    dashboard = json.loads(response.read())
                self.assertEqual(dashboard["data_status"]["event_count"], 3)
                self.assertEqual(dashboard["data_status"]["correction_count"], 1)
                self.assertIn(
                    "FINALIZED",
                    {period["status"] for period in dashboard["data_status"]["card_periods"]},
                )
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/periods", timeout=1
                ) as response:
                    history = json.loads(response.read())
                self.assertEqual(history["period_count"], 1)
                self.assertEqual(history["periods"][0]["card"], "RAK_WORLD")
                self.assertEqual(history["periods"][0]["summary"]["expected_cashback_aed"], "0.00")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()
