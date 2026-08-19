from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping, Protocol

from .browser_recipes import load_data
from .models import money


POSITION_RECONCILIATION_TOLERANCE = Decimal("0.05")
_ORDINAL_DAY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)", re.IGNORECASE)
_SENSITIVE_KEYS = {
    "access_token", "authorization", "cookie", "cookies", "cvv",
    "full_card_number", "mfa_code", "otp", "passcode", "password", "pin",
    "recovery_code", "refresh_token", "secret", "session", "session_token",
}


def _required_text(value: Any, context: str) -> str:
    rendered = str(value or "").strip()
    if not rendered:
        raise ValueError(f"{context} is required")
    return rendered


def _decimal(value: Any, context: str) -> Decimal:
    if value in (None, ""):
        raise ValueError(f"{context} is required")
    try:
        return money(value)
    except Exception as exc:  # Decimal exposes several input-specific exceptions.
        raise ValueError(f"{context} must be a decimal number") from exc


def _datetime(value: Any, context: str) -> datetime:
    raw = _required_text(value, context)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context} must be an ISO datetime") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_of(value: Any, context: str) -> datetime:
    raw = _required_text(value, context)
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        return _datetime(raw, context)
    except ValueError:
        cleaned = _ORDINAL_DAY.sub(r"\1", raw)
        try:
            return datetime.strptime(cleaned, "%b %d %Y, %H:%M UTC").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise ValueError(f"{context} is not a supported as-of timestamp") from exc


def _reject_sensitive_values(value: Any, path: str = "capture") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                raise ValueError(f"Wealth capture contains forbidden sensitive field: {path}.{key}")
            _reject_sensitive_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_values(child, f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: str
    expected: Decimal
    actual: Decimal | None
    difference: Decimal | None
    tolerance: Decimal


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    instrument_id: str
    ticker: str
    name: str
    exchange: str | None
    units: Decimal
    market_value: Decimal
    unit_price: Decimal | None
    allocation_pct: Decimal | None
    performance_pct: Decimal | None
    corporate_action_context: str | None = None


@dataclass(frozen=True, slots=True)
class WealthCashFlow:
    source_id: str
    occurred_at: datetime
    amount: Decimal
    flow_type: str


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    provider_account_id: str
    display_name: str
    product_type: str
    ownership: str | None
    currency: str
    as_of: datetime
    total_value: Decimal
    cash_value: Decimal | None
    earnings: Decimal | None
    return_pct: Decimal | None
    positions: tuple[PositionSnapshot, ...]
    cash_flows: tuple[WealthCashFlow, ...]
    activity_status: str
    reconciliation: ReconciliationResult
    include_in_net_worth: bool
    actual_account_name: str | None
    closed: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WealthExclusion:
    kind: str
    display_name: str
    amount: Decimal
    currency: str
    reason: str


@dataclass(frozen=True, slots=True)
class WealthSnapshot:
    schema_version: int
    snapshot_id: str
    source_identity: str
    capture_id: str
    provider_id: str
    currency: str
    captured_at: datetime
    as_of: datetime
    total_value: Decimal
    earnings: Decimal | None
    return_pct: Decimal | None
    portfolios: tuple[PortfolioSnapshot, ...]
    exclusions: tuple[WealthExclusion, ...]
    reconciliation: ReconciliationResult
    limitations: tuple[str, ...]
    stale_after_seconds: int

    def portfolio(self, provider_account_id: str) -> PortfolioSnapshot:
        matches = [
            row for row in self.portfolios
            if row.provider_account_id == provider_account_id
        ]
        if len(matches) != 1:
            raise ValueError(f"Portfolio identity not found uniquely: {provider_account_id}")
        return matches[0]

    def freshness_status(self, evaluated_at: datetime) -> str:
        checked = evaluated_at if evaluated_at.tzinfo else evaluated_at.replace(tzinfo=timezone.utc)
        age = (checked - self.as_of).total_seconds()
        if age < 0:
            return "AS_OF_IN_FUTURE"
        return "STALE" if age > self.stale_after_seconds else "FRESH"

    def to_dict(self) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, tuple):
                return [encode(item) for item in value]
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            if hasattr(value, "__dataclass_fields__"):
                return encode(asdict(value))
            return value

        return encode(self)


@dataclass(frozen=True, slots=True)
class FXSnapshot:
    schema_version: int
    snapshot_id: str
    provider: str
    base_currency: str
    quote_currency: str
    observed_at: datetime
    rate: Decimal
    precision: int
    max_age_seconds: int
    source_identity: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("FX snapshot schema_version must be 1")
        object.__setattr__(self, "rate", money(self.rate))
        if self.rate <= 0:
            raise ValueError("FX rate must be positive")
        if self.precision < 0 or self.precision > 12:
            raise ValueError("FX precision must be between 0 and 12")
        if self.max_age_seconds <= 0:
            raise ValueError("FX max_age_seconds must be positive")
        object.__setattr__(self, "base_currency", self.base_currency.upper())
        object.__setattr__(self, "quote_currency", self.quote_currency.upper())
        if self.observed_at.tzinfo is None:
            object.__setattr__(self, "observed_at", self.observed_at.replace(tzinfo=timezone.utc))

    def is_fresh_for(self, as_of: datetime) -> bool:
        return abs((self.observed_at - as_of).total_seconds()) <= self.max_age_seconds

    def convert_minor(self, value: Decimal) -> int:
        return int(
            (value * self.rate * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )


@dataclass(frozen=True, slots=True)
class ProviderCapabilityResult:
    provider_id: str
    data_id: str
    status: str
    acquisition_mode: str
    official_api_supported: bool
    official_export_supported: bool
    unattended_refresh_supported: bool
    persist_browser_cookies: bool
    persist_credentials: bool
    reason: str


class WealthProvider(Protocol):
    def capability(self) -> ProviderCapabilityResult: ...

    def parse_capture(self, capture: Mapping[str, Any]) -> WealthSnapshot: ...


class SarwaProvider:
    provider_id = "sarwa"
    data_id = "holdings"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        stale_after_seconds = int(self.config.get("stale_after_seconds") or 604800)
        if stale_after_seconds <= 0:
            raise ValueError("Sarwa stale_after_seconds must be positive")
        raw_accounts = self.config.get("accounts")
        if not isinstance(raw_accounts, list) or not raw_accounts:
            raise ValueError("Sarwa wealth configuration requires accounts")
        self._by_label: dict[str, dict[str, Any]] = {}
        identities: set[str] = set()
        for raw in raw_accounts:
            row = dict(raw)
            identity = _required_text(row.get("provider_account_id"), "provider_account_id")
            if identity in identities:
                raise ValueError(f"Duplicate Sarwa provider_account_id: {identity}")
            identities.add(identity)
            labels = row.get("capture_labels")
            if not isinstance(labels, list) or not labels:
                raise ValueError(f"Sarwa account {identity} requires capture_labels")
            for label in labels:
                key = _required_text(label, "capture_label").casefold()
                if key in self._by_label:
                    raise ValueError(f"Duplicate Sarwa capture label: {label}")
                self._by_label[key] = row

    def capability(self) -> ProviderCapabilityResult:
        capability = dict(self.config.get("capability") or {})
        return ProviderCapabilityResult(
            provider_id=self.provider_id,
            data_id=self.data_id,
            status="USER_ASSISTED_REQUIRED",
            acquisition_mode="USER_ASSISTED_BROWSER_CAPTURE",
            official_api_supported=bool(capability.get("official_api_supported", False)),
            official_export_supported=bool(capability.get("official_export_supported", False)),
            unattended_refresh_supported=False,
            persist_browser_cookies=False,
            persist_credentials=False,
            reason=str(capability.get("reason") or (
                "No verified official API or unattended export; user completes login and MFA"
            )),
        )

    def _identity(self, label: str) -> dict[str, Any]:
        row = self._by_label.get(label.casefold())
        if not row:
            raise ValueError(f"Sarwa portfolio label is not mapped to a stable identity: {label}")
        return row

    def _invest(self, raw: Mapping[str, Any], currency: str) -> PortfolioSnapshot:
        label = _required_text(raw.get("label"), "invest_account.label")
        identity = self._identity(label)
        positions: list[PositionSnapshot] = []
        for index, item in enumerate(raw.get("positions") or []):
            if not isinstance(item, Mapping):
                raise ValueError(f"invest_account.positions[{index}] must be an object")
            ticker = _required_text(item.get("ticker"), f"positions[{index}].ticker").upper()
            units = _decimal(item.get("units"), f"positions[{index}].units")
            market_value = _decimal(
                item.get("market_value_usd"), f"positions[{index}].market_value_usd"
            )
            positions.append(PositionSnapshot(
                instrument_id=f"ticker:{ticker}",
                ticker=ticker,
                name=_required_text(item.get("name"), f"positions[{index}].name"),
                exchange=None,
                units=units,
                market_value=market_value,
                unit_price=(market_value / units) if units else None,
                allocation_pct=(
                    _decimal(item.get("allocation_pct"), "allocation_pct")
                    if item.get("allocation_pct") not in (None, "") else None
                ),
                performance_pct=(
                    _decimal(item.get("performance_pct"), "performance_pct")
                    if item.get("performance_pct") not in (None, "") else None
                ),
            ))
        total = _decimal(raw.get("balance_usd"), "invest_account.balance_usd")
        cash = _decimal(raw.get("cash_usd"), "invest_account.cash_usd")
        components = sum((row.market_value for row in positions), Decimal("0")) + cash
        difference = components - total
        if abs(difference) > POSITION_RECONCILIATION_TOLERANCE:
            status = "MISMATCH"
        elif difference == 0:
            status = "RECONCILED"
        else:
            status = "RECONCILED_WITH_ROUNDING"
        closed = bool(identity.get("closed", False))
        return PortfolioSnapshot(
            provider_account_id=str(identity["provider_account_id"]),
            display_name=label,
            product_type="INVEST",
            ownership=str(raw.get("ownership") or "").strip() or None,
            currency=currency,
            as_of=_as_of(raw.get("as_of"), f"{label}.as_of"),
            total_value=total,
            cash_value=cash,
            earnings=_decimal(raw.get("earnings_usd"), f"{label}.earnings_usd"),
            return_pct=_decimal(raw.get("return_pct"), f"{label}.return_pct"),
            positions=tuple(positions),
            cash_flows=(),
            activity_status=(
                "NO_STABLE_ACTIVITY_ROWS" if not raw.get("recent_activity_lines")
                else "ACTIVITY_REQUIRES_PARSER"
            ),
            reconciliation=ReconciliationResult(
                status=status,
                expected=total,
                actual=components,
                difference=difference,
                tolerance=POSITION_RECONCILIATION_TOLERANCE,
            ),
            include_in_net_worth=bool(identity.get("include_in_net_worth", not closed)),
            actual_account_name=str(identity.get("actual_account_name") or "").strip() or None,
            closed=closed,
            limitations=tuple(str(value) for value in raw.get("limitations") or []),
        )

    def parse_capture(self, capture: Mapping[str, Any]) -> WealthSnapshot:
        _reject_sensitive_values(capture)
        if capture.get("schema_version") != 1:
            raise ValueError("Sarwa capture schema_version must be 1")
        source = capture.get("source")
        if not isinstance(source, Mapping):
            raise ValueError("Sarwa capture requires source")
        if _required_text(source.get("provider"), "source.provider").casefold() != "sarwa":
            raise ValueError("Sarwa parser received a different provider capture")
        capture_id = _required_text(capture.get("capture_id"), "capture.capture_id")
        currency = _required_text(capture.get("currency"), "capture.currency").upper()
        overall = capture.get("overall")
        if not isinstance(overall, Mapping):
            raise ValueError("Sarwa capture requires overall")
        captured_at = _datetime(source.get("captured_at"), "source.captured_at")
        as_of = _as_of(overall.get("as_of"), "overall.as_of")
        total = _decimal(overall.get("balance_usd"), "overall.balance_usd")

        portfolios = [
            self._invest(row, currency)
            for row in capture.get("invest_accounts") or []
            if isinstance(row, Mapping)
        ]
        other = capture.get("other_products")
        if not isinstance(other, Mapping) or not isinstance(other.get("trade"), Mapping):
            raise ValueError("Sarwa capture requires other_products.trade")
        trade = dict(other["trade"])
        trade_label = _required_text(trade.get("label"), "trade.label")
        trade_identity = self._identity(trade_label)
        trade_total = _decimal(trade.get("balance_usd"), "trade.balance_usd")
        portfolios.append(PortfolioSnapshot(
            provider_account_id=str(trade_identity["provider_account_id"]),
            display_name=trade_label,
            product_type="TRADE",
            ownership=str(trade_identity.get("ownership") or "").strip() or None,
            currency=currency,
            as_of=as_of,
            total_value=trade_total,
            cash_value=None,
            earnings=_decimal(trade.get("earnings_usd"), "trade.earnings_usd"),
            return_pct=_decimal(trade.get("return_pct"), "trade.return_pct"),
            positions=(),
            cash_flows=(),
            activity_status="POSITION_AND_ACTIVITY_COMPONENTS_UNAVAILABLE",
            reconciliation=ReconciliationResult(
                status="COMPONENTS_UNAVAILABLE",
                expected=trade_total,
                actual=None,
                difference=None,
                tolerance=POSITION_RECONCILIATION_TOLERANCE,
            ),
            include_in_net_worth=bool(trade_identity.get("include_in_net_worth", True)),
            actual_account_name=str(trade_identity.get("actual_account_name") or "").strip() or None,
            closed=bool(trade_identity.get("closed", False)),
            limitations=("Position-level Trade holdings were not present in the capture",),
        ))

        portfolio_total = sum((row.total_value for row in portfolios), Decimal("0"))
        portfolio_difference = portfolio_total - total
        portfolio_status = "RECONCILED" if portfolio_difference == 0 else "MISMATCH"

        exclusions: list[WealthExclusion] = []
        protection = other.get("protection")
        if isinstance(protection, Mapping) and protection.get("coverage_usd") not in (None, ""):
            exclusions.append(WealthExclusion(
                kind="INSURANCE_COVERAGE",
                display_name=_required_text(protection.get("label"), "protection.label"),
                amount=_decimal(protection.get("coverage_usd"), "protection.coverage_usd"),
                currency=currency,
                reason="INSURANCE_COVERAGE_IS_NOT_AN_ASSET",
            ))

        material = f"sarwa|{capture_id}|{as_of.isoformat()}|{total}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
        return WealthSnapshot(
            schema_version=1,
            snapshot_id=f"wealth:sarwa:{digest}",
            source_identity=f"browser-capture:{capture_id}",
            capture_id=capture_id,
            provider_id="sarwa",
            currency=currency,
            captured_at=captured_at,
            as_of=as_of,
            total_value=total,
            earnings=_decimal(overall.get("earnings_usd"), "overall.earnings_usd"),
            return_pct=_decimal(overall.get("return_pct"), "overall.return_pct"),
            portfolios=tuple(portfolios),
            exclusions=tuple(exclusions),
            reconciliation=ReconciliationResult(
                status=portfolio_status,
                expected=total,
                actual=portfolio_total,
                difference=portfolio_difference,
                tolerance=Decimal("0.01"),
            ),
            limitations=tuple(str(value) for value in source.get("limitations") or []),
            stale_after_seconds=int(self.config.get("stale_after_seconds") or 604800),
        )


_PROVIDERS: dict[str, type[SarwaProvider]] = {"sarwa_holdings_v1": SarwaProvider}


def load_wealth_config(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Wealth sources configuration schema_version must be 1")
    if not isinstance(payload.get("providers"), dict):
        raise ValueError("Wealth sources configuration requires providers")
    return payload


def parse_registered_wealth_capture(
    provider_id: str,
    data_id: str,
    capture_path: str | Path,
    config_path: str | Path,
    *,
    adapters_root: str | Path | None = None,
) -> WealthSnapshot:
    data = load_data(provider_id, data_id, adapters_root)
    parser_id = str(data.get("parser") or "")
    provider_type = _PROVIDERS.get(parser_id)
    if provider_type is None:
        raise ValueError(f"Wealth parser is not registered: {parser_id}")
    config = load_wealth_config(config_path)
    provider_config = config["providers"].get(provider_id)
    if not isinstance(provider_config, Mapping):
        raise ValueError(f"Wealth provider is not configured: {provider_id}")
    capture = json.loads(Path(capture_path).read_text(encoding="utf-8"))
    if not isinstance(capture, Mapping):
        raise ValueError("Wealth capture must be an object")
    return provider_type(provider_config).parse_capture(capture)


def build_actual_wealth_proposal(
    snapshot: WealthSnapshot,
    fx_snapshot: FXSnapshot | None = None,
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    if snapshot.reconciliation.status != "RECONCILED":
        blockers.append("PORTFOLIO_TOTAL_MISMATCH")
    if any(row.reconciliation.status == "MISMATCH" for row in snapshot.portfolios):
        blockers.append("POSITION_TOTAL_MISMATCH")
    freshness = snapshot.freshness_status(evaluated_at or snapshot.as_of)
    if freshness != "FRESH":
        blockers.append("WEALTH_SNAPSHOT_STALE" if freshness == "STALE" else "WEALTH_AS_OF_IN_FUTURE")
    if fx_snapshot is None:
        blockers.append("FX_SNAPSHOT_REQUIRED")
    elif (
        fx_snapshot.base_currency != snapshot.currency
        or fx_snapshot.quote_currency != "AED"
    ):
        blockers.append("FX_PAIR_MISMATCH")
    elif not fx_snapshot.is_fresh_for(snapshot.as_of):
        blockers.append("FX_SNAPSHOT_STALE")

    accounts: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for portfolio in snapshot.portfolios:
        if portfolio.closed or not portfolio.include_in_net_worth:
            excluded.append({
                "provider_account_id": portfolio.provider_account_id,
                "display_name": portfolio.display_name,
                "reason": "CLOSED_ZERO_BALANCE" if portfolio.closed else "EXCLUDED_BY_POLICY",
                "source_value": str(portfolio.total_value),
                "source_currency": portfolio.currency,
            })
            continue
        usable_fx = fx_snapshot if not any(
            value in blockers for value in ("FX_SNAPSHOT_REQUIRED", "FX_PAIR_MISMATCH", "FX_SNAPSHOT_STALE")
        ) else None
        source = (
            f"{snapshot.snapshot_id}|{portfolio.provider_account_id}|"
            f"{usable_fx.snapshot_id if usable_fx else 'fx-required'}"
        )
        imported_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
        accounts.append({
            "provider_account_id": portfolio.provider_account_id,
            "name": portfolio.actual_account_name or f"Sarwa · {portfolio.display_name}",
            "type": "investment",
            "offbudget": True,
            "currency": "AED",
            "source_currency": portfolio.currency,
            "source_value": str(portfolio.total_value),
            "source_as_of": portfolio.as_of.isoformat(),
            "wealth_snapshot_id": snapshot.snapshot_id,
            "fx_snapshot_id": usable_fx.snapshot_id if usable_fx else None,
            "initial_balance_minor": (
                usable_fx.convert_minor(portfolio.total_value) if usable_fx else None
            ),
            "valuation_strategy": "AGGREGATE_BALANCE_ADJUSTMENT",
            "valuation_imported_id": f"wealth:sarwa:{imported_hash}",
            "positions_are_ledger_transactions": False,
            "review_required": True,
        })
    return {
        "schema_version": 1,
        "mode": "PROPOSAL_ONLY",
        "actual_writes_allowed": False,
        "status": "BLOCKED" if blockers else "READY_FOR_REVIEW",
        "blockers": blockers,
        "wealth_snapshot_id": snapshot.snapshot_id,
        "wealth_snapshot_freshness": freshness,
        "accounts": accounts,
        "excluded_accounts": excluded,
        "non_asset_exclusions": [
            {
                "kind": row.kind,
                "display_name": row.display_name,
                "amount": str(row.amount),
                "currency": row.currency,
                "reason": row.reason,
            }
            for row in snapshot.exclusions
        ],
    }
