from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

from jsonschema import Draft202012Validator, FormatChecker

from finance_tracker.cashback import load_program_configuration, validate_program_configuration


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "cashback-programs.json"
SCHEMA_PATH = ROOT / "config" / "cashback-profile-schema-v2.json"
LEGACY_SCHEMA_PATH = ROOT / "config" / "cashback-profile-schema-v1.json"
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
        for field in ("excluded_categories", "excluded_channels"):
            paths.update(
                f"buckets.{code}.{field}[{index}]"
                for index, value in enumerate(bucket.get(field, []))
                if value
            )
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
    if ".excluded_categories[" in path or ".excluded_channels[" in path:
        return "EXCLUSION"
    if path.startswith("tiers."):
        return "TIER"
    return "PROGRAMME"


def authoritative_claims(program: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": claim_kind(path),
            "path": path,
            "reference_ids": ["test-issuer-evidence"],
            "effective_start": "2026-08-01",
            "effective_end": None,
        }
        for path in sorted(authoritative_claim_paths(program))
    ]


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
                if reference["authority"] == "AUTHORITATIVE":
                    raise ValueError(f"Cashback program {card} authoritative evidence requires content")
                continue
            fixture_path = ROOT / fixture
            observed = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
            if observed != reference.get("sha256"):
                raise ValueError(
                    f"Cashback program {card} evidence digest drift for {reference['id']}: "
                    f"expected {reference.get('sha256')}, observed {observed}"
                )
    validate_program_configuration(source)


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
        program["buckets"][0]["excluded_categories"] = ["TEST_ONLY_EXCLUSION"]
        program["source_references"] = [authoritative_fixture_reference()]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises the authoritative provenance contract.",
            "claims": authoritative_claims(program),
        }

        validate_provenance(source)
        self.assertIn(
            "buckets.RAK_GROCERY.excluded_categories[0]",
            {claim["path"] for claim in program["provenance"]["claims"]},
        )

    def test_authoritative_claim_missing_path_is_rejected(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        program["source_references"] = [authoritative_fixture_reference()]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises incomplete claim coverage.",
            "claims": authoritative_claims(program)[1:],
        }

        with self.assertRaisesRegex(ValueError, "incomplete provenance claims"):
            validate_provenance(source)

    def test_authoritative_duplicate_claim_path_is_rejected(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        program["source_references"] = [authoritative_fixture_reference()]
        claims = authoritative_claims(program)
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises duplicate claim rejection.",
            "claims": claims + [copy.deepcopy(claims[0])],
        }

        with self.assertRaisesRegex(ValueError, "duplicate provenance claims"):
            validate_program_configuration(source)

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
            "claims": authoritative_claims(program),
        }

        with self.assertRaisesRegex(ValueError, "schema error"):
            validate_provenance(source)

    def test_reference_validation_precedes_claim_validation(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["authority"] = "INVALID"
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises reference-phase ordering.",
            "claims": [],
        }

        with self.assertRaisesRegex(ValueError, "invalid evidence authority"):
            validate_program_configuration(source)

    def test_claim_validation_precedes_fixture_digest_validation(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["sha256"] = "0" * 64
        program["source_references"] = [reference]
        claims = authoritative_claims(program)
        claims[0]["path"] = "unknown"
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises claim-phase ordering.",
            "claims": claims,
        }

        with self.assertRaisesRegex(ValueError, "incomplete provenance claims"):
            validate_program_configuration(source)

    def test_interval_validation_precedes_fixture_digest_validation(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["sha256"] = "0" * 64
        program["source_references"] = [reference]
        claims = authoritative_claims(program)
        claims[0]["effective_start"] = (
            datetime.now(timezone.utc).date() + timedelta(days=1)
        ).isoformat()
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises interval-phase ordering.",
            "claims": claims,
        }

        with self.assertRaisesRegex(ValueError, "exceeds the programme interval"):
            validate_program_configuration(source)

    def test_drifted_issuer_fixture_digest_is_rejected(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["sha256"] = "0" * 64
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises digest drift detection.",
            "claims": authoritative_claims(program),
        }

        with self.assertRaisesRegex(ValueError, "evidence digest drift"):
            validate_provenance(source)

    def test_v2_requires_provenance_but_v1_profiles_remain_compatible(self) -> None:
        source = copy.deepcopy(self.config)
        del source["programs"][0]["provenance"]
        with self.assertRaisesRegex(ValueError, "schema error"):
            validate_provenance(source)

        legacy = load_json(ROOT / "examples" / "cashback-profiles" / "flat-rate-usd.json")
        legacy_schema = load_json(LEGACY_SCHEMA_PATH)
        Draft202012Validator(legacy_schema, format_checker=FormatChecker()).validate(legacy)
        validate_program_configuration(legacy)

    def test_runtime_rejects_authoritative_programme_without_claims(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        program["source_references"] = [authoritative_fixture_reference()]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises runtime empty-claim rejection.",
            "claims": [],
        }

        with self.assertRaisesRegex(ValueError, "schema error"):
            validate_provenance(source)
        with self.assertRaisesRegex(ValueError, "requires authoritative provenance claims"):
            validate_program_configuration(source)

    def test_claim_interval_must_be_covered_by_issuer_interval(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["effective_start"] = date.today().isoformat()
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises non-overlapping evidence.",
            "claims": authoritative_claims(program),
        }

        with self.assertRaisesRegex(ValueError, "does not cover claim interval"):
            validate_program_configuration(source)

    def test_issuer_interval_must_cover_programme_end(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["effective_end"] = (date.today() - timedelta(days=1)).isoformat()
        program["source_references"] = [reference]
        claims = authoritative_claims(program)
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises evidence interval end coverage.",
            "claims": claims,
        }

        with self.assertRaisesRegex(ValueError, "does not cover claim interval"):
            validate_program_configuration(source)

    def test_open_current_program_rejects_claim_ending_before_today(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        program["source_references"] = [authoritative_fixture_reference()]
        claims = authoritative_claims(program)
        claims[0]["effective_end"] = (date.today() - timedelta(days=1)).isoformat()
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises exact current boundary coverage.",
            "claims": claims,
        }

        with self.assertRaisesRegex(ValueError, "does not span the programme interval"):
            validate_program_configuration(source)

    def test_open_current_program_rejects_future_claim(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        program["source_references"] = [authoritative_fixture_reference()]
        claims = authoritative_claims(program)
        claims[0]["effective_start"] = (date.today() + timedelta(days=1)).isoformat()
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises current programme boundary.",
            "claims": claims,
        }

        with self.assertRaisesRegex(ValueError, "exceeds the programme interval"):
            validate_program_configuration(source)

    def test_open_current_program_rejects_today_only_evidence(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["effective_start"] = date.today().isoformat()
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises future evidence boundary.",
            "claims": authoritative_claims(program),
        }

        with self.assertRaisesRegex(ValueError, "does not cover claim interval"):
            validate_program_configuration(source)

    def test_open_current_program_rejects_future_evidence(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["effective_start"] = (date.today() + timedelta(days=1)).isoformat()
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises future evidence coverage.",
            "claims": authoritative_claims(program),
        }

        with self.assertRaisesRegex(ValueError, "does not cover claim interval"):
            validate_program_configuration(source)

    def test_pre_dating_evidence_may_cover_the_programme(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        reference["effective_start"] = "2026-07-01"
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises pre-dating issuer coverage.",
            "claims": authoritative_claims(program),
        }

        validate_program_configuration(source)

    def test_configured_programme_boundary_requires_exact_claim_end(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        programme_end = (date.today() + timedelta(days=7)).isoformat()
        program["effective_end"] = programme_end
        reference = authoritative_fixture_reference()
        reference["effective_start"] = "2026-07-01"
        reference["effective_end"] = programme_end
        program["source_references"] = [reference]
        claims = authoritative_claims(program)
        for claim in claims:
            claim["effective_end"] = programme_end
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises exact programme interval coverage.",
            "claims": claims,
        }

        validate_program_configuration(source)

    def test_authoritative_reference_without_fixture_content_is_rejected(self) -> None:
        source = copy.deepcopy(self.config)
        program = source["programs"][0]
        reference = authoritative_fixture_reference()
        del reference["fixture"]
        program["source_references"] = [reference]
        program["provenance"] = {
            "authority": "AUTHORITATIVE",
            "reason": "Test-only fixture exercises content readback requirement.",
            "claims": authoritative_claims(program),
        }

        with self.assertRaisesRegex(ValueError, "schema error"):
            validate_provenance(source)

    def test_loader_validates_versioned_schema_and_preserves_v1_compatibility(self) -> None:
        legacy = load_program_configuration(ROOT / "examples" / "cashback-profiles" / "flat-rate-usd.json")
        self.assertEqual(legacy["schema_version"], 1)

        for field in ("label", "url"):
            source = copy.deepcopy(self.config)
            reference = authoritative_fixture_reference()
            del reference[field]
            source["programs"][0]["source_references"] = [reference]
            source["programs"][0]["provenance"] = {
                "authority": "AUTHORITATIVE",
                "reason": "Test-only fixture exercises loader evidence metadata validation.",
                "claims": authoritative_claims(source["programs"][0]),
            }
            with self.subTest(missing=field), TemporaryDirectory() as directory:
                invalid_path = Path(directory) / "invalid-profile.json"
                invalid_path.write_text(json.dumps(source), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, r"schema error at programs\.0\.source_references\.0"):
                    load_program_configuration(invalid_path)


if __name__ == "__main__":
    import unittest

    unittest.main()
