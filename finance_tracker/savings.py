from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .models import money


@dataclass(frozen=True, slots=True)
class SavingsAllocation:
    goal: str
    account: str
    reserved_aed: Decimal
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "reserved_aed", money(self.reserved_aed))
        if self.reserved_aed < 0:
            raise ValueError("reserved_aed cannot be negative")


@dataclass(frozen=True, slots=True)
class AccountLiquidity:
    account: str
    gross_balance_aed: Decimal
    reserved_savings_aed: Decimal
    safe_to_spend_aed: Decimal


def account_liquidity(
    account: str,
    gross_balance_aed: Decimal,
    allocations: Iterable[SavingsAllocation],
) -> AccountLiquidity:
    """Return gross, earmarked, and safe balances without moving money.

    Actual or versioned configuration owns the allocation rows. The worker only
    sums active allocations for the account and publishes the derived snapshot.
    """
    gross = money(gross_balance_aed)
    reserved = sum(
        (allocation.reserved_aed for allocation in allocations if allocation.active and allocation.account == account),
        start=Decimal("0"),
    )
    return AccountLiquidity(account, gross, reserved, gross - reserved)
