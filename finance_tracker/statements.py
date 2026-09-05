from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


_MONEY = r"(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}"
_SIGNED_MONEY = rf"[+-]?{_MONEY}"


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value.replace(",", ""))


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _period_word_date(
    day: str,
    month: str,
    period_start: date | None,
    period_end: date | None,
) -> date:
    """Resolve a yearless statement-row date from the stated period."""
    if period_start is None or period_end is None:
        raise ValueError("Statement period is required to resolve yearless transaction dates")
    if period_start > period_end:
        raise ValueError("Statement period start must not follow period end")
    candidates: list[date] = []
    for year in range(period_start.year, period_end.year + 1):
        try:
            candidate = datetime.strptime(f"{day} {month} {year}", "%d %b %Y").date()
        except ValueError:
            continue
        if period_start <= candidate <= period_end:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError(
            f"Statement row date {day} {month} does not resolve uniquely within "
            f"{period_start.isoformat()}..{period_end.isoformat()}"
        )
    return candidates[0]


def _transaction_word_date(day: str, month: str, post_date: date) -> date:
    """Resolve a transaction date as the latest valid occurrence by posting."""
    candidates: list[date] = []
    for year in (post_date.year, post_date.year - 1):
        try:
            candidate = datetime.strptime(f"{day} {month} {year}", "%d %b %Y").date()
        except ValueError:
            continue
        if candidate <= post_date:
            candidates.append(candidate)
    if not candidates:
        raise ValueError(
            f"Statement transaction date {day} {month} cannot be resolved from "
            f"posting date {post_date.isoformat()}"
        )
    return max(candidates)


@dataclass(frozen=True, slots=True)
class NormalizedStatementTransaction:
    """Bank-neutral transaction emitted by every statement adapter."""

    transaction_id: str
    transaction_date: date
    post_date: date | None
    card_last4: str | None
    description: str
    amount_aed: Decimal
    direction: str
    transaction_type: str
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
            Decimal("0"),
        )

    @property
    def credit_total_aed(self) -> Decimal:
        return sum(
            (row.amount_aed for row in self.transactions if row.direction == "CREDIT"),
            Decimal("0"),
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
            "opening_balance_aed": None if self.opening_balance_aed is None else str(self.opening_balance_aed),
            "closing_balance_aed": None if self.closing_balance_aed is None else str(self.closing_balance_aed),
            "minimum_payment_aed": None if self.minimum_payment_aed is None else str(self.minimum_payment_aed),
            "total_payment_due_aed": None if self.total_payment_due_aed is None else str(self.total_payment_due_aed),
            "card_last4s": list(self.card_last4s),
            "transaction_count": len(self.transactions),
            "debit_total_aed": str(self.debit_total_aed),
            "credit_total_aed": str(self.credit_total_aed),
            "calculated_closing_balance_aed": None if self.calculated_closing_balance_aed is None else str(self.calculated_closing_balance_aed),
            "balance_difference_aed": None if self.balance_difference_aed is None else str(self.balance_difference_aed),
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

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        """Parse text into the canonical normalized statement contract."""


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
        raise RuntimeError("PDF extraction requires the optional pdfplumber package") from exc
    with pdfplumber.open(Path(path), password=password) as pdf:
        return "\n\n".join(
            page.extract_text(x_tolerance=2, y_tolerance=3) or "" for page in pdf.pages
        )


def _transaction_id(
    bank: str,
    card_last4: str | None,
    transaction_date: date,
    description: str,
    amount_aed: Decimal,
    direction: str,
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


def _type(description: str, direction: str) -> str:
    normalized = description.upper()
    if any(token in normalized for token in ("PAYMENT RECEIVED", "CREDIT REPAYMENT", "CARD REPAYMENT")):
        return "PAYMENT"
    if "CASHBACK" in normalized and direction == "CREDIT":
        return "REWARD_CREDIT"
    if re.search(r"\bFEES?\b", normalized) or normalized.startswith("VAT ON"):
        return "FEE"
    if direction == "CREDIT":
        return "REFUND"
    return "PURCHASE"


def _finalize(
    bank: str,
    items: Iterable[dict[str, object]],
) -> tuple[NormalizedStatementTransaction, ...]:
    seen: dict[tuple[object, ...], int] = {}
    result: list[NormalizedStatementTransaction] = []
    for item in items:
        fingerprint = (
            item.get("card_last4"),
            item["transaction_date"],
            " ".join(str(item["description"]).upper().split()),
            item["amount_aed"],
            item["direction"],
        )
        occurrence = seen.get(fingerprint, 0) + 1
        seen[fingerprint] = occurrence
        result.append(
            NormalizedStatementTransaction(
                transaction_id=_transaction_id(
                    bank,
                    item.get("card_last4"),
                    item["transaction_date"],
                    str(item["description"]),
                    item["amount_aed"],
                    str(item["direction"]),
                    occurrence,
                ),
                transaction_date=item["transaction_date"],
                post_date=item.get("post_date"),
                card_last4=item.get("card_last4"),
                description=str(item["description"]),
                amount_aed=item["amount_aed"],
                direction=str(item["direction"]),
                transaction_type=_type(str(item["description"]), str(item["direction"])),
                amount_original=item.get("amount_original"),
                currency_original=str(item.get("currency_original", "AED")),
                exchange_rate=item.get("exchange_rate"),
                source_line=item.get("source_line"),
                review_required=bool(item.get("review_required", False)),
            )
        )
    return tuple(result)


class EmiratesIslamicStatementAdapter:
    code = "emirates_islamic_v1"
    bank_name = "Emirates Islamic"

    def detect(self, text: str) -> int:
        upper = text.upper()
        return 100 if "STATEMENT OF CARD ACCOUNT" in upper and "OPENING BALANCE" in upper else 0

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        start_match = re.search(
            r"From:\s*(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})",
            text,
            re.I,
        )
        end_match = re.search(
            r"(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})\s*\nTo:",
            text,
            re.I,
        ) or re.search(
            r"To:\s*(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3})\s+(\d{4})",
            text,
            re.I,
        )

        def word_date(match: re.Match[str] | None) -> date | None:
            return datetime.strptime(" ".join(match.groups()), "%d %b %Y").date() if match else None

        period_start = word_date(start_match)
        period_end = word_date(end_match)
        opening_match = re.search(rf"OPENING BALANCE\s+({_MONEY})", text, re.I)
        card_match = re.search(r"PRIMARY CARD NO:\s*\d{4}X+(\d{4})", text, re.I)
        card_last4 = card_match.group(1) if card_match else None
        metadata = re.search(
            rf"Card Limit Available Limit Minimum Payment Due Payment Due Date Total Payment Due Profit/Other Charges \(AED\) Current Balance \(AED\)\s+"
            rf"{_MONEY}\s+{_MONEY}\s+({_MONEY})\s+(\d{{2}}/\d{{2}}/\d{{2}})\s+({_MONEY})\s+{_MONEY}\s+({_MONEY})",
            text,
            re.I,
        )
        minimum = total_due = closing = None
        due_date = None
        if metadata:
            minimum = _decimal(metadata.group(1))
            due_date = datetime.strptime(metadata.group(2), "%d/%m/%y").date()
            total_due = _decimal(metadata.group(3))
            closing = _decimal(metadata.group(4))

        row_re = re.compile(
            rf"^(\d{{1,2}})\s+([A-Z]{{3}})\s+(\d{{1,2}})\s+([A-Z]{{3}})\s+(.+?)\s+({_MONEY})(CR)?$",
            re.I,
        )
        items: list[dict[str, object]] = []
        for line_number, line in enumerate(lines, 1):
            match = row_re.match(line)
            if not match:
                continue
            post_day, post_month, day, month, description, amount, credit = match.groups()
            post_date = _period_word_date(
                post_day, post_month, period_start, period_end
            )
            items.append(
                {
                    "transaction_date": _transaction_word_date(day, month, post_date),
                    "post_date": post_date,
                    "card_last4": card_last4,
                    "description": description.strip(),
                    "amount_aed": _decimal(amount),
                    "direction": "CREDIT" if credit else "DEBIT",
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
            opening_balance_aed=_decimal(opening_match.group(1)) if opening_match else None,
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
        return 100 if "PREVIOUS BALANCE OUTSTANDING" in upper and "CARD NO" in upper else 0

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        opening_match = re.search(rf"PREVIOUS BALANCE OUTSTANDING\s+({_SIGNED_MONEY})", text, re.I)
        closing_match = re.search(rf"NEW BALANCE OUTSTANDING\s+({_SIGNED_MONEY})", text, re.I)
        header_dates = [
            datetime.strptime(match.group(0), "%d/%m/%y").date()
            for match in re.finditer(r"(?m)^\d{2}/\d{2}/\d{2}$", text)
        ]
        statement_date = header_dates[0] if header_dates else None
        due_date = header_dates[1] if len(header_dates) > 1 else None
        current_card: str | None = None
        card_last4s: list[str] = []
        items: list[dict[str, object]] = []
        pending_foreign_index: int | None = None
        row_re = re.compile(rf"^(\d{{2}}/\d{{2}}/\d{{4}})\s+(.+?)\s+({_MONEY})(?:\s+(CR))?$", re.I)
        foreign_tail = re.compile(rf"^(.*)\s+({_MONEY})\s+([A-Z]{{3}})$", re.I)
        rate_re = re.compile(r"^\[1\s+([A-Z]{3})=AED\s+([0-9.]+)\]$", re.I)
        for line_number, line in enumerate(lines, 1):
            card_match = re.search(r"Card No\s*:\s*X+(\d{4})", line, re.I)
            if card_match:
                current_card = card_match.group(1)
                if current_card not in card_last4s:
                    card_last4s.append(current_card)
                continue
            rate_match = rate_re.match(line)
            if rate_match and pending_foreign_index is not None:
                items[pending_foreign_index]["exchange_rate"] = Decimal(rate_match.group(2))
                pending_foreign_index = None
                continue
            row_match = row_re.match(line)
            if not row_match:
                continue
            when_raw, body, final_amount, credit = row_match.groups()
            if body.upper() in {"PREVIOUS BALANCE OUTSTANDING", "NEW BALANCE OUTSTANDING"}:
                continue
            foreign_match = foreign_tail.match(body)
            description = body
            amount_original = None
            currency_original = "AED"
            if foreign_match:
                description, original, currency_original = foreign_match.groups()
                amount_original = _decimal(original)
            items.append(
                {
                    "transaction_date": datetime.strptime(when_raw, "%d/%m/%Y").date(),
                    "post_date": None,
                    "card_last4": current_card,
                    "description": description.strip(),
                    "amount_aed": _decimal(final_amount),
                    "direction": "CREDIT" if credit else "DEBIT",
                    "amount_original": amount_original,
                    "currency_original": currency_original.upper(),
                    "source_line": line_number,
                    "review_required": current_card is None,
                }
            )
            pending_foreign_index = len(items) - 1 if amount_original is not None else None
        transactions = _finalize("ADCB", items)
        dates = [row.transaction_date for row in transactions]
        warnings: list[str] = []
        if not transactions:
            warnings.append("No transaction rows were parsed")
        if any(row.card_last4 is None for row in transactions):
            warnings.append("One or more transactions appeared before a card section header")
        return NormalizedStatement(
            bank=self.bank_name,
            adapter=self.code,
            source_file=source_file,
            statement_date=statement_date,
            period_start=min(dates) if dates else None,
            period_end=statement_date or (max(dates) if dates else None),
            payment_due_date=due_date,
            opening_balance_aed=_decimal(opening_match.group(1)) if opening_match else None,
            closing_balance_aed=_decimal(closing_match.group(1)) if closing_match else None,
            minimum_payment_aed=None,
            total_payment_due_aed=None,
            card_last4s=tuple(card_last4s),
            transactions=transactions,
            warnings=tuple(warnings),
        )


class WioCreditStatementAdapter:
    code = "wio_credit_v1"
    bank_name = "Wio"

    def detect(self, text: str) -> int:
        upper = text.upper()
        return 100 if "CREDIT STATEMENT" in upper and "ACCOUNT NUMBER" in upper and "WIO" in upper else 0

    def parse(self, text: str, source_file: str = "") -> NormalizedStatement:
        period = re.search(
            r"FROM\s+(\d{2}/\d{2}/\d{4})\s+TO\s+(\d{2}/\d{2}/\d{4})",
            text,
            re.I,
        )
        period_start = datetime.strptime(period.group(1), "%d/%m/%Y").date() if period else None
        period_end = datetime.strptime(period.group(2), "%d/%m/%Y").date() if period else None
        account_match = re.search(r"ACCOUNT NUMBER\s+\d*(\d{4})", text, re.I)
        account_last4 = account_match.group(1) if account_match else None
        due = re.search(
            rf"PAYMENT DUE DATE MIN\. PAYMENT DUE TOTAL TO PAY\s+"
            rf"(\d{{2}}/\d{{2}}/\d{{4}})\s+({_MONEY})\s+({_MONEY})",
            text,
            re.I,
        )
        opening = re.search(rf"Balance From Last Statement\s+({_SIGNED_MONEY})", text, re.I)
        # Wio's first-generation credit statement labelled this field only as
        # "Closing Balance". Newer statements append "(Total to pay)". Both
        # labels represent the same reconciled statement fact.
        closing = re.search(
            rf"Closing balance(?:\s+\(Total to pay\))?\s+({_SIGNED_MONEY})",
            text,
            re.I,
        )
        row_re = re.compile(
            rf"^(\d{{2}}/\d{{2}}/\d{{4}})\s+([A-Z]\d+)\s+(.+?)(?:\s+\*{{4}}(\d{{4}}))?\s+([+-])({_MONEY})$",
            re.I,
        )
        rate_re = re.compile(r"^Rate:\s*([0-9.]+)\s*\(AED/([A-Z]{3})\)$", re.I)
        items: list[dict[str, object]] = []
        card_last4s = [account_last4] if account_last4 else []
        pending_foreign_index: int | None = None
        for line_number, line in enumerate((line.strip() for line in text.splitlines()), 1):
            rate_match = rate_re.match(line)
            if rate_match and pending_foreign_index is not None:
                items[pending_foreign_index]["exchange_rate"] = Decimal(rate_match.group(1))
                items[pending_foreign_index]["currency_original"] = rate_match.group(2).upper()
                pending_foreign_index = None
                continue
            match = row_re.match(line)
            if not match:
                continue
            when_raw, reference, description, card_last4, sign, amount = match.groups()
            resolved_last4 = card_last4 or account_last4
            if resolved_last4 and resolved_last4 not in card_last4s:
                card_last4s.append(resolved_last4)
            items.append(
                {
                    "transaction_date": datetime.strptime(when_raw, "%d/%m/%Y").date(),
                    "post_date": None,
                    "card_last4": resolved_last4,
                    "description": f"{description.strip()} [{reference}]",
                    "amount_aed": _decimal(amount),
                    "direction": "CREDIT" if sign == "+" else "DEBIT",
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
            payment_due_date=datetime.strptime(due.group(1), "%d/%m/%Y").date() if due else None,
            opening_balance_aed=_decimal(opening.group(1)) if opening else None,
            closing_balance_aed=_decimal(closing.group(1)) if closing else None,
            minimum_payment_aed=_decimal(due.group(2)) if due else None,
            total_payment_due_aed=_decimal(due.group(3)) if due else None,
            card_last4s=tuple(card_last4s),
            transactions=transactions,
            warnings=warnings,
        )


DEFAULT_STATEMENT_ADAPTERS = StatementAdapterRegistry(
    (EmiratesIslamicStatementAdapter(), AdcbStatementAdapter(), WioCreditStatementAdapter())
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
