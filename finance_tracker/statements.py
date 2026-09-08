from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, NotRequired, Protocol, TypedDict, runtime_checkable

_MONEY = r"(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}"
_SIGNED_MONEY = rf"[+-]?{_MONEY}"

_COMPACT_ROW_PREFIX = re.compile(
    r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{1,2}\s+[A-Za-z]{3}\s+"
)
_FULL_DATE_ROW_PREFIX = re.compile(r"^\d{2}/\d{2}/\d{4}\s+")

_TransactionDirection = Literal["DEBIT", "CREDIT"]
_TransactionType = Literal[
    "PURCHASE",
    "PAYMENT",
    "REFUND",
    "REWARD_CREDIT",
    "FEE",
    "CREDIT",
]


class _StagedTransaction(TypedDict):
    transaction_date: date
    post_date: date | None
    card_last4: str | None
    description: str
    amount_aed: Decimal
    direction: _TransactionDirection
    source_line: int
    amount_original: NotRequired[Decimal | None]
    currency_original: NotRequired[str]
    exchange_rate: NotRequired[Decimal | None]
    review_required: NotRequired[bool]


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value.replace(",", ""))


def _required_decimal(value: str) -> Decimal:
    parsed = _decimal(value)
    if parsed is None:
        raise ValueError("Expected a decimal value")
    return parsed


def _parse_date(value: str, format: str) -> date:
    return datetime.strptime(value, format).date()


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True, slots=True)
class NormalizedStatementTransaction:
    """Bank-neutral transaction emitted by every statement adapter."""

    transaction_id: str
    transaction_date: date
    post_date: date | None
    card_last4: str | None
    description: str
    amount_aed: Decimal
    direction: _TransactionDirection
    transaction_type: _TransactionType
    amount_original: Decimal | None = None
    currency_original: str = "AED"
    exchange_rate: Decimal | None = None
    source_line: int | None = None
    review_required: bool = False

    @property
    def signed_amount_aed(self) -> Decimal:
        return -self.amount_aed if self.direction == "CREDIT" else self.amount_aed

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["transaction_date"] = self.transaction_date.isoformat()
        result["post_date"] = _iso(self.post_date)
        for name in ("amount_aed", "amount_original", "exchange_rate"):
            value = result[name]
            result[name] = None if value is None else str(value)
        result["signed_amount_aed"] = str(self.signed_amount_aed)
        return result


@dataclass(frozen=True, slots=True)
class NormalizedStatement:
    """Canonical statement contract consumed by reconciliation and Actual import."""

    bank: str
    adapter: str
    source_file: str
    statement_date: date | None
    period_start: date | None
    period_end: date | None
    payment_due_date: date | None
    opening_balance_aed: Decimal | None
    closing_balance_aed: Decimal | None
    minimum_payment_aed: Decimal | None
    total_payment_due_aed: Decimal | None
    card_last4s: tuple[str, ...]
    transactions: tuple[NormalizedStatementTransaction, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def debit_total_aed(self) -> Decimal:
        return sum(
            (row.amount_aed for row in self.transactions if row.direction == "DEBIT"),
            Decimal(0),
        )

    @property
    def credit_total_aed(self) -> Decimal:
        return sum(
            (row.amount_aed for row in self.transactions if row.direction == "CREDIT"),
            Decimal(0),
        )

    @property
    def calculated_closing_balance_aed(self) -> Decimal | None:
        if self.opening_balance_aed is None:
            return None
        return self.opening_balance_aed + self.debit_total_aed - self.credit_total_aed

    @property
    def balance_difference_aed(self) -> Decimal | None:
        calculated = self.calculated_closing_balance_aed
        if calculated is None or self.closing_balance_aed is None:
            return None
        return calculated - self.closing_balance_aed

    @property
    def balance_tied(self) -> bool:
        """Whether statement arithmetic ties; this is not ledger reconciliation."""
        difference = self.balance_difference_aed
        return difference is not None and abs(difference) <= Decimal("0.01")

    def to_dict(self) -> dict[str, object]:
        return {
            "bank": self.bank,
            "adapter": self.adapter,
            "source_file": self.source_file,
            "statement_date": _iso(self.statement_date),
            "period_start": _iso(self.period_start),
            "period_end": _iso(self.period_end),
            "payment_due_date": _iso(self.payment_due_date),
            "opening_balance_aed": None
            if self.opening_balance_aed is None
            else str(self.opening_balance_aed),
            "closing_balance_aed": None
            if self.closing_balance_aed is None
            else str(self.closing_balance_aed),
            "minimum_payment_aed": None
            if self.minimum_payment_aed is None
            else str(self.minimum_payment_aed),
            "total_payment_due_aed": None
            if self.total_payment_due_aed is None
            else str(self.total_payment_due_aed),
            "card_last4s": list(self.card_last4s),
            "transaction_count": len(self.transactions),
            "debit_total_aed": str(self.debit_total_aed),
            "credit_total_aed": str(self.credit_total_aed),
            "calculated_closing_balance_aed": None
            if self.calculated_closing_balance_aed is None
            else str(self.calculated_closing_balance_aed),
            "balance_difference_aed": None
            if self.balance_difference_aed is None
            else str(self.balance_difference_aed),
            "balance_tied": self.balance_tied,
            "ledger_reconciled": False,
            "warnings": list(self.warnings),
            "transactions": [row.to_dict() for row in self.transactions],
        }


@runtime_checkable
class BankStatementAdapter(Protocol):
    """Extension API: add a bank without changing downstream processing."""

    code: str
    bank_name: str

    def detect(self, text: str) -> int:
        """Return a confidence from 0 to 100 for this statement layout."""
        ...

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        """Parse text into the canonical normalized statement contract."""
        ...


class StatementAdapterRegistry:
    def __init__(self, adapters: Iterable[BankStatementAdapter] = ()) -> None:
        self._adapters: dict[str, BankStatementAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: BankStatementAdapter) -> None:
        if not isinstance(adapter, BankStatementAdapter):
            raise TypeError("adapter must implement BankStatementAdapter")
        if adapter.code in self._adapters:
            raise ValueError(f"Statement adapter already registered: {adapter.code}")
        self._adapters[adapter.code] = adapter

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def adapter(self, code: str) -> BankStatementAdapter:
        try:
            return self._adapters[code]
        except KeyError as exc:
            raise ValueError(f"Unknown statement adapter: {code}") from exc

    def detect(self, text: str) -> BankStatementAdapter:
        ranked = sorted(
            ((adapter.detect(text), adapter) for adapter in self._adapters.values()),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            raise ValueError("No statement adapter recognized this document")
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            raise ValueError("Statement adapter detection was ambiguous")
        return ranked[0][1]

    def parse(
        self,
        text: str,
        source_file: str = "",
        adapter_code: str | None = None,
    ) -> NormalizedStatement:
        adapter = self.adapter(adapter_code) if adapter_code else self.detect(text)
        return adapter.parse(text, source_file=source_file)


def extract_pdf_text(path: str | Path, password: str | None = None) -> str:
    """Extract text without persisting a decrypted PDF or the supplied password."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError(
            "PDF extraction requires the optional pdfplumber package"
        ) from exc
    with pdfplumber.open(Path(path), password=password) as pdf:
        return "\n\n".join(
            page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages
        )


def _statement_date_years(
    period_start: date | None, period_end: date | None
) -> tuple[int, ...]:
    years = tuple(
        dict.fromkeys(
            bound.year
            for bound in (period_start, period_end)
            if bound is not None
        )
    )
    if not years:
        raise ValueError(
            "Cannot resolve omitted transaction years without statement bounds"
        )
    return years


def _in_statement_bounds(
    value: date, period_start: date | None, period_end: date | None
) -> bool:
    return (
        (period_start is None or value >= period_start)
        and (period_end is None or value <= period_end)
    )


def _compact_date_candidates(
    day: str, month: str, years: tuple[int, ...]
) -> tuple[date, ...]:
    candidates: list[date] = []
    for year in years:
        try:
            candidates.append(_parse_date(f"{day} {month} {year}", "%d %b %Y"))
        except ValueError:
            continue
    return tuple(candidates)


def _resolve_compact_dates(
    post_day: str,
    post_month: str,
    transaction_day: str,
    transaction_month: str,
    period_start: date | None,
    period_end: date | None,
) -> tuple[date, date]:
    years = _statement_date_years(period_start, period_end)
    transaction_candidates = _compact_date_candidates(
        transaction_day, transaction_month, years
    )
    post_candidates = _compact_date_candidates(post_day, post_month, years)
    bounded_transactions = tuple(
        candidate
        for candidate in transaction_candidates
        if _in_statement_bounds(candidate, period_start, period_end)
    )
    bounded_posts = tuple(
        candidate
        for candidate in post_candidates
        if _in_statement_bounds(candidate, period_start, period_end)
    )
    if bounded_transactions:
        transaction_candidates = bounded_transactions
    if bounded_posts:
        post_candidates = bounded_posts
    pairs = [
        (transaction_date, post_date)
        for transaction_date in transaction_candidates
        for post_date in post_candidates
    ]
    bounded_pairs = [
        pair
        for pair in pairs
        if _in_statement_bounds(pair[1], period_start, period_end)
    ]
    ordered_bounded_pairs = [
        pair for pair in bounded_pairs if pair[0] <= pair[1]
    ]
    if len(ordered_bounded_pairs) == 1:
        return ordered_bounded_pairs[0]
    if len(bounded_pairs) == 1:
        return bounded_pairs[0]
    if len(pairs) == 1:
        return pairs[0]
    raise ValueError(
        "Cannot unambiguously resolve compact transaction/post dates "
        "from statement bounds"
    )


def _unparsed_row_error(adapter: str, line_number: int) -> ValueError:
    return ValueError(
        f"{adapter} transaction row at line {line_number} could not be parsed"
    )


def _transaction_id(
    bank: str,
    card_last4: str | None,
    transaction_date: date,
    description: str,
    amount_aed: Decimal,
    direction: _TransactionDirection,
    occurrence: int,
) -> str:
    raw = "|".join(
        (
            bank,
            card_last4 or "",
            transaction_date.isoformat(),
            " ".join(description.upper().split()),
            str(amount_aed),
            direction,
            str(occurrence),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _type(
    description: str, direction: _TransactionDirection
) -> _TransactionType:
    normalized = description.upper()
    if any(
        token in normalized
        for token in ("PAYMENT RECEIVED", "CREDIT REPAYMENT", "CARD REPAYMENT")
    ):
        return "PAYMENT"
    if "CASHBACK" in normalized and direction == "CREDIT":
        return "REWARD_CREDIT"
    if "FEE" in normalized or normalized.startswith("VAT ON"):
        return "FEE"
    if direction == "CREDIT" and "REFUND" in normalized:
        return "REFUND"
    if direction == "CREDIT":
        return "CREDIT"
    return "PURCHASE"


def _finalize(
    bank: str,
    items: Iterable[_StagedTransaction],
) -> tuple[NormalizedStatementTransaction, ...]:
    seen: dict[tuple[object, ...], int] = {}
    result: list[NormalizedStatementTransaction] = []
    for item in items:
        fingerprint = (
            item["card_last4"],
            item["transaction_date"],
            " ".join(item["description"].upper().split()),
            item["amount_aed"],
            item["direction"],
        )
        occurrence = seen.get(fingerprint, 0) + 1
        seen[fingerprint] = occurrence
        result.append(
            NormalizedStatementTransaction(
                transaction_id=_transaction_id(
                    bank,
                    item["card_last4"],
                    item["transaction_date"],
                    item["description"],
                    item["amount_aed"],
                    item["direction"],
                    occurrence,
                ),
                transaction_date=item["transaction_date"],
                post_date=item["post_date"],
                card_last4=item["card_last4"],
                description=item["description"],
                amount_aed=item["amount_aed"],
                direction=item["direction"],
                transaction_type=_type(item["description"], item["direction"]),
                amount_original=item.get("amount_original"),
                currency_original=item.get("currency_original", "AED"),
                exchange_rate=item.get("exchange_rate"),
                source_line=item["source_line"],
                review_required=item.get("review_required", False),
            )
        )
    return tuple(result)


class EmiratesIslamicStatementAdapter:
    code = "emirates_islamic_v1"
    bank_name = "Emirates Islamic"

    def detect(self, text: str) -> int:
        upper = text.upper()
        return (
            100
            if "STATEMENT OF CARD ACCOUNT" in upper and "OPENING BALANCE" in upper
            else 0
        )

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        start_match = re.search(
            r"From:\s*(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})",
            text,
            re.IGNORECASE,
        )
        end_match = re.search(
            r"(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})\s*\nTo:",
            text,
            re.IGNORECASE,
        ) or re.search(
            r"To:\s*(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})",
            text,
            re.IGNORECASE,
        )

        def word_date(match: re.Match[str] | None) -> date | None:
            if match is None:
                return None
            return _parse_date(
                f"{match.group(1)} {match.group(2)} {match.group(3)}",
                "%d %b %Y",
            )

        period_start = word_date(start_match)
        period_end = word_date(end_match)
        opening_match = re.search(
            rf"OPENING BALANCE\s+({_MONEY})", text, re.IGNORECASE
        )
        card_match = re.search(
            r"PRIMARY CARD NO:\s*\d{4}X+(\d{4})", text, re.IGNORECASE
        )
        card_last4 = card_match.group(1) if card_match else None
        metadata = re.search(
            rf"Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges \(AED\) Current Balance \(AED\)\s+"
            rf"{_MONEY}\s+{_MONEY}\s+({_MONEY})\s+(\d{{2}}/\d{{2}}/\d{{2}})\s+({_MONEY})\s+{_MONEY}\s+({_MONEY})",
            text,
            re.IGNORECASE,
        )
        minimum: Decimal | None = None
        total_due: Decimal | None = None
        closing: Decimal | None = None
        due_date: date | None = None
        if metadata:
            minimum = _required_decimal(metadata.group(1))
            due_date = _parse_date(metadata.group(2), "%d/%m/%y")
            total_due = _required_decimal(metadata.group(3))
            closing = _required_decimal(metadata.group(4))

        row_re = re.compile(
            rf"^(\d{{1,2}})\s+([A-Z]{{3}})\s+(\d{{1,2}})\s+([A-Z]{{3}})\s+(.+?)\s+({_MONEY})(CR)?$",
            re.IGNORECASE,
        )
        compact_prefix = _COMPACT_ROW_PREFIX
        items: list[_StagedTransaction] = []
        for line_number, line in enumerate(lines, 1):
            match = row_re.match(line)
            if not match:
                if compact_prefix.match(line):
                    raise _unparsed_row_error(self.code, line_number)
                continue
            post_day = match.group(1)
            post_month = match.group(2)
            day = match.group(3)
            month = match.group(4)
            description = match.group(5)
            amount = match.group(6)
            credit = match.group(7)
            transaction_date, post_date = _resolve_compact_dates(
                post_day,
                post_month,
                day,
                month,
                period_start,
                period_end,
            )
            direction: _TransactionDirection = "CREDIT" if credit else "DEBIT"
            items.append(
                {
                    "transaction_date": transaction_date,
                    "post_date": post_date,
                    "card_last4": card_last4,
                    "description": description.strip(),
                    "amount_aed": _required_decimal(amount),
                    "direction": direction,
                    "source_line": line_number,
                }
            )
        transactions = _finalize("EMIRATES_ISLAMIC", items)
        return NormalizedStatement(
            bank=self.bank_name,
            adapter=self.code,
            source_file=source_file,
            statement_date=period_end,
            period_start=period_start,
            period_end=period_end,
            payment_due_date=due_date,
            opening_balance_aed=_decimal(opening_match.group(1))
            if opening_match
            else None,
            closing_balance_aed=closing,
            minimum_payment_aed=minimum,
            total_payment_due_aed=total_due,
            card_last4s=tuple(filter(None, (card_last4,))),
            transactions=transactions,
            warnings=() if transactions else ("No transaction rows were parsed",),
        )


class AdcbStatementAdapter:
    code = "adcb_v1"
    bank_name = "ADCB"

    def detect(self, text: str) -> int:
        upper = text.upper()
        return (
            100 if "PREVIOUS BALANCE OUTSTANDING" in upper and "CARD NO" in upper else 0
        )

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        opening_match = re.search(
            rf"PREVIOUS BALANCE OUTSTANDING\s+({_SIGNED_MONEY})", text, re.IGNORECASE
        )
        closing_match = re.search(
            rf"NEW BALANCE OUTSTANDING\s+({_SIGNED_MONEY})", text, re.IGNORECASE
        )
        header_dates = [
            _parse_date(match.group(0), "%d/%m/%y")
            for match in re.finditer(r"(?m)^\d{2}/\d{2}/\d{2}$", text)
        ]
        statement_date = header_dates[0] if header_dates else None
        due_date = header_dates[1] if len(header_dates) > 1 else None
        current_card: str | None = None
        card_last4s: list[str] = []
        items: list[_StagedTransaction] = []
        pending_foreign_index: int | None = None
        row_re = re.compile(
            rf"^(\d{{2}}/\d{{2}}/\d{{4}})\s+(.+?)\s+({_MONEY})(?:\s+(CR))?$",
            re.IGNORECASE,
        )
        foreign_tail = re.compile(
            rf"^(.*)\s+({_MONEY})\s+([A-Z]{{3}})$", re.IGNORECASE
        )
        rate_re = re.compile(
            r"^\[1\s+([A-Z]{3})=AED\s+([0-9]+(?:\.[0-9]+)?)\]$",
            re.IGNORECASE,
        )
        full_date_prefix = _FULL_DATE_ROW_PREFIX
        for line_number, line in enumerate(lines, 1):
            card_match = re.search(
                r"Card No\s*:\s*X+(\d{4})", line, re.IGNORECASE
            )
            if card_match:
                current_card = card_match.group(1)
                if current_card is not None and current_card not in card_last4s:
                    card_last4s.append(current_card)
                continue
            rate_match = rate_re.match(line)
            if rate_match and pending_foreign_index is not None:
                items[pending_foreign_index]["exchange_rate"] = _required_decimal(
                    rate_match.group(2)
                )
                pending_foreign_index = None
                continue
            row_match = row_re.match(line)
            if not row_match:
                if (
                    full_date_prefix.match(line)
                    and "PREVIOUS BALANCE OUTSTANDING" not in line.upper()
                    and "NEW BALANCE OUTSTANDING" not in line.upper()
                ):
                    raise _unparsed_row_error(self.code, line_number)
                continue
            when_raw = row_match.group(1)
            body = row_match.group(2)
            final_amount = row_match.group(3)
            credit = row_match.group(4)
            if body.upper() in {
                "PREVIOUS BALANCE OUTSTANDING",
                "NEW BALANCE OUTSTANDING",
            }:
                continue
            foreign_match = foreign_tail.match(body)
            description = body
            amount_original: Decimal | None = None
            currency_original = "AED"
            if foreign_match:
                description = foreign_match.group(1)
                original = foreign_match.group(2)
                currency_original = foreign_match.group(3)
                amount_original = _required_decimal(original)
            direction: _TransactionDirection = "CREDIT" if credit else "DEBIT"
            items.append(
                {
                    "transaction_date": _parse_date(when_raw, "%d/%m/%Y"),
                    "post_date": None,
                    "card_last4": current_card,
                    "description": description.strip(),
                    "amount_aed": _required_decimal(final_amount),
                    "direction": direction,
                    "amount_original": amount_original,
                    "currency_original": currency_original.upper(),
                    "source_line": line_number,
                    "review_required": current_card is None,
                }
            )
            pending_foreign_index = (
                len(items) - 1 if amount_original is not None else None
            )
        transactions = _finalize("ADCB", items)
        dates = [row.transaction_date for row in transactions]
        warnings: list[str] = []
        if not transactions:
            warnings.append("No transaction rows were parsed")
        if any(row.card_last4 is None for row in transactions):
            warnings.append(
                "One or more transactions appeared before a card section header"
            )
        return NormalizedStatement(
            bank=self.bank_name,
            adapter=self.code,
            source_file=source_file,
            statement_date=statement_date,
            period_start=min(dates) if dates else None,
            period_end=statement_date or (max(dates) if dates else None),
            payment_due_date=due_date,
            opening_balance_aed=_decimal(opening_match.group(1))
            if opening_match
            else None,
            closing_balance_aed=_decimal(closing_match.group(1))
            if closing_match
            else None,
            minimum_payment_aed=None,
            total_payment_due_aed=None,
            card_last4s=tuple(card_last4s),
            transactions=transactions,
            warnings=tuple(warnings),
        )


class RakbankStatementAdapter:
    code = "rakbank_v1"
    bank_name = "RAKBANK"

    def detect(self, text: str) -> int:
        upper = text.upper()
        return (
            100
            if "RAKBANK" in upper
            and "STATEMENT PERIOD" in upper
            and "CARD NUMBER" in upper
            and "CURRENT BALANCE" in upper
            else 0
        )

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        lines = text.splitlines()
        table_header_index = next(
            (
                index
                for index, line in enumerate(lines)
                if re.fullmatch(r"DATE\s+TRANSACTION", line.strip(), re.IGNORECASE)
            ),
            None,
        )
        if table_header_index is None:
            raise ValueError(f"{self.code} transaction table header was not found")

        period = re.search(
            r"STATEMENT\s+PERIOD\s*:\s*(\d{2}/\d{2}/\d{4})\s+TO\s+"
            r"(\d{2}/\d{2}/\d{4})",
            text,
            re.IGNORECASE,
        )
        if period is None:
            raise ValueError(f"{self.code} statement period is missing")
        period_start = _parse_date(period.group(1), "%d/%m/%Y")
        period_end = _parse_date(period.group(2), "%d/%m/%Y")
        issued = re.search(
            r"DATE\s+ISSUED\s*:\s*(\d{2}/\d{2}/\d{4})",
            text,
            re.IGNORECASE,
        )
        statement_date = (
            _parse_date(issued.group(1), "%d/%m/%Y") if issued else None
        )
        due_match = re.search(
            r"PAYMENT\s+DUE\s+DATE[^\d]{0,120}(\d{2}/\d{2}/\d{4})",
            text,
            re.IGNORECASE,
        )
        payment_due = (
            _parse_date(due_match.group(1), "%d/%m/%Y") if due_match else None
        )
        opening_match = re.search(
            rf"PREVIOUS\s+BALANCE\s+AED\s+({_MONEY})", text, re.IGNORECASE
        )
        closing_match = re.search(
            rf"CURRENT\s+BALANCE\s+AED\s+({_MONEY})", text, re.IGNORECASE
        )
        if opening_match is None or closing_match is None:
            raise ValueError(f"{self.code} printed balance summary is incomplete")

        header_text = "\n".join(lines[:table_header_index])
        minimum_match = re.search(
            rf"MINIMUM\s+PAYMENT\s+DUE[\s\S]{{0,120}}?AED\s+({_MONEY})",
            header_text,
            re.IGNORECASE,
        )
        total_due_match = re.search(
            rf"TOTAL\s+AMOUNT\s+DUE[\s\S]{{0,180}}?AED\s+({_MONEY})",
            header_text,
            re.IGNORECASE,
        )

        card_last4s: list[str] = []
        for line in lines:
            card_match = re.search(
                r"CARD\s+NUMBER\s*:\s*([^:\r\n]+)", line, re.IGNORECASE
            )
            if card_match:
                groups = re.findall(r"\d{4}", card_match.group(1))
                if groups and groups[-1] not in card_last4s:
                    card_last4s.append(groups[-1])

        date_prefix = re.compile(r"^(\d{2}/\d{2}/\d{4})\b")
        items: list[_StagedTransaction] = []
        index = table_header_index + 1
        while index < len(lines):
            line = lines[index].strip()
            if not line:
                index += 1
                continue
            date_match = date_prefix.match(line)
            if date_match is None:
                index += 1
                continue

            source_line = index + 1
            combined = line
            next_index = index + 1
            while len(re.findall(_MONEY, combined)) < 2 and next_index < len(lines):
                continuation = lines[next_index].strip()
                if date_prefix.match(continuation):
                    break
                if continuation:
                    combined = f"{combined} {continuation}"
                next_index += 1
            money_matches = list(re.finditer(_MONEY, combined))
            if len(money_matches) != 2:
                raise _unparsed_row_error(self.code, source_line)

            prefix = combined[date_match.end() : money_matches[0].start()].strip()
            currency_matches = list(re.finditer(r"\b[A-Z]{3}\b", prefix))
            if not currency_matches:
                raise _unparsed_row_error(self.code, source_line)
            currency_match = currency_matches[-1]
            currency = currency_match.group(0).upper()
            description = prefix[: currency_match.start()].strip(" \t,;:")
            if not description:
                raise _unparsed_row_error(self.code, source_line)

            tail = combined[money_matches[0].end() :]
            credit = bool(re.search(r"\bCR\b", tail, re.IGNORECASE))
            explicit_debit = bool(
                re.search(
                    rf"(?:^|\s)-\s*{_MONEY}(?:\s+CR)?\s*$",
                    tail,
                    re.IGNORECASE,
                )
            )
            if currency == "AED" and not credit and not explicit_debit:
                raise ValueError(
                    f"{self.code} transaction direction is not explicit at line "
                    f"{source_line}"
                )
            transaction_date = _parse_date(date_match.group(1), "%d/%m/%Y")
            if not _in_statement_bounds(transaction_date, period_start, period_end):
                raise ValueError(
                    f"{self.code} transaction date is outside statement period "
                    f"at line {source_line}"
                )
            original = money_matches[0].group(0)
            posted = money_matches[1].group(0)
            items.append(
                {
                    "transaction_date": transaction_date,
                    "post_date": None,
                    "card_last4": card_last4s[0] if card_last4s else None,
                    "description": description,
                    "amount_aed": _required_decimal(posted),
                    "direction": "CREDIT" if credit else "DEBIT",
                    "source_line": source_line,
                    "amount_original": (
                        None if currency == "AED" else _required_decimal(original)
                    ),
                    "currency_original": currency,
                }
            )
            index = next_index

        if not items:
            raise ValueError(f"{self.code} transaction rows were not parsed")

        summary_start = next(
            (
                index
                for index, line in enumerate(lines)
                if re.search(r"PREVIOUS\s+BALANCE", line, re.IGNORECASE)
            ),
            None,
        )
        summary_end = next(
            (
                index
                for index, line in enumerate(lines)
                if re.search(r"CURRENT\s+BALANCE", line, re.IGNORECASE)
            ),
            None,
        )
        if summary_start is None or summary_end is None or summary_start > summary_end:
            raise ValueError(f"{self.code} printed balance summary is incomplete")
        summary_net = Decimal(0)
        summary_components = 0
        for line in lines[summary_start : summary_end + 1]:
            summary_match = re.search(
                rf"AED\s+({_MONEY})(?P<tail>.*)$", line, re.IGNORECASE
            )
            if summary_match is None:
                continue
            tail = summary_match.group("tail")
            if "+" in tail:
                summary_net += _required_decimal(summary_match.group(1))
                summary_components += 1
            elif "-" in tail:
                summary_net -= _required_decimal(summary_match.group(1))
                summary_components += 1
        if summary_components == 0:
            raise ValueError(f"{self.code} printed transaction summary is incomplete")

        transactions = _finalize(self.bank_name, items)
        row_net = sum(
            (row.signed_amount_aed for row in transactions), Decimal(0)
        )
        opening = _required_decimal(opening_match.group(1))
        closing = _required_decimal(closing_match.group(1))
        if row_net != summary_net:
            raise ValueError(
                f"{self.code} transaction rows do not reconcile to printed summary"
            )
        if opening + row_net != closing:
            raise ValueError(
                f"{self.code} printed summary does not reconcile to balances"
            )
        return NormalizedStatement(
            bank=self.bank_name,
            adapter=self.code,
            source_file=source_file,
            statement_date=statement_date,
            period_start=period_start,
            period_end=period_end,
            payment_due_date=payment_due,
            opening_balance_aed=opening,
            closing_balance_aed=closing,
            minimum_payment_aed=(
                _required_decimal(minimum_match.group(1))
                if minimum_match
                else None
            ),
            total_payment_due_aed=(
                _required_decimal(total_due_match.group(1))
                if total_due_match
                else None
            ),
            card_last4s=tuple(card_last4s),
            transactions=transactions,
            warnings=(),
        )


class WioCreditStatementAdapter:
    code = "wio_credit_v1"
    bank_name = "Wio"

    def detect(self, text: str) -> int:
        upper = text.upper()
        return (
            100
            if "CREDIT STATEMENT" in upper
            and "ACCOUNT NUMBER" in upper
            and "WIO" in upper
            else 0
        )

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        period = re.search(
            r"FROM\s+(\d{2}/\d{2}/\d{4})\s+TO\s+(\d{2}/\d{2}/\d{4})",
            text,
            re.IGNORECASE,
        )
        period_start = (
            _parse_date(period.group(1), "%d/%m/%Y") if period else None
        )
        period_end = (
            _parse_date(period.group(2), "%d/%m/%Y") if period else None
        )
        account_match = re.search(r"ACCOUNT NUMBER\s+\d*(\d{4})", text, re.IGNORECASE)
        account_last4 = account_match.group(1) if account_match else None
        due = re.search(
            rf"PAYMENT DUE DATE MIN\. PAYMENT DUE TOTAL TO PAY\s+"
            rf"(\d{{2}}/\d{{2}}/\d{{4}})\s+({_MONEY})\s+({_MONEY})",
            text,
            re.IGNORECASE,
        )
        opening = re.search(
            rf"Balance From Last Statement\s+({_SIGNED_MONEY})", text, re.IGNORECASE
        )
        # Wio's first-generation credit statement labelled this field only as
        # "Closing Balance". Newer statements append "(Total to pay)". Both
        # labels represent the same reconciled statement fact.
        closing = re.search(
            rf"Closing balance(?:\s+\(Total to pay\))?\s+({_SIGNED_MONEY})",
            text,
            re.IGNORECASE,
        )
        row_re = re.compile(
            rf"^(\d{{2}}/\d{{2}}/\d{{4}})\s+([A-Z]\d+)\s+(.+?)(?:\s+\*{{4}}(\d{{4}}))?\s+([+-])({_MONEY})$",
            re.IGNORECASE,
        )
        rate_re = re.compile(
            r"^Rate:\s*([0-9]+(?:\.[0-9]+)?)\s*\(AED/([A-Z]{3})\)$",
            re.IGNORECASE,
        )
        items: list[_StagedTransaction] = []
        card_last4s = [account_last4] if account_last4 else []
        pending_foreign_index: int | None = None
        full_date_prefix = _FULL_DATE_ROW_PREFIX
        for line_number, line in enumerate(
            (line.strip() for line in text.splitlines()), 1
        ):
            rate_match = rate_re.match(line)
            if rate_match and pending_foreign_index is not None:
                items[pending_foreign_index]["exchange_rate"] = _required_decimal(
                    rate_match.group(1)
                )
                items[pending_foreign_index]["currency_original"] = rate_match.group(
                    2
                ).upper()
                pending_foreign_index = None
                continue
            match = row_re.match(line)
            if not match:
                if full_date_prefix.match(line) and not re.fullmatch(
                    rf"\d{{2}}/\d{{2}}/\d{{4}}\s+{_MONEY}\s+{_MONEY}",
                    line,
                ):
                    raise _unparsed_row_error(self.code, line_number)
                continue
            when_raw = match.group(1)
            reference = match.group(2)
            description = match.group(3)
            card_last4 = match.group(4)
            sign = match.group(5)
            amount = match.group(6)
            resolved_last4 = card_last4 or account_last4
            if resolved_last4 and resolved_last4 not in card_last4s:
                card_last4s.append(resolved_last4)
            direction: _TransactionDirection = "CREDIT" if sign == "+" else "DEBIT"
            items.append(
                {
                    "transaction_date": _parse_date(when_raw, "%d/%m/%Y"),
                    "post_date": None,
                    "card_last4": resolved_last4,
                    "description": f"{description.strip()} [{reference}]",
                    "amount_aed": _required_decimal(amount),
                    "direction": direction,
                    "source_line": line_number,
                    "review_required": resolved_last4 is None,
                }
            )
            pending_foreign_index = (
                len(items) - 1
                if sign == "-" and _type(description.strip(), "DEBIT") == "PURCHASE"
                else None
            )
        transactions = _finalize("WIO", items)
        warnings = () if transactions else ("No transaction rows were parsed",)
        return NormalizedStatement(
            bank=self.bank_name,
            adapter=self.code,
            source_file=source_file,
            statement_date=period_end,
            period_start=period_start,
            period_end=period_end,
            payment_due_date=_parse_date(due.group(1), "%d/%m/%Y") if due else None,
            opening_balance_aed=_decimal(opening.group(1)) if opening else None,
            closing_balance_aed=_decimal(closing.group(1)) if closing else None,
            minimum_payment_aed=_required_decimal(due.group(2)) if due else None,
            total_payment_due_aed=_required_decimal(due.group(3)) if due else None,
            card_last4s=tuple(card_last4s),
            transactions=transactions,
            warnings=warnings,
        )


DEFAULT_STATEMENT_ADAPTERS = StatementAdapterRegistry(
    (
        EmiratesIslamicStatementAdapter(),
        AdcbStatementAdapter(),
        RakbankStatementAdapter(),
        WioCreditStatementAdapter(),
    )
)


def parse_statement_text(
    text: str,
    source_file: str = "",
    adapter_code: str | None = None,
) -> NormalizedStatement:
    return DEFAULT_STATEMENT_ADAPTERS.parse(text, source_file, adapter_code)


def parse_statement_pdf(
    path: str | Path,
    password: str | None = None,
    adapter_code: str | None = None,
) -> NormalizedStatement:
    path = Path(path)
    return parse_statement_text(
        extract_pdf_text(path, password=password),
        source_file=path.name,
        adapter_code=adapter_code,
    )
