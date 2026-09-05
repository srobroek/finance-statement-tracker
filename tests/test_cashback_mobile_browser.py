"""Exercise the real mobile UI with fictional data in a loopback Chrome fixture."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def fixture_dashboard() -> dict:
    cards = []
    for code, name, spent in (("RAK", "RAK", 4250.25), ("SC", "SimplyCash", 2320.50), ("EI_AMAZON", "EI Amazon", 123.45)):
        cards.append({
            "card": code, "name": name, "short_name": name, "total_spend_aed": spent,
            "tier": "BASE", "safety_target_aed": 5500, "period_end": "2026-09-30",
            "tiers": [{"code": "ENHANCED", "minimum_spend_aed": 5000, "met": False}],
            "buckets": [{"code": f"{code}_GROCERY", "spend_aed": 1245.25,
                         "spend_cap_aed": 1500, "status": "OPEN"}],
        })
    routes = []
    for code, label in (("GROCERY", "Groceries"), ("DINING", "Dining"),
                        ("TRAVEL", "Travel"), ("FUEL", "Fuel"),
                        ("APPLE_PAY", "Apple Pay"), ("ONLINE", "Online"),
                        ("AMAZON", "Amazon"), ("FOREIGN", "Foreign spend"),
                        ("UTILITIES", "Utilities")):
        candidates = []
        for order, card in enumerate(cards):
            candidates.append({
                "card": card["card"], "status": "PREFERRED" if order == 0 else "FALLBACK",
                "order": order + 1, "payment_channel": "APPLE_PAY_POS" if order == 0 else "PHYSICAL_POS",
                "position_mode": "LIMITED", "target_rate_percent": 5 if order == 0 else 2,
                "current_tier_rate_percent": 1, "tier_before": "BASE", "target_tier": "ENHANCED",
                "estimate_basis": "CONDITIONAL_TARGET_TIER", "tier_remaining_aed": 749.75,
                "bucket": f"{card['card']}_GROCERY", "bucket_spend_aed": 1245.25,
                "bucket_cap_aed": 1500, "bucket_remaining_aed": 254.75,
                "card_spend_aed": card["total_spend_aed"], "tier_threshold_aed": 5000,
                "configured_fx_fee_percent": 0,
            })
        if code == "AMAZON":
            candidates = [candidates[1], candidates[0], candidates[2]]
        routes.append({"code": code, "label": label, "purchase_type": code, "active": True,
                       "use_card": "RAK", "ranked_cards": candidates})
    return {"currency": "AED", "profile": {"name": "Fictional mobile QA"}, "cards": cards,
            "routing_graphs": routes, "alerts": [{
                "key": "minimum:RAK:2026-09-01:2026-09-30",
                "title": "Fictional configured target risk", "detail": "Fixture only",
            }], "data_status": {
                "last_successful_check_at": "2026-09-05T04:05:00Z", "is_stale": False,
                "variance_count": 0, "acknowledged_alerts": []}}


class _FixtureHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        responses = {"/api/dashboard": fixture_dashboard(), "/api/periods": {"periods": []},
                     "/api/push/config": {"enabled": False}}
        if path in responses:
            body = json.dumps(responses[path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args: object) -> None:
        pass


class CashbackMobileBrowserTests(unittest.TestCase):
    def test_mobile_grid_details_and_card_navigation(self) -> None:
        chrome = next((path for name in ("google-chrome", "chromium", "chromium-browser")
                       if (path := shutil.which(name))), None)
        node = shutil.which("node")
        if not chrome or not node:
            if os.environ.get("CI") == "true":
                self.fail("Mobile browser CI requires Chrome and Node 22+")
            self.skipTest("Chrome and Node 22+ required for mobile browser interaction")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(
            _FixtureHandler, directory=str(ROOT / "apps/cashback-control/web")))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run([
                node, str(ROOT / "tests/browser/cashback-mobile.mjs"), chrome,
                f"http://127.0.0.1:{server.server_port}/index.html",
                str(ROOT / "artifacts/cashback-mobile"),
            ], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

