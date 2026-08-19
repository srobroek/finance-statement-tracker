#!/usr/bin/env python3
"""Validate redacted Microsoft OAuth expiry transitions without token data."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any


EXPECTED_TYPES = {
    "outlook": "microsoftOutlookOAuth2Api",
    "onedrive": "microsoftOneDriveOAuth2Api",
}


def timestamp(value: Any, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field.upper()}_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field.upper()}_INVALID") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field.upper()}_TIMEZONE_REQUIRED")
    return parsed


def validate_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or snapshot.get("status") != "VERIFIED":
        raise ValueError("OAUTH_METADATA_SNAPSHOT_INVALID")
    if set(snapshot.get("credentials", {})) != set(EXPECTED_TYPES):
        raise ValueError("OAUTH_METADATA_CREDENTIAL_SET_INVALID")
    timestamp(snapshot.get("observed_at_utc"), "observed_at_utc")
    for label, expected_type in EXPECTED_TYPES.items():
        row = snapshot["credentials"][label]
        if row.get("credential_type") != expected_type:
            raise ValueError(f"{label.upper()}_CREDENTIAL_TYPE_MISMATCH")
        if row.get("expiration_observed") is not True:
            raise ValueError(f"{label.upper()}_EXPIRY_REQUIRED")
        if row.get("access_token_present") is not True or row.get("refresh_token_present") is not True:
            raise ValueError(f"{label.upper()}_TOKEN_PRESENCE_METADATA_INVALID")
        timestamp(row.get("expires_at_utc"), f"{label}_expires_at_utc")
        timestamp(row.get("credential_updated_at_utc"), f"{label}_credential_updated_at_utc")
        if not isinstance(row.get("expired_at_readback"), bool):
            raise ValueError(f"{label.upper()}_EXPIRED_FLAG_INVALID")
    return snapshot


def require_expired_before(snapshot: Any) -> None:
    before = validate_snapshot(snapshot)
    observed = timestamp(before["observed_at_utc"], "observed_at_utc")
    for label in EXPECTED_TYPES:
        row = before["credentials"][label]
        expiry = timestamp(row["expires_at_utc"], f"{label}_expires_at_utc")
        if row["expired_at_readback"] is not True or expiry > observed:
            raise ValueError(f"{label.upper()}_TOKEN_NOT_EXPIRED_BEFORE_FIRST_EXECUTION")


def validate_refresh(snapshots: Any) -> dict[str, Any]:
    if not isinstance(snapshots, list) or len(snapshots) != 3:
        raise ValueError("EXACT_THREE_METADATA_SNAPSHOTS_REQUIRED")
    before, after_first, after_second = [validate_snapshot(snapshot) for snapshot in snapshots]
    require_expired_before(before)
    summary: dict[str, Any] = {}
    for label, expected_type in EXPECTED_TYPES.items():
        rows = [snapshot["credentials"][label] for snapshot in (before, after_first, after_second)]
        expiry = [timestamp(row["expires_at_utc"], f"{label}_expires_at_utc") for row in rows]
        updated = [timestamp(row["credential_updated_at_utc"], f"{label}_credential_updated_at_utc") for row in rows]
        observed_after_first = timestamp(after_first["observed_at_utc"], "after_first_observed_at_utc")
        observed_after_second = timestamp(after_second["observed_at_utc"], "after_second_observed_at_utc")
        if expiry[1] <= expiry[0] or rows[1]["expired_at_readback"] is not False or expiry[1] <= observed_after_first:
            raise ValueError(f"{label.upper()}_FIRST_EXECUTION_DID_NOT_REFRESH_EXPIRED_TOKEN")
        if expiry[2] < expiry[1] or rows[2]["expired_at_readback"] is not False or expiry[2] <= observed_after_second:
            raise ValueError(f"{label.upper()}_POST_RESTART_EXPIRY_REGRESSION")
        if updated[1] < updated[0] or updated[2] < updated[1]:
            raise ValueError(f"{label.upper()}_UPDATED_AT_REGRESSION")
        summary[label] = {
            "credential_type": expected_type,
            "expired_before_first_execution": True,
            "first_execution_expiry_advanced": True,
            "first_execution_expiry_future": True,
            "post_restart_expiry_non_regression": True,
            "post_restart_expiry_future": True,
            "updated_at_non_regression": True,
            "updated_at_used_as_refresh_proof": False,
            "refresh_proven_by_expiry_transition": True,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-expired-before", action="store_true")
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    try:
        if args.require_expired_before:
            require_expired_before(payload)
            return 0
        print(json.dumps(validate_refresh(payload), separators=(",", ":")))
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
