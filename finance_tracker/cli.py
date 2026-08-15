from __future__ import annotations

import argparse
import calendar
import json
from datetime import date, datetime
from pathlib import Path

from .cashback import PaymentIntent, poc_programs, recommend
from .models import Transaction, money
from .reports import evaluate_month_close, month_close_markdown


def _load_transactions(path: Path) -> list[Transaction]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        Transaction(
            transaction_id=str(row["transaction_id"]),
            transaction_at=datetime.fromisoformat(row["transaction_at"]),
            card=str(row["card"]),
            merchant_raw=str(row["merchant_raw"]),
            amount_aed=money(row["amount_aed"]),
            currency=str(row.get("currency", "AED")),
            channel=str(row.get("channel", "UNKNOWN")),
            category=row.get("category"),
            reward_bucket=row.get("reward_bucket"),
            is_refund=bool(row.get("is_refund", False)),
        )
        for row in rows
    ]


def _demo() -> int:
    intent = PaymentIntent("AMAZON", money("500"), "AED", "ONLINE")
    result = recommend(poc_programs(), [], intent)
    print(json.dumps({
        "category": result.category,
        "primary_card": result.primary_card,
        "bucket": result.primary_bucket,
        "alternative": result.alternative_card,
        "reason": result.reason,
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finance-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo")
    close = subparsers.add_parser("month-close")
    close.add_argument("--input", type=Path, required=True)
    close.add_argument("--month", required=True)
    close.add_argument("--output", type=Path, required=True)
    close.add_argument(
        "--statement-status",
        type=Path,
        required=True,
        help='JSON object such as {"RAK_WORLD":"RECEIVED","SC_PLATINUM_X":"RECEIVED"}',
    )
    close.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    close.add_argument("--grace-days", type=int, default=5)
    args = parser.parse_args(argv)
    if args.command == "demo":
        return _demo()
    year, month_number = (int(part) for part in args.month.split("-", 1))
    period_end = date(year, month_number, calendar.monthrange(year, month_number)[1])
    statuses = json.loads(args.statement_status.read_text(encoding="utf-8"))
    if not isinstance(statuses, dict) or not statuses:
        raise ValueError("statement-status must be a non-empty JSON object")
    gate = evaluate_month_close(args.as_of, period_end, statuses.keys(), statuses, args.grace_days)
    if not gate.eligible:
        print(json.dumps({
            "status": gate.status,
            "missing_statements": gate.missing_statements,
            "grace_period_exceeded": gate.grace_period_exceeded,
        }, indent=2))
        return 2
    transactions = _load_transactions(args.input)
    report = month_close_markdown(transactions, args.month)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
