#!/usr/bin/env python3
"""Accept only exact redacted WF23 success or terminal failure lines."""

from __future__ import annotations

import datetime as dt
import json
import sys


MAX_BYTES = 65_536
SUCCESS_PREFIX = "transient WF23 execution verified:"
FAILURE_PREFIX = "transient WF23 execution failed:"
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
TERMINALITY_CODE = "WF23_EXECUTION_NOT_FINISHED_SUCCESS"
TERMINAL_FAILURE_CODES = TIMEOUT_CODES | AUTH_FAILURE_CODES | {TERMINALITY_CODE}
SUCCESS_KEYS = {
    "schema_version",
    "status",
    "execution_id",
    "outlook_read_succeeded",
    "outlook_items_observed",
    "outlook_max_messages",
    "outlook_server_filter_applied",
    "outlook_window_start",
    "outlook_window_end",
    "onedrive_root_read_succeeded",
    "onedrive_root_items_observed",
    "provider_writes",
    "message_fields_recorded",
    "file_fields_recorded",
    "credential_values_recorded",
    "token_values_recorded",
    "production_workflows_activated",
    "actual_writes",
    "cashback_writes",
    "verified_at",
}
FAILURE_KEYS = {
    "schema_version",
    "status",
    "error_code",
    "provider_response_logged",
    "secret_values_recorded",
}
REJECTED_DIAGNOSTIC = "WF23_REDACTED_OUTPUT_REJECTED"


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_timestamp(value: object) -> dt.datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except (TypeError, ValueError, OverflowError):
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH") from None
    if parsed.tzinfo is None:
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    return parsed


def decode_line(raw: str, prefix: str) -> dict[str, object]:
    if len(raw.encode("utf-8")) > MAX_BYTES or "\x00" in raw or "\r" in raw:
        raise ValueError("WF23_EXECUTION_OUTPUT_INVALID")
    if raw.endswith("\n"):
        raw = raw[:-1]
    if "\n" in raw or not raw.startswith(prefix):
        raise ValueError("WF23_EXECUTION_OUTPUT_INVALID")
    value = json.loads(raw[len(prefix) :], object_pairs_hook=reject_duplicate_keys)
    if type(value) is not dict:
        raise ValueError("WF23_EXECUTION_OUTPUT_INVALID")
    return value


def parse_success(raw: str) -> dict[str, object]:
    value = decode_line(raw, SUCCESS_PREFIX)
    if set(value) != SUCCESS_KEYS:
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    if value["schema_version"] != "microsoft-oauth-refresh-proof-receipt-v1" or value["status"] != "VERIFIED":
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    if type(value["execution_id"]) is not str or not value["execution_id"].isdigit():
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    true_fields = {
        "outlook_read_succeeded",
        "outlook_server_filter_applied",
        "onedrive_root_read_succeeded",
    }
    false_fields = {
        "provider_writes",
        "message_fields_recorded",
        "file_fields_recorded",
        "credential_values_recorded",
        "token_values_recorded",
        "production_workflows_activated",
        "actual_writes",
        "cashback_writes",
    }
    if any(type(value[key]) is not bool or value[key] is not True for key in true_fields):
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    if any(type(value[key]) is not bool or value[key] is not False for key in false_fields):
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    if type(value["outlook_max_messages"]) is not int or value["outlook_max_messages"] != 1:
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    if type(value["outlook_items_observed"]) is not int or not 0 <= value["outlook_items_observed"] <= 1:
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    if type(value["onedrive_root_items_observed"]) is not int or value["onedrive_root_items_observed"] < 0:
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    start = parse_timestamp(value["outlook_window_start"])
    end = parse_timestamp(value["outlook_window_end"])
    parse_timestamp(value["verified_at"])
    if start > end:
        raise ValueError("WF23_EXECUTION_RECEIPT_CONTRACT_MISMATCH")
    return value


def parse_terminal_failure(raw: str) -> str:
    value = decode_line(raw, FAILURE_PREFIX)
    if set(value) != FAILURE_KEYS:
        raise ValueError("WF23_TERMINAL_FAILURE_RECEIPT_CONTRACT_MISMATCH")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("WF23_TERMINAL_FAILURE_RECEIPT_CONTRACT_MISMATCH")
    if value["status"] != "FAILED" or type(value["error_code"]) is not str or value["error_code"] not in TERMINAL_FAILURE_CODES:
        raise ValueError("WF23_TERMINAL_FAILURE_RECEIPT_CONTRACT_MISMATCH")
    if type(value["provider_response_logged"]) is not bool or value["provider_response_logged"] is not False:
        raise ValueError("WF23_TERMINAL_FAILURE_RECEIPT_CONTRACT_MISMATCH")
    if type(value["secret_values_recorded"]) is not bool or value["secret_values_recorded"] is not False:
        raise ValueError("WF23_TERMINAL_FAILURE_RECEIPT_CONTRACT_MISMATCH")
    return value["error_code"]


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"success", "terminal-failure"}:
        print(REJECTED_DIAGNOSTIC, file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read(MAX_BYTES + 1)
        if sys.argv[1] == "success":
            print(json.dumps(parse_success(raw), separators=(",", ":")))
        else:
            print(parse_terminal_failure(raw))
        return 0
    except Exception:
        # The parser processes an untrusted transport boundary. Never let a
        # JSON, Unicode, timestamp, or custom exception echo input-derived text.
        print(REJECTED_DIAGNOSTIC, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
