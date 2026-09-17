#!/usr/bin/env python3
"""Probe Cashback health from inside its container without exposing credentials."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


HEALTH_URL = "http://127.0.0.1:5010/api/health"


def probe() -> bool:
    token = os.environ.get("CASHBACK_INGEST_TOKEN", "")
    if not token:
        return False
    request = urllib.request.Request(
        HEALTH_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                return False
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


if __name__ == "__main__":
    if probe():
        print('{"status":"ok"}')
        raise SystemExit(0)
    raise SystemExit(1)
