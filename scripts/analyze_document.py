"""Diagnose how a supplier statement or accounting export is interpreted.

Use this when testing with a real (preferably anonymised) document. It runs the
same parsers as the application and prints what was detected. Nothing is
stored, uploaded or logged to disk.

Usage:
    python scripts/analyze_document.py path/to/statement.pdf
    python scripts/analyze_document.py bills.csv --role accounting --supplier "Acme Ltd"
    python scripts/analyze_document.py statement.pdf --show 50
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.accounting import get_file_provider  # noqa: E402
from src.accounting.base import AccountingDataKind, AccountingQuery  # noqa: E402
from src.models.reconciliation import ReconciliationConfig  # noqa: E402
from src.parsers.base import StatementParserError, get_statement_parser  # noqa: E402
from src.reconciliation.balance_checker import BalanceChecker  # noqa: E402
from src.services.normalization_service import (  # noqa: E402
    NormalizationContext,
    normalize_transactions,
)
from src.utils.money import format_money, money_sum  # noqa: E402


def _section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose document parsing (local only).")
    parser.add_argument("path", type=Path)
    parser.add_argument("--role", choices=["statement", "accounting", "payments"],
                        default="statement")  # fmt: skip
    parser.add_argument("--supplier", default="", help="supplier name (for accounting exports)")
    parser.add_argument("--currency", default=None)
    parser.add_argument("--show", type=int, default=15, help="transactions to print")
    args = parser.parse_args(argv)

    if not args.path.is_file():
        parser.error(f"File not found: {args.path}")

    try:
        if args.role == "statement":
            parsed = get_statement_parser(args.path).parse(
                args.path, expected_currency=args.currency
            )
            table, mapping, result = parsed.table, parsed.mapping, parsed.result
            missing = parsed.missing_required
        else:
            kind = (
                AccountingDataKind.PAYMENTS if args.role == "payments" else AccountingDataKind.BILLS
            )
            loaded = get_file_provider(args.path).load(
                AccountingQuery(supplier_name=args.supplier or None, currency=args.currency), kind
            )
            table, mapping, result = loaded.table, loaded.mapping, loaded.result
            missing = loaded.missing_required
    except (StatementParserError, Exception) as exc:  # report every failure plainly
        print(f"FAILED: {exc}")
        return 1

    _section("Extraction")
    print(f"Method: {table.method}   pages: {table.page_count}   rows: {len(table.rows)}")
    print(f"Header row recognised: {table.header_detected}")
    print(f"Columns: {table.headers}")
    for warning in table.warnings:
        print(f"WARNING: {warning}")

    _section("Column mapping")
    for field, column in mapping.fields.items():
        note = mapping.notes.get(field, "")
        print(f"  {field:<24} <- {column!r:<28} confidence {mapping.confidence.get(field, '?')}"
              f"{'  (' + note + ')' if note else ''}")  # fmt: skip
    order = {True: "day first", False: "month first", None: "not determined"}[mapping.dayfirst]
    print(f"  date order: {order}   decimal comma: {mapping.decimal_comma}")
    if missing:
        print(f"MISSING REQUIRED: {', '.join(missing)}")

    transactions = result.transactions
    if args.supplier and args.role != "statement":
        transactions, excluded = normalize_transactions(
            transactions, NormalizationContext(args.supplier), result.report
        )
        print(f"Supplier filter: kept {len(transactions)}, excluded {excluded}")

    _section("Transactions")
    by_type = Counter(t.transaction_type.value for t in transactions)
    print(f"Total: {len(transactions)}  " + "  ".join(f"{k}: {v}" for k, v in by_type.items()))
    for kind in by_type:
        total = money_sum(t.amount for t in transactions if t.transaction_type.value == kind)
        print(f"  {kind:<12} total {format_money(total)}")
    for txn in transactions[: args.show]:
        print(f"  row {txn.source_row!s:>4}  {txn.invoice_date!s:<10}  "
              f"{txn.transaction_type.value:<11}  {txn.display_reference:<18} "
              f"{format_money(txn.amount):>14}  {(txn.description or '')[:40]}")  # fmt: skip

    if args.role == "statement":
        _section("Balances")
        balances = result.balances
        print(f"Opening: {format_money(balances.opening_balance, blank='not found')} "
              f"({balances.opening_source})   Closing: "
              f"{format_money(balances.closing_balance, blank='not found')} "
              f"({balances.closing_source})")  # fmt: skip
        check = BalanceChecker(ReconciliationConfig()).check(transactions, [], [], balances, [])
        print(f"Arithmetic check: {check.status.value} - {check.reason}")
        for line in check.running_balance_breaks[:10]:
            print(f"  {line}")

    _section("Validation")
    report = result.report
    print(f"Read {report.records_read}, valid {len(transactions)}, skipped {report.skipped}, "
          f"warnings {report.warnings}, errors {report.errors}")  # fmt: skip
    for issue in report.issues[:40]:
        print(f"  {issue.severity.value:<7} {issue.message}")
    return 0 if transactions and not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Decimal", "main"]
