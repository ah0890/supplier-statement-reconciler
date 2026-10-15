"""Statement balance reconciliation and suggested outcome."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.models.reconciliation import (
    BalanceStatus,
    MatchStatus,
    ReconciliationOutcome,
    ReviewStatus,
)
from src.models.transaction import StatementBalances, TransactionSource, TransactionType
from src.reconciliation.balance_checker import (
    CATEGORY_ACCOUNTING_ONLY,
    CATEGORY_AMOUNTS,
    CATEGORY_DUPLICATES,
    CATEGORY_MISSING,
    CATEGORY_PAYMENTS,
)
from src.reconciliation.engine import ReconciliationEngine
from tests.conftest import make_txn

D = date(2026, 9, 10)
CN, PAY = TransactionType.CREDIT_NOTE, TransactionType.PAYMENT


def _pay(source, ref, amount):
    return make_txn(source, None, amount, D, PAY, reference=ref)


def _balances(opening, closing):
    return StatementBalances(
        opening_balance=None if opening is None else Decimal(opening),
        closing_balance=None if closing is None else Decimal(closing),
    )


def test_balanced_statement(stmt, acct):
    statement = [stmt("INV-1", "1000.00"), stmt("CN-1", "100.00", txn_type=CN),
                 _pay(TransactionSource.STATEMENT, "PMT-1", "400.00")]  # fmt: skip
    run = ReconciliationEngine().run(
        statement, [acct("INV-1", "1000.00"), acct("CN-1", "100.00", txn_type=CN)],
        [_pay(TransactionSource.PAYMENTS, "PMT-1", "400.00")],
        balances=_balances("500.00", "1000.00"),
    )  # fmt: skip
    balance = run.balance
    assert balance.status == BalanceStatus.BALANCED
    assert balance.expected_closing_balance == Decimal("1000.00")
    assert balance.total_invoices == Decimal("1000.00")
    assert balance.total_credit_notes == Decimal("100.00")
    assert balance.total_payments == Decimal("400.00")
    assert balance.accounting_balance == Decimal("1000.00")
    assert balance.statement_vs_accounting_difference == Decimal("0.00")
    assert run.summary.suggested_outcome == ReconciliationOutcome.RECONCILED


def test_statement_arithmetic_mismatch(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("INV-1", "100.00")], [acct("INV-1", "100.00")],
        balances=_balances("0.00", "150.00"),
    )  # fmt: skip
    assert run.balance.status == BalanceStatus.BALANCE_MISMATCH
    assert run.balance.difference == Decimal("50.00")
    balance_rows = [r for r in run.results if r.status == MatchStatus.BALANCE_MISMATCH]
    assert len(balance_rows) == 1
    assert run.summary.suggested_outcome == ReconciliationOutcome.NOT_RECONCILED


def test_missing_balances_make_check_impossible(stmt):
    run = ReconciliationEngine().run([stmt("INV-1", "100.00")], [], balances=_balances(None, None))
    assert run.balance.status == BalanceStatus.BALANCE_CHECK_NOT_POSSIBLE
    assert "closing balance was not found" in run.balance.reason


def test_missing_opening_with_consistent_open_item_statement(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("INV-1", "100.00"), stmt("INV-2", "50.00")],
        [acct("INV-1", "100.00"), acct("INV-2", "50.00")],
        balances=_balances(None, "150.00"),
    )
    assert run.balance.status == BalanceStatus.BALANCED
    assert "open-item" in run.balance.reason


def test_missing_opening_with_inconsistent_total(stmt):
    run = ReconciliationEngine().run([stmt("INV-1", "100.00")], [], balances=_balances(None, "999"))
    assert run.balance.status == BalanceStatus.BALANCE_CHECK_NOT_POSSIBLE
    assert "opening balance was not found" in run.balance.reason


def test_running_balance_breaks_are_reported(stmt):
    statement = [
        stmt("INV-1", "100.00", balance=Decimal("1100.00"), source_row=5),
        stmt("INV-2", "50.00", balance=Decimal("1200.00"), source_row=6),  # should be 1150
    ]
    run = ReconciliationEngine().run(statement, [], balances=_balances("1000.00", "1200.00"))
    breaks = run.balance.running_balance_breaks
    assert len(breaks) == 1
    assert "Row 6" in breaks[0] and "expected 1,150.00" in breaks[0]


def test_difference_is_fully_explained_by_exceptions(stmt, acct):
    statement = [
        stmt("INV-1", "1000.00"),  # matched
        stmt("INV-2", "500.00"),  # missing in accounting   -> +500
        stmt("INV-3", "300.00"),  # amount mismatch (acct 280) -> +20
        stmt("CN-1", "50.00", txn_type=CN),  # missing credit -> -50
        _pay(TransactionSource.STATEMENT, "PMT-1", "700.00"),  # not in payment records -> -700
    ]
    accounting = [
        acct("INV-1", "1000.00", source_row=1),
        acct("INV-1", "1000.00", source_row=2),  # duplicate bill -> -1000
        acct("INV-3", "280.00"),
        acct("INV-9", "75.00"),  # not on statement -> -75
    ]
    payments = [_pay(TransactionSource.PAYMENTS, "PMT-2", "120.00")]  # not on statement -> +120
    opening = Decimal("200.00")
    closing = opening + Decimal("1000") + 500 + 300 - 50 - 700  # supplier arithmetic is right
    run = ReconciliationEngine().run(
        statement, accounting, payments, balances=_balances(str(opening), str(closing))
    )
    balance = run.balance
    assert balance.status == BalanceStatus.BALANCED
    explained = balance.explained_difference
    assert explained[CATEGORY_MISSING] == Decimal("450.00")
    assert explained[CATEGORY_AMOUNTS] == Decimal("20.00")
    assert explained[CATEGORY_DUPLICATES] == Decimal("-1000.00")
    assert explained[CATEGORY_ACCOUNTING_ONLY] == Decimal("-75.00")
    assert explained[CATEGORY_PAYMENTS] == Decimal("-580.00")
    assert balance.statement_vs_accounting_difference == sum(explained.values())
    assert balance.unexplained_difference == Decimal("0.00")

    # Payment issues require review, so the outcome is not final yet.
    assert run.summary.suggested_outcome == ReconciliationOutcome.NOT_RECONCILED
    for result in run.results:
        if result.review_status == ReviewStatus.PENDING:
            result.review_status = ReviewStatus.ACCEPTED
    from src.reconciliation.engine import summarise

    summary = summarise(run, 5, 4, 1)
    assert summary.suggested_outcome == ReconciliationOutcome.RECONCILED_WITH_EXCEPTIONS


def test_unexplained_difference_when_rows_were_excluded(stmt, acct):
    # The statement's closing balance includes an invoice whose row failed to import.
    run = ReconciliationEngine().run(
        [stmt("INV-1", "100.00")], [acct("INV-1", "100.00")],
        balances=_balances("0.00", "100.00"),
    )  # fmt: skip
    assert run.balance.unexplained_difference == Decimal("0.00")
    run = ReconciliationEngine().run(
        [stmt("INV-1", "100.00")], [acct("INV-1", "100.00")],
        balances=_balances("0.00", "130.00"),
    )  # fmt: skip
    # Statement arithmetic itself is off by 30 - explained as a statement arithmetic item.
    assert run.balance.status == BalanceStatus.BALANCE_MISMATCH
    assert run.balance.unexplained_difference == Decimal("0.00")
    assert run.summary.suggested_outcome == ReconciliationOutcome.NOT_RECONCILED
