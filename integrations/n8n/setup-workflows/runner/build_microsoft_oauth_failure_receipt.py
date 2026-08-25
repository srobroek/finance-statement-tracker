#!/usr/bin/env python3
"""Write a redacted WF23 failure receipt with three-state postconditions."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re


TIMEOUT_CODES = {
    "WF23_TIMEOUT_CONFIG_LOAD",
    "WF23_TIMEOUT_MODULE_LOAD",
    "WF23_TIMEOUT_COMMAND_INIT",
    "WF23_TIMEOUT_COMMAND_RUN",
    "WF23_TIMEOUT_RAW_CAPTURE",
    "WF23_TIMEOUT_FINALIZE",
}
AUTH_FAILURE_CODES = {
    "OUTLOOK_AUTH_REQUIRED",
    "ONEDRIVE_AUTH_REQUIRED",
}
TERMINAL_FAILURE_CODES = TIMEOUT_CODES | AUTH_FAILURE_CODES


def flag(value: str) -> bool:
    if value not in {"true", "false"}:
        raise ValueError("BOOLEAN_FLAG_REQUIRED")
    return value == "true"


def build_receipt(
    run_id: str,
    stage: str,
    cleanup_verified: bool,
    workflow_boundary_restored: bool,
    execution_rows_zero: bool,
    data_table_digest_restored: bool,
    failure_code: str = "",
) -> dict:
    all_clean = workflow_boundary_restored and execution_rows_zero and data_table_digest_restored
    if cleanup_verified and not all_clean:
        raise ValueError("INVALID_CLEAN_BOUNDARY_ASSERTION")
    if failure_code and failure_code not in TERMINAL_FAILURE_CODES:
        raise ValueError("INVALID_FAILURE_CODE")
    return {
        "schema_version": 1,
        "status": "FAILED_CLEAN_BOUNDARY_RESTORED" if cleanup_verified else "FAILED_REVIEW_REQUIRED",
        "scope": "TRANSIENT_MICROSOFT_OAUTH_REFRESH_PROOF",
        "run_id": run_id,
        "failure_stage": stage if re.fullmatch(r"[a-z_]+", stage) else "unknown",
        "failure_code": failure_code or None,
        "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "cleanup_verified": cleanup_verified,
        "postconditions": {
            "workflow_baseline_restored": True if workflow_boundary_restored else None,
            "execution_rows_zero": True if execution_rows_zero else None,
            "official_data_table_digest_restored": True if data_table_digest_restored else None,
        },
        "raw_irun_persisted": False if execution_rows_zero else None,
        "provider_response_logged": False,
        "production_workflows_activated": False if workflow_boundary_restored else None,
        "finance_data_table_writes": False if data_table_digest_restored else None,
        "actual_writes": False,
        "cashback_writes": False,
        "secret_values_recorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=pathlib.Path)
    parser.add_argument("run_id")
    parser.add_argument("stage")
    parser.add_argument("cleanup_verified")
    parser.add_argument("workflow_boundary_restored")
    parser.add_argument("execution_rows_zero")
    parser.add_argument("data_table_digest_restored")
    parser.add_argument("failure_code", nargs="?", default="")
    args = parser.parse_args()
    payload = build_receipt(
        args.run_id,
        args.stage,
        flag(args.cleanup_verified),
        flag(args.workflow_boundary_restored),
        flag(args.execution_rows_zero),
        flag(args.data_table_digest_restored),
        args.failure_code,
    )
    args.target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    args.target.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
