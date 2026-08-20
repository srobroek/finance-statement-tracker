from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest import TestCase

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "cashback-programs.json"
SCHEMA_PATH = ROOT / "config" / "cashback-profile-schema-v1.json"
FIXTURE_PATH = "tests/fixtures/cashback-provenance/issuer-evidence.txt"
FIXTURE_SHA256 = "d5da1c52b660e399c37e5e8a7faf353cf63bef3963cdd49c0c87b9ea2d32e56d"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def authoritative_claim_paths(program: dict[str, Any]) -> set[str]:
    paths = {"programme"}
    for tier in program.get("tiers", []):
        code = str(tier["code"])
        paths.add(f"tiers.{code}")
        for bucket in (tier.get("rates") or {}):
            paths.add(f"tiers.{code}.rates.{bucket}")
        for bucket, value in (tier.get("cashback_caps_aed") or {}).items():
            if value is not None:
                paths.add(f"tiers.{code}.cashback_caps_aed.{bucket}")
    for bucket in program.get("buckets", []):
        code = str(bucket["code"])
        for field in ("cashback_cap", "cashback_cap_aed", "spend_cap", "spend_cap_aed"):
            if field in bucket and bucket[field] is not None:
                paths.add(f"buckets.{code}.{field}")
    paths.update(
        f"exclusions[{index}]" for index, value in enumerate(program.get("exclusions", [])) if value
    )
    return paths


def claim_kind(path: str) -> str:
    if ".rates." in path:
        return "RATE"
    if ".cashback_caps_aed." in path or path.rsplit(".", 1)[-1] in {
        "cashback_cap",
        "cashback_cap_aed",
        "spend_cap",
        "spend_cap_aed",
    }:
        return "CAP"
    if path.startswith("exclusions["):
        return "EXCLUSION"
    if path.startswith("tiers."):
        return "TIER"
    return "PROGRAMME"


def validate_provenance(source: dict[str, Any]) -> None:
    schema = load_json(SCHEMA_PATH)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(source), key=lambda error: list(error.absolute_path))
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "$"
        raise ValueError(f"cashback profile schema error at {location}: {errors[0].message}")

    for program in source["programs"]:
        card = str(program["card"])
        provenance = program.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"Cashback program {card} is missing provenance")
        references = program.get("source_references") or []
        references_by_id = {reference["id"]: reference for reference in references}
        if len(references_by_id) != len(references):
            raise ValueError(f"Cashback program {card} contains duplicate provenance reference ids")
        claims = provenance["claims"]
        for claim in claims:
            missing = set(claim["reference_ids"]) - set(references_by_id)
            if missing:
                raise ValueError(
                    f"Cashback program {card} claim {claim['path']} references unknown evidence: "
                    + ", ".join(sorted(missing))
                )
            if claim["kind"] != claim_kind(claim["path"]):
                raise ValueError(
                    f"Cashback program {card} claim {claim['path']} has an invalid kind"
                )
        if provenance["authority"] == "AUTHORITATIVE":
            expected_paths = authoritative_claim_paths(program)
            actual_paths = {claim["path"] for claim in claims}
            if actual_paths != expected_paths:
                missing = ", ".join(sorted(expected_paths - actual_paths))
                extra = ", ".join(sorted(actual_paths - expected_paths))
                raise ValueError(
                    f"Cashback program {card} has incomplete provenance claims"
                    + (f"; missing={missing}" if missing else "")
                    + (f"; extra={extra}" if extra else "")
                )
            for claim in claims:
                for reference_id in claim["reference_ids"]:
                    reference = references_by_id[reference_id]
                    if reference["authority"] != "AUTHORITATIVE":
                        raise ValueError(
                            f"Cashback program {card} claim {claim['path']} uses non-authoritative evidence"
                        )
        for reference in references:
            if reference["authority"] != "AUTHORITATIVE":
                continue
            if reference["effective_start"] is None:
                raise ValueError(f"Cashback program {card} authoritative evidence is undated")
            if reference["effective_end"] and reference["effective_end"] < reference["effective_start"]:
                raise ValueError(f"Cashback program {card} authoritative evidence has an invalid date range")
        for reference in references:
            fixture = reference.get("fixture")
            if not fixture:
                continue
            fixture_path = ROOT / fixture
            observed = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
            if observed != reference.get("sha256"):
                raise ValueError(
                    f"Cashback program {card} evidence digest drift for {reference['id']}: "
                    f"expected {reference.get('sha256')}, observed {observed}"
                )


def authoritative_fixture_reference() -> dict[str, Any]:
    return {
        "id": "test-issuer-evidence",
        "label": "Redacted test-only issuer evidence",
        "url": "https://issuer.example.invalid/test-only-cashback-evidence.txt",
        "effective_start": "2026-08-01",
        "effective_end": None,
        "authority": "AUTHORITATIVE",
        "sha256": FIXTURE_SHA256,
        "fixture": FIXTURE_PATH,
    }


class CashbackProgrammeProvenanceTests(TestCase):
    def setUp(self) -> None:
        self.config = load_json(CONFIG_PATH)

    def test_seed_config_is_schema_valid_and_explicitly_non_authoritative(self) -> None:
        validate_provenance(self.config)
        for program in self.config["programs"]:
            self.assertEqual(program["provenance"]["authority"], "NON_AUTHORITATIVE")
            self.assertEqual(program["provenance"]["claims"], [])
            self.assertTrue(program["provenance"]["reason"])
            self.assertTrue(
                all(reference["authority"] == "NON_AUTHORITATIVE" for reference in program.get("source_references", []))
            )

    def test_authoritative_claims_cover_rates_caps_exclusions_and_tiers(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        program["source_references"] = [authoritative_fixture_reference()]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises the authoritative provenance contract.",
            "claims": [
                {"kind": claim_kind(path), "path": path, "reference_ids": ["test-issuer-evidence"]}
                for path in sorted(authoritative_claim_paths(program))
            ],
        }

        validate_provenance(source)

    def test_authoritative_claim_missing_path_is_rejected(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        program["source_references"] = [authoritative_fixture_reference()]
        expected_paths = sorted(authoritative_claim_paths(program))
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises incomplete claim coverage.",
            "claims": [
                {"kind": claim_kind(path), "path": path, "reference_ids": ["test-issuer-evidence"]}
                for path in expected_paths[1:]
            ],
        }

        with self.assertRaisesRegex(ValueError, "incomplete provenance claims"):
            validate_provenance(source)

    def test_authoritative_reference_requires_sha256_and_effective_start(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        del reference["sha256"]
        del reference["effective_start"]
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises missing evidence metadata.",
            "claims": [
                {"kind": claim_kind(path), "path": path, "reference_ids": ["test-issuer-evidence"]}
                for path in sorted(authoritative_claim_paths(program))
            ],
        }

        with self.assertRaisesRegex(ValueError, "schema error"):
            validate_provenance(source)

    def test_drifted_issuer_fixture_digest_is_rejected(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["sha256"] = "0" * 64
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises digest drift detection.",
            "claims": [
                {"kind": claim_kind(path), "path": path, "reference_ids": ["test-issuer-evidence"]}
                for path in sorted(authoritative_claim_paths(program))
            ],
        }

        with self.assertRaisesRegex(ValueError, "evidence digest drift"):
            validate_provenance(source)


if __name__ == "__main__":
    import unittest

    unittest.main()
