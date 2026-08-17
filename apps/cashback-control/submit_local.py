"""Submit stdin JSON to the companion from inside its own container."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--endpoint",
        choices=(
            "events",
            "events/validate",
            "ingest-runs",
            "ingest-state",
            "outlook/messages",
            "reconcile",
            "corrections",
            "periods/finalize",
        ),
        default="events",
    )
    arguments = parser.parse_args()
    payload = sys.stdin.buffer.read()
    if not payload:
        raise ValueError("Expected a JSON event payload on stdin")
    token = os.environ.get("CASHBACK_INGEST_TOKEN")
    if not token:
        raise RuntimeError("CASHBACK_INGEST_TOKEN is not configured")
    request = urllib.request.Request(
        f"http://127.0.0.1:5010/api/{arguments.endpoint}",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            sys.stdout.buffer.write(response.read())
            sys.stdout.write("\n")
    except urllib.error.HTTPError as error:
        sys.stderr.buffer.write(error.read())
        sys.stderr.write("\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
