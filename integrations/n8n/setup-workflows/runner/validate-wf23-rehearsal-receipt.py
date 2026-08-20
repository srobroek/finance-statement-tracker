#!/usr/bin/env python3
"""Validate one recent, redacted WF23 PostgreSQL rollback-rehearsal receipt."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys


EXPECTED_KEYS = {
    "schema_version",
    "status",
    "scope",
    "recorded_at_utc",
    "commits",
    "workflow_source_sha256",
    "sql_sha256",
    "live_pre_state",
    "transaction_outcome",
    "production_sql_body_completed",
    "post_state_unchanged",
    "services_healthy",
    "provider_calls",
    "secret_values_recorded",
}
EXPECTED_PRE_STATE_KEYS = {
    "project_id",
    "workflow_id",
    "execution_id",
    "project_state",
    "folder_placements",
    "tag_edges",
    "bad_tag_sets",
    "setup_ids",
    "wf23_workflows",
    "wf23_executions",
    "wf23_execution_data_rows",
    "wf23_histories",
    "workflow_corpus_sha256",
    "credential_corpus_sha256",
    "finance_data_table_sha256",
    "orphan_signature",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_KEY")
        result[key] = value
    return result


def strict_int(value: object) -> bool:
    return type(value) is int


def validate_receipt(
    value: object,
    *,
    finance_commit: str,
    orchestrator_commit: str,
    source_sha256: str,
    sql_sha256: str,
    workflow_corpus_sha256: str,
    credential_corpus_sha256: str,
    data_table_sha256: str,
    now: dt.datetime,
) -> None:
    if type(value) is not dict or set(value) != EXPECTED_KEYS:
        raise ValueError("RECEIPT_SHAPE")
    if not strict_int(value["schema_version"]) or value["schema_version"] != 1:
        raise ValueError("SCHEMA_VERSION")
    if value["status"] != "VERIFIED" or value["scope"] != "WF23_POSTGRESQL_ROLLBACK_REHEARSAL":
        raise ValueError("RECEIPT_SCOPE")
    if value["commits"] != {"finance": finance_commit, "orchestrator": orchestrator_commit}:
        raise ValueError("COMMIT_BINDING")
    for observed, expected in (
        (value["workflow_source_sha256"], source_sha256),
        (value["sql_sha256"], sql_sha256),
    ):
        if type(observed) is not str or not SHA256_RE.fullmatch(observed) or observed != expected:
            raise ValueError("SOURCE_BINDING")

    pre_state = value["live_pre_state"]
    if type(pre_state) is not dict or set(pre_state) != EXPECTED_PRE_STATE_KEYS:
        raise ValueError("PRE_STATE_SHAPE")
    fixed_state = {
        "project_id": "gT5rxq26L0PoNUWX",
        "workflow_id": "10000000-0000-4000-8000-000000000023",
        "execution_id": 15,
        "project_state": "22|0|0",
        "folder_placements": 22,
        "tag_edges": 66,
        "bad_tag_sets": 0,
        "setup_ids": 1,
        "wf23_workflows": 1,
        "wf23_executions": 1,
        "wf23_execution_data_rows": 1,
        "wf23_histories": 1,
        "orphan_signature": "ORPHANED_SOFT_DELETED_EXECUTION",
    }
    for key, expected in fixed_state.items():
        observed = pre_state[key]
        if type(expected) is int and not strict_int(observed):
            raise ValueError("PRE_STATE_TYPE")
        if observed != expected:
            raise ValueError("PRE_STATE_VALUE")
    for key, expected in (
        ("workflow_corpus_sha256", workflow_corpus_sha256),
        ("credential_corpus_sha256", credential_corpus_sha256),
        ("finance_data_table_sha256", data_table_sha256),
    ):
        observed = pre_state[key]
        if type(observed) is not str or not SHA256_RE.fullmatch(observed) or observed != expected:
            raise ValueError("PRE_STATE_DIGEST")

    if value["transaction_outcome"] != "ROLLED_BACK":
        raise ValueError("TRANSACTION_OUTCOME")
    for key in ("production_sql_body_completed", "post_state_unchanged", "services_healthy"):
        if type(value[key]) is not bool or value[key] is not True:
            raise ValueError("VERIFIED_POSTCONDITION")
    for key in ("provider_calls", "secret_values_recorded"):
        if type(value[key]) is not bool or value[key] is not False:
            raise ValueError("FORBIDDEN_EFFECT")

    timestamp = value["recorded_at_utc"]
    if type(timestamp) is not str:
        raise ValueError("TIMESTAMP_TYPE")
    try:
        recorded = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except Exception:
        raise ValueError("TIMESTAMP_INVALID") from None
    if recorded.tzinfo is None:
        raise ValueError("TIMESTAMP_TIMEZONE")
    age = (now.astimezone(dt.timezone.utc) - recorded.astimezone(dt.timezone.utc)).total_seconds()
    if age < -60 or age > 900:
        raise ValueError("TIMESTAMP_NOT_RECENT")


def main() -> int:
    if len(sys.argv) != 9:
        print("WF23_REHEARSAL_RECEIPT_REJECTED", file=sys.stderr)
        return 2
    try:
        path = pathlib.Path(sys.argv[1])
        if path.stat().st_size > 16_384:
            raise ValueError("RECEIPT_TOO_LARGE")
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
        validate_receipt(
            value,
            finance_commit=sys.argv[2],
            orchestrator_commit=sys.argv[3],
            source_sha256=sys.argv[4],
            sql_sha256=sys.argv[5],
            workflow_corpus_sha256=sys.argv[6],
            credential_corpus_sha256=sys.argv[7],
            data_table_sha256=sys.argv[8],
            now=dt.datetime.now(dt.timezone.utc),
        )
    except Exception:
        print("WF23_REHEARSAL_RECEIPT_REJECTED", file=sys.stderr)
        return 1
    print("WF23_REHEARSAL_RECEIPT_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
