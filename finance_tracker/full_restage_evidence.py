from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _transaction_ids(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if row.get("transaction_id"):
        values.append(str(row["transaction_id"]))
    values.extend(str(value) for value in (row.get("transaction_ids") or []) if value)
    return list(dict.fromkeys(values))


def build_evidence_links(
    manifests_root: str | Path,
    catalogue_path: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    manifests_root = Path(manifests_root).resolve()
    output_root = Path(output_root).resolve()
    catalogue = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    if not isinstance(catalogue, list):
        raise ValueError("Evidence catalogue must be an array")

    source_by_transaction: dict[str, str] = {}
    source_names: list[str] = []
    for manifest_path in sorted(manifests_root.glob("*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_names.append(manifest_path.stem)
        for envelope in manifest.get("envelopes") or []:
            for record in envelope.get("records") or []:
                transaction_id = str(record.get("imported_id") or "")
                if not transaction_id:
                    raise ValueError(f"Manifest record lacks imported_id: {manifest_path}")
                previous = source_by_transaction.setdefault(transaction_id, manifest_path.stem)
                if previous != manifest_path.stem:
                    raise ValueError(f"Transaction appears in multiple sources: {transaction_id}")

    links_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmatched: list[str] = []
    identities: set[tuple[str, str]] = set()
    for row in catalogue:
        if str(row.get("document_type") or "") == "statement":
            continue
        evidence_id = str(row.get("evidence_id") or "")
        relative_path = str(row.get("relative_path") or "")
        document_type = str(row.get("document_type") or "")
        for transaction_id in _transaction_ids(row):
            source = source_by_transaction.get(transaction_id)
            if source is None:
                unmatched.append(transaction_id)
                continue
            identity = (transaction_id, evidence_id)
            if identity in identities:
                continue
            identities.add(identity)
            links_by_source[source].append(
                {
                    "transaction_id": transaction_id,
                    "evidence_id": evidence_id,
                    "relative_path": relative_path,
                    "document_type": document_type,
                }
            )

    output_root.mkdir(parents=True, exist_ok=True)
    for source in source_names:
        links = sorted(
            links_by_source[source],
            key=lambda item: (item["transaction_id"], item["evidence_id"]),
        )
        (output_root / f"{source}.json").write_text(
            json.dumps(links, indent=2) + "\n", encoding="utf-8"
        )
    return {
        "schema_version": "full-restage-evidence-build-v1",
        "manifest_count": len(source_names),
        "link_count": len(identities),
        "unmatched_transaction_count": len(set(unmatched)),
        "unmatched_transaction_ids": sorted(set(unmatched)),
        "by_source": {
            source: len(links_by_source[source]) for source in sorted(source_names)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact per-source evidence links")
    parser.add_argument("--manifests-root", required=True)
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    report = build_evidence_links(args.manifests_root, args.catalogue, args.output_root)
    payload = json.dumps(report, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
