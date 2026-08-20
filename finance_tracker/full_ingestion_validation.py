from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .actual_notes import validate_actual_notes


_SPACE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ExpectedRecord:
    source_id: str
    manifest_path: str
    account: str
    record: dict[str, Any]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_payee(value: object) -> str:
    return _SPACE.sub(" ", str(value or "").casefold()).strip()


def _economic_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(row.get("date") or ""),
        int(row.get("amount") or 0),
        _normalized_payee(row.get("imported_payee")),
    )


def _manifest_paths(root: Path, source: dict[str, Any]) -> list[Path]:
    paths: set[Path] = set()
    for value in source.get("files") or []:
        path = (root / str(value)).resolve()
        if not path.is_file():
            raise ValueError(f"Missing ingestion manifest: {value}")
        paths.add(path)
    for pattern in source.get("globs") or []:
        matches = [path.resolve() for path in root.glob(str(pattern)) if path.is_file()]
        if not matches:
            raise ValueError(f"Ingestion manifest glob matched no files: {pattern}")
        paths.update(matches)
    if not paths:
        raise ValueError(f"Manifest source {source.get('id')!r} has no files or globs")
    return sorted(paths, key=lambda path: str(path).casefold())


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _resolve_catalogue_path(root: Path, relative_path: object) -> Path | None:
    token = str(relative_path or "").strip().replace("\\", "/")
    if not token:
        return None
    path = (root / token).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _evidence_index(root: Path, catalogue_path: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    payload = _read_json(catalogue_path)
    if not isinstance(payload, list):
        raise ValueError("Evidence catalogue must be a JSON array")
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        token = str(row.get("sha256") or "").casefold()
        if token:
            by_hash[token].append(row)
        rows.append(row)
    return dict(by_hash), rows


def _manifest_artifact_audit(
    root: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    evidence_by_hash: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    source = manifest.get("source_evidence") or {}
    expected_hash = str(source.get("document_sha256") or "").casefold()
    candidates: list[Path] = []
    for row in evidence_by_hash.get(expected_hash, []):
        candidate = _resolve_catalogue_path(root, row.get("relative_path"))
        if candidate is not None:
            candidates.append(candidate)
    filename = str(source.get("source_filename") or "").strip()
    if filename:
        candidates.extend(manifest_path.parent.parent.glob(filename))
        candidates.extend(manifest_path.parent.parent.parent.glob(filename))

    artifact = manifest.get("artifact") or {}
    artifact_path = str(artifact.get("local_path") or "").strip()
    if artifact_path:
        candidate = Path(artifact_path)
        candidates.append(candidate if candidate.is_absolute() else (root / candidate))
    download_reference = str(artifact.get("download_reference") or "")
    if not expected_hash and download_reference.startswith("sha256:"):
        expected_hash = download_reference.removeprefix("sha256:").casefold()

    unique_candidates = sorted({path.resolve() for path in candidates}, key=str)
    existing = [path for path in unique_candidates if path.is_file()]
    actual_hash = _sha256(existing[0]) if existing else None
    return {
        "manifest": _relative(manifest_path, root),
        "expected_sha256": expected_hash or None,
        "artifact": _relative(existing[0], root) if existing else None,
        "exists": bool(existing),
        "hash_matches": bool(existing and expected_hash and actual_hash == expected_hash),
        "actual_sha256": actual_hash,
    }


def _load_expected(
    root: Path,
    config: dict[str, Any],
    evidence_by_hash: dict[str, list[dict[str, Any]]],
) -> tuple[list[ExpectedRecord], list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[ExpectedRecord] = []
    manifests: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for source in config.get("manifest_sources") or []:
        source_id = str(source.get("id") or "").strip()
        if not source_id:
            raise ValueError("Every manifest source requires an id")
        allowed_accounts = set(str(value) for value in source.get("accounts") or [])
        prefixes = tuple(str(value) for value in source.get("imported_id_prefixes") or [])
        for path in _manifest_paths(root, source):
            manifest = _read_json(path)
            if not isinstance(manifest, dict):
                raise ValueError(f"Ingestion manifest must be an object: {_relative(path, root)}")
            envelopes = manifest.get("envelopes") or []
            statement = manifest.get("statement") or {}
            count = sum(len(envelope.get("records") or []) for envelope in envelopes)
            errors: list[str] = []
            if source.get("require_balance_tied") and statement.get("balance_tied") is not True:
                errors.append("STATEMENT_NOT_BALANCE_TIED")
            stated_count = statement.get("transaction_count")
            if stated_count is not None and int(stated_count) != count:
                errors.append("STATEMENT_RECORD_COUNT_MISMATCH")
            if manifest.get("review_count") not in (None, 0):
                errors.append("REVIEW_ITEMS_REMAIN")
            for envelope in envelopes:
                account = str(envelope.get("account") or "")
                if allowed_accounts and account not in allowed_accounts:
                    errors.append(f"UNEXPECTED_ACCOUNT:{account}")
                for record in envelope.get("records") or []:
                    imported_id = str(record.get("imported_id") or "")
                    if not imported_id:
                        errors.append("MISSING_IMPORTED_ID")
                    elif prefixes and not imported_id.startswith(prefixes):
                        errors.append(f"UNEXPECTED_IMPORTED_ID:{imported_id}")
                    records.append(ExpectedRecord(source_id, _relative(path, root), account, dict(record)))
            manifests.append({
                "source_id": source_id,
                "path": _relative(path, root),
                "record_count": count,
                "balance_tied": statement.get("balance_tied"),
                "errors": sorted(set(errors)),
            })
            artifacts.append(_manifest_artifact_audit(root, manifest, path, evidence_by_hash))
    return records, manifests, artifacts


def _statement_evidence_audit(root: Path, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for row in rows:
        if row.get("entity_type") != "CARD_PERIOD" or row.get("document_type") != "statement":
            continue
        expected_hash = str(row.get("sha256") or "").casefold()
        if not expected_hash or expected_hash in seen_hashes:
            continue
        seen_hashes.add(expected_hash)
        path = _resolve_catalogue_path(root, row.get("relative_path"))
        exists = bool(path and path.is_file())
        actual_hash = _sha256(path) if exists and path is not None else None
        result.append({
            "bank": row.get("bank"),
            "card_code": row.get("card_code"),
            "statement_date": row.get("statement_date"),
            "path": str(row.get("relative_path") or ""),
            "exists": exists,
            "expected_sha256": expected_hash,
            "actual_sha256": actual_hash,
            "hash_matches": actual_hash == expected_hash,
        })
    return sorted(result, key=lambda row: (str(row["statement_date"]), str(row["bank"])))


def _actual_rows(snapshot: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    scope = config.get("snapshot_scope") or {}
    accounts = set(str(value) for value in scope.get("accounts") or [])
    prefixes = tuple(str(value) for value in scope.get("imported_id_prefixes") or [])
    result = []
    for raw in snapshot.get("transactions") or []:
        if not isinstance(raw, dict) or raw.get("tombstone"):
            continue
        imported_id = str(raw.get("imported_id") or "")
        account = str(raw.get("account_name") or "")
        if not imported_id:
            continue
        if accounts and account not in accounts:
            continue
        if prefixes and not imported_id.startswith(prefixes):
            continue
        result.append(dict(raw))
    return result


def _untracked_actual_rows(snapshot: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return scoped rows that cannot be traced to an import or native transfer."""
    scope = config.get("snapshot_scope") or {}
    accounts = set(str(value) for value in scope.get("accounts") or [])
    result = []
    for raw in snapshot.get("transactions") or []:
        if not isinstance(raw, dict) or raw.get("tombstone"):
            continue
        if accounts and str(raw.get("account_name") or "") not in accounts:
            continue
        if raw.get("imported_id") or raw.get("starting_balance_flag") or raw.get("transfer_id"):
            continue
        result.append(dict(raw))
    return result


def _suppressed_statement_duplicates(
    missing: Iterable[ExpectedRecord],
    expected: Iterable[ExpectedRecord],
    actual_by_id: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], set[str]]:
    expected_rows = list(expected)
    # The direct Actual import path partitions one statement import at a time. Browser rows
    # are existing candidates, not part of the incoming statement multiplicity.
    incoming_counts = Counter(
        _economic_key(item.record)
        for item in expected_rows
        if str(item.record.get("imported_id") or "").startswith("statement:")
    )
    browser_by_key: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for rows in actual_by_id.values():
        for row in rows:
            if str(row.get("imported_id") or "").startswith("browser:"):
                browser_by_key[_economic_key(row)].append(row)
    suppressed: list[dict[str, Any]] = []
    ids: set[str] = set()
    for item in missing:
        imported_id = str(item.record.get("imported_id") or "")
        if not imported_id.startswith("statement:"):
            continue
        key = _economic_key(item.record)
        matches = browser_by_key.get(key) or []
        if incoming_counts[key] == 1 and len(matches) == 1:
            ids.add(imported_id)
            suppressed.append({
                "imported_id": imported_id,
                "matched_existing_id": matches[0].get("imported_id"),
                "date": item.record.get("date"),
                "amount": item.record.get("amount"),
                "imported_payee": item.record.get("imported_payee"),
            })
    return suppressed, ids


def _expected_field(item: ExpectedRecord, field: str) -> Any:
    if field == "account":
        return item.account
    return item.record.get(field)


def _actual_field(row: dict[str, Any], field: str) -> Any:
    if field == "account":
        return row.get("account_name")
    return row.get(field)


def validate_full_ingestion(
    root: Path,
    config: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Audit complete source-to-Actual coverage without mutating the ledger."""
    root = root.resolve()
    catalogue_path = (root / str(config["evidence_catalogue"])).resolve()
    evidence_by_hash, evidence_rows = _evidence_index(root, catalogue_path)
    expected, manifests, artifacts = _load_expected(root, config, evidence_by_hash)
    actual = _actual_rows(snapshot, config)
    untracked_actual = _untracked_actual_rows(snapshot, config)
    sample_limit = int(config.get("sample_limit") or 25)

    expected_by_id: dict[str, list[ExpectedRecord]] = defaultdict(list)
    for item in expected:
        expected_by_id[str(item.record.get("imported_id") or "")].append(item)
    actual_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in actual:
        actual_by_id[str(row.get("imported_id") or "")].append(row)

    duplicate_expected = {
        key: [item.manifest_path for item in rows]
        for key, rows in expected_by_id.items()
        if not key or len(rows) != 1
    }
    duplicate_actual = {
        key: len(rows)
        for key, rows in actual_by_id.items()
        if not key or len(rows) != 1
    }
    missing_rows = [
        rows[0]
        for imported_id, rows in expected_by_id.items()
        if imported_id and len(rows) == 1 and imported_id not in actual_by_id
    ]
    suppressed, suppressed_ids = _suppressed_statement_duplicates(
        missing_rows, expected, actual_by_id
    )
    suppression_targets = {
        str(row["matched_existing_id"])
        for row in suppressed
    }
    missing = [
        str(item.record["imported_id"])
        for item in missing_rows
        if str(item.record["imported_id"]) not in suppressed_ids
    ]
    unexpected = sorted(
        imported_id
        for imported_id in actual_by_id
        if imported_id not in expected_by_id and imported_id not in suppression_targets
    )

    mismatches: list[dict[str, Any]] = []
    exact_fields = [str(value) for value in config.get("required_exact_fields") or []]
    for imported_id, expected_rows in expected_by_id.items():
        actual_rows = actual_by_id.get(imported_id) or []
        if len(expected_rows) != 1 or len(actual_rows) != 1:
            continue
        expected_item = expected_rows[0]
        actual_row = actual_rows[0]
        for field in exact_fields:
            desired = _expected_field(expected_item, field)
            observed = _actual_field(actual_row, field)
            if desired != observed:
                mismatches.append({
                    "imported_id": imported_id,
                    "field": field,
                    "expected": desired,
                    "actual": observed,
                })

    note_violations: list[dict[str, str]] = []
    for row in actual:
        try:
            validate_actual_notes(str(row.get("notes") or ""))
        except ValueError as error:
            note_violations.append({
                "imported_id": str(row.get("imported_id") or ""),
                "error": str(error),
            })

    evidence_audit = _statement_evidence_audit(root, evidence_rows)
    account_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"source_records": 0, "actual_records": 0, "source_amount": 0, "actual_amount": 0}
    )
    for item in expected:
        row = account_rows[item.account]
        row["source_records"] += 1
        row["source_amount"] += int(item.record.get("amount") or 0)
    for item in actual:
        account = str(item.get("account_name") or "")
        row = account_rows[account]
        row["actual_records"] += 1
        row["actual_amount"] += int(item.get("amount") or 0)

    manifest_errors = [
        {"path": row["path"], "errors": row["errors"]}
        for row in manifests
        if row["errors"]
    ]
    artifact_failures = [
        row for row in artifacts
        if row["expected_sha256"] and (not row["exists"] or not row["hash_matches"])
    ]
    statement_evidence_failures = [
        row for row in evidence_audit if not row["exists"] or not row["hash_matches"]
    ]
    status = "PASS" if not any((
        duplicate_expected,
        duplicate_actual,
        missing,
        unexpected,
        mismatches,
        note_violations,
        untracked_actual,
        manifest_errors,
        artifact_failures,
        statement_evidence_failures,
    )) else "FAIL"
    return {
        "schema_version": "full-ingestion-audit-v1",
        "status": status,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "counts": {
            "manifests": len(manifests),
            "source_records": len(expected),
            "source_unique_imported_ids": len(expected_by_id),
            "actual_records": len(actual),
            "suppressed_cross_source_duplicates": len(suppressed),
            "missing": len(missing),
            "unexpected": len(unexpected),
            "field_mismatches": len(mismatches),
            "note_violations": len(note_violations),
            "untracked_actual_rows": len(untracked_actual),
            "statement_evidence": len(evidence_audit),
            "statement_evidence_failures": len(statement_evidence_failures),
        },
        "accounts": dict(sorted(account_rows.items())),
        "suppressed_cross_source_duplicates": suppressed,
        "duplicate_source_ids": duplicate_expected,
        "duplicate_actual_ids": duplicate_actual,
        "missing": missing[:sample_limit],
        "unexpected": unexpected[:sample_limit],
        "field_mismatches": mismatches[:sample_limit],
        "note_violations": note_violations[:sample_limit],
        "untracked_actual_rows": [
            {
                "actual_id": row.get("id"),
                "account": row.get("account_name"),
                "date": row.get("date"),
                "amount": row.get("amount"),
                "payee": row.get("payee_name"),
            }
            for row in untracked_actual[:sample_limit]
        ],
        "manifest_errors": manifest_errors,
        "artifact_failures": artifact_failures[:sample_limit],
        "statement_evidence_failures": statement_evidence_failures[:sample_limit],
        "manifests": manifests,
    }


def run_full_ingestion_audit(
    root: Path,
    config_path: Path,
    snapshot_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    config = _read_json(config_path)
    if config.get("schema_version") != "full-ingestion-validation-v1":
        raise ValueError("Unsupported full ingestion validation config")
    snapshot = _read_json(snapshot_path)
    report = validate_full_ingestion(root, config, snapshot)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
