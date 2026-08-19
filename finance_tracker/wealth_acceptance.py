"""Read-only acceptance checks for cash, wealth, FX, and Actual evidence bundles.

This module deliberately has no Actual or remote-service client.  It verifies
immutable artifacts and readback receipts only; passing a disposable bundle is
not authority to write production data.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping


REQUIREMENTS = (
    "fab-complete-inventory",
    "sarwa-wealth-accounts",
    "adcb-closed-zero",
    "actual-ui-api-parity",
    "wealth-net-worth",
)
_ALL_REQUIREMENTS = frozenset(REQUIREMENTS)
_STABLE_ACCOUNT_ID = re.compile(r"^[a-z0-9]+(?::[a-z0-9-]+)+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_REQUIREMENTS = {
    "FAB_ACCOUNT_INVENTORY": frozenset(("fab-complete-inventory", "wealth-net-worth")),
    "SARWA_WEALTH_SNAPSHOT": frozenset(("sarwa-wealth-accounts", "wealth-net-worth")),
    "FX_SNAPSHOT": frozenset(("sarwa-wealth-accounts", "wealth-net-worth")),
    "ADCB_ZERO_EVIDENCE": frozenset(("adcb-closed-zero",)),
    "ACTUAL_API_READBACK": _ALL_REQUIREMENTS,
    "ACTUAL_UI_READBACK": _ALL_REQUIREMENTS,
}


def _datetime(value: Any, context: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be an ISO datetime") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decimal(value: Any, context: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{context} must be a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{context} must be finite")
    return result


def _minor(value: Decimal, rate: Decimal) -> int:
    return int((value * rate * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_artifact_path(base_dir: Path, value: Any, context: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} is required")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError(f"{context} must be relative to the evidence bundle")
    resolved_base = base_dir.resolve()
    resolved = (resolved_base / candidate).resolve()
    if resolved != resolved_base and resolved_base not in resolved.parents:
        raise ValueError(f"{context} escapes the evidence bundle directory")
    return resolved


def load_wealth_acceptance_bundle(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Wealth acceptance evidence bundle must be an object")
    return payload


class _Validator:
    def __init__(
        self,
        payload: Mapping[str, Any],
        base_dir: Path,
        requirement_id: str | None,
    ) -> None:
        self.payload = dict(payload)
        self.base_dir = base_dir
        self.requirement_id = requirement_id
        self.issues: list[dict[str, str]] = []
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.references: dict[str, dict[str, Any]] = {}

    def issue(self, requirements: set[str] | frozenset[str], code: str, message: str) -> None:
        for requirement in sorted(requirements):
            if self.requirement_id is None or self.requirement_id == requirement:
                self.issues.append({
                    "requirement_id": requirement,
                    "code": code,
                    "message": message,
                })

    def _requirements_for_kind(self, kind: Any) -> frozenset[str]:
        return _ARTIFACT_REQUIREMENTS.get(str(kind), _ALL_REQUIREMENTS)

    def load_artifacts(self) -> None:
        raw = self.payload.get("artifacts")
        if not isinstance(raw, list) or not raw:
            self.issue(_ALL_REQUIREMENTS, "ARTIFACTS_REQUIRED", "artifacts must be a non-empty array")
            return
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                self.issue(_ALL_REQUIREMENTS, "ARTIFACT_REFERENCE_INVALID", f"artifacts[{index}] is not an object")
                continue
            reference = dict(item)
            identity = str(reference.get("id") or "").strip()
            kind = str(reference.get("kind") or "").strip()
            requirements = self._requirements_for_kind(kind)
            if not identity or identity in self.references:
                self.issue(requirements, "ARTIFACT_ID_INVALID", f"artifact id is missing or duplicated: {identity}")
                continue
            self.references[identity] = reference
            if kind not in _ARTIFACT_REQUIREMENTS:
                self.issue(requirements, "ARTIFACT_KIND_INVALID", f"{identity}: unsupported artifact kind {kind}")
            if reference.get("non_empty") is not True:
                self.issue(requirements, "ARTIFACT_EMPTY", f"{identity}: artifact is not marked non-empty")
            if not str(reference.get("reviewer") or "").strip():
                self.issue(requirements, "ARTIFACT_REVIEW_REQUIRED", f"{identity}: reviewer is required")
            try:
                _datetime(reference.get("observed_at"), f"{identity}.observed_at")
            except ValueError as exc:
                self.issue(requirements, "ARTIFACT_OBSERVED_AT_INVALID", str(exc))
            try:
                source_path = _safe_artifact_path(
                    self.base_dir, reference.get("source_path"), f"{identity}.source_path"
                )
                archive_path = _safe_artifact_path(
                    self.base_dir, reference.get("archive_path"), f"{identity}.archive_path"
                )
            except ValueError as exc:
                self.issue(requirements, "ARTIFACT_PATH_INVALID", str(exc))
                continue
            source_bytes: bytes | None = None
            archive_bytes: bytes | None = None
            try:
                source_bytes = source_path.read_bytes()
                if not source_bytes:
                    raise ValueError("source is empty")
            except (OSError, ValueError) as exc:
                self.issue(requirements, "SOURCE_ARTIFACT_UNREADABLE", f"{identity}: {exc}")
            try:
                archive_bytes = archive_path.read_bytes()
                if not archive_bytes:
                    raise ValueError("archive is empty")
            except (OSError, ValueError) as exc:
                self.issue(requirements, "ARCHIVE_ARTIFACT_UNREADABLE", f"{identity}: {exc}")
            expected_source = str(reference.get("source_sha256") or "")
            expected_archive = str(reference.get("archive_sha256") or "")
            if not _SHA256.fullmatch(expected_source):
                self.issue(requirements, "SOURCE_SHA256_INVALID", f"{identity}: source SHA-256 is invalid")
            if not _SHA256.fullmatch(expected_archive):
                self.issue(requirements, "ARCHIVE_SHA256_INVALID", f"{identity}: archive SHA-256 is invalid")
            if source_bytes is not None and _sha256_bytes(source_bytes) != expected_source:
                self.issue(requirements, "SOURCE_SHA256_MISMATCH", f"{identity}: source SHA-256 does not match")
            if archive_bytes is not None and _sha256_bytes(archive_bytes) != expected_archive:
                self.issue(requirements, "ARCHIVE_SHA256_MISMATCH", f"{identity}: archive SHA-256 does not match")
            if expected_source and expected_archive and expected_source != expected_archive:
                self.issue(requirements, "ARCHIVE_SOURCE_HASH_MISMATCH", f"{identity}: archive is not byte-identical to source")
            if source_bytes is None:
                continue
            try:
                parsed = json.loads(source_bytes)
            except json.JSONDecodeError as exc:
                self.issue(requirements, "SOURCE_ARTIFACT_JSON_INVALID", f"{identity}: {exc}")
                continue
            if not isinstance(parsed, dict):
                self.issue(requirements, "SOURCE_ARTIFACT_INVALID", f"{identity}: source must be an object")
                continue
            self.artifacts[identity] = parsed

    def artifact(self, policy_key: str, kind: str, requirements: set[str]) -> dict[str, Any] | None:
        policy = self.payload.get("policy")
        if not isinstance(policy, Mapping):
            self.issue(requirements, "POLICY_REQUIRED", "policy must be an object")
            return None
        identity = str(policy.get(policy_key) or "").strip()
        reference = self.references.get(identity)
        artifact = self.artifacts.get(identity)
        if not identity or reference is None or artifact is None:
            self.issue(requirements, "REQUIRED_ARTIFACT_MISSING", f"{policy_key}: referenced artifact is unavailable")
            return None
        if reference.get("kind") != kind:
            self.issue(requirements, "ARTIFACT_KIND_MISMATCH", f"{identity}: expected {kind}")
            return None
        return artifact

    def _policy_ids(self, key: str, requirements: set[str]) -> set[str]:
        policy = self.payload.get("policy")
        values = policy.get(key) if isinstance(policy, Mapping) else None
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            self.issue(requirements, "POLICY_ACCOUNT_IDS_INVALID", f"policy.{key} must be an array of stable IDs")
            return set()
        result = set(values)
        if len(result) != len(values) or any(not _STABLE_ACCOUNT_ID.fullmatch(value) for value in result):
            self.issue(requirements, "POLICY_ACCOUNT_IDS_INVALID", f"policy.{key} contains duplicate or unsafe IDs")
        return result

    def validate_fab(self, api: dict[str, Any] | None) -> None:
        requirements = {"fab-complete-inventory", "wealth-net-worth"}
        fab = self.artifact("fab_inventory_artifact_id", "FAB_ACCOUNT_INVENTORY", requirements)
        if fab is None:
            return
        if fab.get("provider_id") != "fab" or fab.get("schema_version") != 1:
            self.issue(requirements, "FAB_INVENTORY_INVALID", "FAB inventory provider/schema is invalid")
        if fab.get("inventory_complete") is not True:
            self.issue(requirements, "FAB_INVENTORY_INCOMPLETE", "FAB portal inventory is not declared complete")
        accounts = fab.get("accounts")
        if not isinstance(accounts, list):
            self.issue(requirements, "FAB_ACCOUNTS_INVALID", "FAB accounts must be an array")
            return
        expected = self._policy_ids("expected_fab_account_ids", requirements)
        observed = [str(row.get("provider_account_id") or "") for row in accounts if isinstance(row, Mapping)]
        if len(observed) != len(set(observed)) or set(observed) != expected:
            self.issue(requirements, "FAB_ACCOUNT_SET_MISMATCH", "FAB observed account set does not equal the reviewed inventory")
        if fab.get("expected_account_count") != len(accounts):
            self.issue(requirements, "FAB_ACCOUNT_COUNT_MISMATCH", "FAB expected_account_count does not match accounts")
        api_accounts = _account_map(api, self.issue, requirements, "API") if api else {}
        for index, row in enumerate(accounts):
            if not isinstance(row, Mapping):
                self.issue(requirements, "FAB_ACCOUNT_INVALID", f"FAB accounts[{index}] is invalid")
                continue
            identity = str(row.get("provider_account_id") or "")
            if not _STABLE_ACCOUNT_ID.fullmatch(identity):
                self.issue(requirements, "STABLE_ACCOUNT_IDENTITY_INVALID", f"FAB account ID is unsafe: {identity}")
            if not isinstance(row.get("balance_minor"), int):
                self.issue(requirements, "FAB_BALANCE_INVALID", f"{identity}: balance_minor must be an integer")
            try:
                _datetime(row.get("as_of"), f"{identity}.as_of")
            except ValueError as exc:
                self.issue(requirements, "FAB_AS_OF_INVALID", str(exc))
            actual = api_accounts.get(identity)
            if actual is None or actual.get("balance_minor") != row.get("balance_minor"):
                self.issue(requirements, "FAB_ACTUAL_BALANCE_MISMATCH", f"{identity}: source and Actual balances differ")

    def validate_sarwa(self, api: dict[str, Any] | None) -> None:
        requirements = {"sarwa-wealth-accounts", "wealth-net-worth"}
        policy = self.payload.get("policy")
        if not isinstance(policy, Mapping):
            self.issue(requirements, "POLICY_REQUIRED", "policy must be an object")
            return
        snapshot_keys = policy.get("sarwa_snapshot_artifact_ids")
        fx_keys = policy.get("fx_snapshot_artifact_ids")
        if not isinstance(snapshot_keys, list) or len(snapshot_keys) != 2:
            self.issue(requirements, "SARWA_T1_T2_REQUIRED", "Exactly two ordered Sarwa snapshot artifact IDs are required")
            return
        if not isinstance(fx_keys, list) or len(fx_keys) != 2:
            self.issue(requirements, "FX_T1_T2_REQUIRED", "Exactly two ordered FX artifact IDs are required")
            return
        snapshots: list[dict[str, Any]] = []
        fx_rows: list[dict[str, Any]] = []
        for identity in snapshot_keys:
            reference = self.references.get(str(identity))
            artifact = self.artifacts.get(str(identity))
            if not reference or reference.get("kind") != "SARWA_WEALTH_SNAPSHOT" or artifact is None:
                self.issue(requirements, "REQUIRED_ARTIFACT_MISSING", f"Sarwa snapshot unavailable: {identity}")
            else:
                snapshots.append(artifact)
        for identity in fx_keys:
            reference = self.references.get(str(identity))
            artifact = self.artifacts.get(str(identity))
            if not reference or reference.get("kind") != "FX_SNAPSHOT" or artifact is None:
                self.issue(requirements, "REQUIRED_ARTIFACT_MISSING", f"FX snapshot unavailable: {identity}")
            else:
                fx_rows.append(artifact)
        if len(snapshots) != 2 or len(fx_rows) != 2:
            return
        included = self._policy_ids("expected_sarwa_account_ids", requirements)
        excluded = self._policy_ids("excluded_sarwa_account_ids", requirements)
        aggregate_only = self._policy_ids("aggregate_only_sarwa_account_ids", requirements)
        all_expected = included | excluded
        evaluated_at = _try_datetime(policy.get("evaluated_at"), "policy.evaluated_at", self.issue, requirements)
        stale_after = policy.get("wealth_stale_after_seconds")
        if not isinstance(stale_after, int) or stale_after <= 0:
            self.issue(requirements, "WEALTH_STALENESS_POLICY_INVALID", "wealth_stale_after_seconds must be positive")
            stale_after = 0
        snapshot_portfolios: list[dict[str, Mapping[str, Any]]] = []
        snapshot_targets: list[dict[str, int]] = []
        snapshot_ids: list[str] = []
        for sequence, (snapshot, fx) in enumerate(zip(snapshots, fx_rows), start=1):
            snapshot_id = str(snapshot.get("snapshot_id") or "")
            snapshot_ids.append(snapshot_id)
            if snapshot.get("schema_version") != 1 or snapshot.get("provider_id") != "sarwa":
                self.issue(requirements, "SARWA_SNAPSHOT_INVALID", f"T{sequence}: provider/schema is invalid")
            portfolios = snapshot.get("portfolios")
            if not isinstance(portfolios, list):
                self.issue(requirements, "SARWA_PORTFOLIOS_INVALID", f"T{sequence}: portfolios must be an array")
                continue
            by_id: dict[str, Mapping[str, Any]] = {}
            for row in portfolios:
                if not isinstance(row, Mapping):
                    continue
                identity = str(row.get("provider_account_id") or "")
                if identity in by_id or not _STABLE_ACCOUNT_ID.fullmatch(identity):
                    self.issue(requirements, "STABLE_ACCOUNT_IDENTITY_INVALID", f"T{sequence}: duplicate or unsafe ID {identity}")
                by_id[identity] = row
            if set(by_id) != all_expected:
                self.issue(requirements, "STABLE_ACCOUNT_IDENTITY_MISMATCH", f"T{sequence}: portfolio IDs differ from reviewed identities")
            for identity in included:
                row = by_id.get(identity)
                if row is None or row.get("closed") is not False or row.get("include_in_net_worth") is not True:
                    self.issue(requirements, "SARWA_INCLUDED_ACCOUNT_INVALID", f"T{sequence}: included account invalid {identity}")
            for identity in excluded:
                row = by_id.get(identity)
                if row is None or row.get("closed") is not True or row.get("include_in_net_worth") is not False:
                    self.issue(requirements, "SARWA_EXCLUDED_ACCOUNT_INVALID", f"T{sequence}: excluded account invalid {identity}")
            try:
                aggregate = sum((_decimal(row.get("total_value"), "portfolio.total_value") for row in by_id.values()), Decimal("0"))
                declared = _decimal(snapshot.get("total_value"), "snapshot.total_value")
                if aggregate != declared:
                    self.issue(requirements, "PORTFOLIO_TOTAL_MISMATCH", f"T{sequence}: portfolios do not equal snapshot total")
            except ValueError as exc:
                self.issue(requirements, "SARWA_VALUE_INVALID", f"T{sequence}: {exc}")
            for identity, row in by_id.items():
                if row.get("components_available") is False:
                    if identity not in aggregate_only:
                        self.issue(requirements, "POSITION_COMPONENTS_UNAVAILABLE", f"T{sequence}: {identity} lacks components without reviewed policy")
                    continue
                try:
                    positions = row.get("positions")
                    if not isinstance(positions, list):
                        raise ValueError("positions must be an array")
                    positions_total = sum(
                        (_decimal(position.get("market_value"), "position.market_value") for position in positions if isinstance(position, Mapping)),
                        Decimal("0"),
                    )
                    cash = _decimal(row.get("cash_value"), "portfolio.cash_value")
                    total = _decimal(row.get("total_value"), "portfolio.total_value")
                    tolerance = _decimal(policy.get("position_tolerance"), "policy.position_tolerance")
                    if abs(positions_total + cash - total) > tolerance:
                        self.issue(requirements, "POSITION_TOTAL_MISMATCH", f"T{sequence}: positions plus cash differ for {identity}")
                except ValueError as exc:
                    self.issue(requirements, "POSITION_VALUE_INVALID", f"T{sequence}: {identity}: {exc}")
            as_of = _try_datetime(snapshot.get("as_of"), f"snapshot T{sequence}.as_of", self.issue, requirements)
            if as_of and evaluated_at and stale_after:
                age = (evaluated_at - as_of).total_seconds()
                if age < 0:
                    self.issue(requirements, "WEALTH_AS_OF_IN_FUTURE", f"T{sequence}: wealth snapshot is in the future")
                elif age > stale_after:
                    self.issue(requirements, "WEALTH_SNAPSHOT_STALE", f"T{sequence}: wealth snapshot is stale")
            rate: Decimal | None = None
            try:
                rate = _decimal(fx.get("rate"), "fx.rate")
                if rate <= 0:
                    raise ValueError("fx.rate must be positive")
            except ValueError as exc:
                self.issue(requirements, "FX_RATE_INVALID", f"T{sequence}: {exc}")
            if fx.get("base_currency") != snapshot.get("currency") or fx.get("quote_currency") != "AED":
                self.issue(requirements, "FX_PAIR_MISMATCH", f"T{sequence}: FX pair does not match wealth currency/AED")
            observed_at = _try_datetime(fx.get("observed_at"), f"fx T{sequence}.observed_at", self.issue, requirements)
            max_age = fx.get("max_age_seconds")
            if not isinstance(max_age, int) or max_age <= 0:
                self.issue(requirements, "FX_STALENESS_POLICY_INVALID", f"T{sequence}: max_age_seconds must be positive")
            elif as_of and observed_at:
                age = (as_of - observed_at).total_seconds()
                if age < 0:
                    self.issue(requirements, "FX_AS_OF_IN_FUTURE", f"T{sequence}: FX observation occurs after wealth as-of")
                elif age > max_age:
                    self.issue(requirements, "FX_SNAPSHOT_STALE", f"T{sequence}: FX snapshot is stale for wealth as-of")
            targets: dict[str, int] = {}
            if rate is not None:
                for identity in included:
                    row = by_id.get(identity)
                    if row is not None:
                        try:
                            targets[identity] = _minor(_decimal(row.get("total_value"), "portfolio.total_value"), rate)
                        except ValueError as exc:
                            self.issue(requirements, "SARWA_VALUE_INVALID", f"T{sequence}: {exc}")
            snapshot_targets.append(targets)
            snapshot_portfolios.append(by_id)
        if len(snapshot_portfolios) == 2 and set(snapshot_portfolios[0]) != set(snapshot_portfolios[1]):
            self.issue(requirements, "STABLE_ACCOUNT_IDENTITY_MISMATCH", "T1 and T2 portfolio identities differ")
        if len(set(snapshot_ids)) != 2 or any(not value for value in snapshot_ids):
            self.issue(requirements, "SNAPSHOT_IDENTITY_INVALID", "T1 and T2 require distinct snapshot IDs")
        if api is not None and len(snapshot_targets) == 2:
            self._validate_valuations(api, snapshot_ids, snapshot_targets, included, excluded, requirements)

    def _validate_valuations(
        self,
        api: Mapping[str, Any],
        snapshot_ids: list[str],
        targets: list[dict[str, int]],
        included: set[str],
        excluded: set[str],
        requirements: set[str],
    ) -> None:
        valuations = api.get("valuations")
        if not isinstance(valuations, list):
            self.issue(requirements, "VALUATION_EVIDENCE_REQUIRED", "Actual valuation readback is missing")
            return
        imported_ids: set[str] = set()
        by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in valuations:
            if not isinstance(row, Mapping):
                continue
            key = (str(row.get("snapshot_id") or ""), str(row.get("provider_account_id") or ""))
            if key in by_key:
                self.issue(requirements, "VALUATION_DUPLICATE", f"Duplicate valuation {key}")
            by_key[key] = row
            imported_id = str(row.get("imported_id") or "")
            if not imported_id or imported_id in imported_ids:
                self.issue(requirements, "VALUATION_IMPORTED_ID_INVALID", f"Missing or duplicate valuation imported_id {imported_id}")
            imported_ids.add(imported_id)
        initial = self.payload.get("policy", {}).get("initial_observed_balances_minor", {})
        if not isinstance(initial, Mapping):
            initial = {}
            self.issue(requirements, "INITIAL_BALANCE_POLICY_INVALID", "initial_observed_balances_minor must be an object")
        for sequence, snapshot_id in enumerate(snapshot_ids):
            for identity in included:
                row = by_key.get((snapshot_id, identity))
                if row is None:
                    self.issue(requirements, "VALUATION_EVIDENCE_REQUIRED", f"Missing T{sequence + 1} valuation for {identity}")
                    continue
                expected_before = initial.get(identity) if sequence == 0 else targets[0].get(identity)
                expected_target = targets[sequence].get(identity)
                if row.get("observed_before_minor") != expected_before:
                    self.issue(requirements, "VALUATION_OBSERVED_BALANCE_MISMATCH", f"T{sequence + 1}: observed balance mismatch for {identity}")
                if row.get("target_balance_minor") != expected_target:
                    self.issue(requirements, "VALUATION_TARGET_MISMATCH", f"T{sequence + 1}: target mismatch for {identity}")
                if (
                    isinstance(expected_before, int)
                    and isinstance(expected_target, int)
                    and row.get("delta_minor") != expected_target - expected_before
                ):
                    self.issue(requirements, "VALUATION_DELTA_MISMATCH", f"T{sequence + 1}: valuation is not target minus observed for {identity}")
        accounts = _account_map(api, self.issue, requirements, "API")
        actual_sarwa = {
            identity for identity in accounts
            if identity.startswith("sarwa:")
        }
        if actual_sarwa != included:
            self.issue(
                requirements,
                "SARWA_ACTUAL_ACCOUNT_SET_MISMATCH",
                "Actual Sarwa account set does not equal the reviewed included portfolios",
            )
        for identity in included:
            row = accounts.get(identity)
            if row is None or row.get("offbudget") is not True or row.get("closed") is not False:
                self.issue(requirements, "SARWA_ACTUAL_ACCOUNT_INVALID", f"Actual off-budget account is missing/invalid: {identity}")
            elif row.get("balance_minor") != targets[1].get(identity):
                self.issue(requirements, "SARWA_ACTUAL_BALANCE_MISMATCH", f"Actual T2 balance differs: {identity}")
        for identity in excluded:
            if identity in accounts:
                self.issue(requirements, "SARWA_EXCLUDED_ACCOUNT_PRESENT", f"Excluded Sarwa account exists in Actual: {identity}")
        replay = api.get("replay")
        if not isinstance(replay, Mapping):
            self.issue(requirements, "VALUATION_REPLAY_EVIDENCE_REQUIRED", "Actual replay hashes are missing")
        else:
            for stage in ("t1", "t2"):
                state = str(replay.get(f"{stage}_state_hash") or "")
                replayed = str(replay.get(f"{stage}_replay_state_hash") or "")
                if not _SHA256.fullmatch(state) or not _SHA256.fullmatch(replayed) or state != replayed:
                    self.issue(requirements, "VALUATION_REPLAY_DRIFT", f"{stage.upper()} replay state is not idempotent")

    def validate_adcb(self, api: dict[str, Any] | None) -> None:
        requirements = {"adcb-closed-zero"}
        evidence = self.artifact("adcb_zero_artifact_id", "ADCB_ZERO_EVIDENCE", requirements)
        if evidence is None:
            return
        account_id = str(self.payload.get("policy", {}).get("adcb_account_id") or "")
        if evidence.get("account_id") != account_id:
            self.issue(requirements, "ADCB_ACCOUNT_ID_MISMATCH", "ADCB evidence is for a different account")
        if evidence.get("synthetic_adjustment") is not False:
            self.issue(requirements, "ADCB_SYNTHETIC_BALANCING_PROHIBITED", "ADCB closure cannot use a synthetic balancing row")
        if evidence.get("closing_balance_minor") != 0:
            self.issue(requirements, "ADCB_CLOSING_BALANCE_NOT_ZERO", "ADCB closing evidence is not exactly zero")
        if evidence.get("evidence_type") not in {"ISSUER_CLOSING_STATEMENT", "ISSUER_PORTAL_CLOSURE_CONFIRMATION"}:
            self.issue(requirements, "ADCB_ISSUER_EVIDENCE_REQUIRED", "ADCB zero requires issuer evidence")
        if evidence.get("retain_history") is not True:
            self.issue(requirements, "ADCB_HISTORY_RETENTION_REQUIRED", "ADCB history retention is not evidenced")
        chain = evidence.get("statement_chain")
        if not isinstance(chain, list) or not chain:
            self.issue(requirements, "ADCB_STATEMENT_CHAIN_REQUIRED", "ADCB statement chain is missing")
        else:
            previous: int | None = None
            for index, row in enumerate(chain):
                if not isinstance(row, Mapping):
                    self.issue(requirements, "ADCB_STATEMENT_CHAIN_INVALID", f"ADCB statement {index} is invalid")
                    continue
                opening = row.get("opening_balance_minor")
                activity = row.get("activity_minor")
                closing = row.get("closing_balance_minor")
                if not all(isinstance(value, int) for value in (opening, activity, closing)):
                    self.issue(requirements, "ADCB_STATEMENT_CHAIN_INVALID", f"ADCB statement {index} balances must be integers")
                    continue
                if opening + activity != closing:
                    self.issue(requirements, "ADCB_STATEMENT_EQUATION_MISMATCH", f"ADCB statement {index} does not balance")
                if previous is not None and opening != previous:
                    self.issue(requirements, "ADCB_STATEMENT_CHAIN_GAP", f"ADCB statement {index} does not open at prior closing")
                previous = closing
            if previous != evidence.get("closing_balance_minor"):
                self.issue(requirements, "ADCB_STATEMENT_CHAIN_FINAL_MISMATCH", "ADCB chain does not end at declared closing balance")
        accounts = _account_map(api, self.issue, requirements, "API") if api else {}
        row = accounts.get(account_id)
        if row is None or row.get("balance_minor") != 0 or row.get("closed") is not True:
            self.issue(requirements, "ADCB_ACTUAL_READBACK_MISMATCH", "Actual does not show the ADCB account closed at zero")
        elif not _SHA256.fullmatch(str(row.get("history_hash") or "")):
            self.issue(requirements, "ADCB_HISTORY_HASH_REQUIRED", "ADCB historical transaction fingerprint is missing")

    def validate_parity(self, api: dict[str, Any] | None, ui: dict[str, Any] | None) -> None:
        requirements = set(_ALL_REQUIREMENTS)
        if api is None or ui is None:
            self.issue(requirements, "ACTUAL_READBACK_REQUIRED", "Actual API and UI readbacks are required")
            return
        if not str(api.get("sync_id") or "") or api.get("sync_id") != ui.get("sync_id"):
            self.issue(requirements, "ACTUAL_SYNC_ID_MISMATCH", "Actual UI and API sync IDs differ")
        api_accounts = _account_map(api, self.issue, requirements, "API")
        ui_accounts = _account_map(ui, self.issue, requirements, "UI")
        actual_ids = [
            str(row.get("actual_account_id") or "")
            for row in api_accounts.values()
        ]
        if any(not value for value in actual_ids) or len(actual_ids) != len(set(actual_ids)):
            self.issue(
                requirements,
                "ACTUAL_ACCOUNT_IDENTITY_INVALID",
                "Actual API account IDs must be present and unique",
            )
        if set(api_accounts) != set(ui_accounts):
            self.issue(requirements, "ACTUAL_ACCOUNT_SET_MISMATCH", "Actual UI and API account sets differ")
        for identity in set(api_accounts) & set(ui_accounts):
            left = api_accounts[identity]
            right = ui_accounts[identity]
            if left.get("balance_minor") != right.get("balance_minor"):
                self.issue(requirements, "ACTUAL_ACCOUNT_BALANCE_MISMATCH", f"Actual UI/API balance differs for {identity}")
            if left.get("closed") != right.get("closed") or left.get("offbudget") != right.get("offbudget"):
                self.issue(requirements, "ACTUAL_ACCOUNT_STATE_MISMATCH", f"Actual UI/API state differs for {identity}")
            if left.get("history_hash") is not None and left.get("history_hash") != right.get("history_hash"):
                self.issue(requirements, "ACTUAL_HISTORY_HASH_MISMATCH", f"Actual UI/API history differs for {identity}")
        api_total = api.get("aggregate_net_worth_minor")
        ui_total = ui.get("aggregate_net_worth_minor")
        if api_total != ui_total:
            self.issue(requirements, "ACTUAL_NET_WORTH_MISMATCH", "Actual UI and API net worth differ")
        if not isinstance(api_total, int) or api_total != sum(
            row.get("balance_minor") for row in api_accounts.values()
            if isinstance(row.get("balance_minor"), int)
        ):
            self.issue(requirements, "ACTUAL_NET_WORTH_EQUATION_MISMATCH", "Actual net worth does not equal signed account balances")

    def validate(self) -> dict[str, Any]:
        if self.requirement_id is not None and self.requirement_id not in _ALL_REQUIREMENTS:
            raise ValueError(f"Unsupported wealth acceptance requirement: {self.requirement_id}")
        if self.payload.get("schema_version") != 1:
            self.issue(_ALL_REQUIREMENTS, "BUNDLE_SCHEMA_INVALID", "bundle schema_version must be 1")
        environment = self.payload.get("environment")
        if not isinstance(environment, Mapping) or not str(environment.get("id") or ""):
            self.issue(_ALL_REQUIREMENTS, "ENVIRONMENT_EVIDENCE_REQUIRED", "environment.id is required")
        if not isinstance(environment, Mapping) or environment.get("kind") not in {"DISPOSABLE", "PRODUCTION"}:
            self.issue(_ALL_REQUIREMENTS, "ENVIRONMENT_KIND_INVALID", "environment.kind must be DISPOSABLE or PRODUCTION")
        self.load_artifacts()
        api = self.artifact("actual_api_artifact_id", "ACTUAL_API_READBACK", set(_ALL_REQUIREMENTS))
        ui = self.artifact("actual_ui_artifact_id", "ACTUAL_UI_READBACK", set(_ALL_REQUIREMENTS))
        self.validate_fab(api)
        self.validate_sarwa(api)
        self.validate_adcb(api)
        self.validate_parity(api, ui)
        selected = [self.requirement_id] if self.requirement_id else list(REQUIREMENTS)
        statuses = {
            identity: (
                "BLOCKED" if any(issue["requirement_id"] == identity for issue in self.issues) else "PASS"
            )
            for identity in selected
        }
        return {
            "schema_version": 1,
            "mode": "READ_ONLY_ACCEPTANCE",
            "status": "BLOCKED" if self.issues else "PASS",
            "production_write_allowed": False,
            "requirements": statuses,
            "issues": self.issues,
        }


def _try_datetime(value: Any, context: str, issue, requirements: set[str]) -> datetime | None:
    try:
        return _datetime(value, context)
    except ValueError as exc:
        issue(requirements, "AS_OF_INVALID", str(exc))
        return None


def _account_map(payload: Mapping[str, Any] | None, issue, requirements: set[str], label: str) -> dict[str, Mapping[str, Any]]:
    if payload is None:
        return {}
    rows = payload.get("accounts")
    if not isinstance(rows, list):
        issue(requirements, "ACTUAL_ACCOUNTS_INVALID", f"Actual {label} accounts must be an array")
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            issue(requirements, "ACTUAL_ACCOUNT_INVALID", f"Actual {label} account is invalid")
            continue
        identity = str(row.get("provider_account_id") or "")
        if not _STABLE_ACCOUNT_ID.fullmatch(identity) or identity in result:
            issue(requirements, "STABLE_ACCOUNT_IDENTITY_INVALID", f"Actual {label} duplicate/unsafe account ID: {identity}")
        result[identity] = row
    return result


def validate_wealth_acceptance_bundle(
    payload: Mapping[str, Any],
    *,
    base_dir: str | Path,
    requirement_id: str | None = None,
) -> dict[str, Any]:
    """Validate an evidence bundle without mutating Actual or source systems."""

    return _Validator(payload, Path(base_dir), requirement_id).validate()
