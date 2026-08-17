from __future__ import annotations

import argparse
import calendar
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from .actual_pipeline import account_maps, export_statement_for_actual
from .actual_pipeline import load_actual_config, load_compiled_rules
from .ai_rules import AIEnrichmentEngine, load_ai_policies, load_ai_provider
from .history import load_history_index
from .mail_ingestion import (
    OutlookScanPlan,
    build_ingest_commit_payload,
    build_outlook_envelope,
    plan_outlook_scan,
)
from .notifications import parse_outlook_notifications
from .notifications import DEFAULT_NOTIFICATION_ADAPTERS
from .notification_sources import load_notification_sources, validate_notification_adapter_coverage
from .actual_snapshot import cashback_dashboard, transactions_from_actual_snapshot
from .browser_ingestion import export_browser_capture_for_actual
from .browser_exports import build_capture_from_export
from .browser_recipes import render_recipe
from .browser_sources import (
    account_source,
    capture_account,
    load_browser_sources,
    validate_source_coverage,
)
from .cashback import (
    PaymentIntent,
    load_program_configuration,
    payment_intents_from_config,
    poc_programs,
    programs_from_config,
    recommend,
)
from .models import Transaction, money
from .platforms import ActualBudgetAdapter
from .reports import evaluate_month_close, month_close_markdown


def _load_transactions(path: Path) -> list[Transaction]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        Transaction(
            transaction_id=str(row["transaction_id"]),
            transaction_at=datetime.fromisoformat(row["transaction_at"]),
            card=str(row.get("card") or row.get("account") or "UNASSIGNED"),
            merchant_raw=str(row["merchant_raw"]),
            amount_aed=money(row["amount_aed"]),
            account=row.get("account"),
            owner=row.get("owner"),
            currency=str(row.get("currency", "AED")),
            amount_original=(
                None if row.get("amount_original") is None else money(row["amount_original"])
            ),
            channel=str(row.get("channel", "UNKNOWN")),
            source_type=str(row.get("source_type", "manual")),
            source_message_id=row.get("source_message_id"),
            vendor=row.get("vendor"),
            category=row.get("category"),
            subcategory=row.get("subcategory"),
            transaction_type=str(row.get("transaction_type", "PURCHASE")),
            reward_bucket=row.get("reward_bucket"),
            tags=set(row.get("tags", [])),
            evidence_policy=row.get("evidence_policy"),
            evidence_status=str(row.get("evidence_status", "NOT_REQUESTED")),
            review_required=bool(row.get("review_required", False)),
            is_refund=bool(row.get("is_refund", False)),
            is_subscription=bool(row.get("is_subscription", False)),
            metadata=dict(row.get("metadata", {})),
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
    actual_export = subparsers.add_parser(
        "actual-export",
        help="Serialize canonical transactions for the official Actual Budget API bridge",
    )
    actual_export.add_argument("--input", type=Path, required=True)
    actual_export.add_argument("--output", type=Path, required=True)
    statement_export = subparsers.add_parser(
        "actual-statement-export",
        help="Decrypt, parse, validate, normalize, and serialize a PDF statement for Actual",
    )
    statement_export.add_argument("--pdf", type=Path, required=True)
    statement_export.add_argument("--config", type=Path, required=True)
    statement_export.add_argument("--output", type=Path, required=True)
    statement_export.add_argument("--rules", type=Path)
    statement_export.add_argument("--history", type=Path)
    statement_export.add_argument("--ai-policies", type=Path)
    statement_export.add_argument("--ai-provider", type=Path)
    statement_export.add_argument("--adapter")
    statement_export.add_argument("--source-message-id")
    statement_export.add_argument("--password-env", default="STATEMENT_PASSWORD")
    statement_export.add_argument("--allow-unbalanced", action="store_true")
    browser_export = subparsers.add_parser(
        "browser-capture-export",
        help="Validate and stage a browser-acquired account, statement, or transaction capture",
    )
    browser_export.add_argument("--input", type=Path, required=True)
    browser_export.add_argument("--config", type=Path, required=True)
    browser_export.add_argument("--output", type=Path, required=True)
    browser_export.add_argument("--rules", type=Path)
    browser_export.add_argument("--history", type=Path)
    browser_export.add_argument("--ai-policies", type=Path)
    browser_export.add_argument("--ai-provider", type=Path)
    browser_file = subparsers.add_parser(
        "browser-export-file",
        help="Parse an official browser export into the versioned browser capture contract",
    )
    browser_file.add_argument("--provider", required=True)
    browser_file.add_argument("--data-id", required=True)
    browser_file.add_argument("--file", type=Path, required=True)
    browser_file.add_argument("--sources", type=Path, required=True)
    browser_file.add_argument("--actual-account", required=True)
    browser_file.add_argument("--output", type=Path, required=True)
    browser_file.add_argument("--adapters-root", type=Path)
    browser_status = subparsers.add_parser(
        "browser-adapters-status",
        help="Validate browser recipes and report account-source coverage",
    )
    browser_status.add_argument("--sources", type=Path, required=True)
    browser_status.add_argument("--adapters-root", type=Path, required=True)
    browser_status.add_argument("--output", type=Path)
    browser_recipe = subparsers.add_parser(
        "browser-render-recipe",
        help="Render the exact provider and data acquisition recipes for a browser run",
    )
    browser_recipe.add_argument("--provider", required=True)
    browser_recipe.add_argument("--data-id", required=True)
    browser_recipe.add_argument("--params", type=Path)
    browser_recipe.add_argument("--adapters-root", type=Path)
    browser_recipe.add_argument("--output", type=Path, required=True)
    cashback_dashboard_parser = subparsers.add_parser(
        "cashback-dashboard",
        help="Calculate cashback pace, bucket headroom, and routing from an Actual snapshot",
    )
    cashback_dashboard_parser.add_argument("--snapshot", type=Path, required=True)
    cashback_dashboard_parser.add_argument("--config", type=Path, required=True)
    cashback_dashboard_parser.add_argument(
        "--cashback-config",
        type=Path,
        default=Path("config/cashback-programs.json"),
    )
    cashback_dashboard_parser.add_argument("--output", type=Path, required=True)
    cashback_dashboard_parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    notification_parser = subparsers.add_parser(
        "cashback-notification-events",
        help="Parse evidence-backed Outlook card notifications into a minimal cashback batch",
    )
    notification_parser.add_argument("--input", type=Path, required=True)
    notification_parser.add_argument("--config", type=Path, required=True)
    notification_parser.add_argument(
        "--cashback-config",
        type=Path,
        default=Path("config/cashback-programs.json"),
    )
    notification_parser.add_argument("--rules", type=Path, required=True)
    notification_parser.add_argument(
        "--notification-sources",
        type=Path,
        default=Path("config/transaction-email-sources.json"),
    )
    notification_parser.add_argument("--output", type=Path, required=True)
    notification_parser.add_argument("--history", type=Path)
    notification_parser.add_argument("--ai-policies", type=Path)
    notification_parser.add_argument("--ai-provider", type=Path)
    outlook_plan = subparsers.add_parser(
        "outlook-scan-plan",
        help="Calculate a frozen cursor-minus-overlap Outlook scan window",
    )
    outlook_plan.add_argument("--ingestion-config", type=Path, required=True)
    outlook_plan.add_argument("--ingest-state", type=Path, required=True)
    outlook_plan.add_argument("--run-upper-bound", type=datetime.fromisoformat, required=True)
    outlook_plan.add_argument("--output", type=Path, required=True)
    outlook_envelope = subparsers.add_parser(
        "outlook-envelope",
        help="Build a retryable exact-message envelope from a frozen scan plan",
    )
    outlook_envelope.add_argument("--plan", type=Path, required=True)
    outlook_envelope.add_argument("--messages", type=Path, required=True)
    outlook_envelope.add_argument("--output", type=Path, required=True)
    outlook_commit = subparsers.add_parser(
        "outlook-commit-payload",
        help="Validate the companion response and build the only cursor-commit payload",
    )
    outlook_commit.add_argument("--envelope", type=Path, required=True)
    outlook_commit.add_argument("--service-response", type=Path, required=True)
    outlook_commit.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "demo":
        return _demo()
    if args.command == "actual-export":
        envelopes = ActualBudgetAdapter().serialize_import(_load_transactions(args.input))
        payload = [asdict(envelope) for envelope in envelopes]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "actual-statement-export":
        run = export_statement_for_actual(
            args.pdf,
            args.config,
            args.output,
            password_env=args.password_env,
            adapter_code=args.adapter,
            source_message_id=args.source_message_id,
            rules_path=args.rules,
            history_path=args.history,
            ai_policies_path=args.ai_policies,
            ai_provider_path=args.ai_provider,
            allow_unbalanced=args.allow_unbalanced,
        )
        print(json.dumps({
            "output": str(args.output),
            "bank": run.bank,
            "transactions": run.statement["transaction_count"],
            "balance_tied": run.statement["balance_tied"],
            "review_count": run.review_count,
        }, indent=2))
        return 0
    if args.command == "browser-capture-export":
        run = export_browser_capture_for_actual(
            args.input,
            args.config,
            args.output,
            rules_path=args.rules,
            history_path=args.history,
            ai_policies_path=args.ai_policies,
            ai_provider_path=args.ai_provider,
        )
        print(json.dumps({
            "output": str(args.output),
            "capture_id": run.capture_id,
            "staging_status": run.staging_status,
            "transactions": len(run.transactions),
            "review_count": run.review_count,
            "import_blockers": list(run.import_blockers),
        }, indent=2))
        return 0
    if args.command == "browser-export-file":
        sources = load_browser_sources(args.sources)
        source = account_source(sources, args.actual_account)
        allowed = (
            str(source.get("provider_id") or "") == args.provider
            and args.data_id in source.get("data_ids", [])
        )
        if not allowed:
            raise ValueError(
                f"Browser source {args.provider}/{args.data_id} is not configured for {args.actual_account}"
            )
        capture = build_capture_from_export(
            args.provider,
            args.data_id,
            args.file,
            capture_account(source),
            adapters_root=args.adapters_root,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(capture, indent=2), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "browser-adapters-status":
        status = validate_source_coverage(load_browser_sources(args.sources), args.adapters_root)
        rendered = json.dumps(status, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
            print(args.output)
        else:
            print(rendered)
        return 0 if status["status"] == "ok" else 2
    if args.command == "browser-render-recipe":
        parameters = json.loads(args.params.read_text(encoding="utf-8")) if args.params else {}
        if not isinstance(parameters, dict):
            raise ValueError("recipe params must be a JSON object")
        recipe = render_recipe(args.provider, args.data_id, parameters, args.adapters_root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(recipe, indent=2), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "cashback-dashboard":
        config = load_actual_config(args.config)
        cashback_config = load_program_configuration(args.cashback_config)
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        transactions = transactions_from_actual_snapshot(snapshot, config, cashback_config)
        result = cashback_dashboard(
            programs_from_config(cashback_config, args.as_of),
            transactions,
            args.as_of,
            payment_intents_from_config(cashback_config),
            routing_profiles=cashback_config.get("routing_profiles") or (),
            route_policies=cashback_config.get("route_policies") or None,
            base_currency=str(cashback_config.get("currency") or "AED"),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "cashback-notification-events":
        config = load_actual_config(args.config)
        notification_sources = load_notification_sources(args.notification_sources)
        validate_notification_adapter_coverage(
            notification_sources,
            (adapter.code for adapter in DEFAULT_NOTIFICATION_ADAPTERS),
        )
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        messages = payload if isinstance(payload, list) else payload.get("messages", [])
        if not isinstance(messages, list) or any(not isinstance(row, dict) for row in messages):
            raise ValueError("notification input must be a list or an object with a messages list")
        card_by_last4, _ = account_maps(config)
        ai_engine = None
        ai_resolver = None
        if args.ai_policies or args.ai_provider:
            if not args.ai_policies or not args.ai_provider:
                raise ValueError("--ai-policies and --ai-provider must be supplied together")
            ai_engine = AIEnrichmentEngine(load_ai_policies(args.ai_policies))
            ai_resolver = load_ai_provider(args.ai_provider)
        batch = parse_outlook_notifications(
            messages,
            card_by_last4,
            load_compiled_rules(args.rules),
            history_index=(load_history_index(args.history) if args.history else None),
            ai_engine=ai_engine,
            ai_resolver=ai_resolver,
            cashback_config=load_program_configuration(args.cashback_config),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(batch.to_dict(), indent=2), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "outlook-scan-plan":
        config = json.loads(args.ingestion_config.read_text(encoding="utf-8"))
        state = json.loads(args.ingest_state.read_text(encoding="utf-8"))
        plan = plan_outlook_scan(config, state, args.run_upper_bound)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "outlook-envelope":
        plan_payload = json.loads(args.plan.read_text(encoding="utf-8"))
        messages_payload = json.loads(args.messages.read_text(encoding="utf-8"))
        messages = messages_payload.get("messages") if isinstance(messages_payload, dict) else messages_payload
        if not isinstance(messages, list):
            raise ValueError("messages input must be a list or an object with a messages list")
        plan = OutlookScanPlan(**plan_payload)
        envelope = build_outlook_envelope(plan, messages)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "outlook-commit-payload":
        envelope = json.loads(args.envelope.read_text(encoding="utf-8"))
        response = json.loads(args.service_response.read_text(encoding="utf-8"))
        payload = build_ingest_commit_payload(envelope, response)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(args.output)
        return 0
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
