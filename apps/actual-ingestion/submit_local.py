"""Submit stdin JSON to the ingestion worker from inside its container."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id")
    arguments = parser.parse_args()
    token = os.environ.get("FINANCE_INGEST_TOKEN")
    if not token:
        raise RuntimeError("FINANCE_INGEST_TOKEN is not configured")
    if arguments.job_id:
        url = f"http://127.0.0.1:5020/api/jobs/{arguments.job_id}"
        payload = None
        method = "GET"
    else:
        url = "http://127.0.0.1:5020/api/jobs"
        payload = sys.stdin.buffer.read()
        if not payload:
            raise ValueError("Expected a JSON job payload on stdin")
        method = "POST"
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=330) as response:
            sys.stdout.buffer.write(response.read())
            sys.stdout.write("\n")
    except urllib.error.HTTPError as error:
        sys.stderr.buffer.write(error.read())
        sys.stderr.write("\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
