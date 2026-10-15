"""Statement balance reconciliation.

Two checks are performed:

1. **Statement arithmetic** - ``opening + invoices - credit notes - payments``
   must equal the supplier's closing balance. Running-balance columns are
   also checked line by line, which catches extraction errors early.

2. **Statement vs accounting** - the period is replayed through the
   accounting records from the same opening balance
   (``opening + bills - credits - recorded payments``). The difference to the
   supplier's closing balance is then broken down by exception category.
   Every transaction appears in exactly one result, so the categories add up
   to the whole difference; anything left over is reported as *unexplained*.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from src.models.reconciliation import (
    BalanceCheckResult,
    BalanceStatus,
    MatchResult,
    MatchStatus,
    ReconciliationConfig,
    ResultKind,
)
from src.models.transaction import CanonicalTransaction, StatementBalances, TransactionType
from src.utils.money import ZERO, format_money, money_sum

MAX_RUNNING_BREAKS = 50

CATEGORY_STATEMENT_ARITHMETIC = "Supplier statement arithmetic"
CATEGORY_MISSING = "Items missing in accounting"
CATEGORY_ACCOUNTING_ONLY = "Accounting items not on statement"
CATEGORY_AMOUNTS = "Amount differences"
CATEGORY_DUPLICATES = "Duplicates"
CATEGORY_PAYMENTS = "Payment differences"
CATEGORY_UNRESOLVED = "Unresolved items"


def _sign(kind: ResultKind) -> int:
    return 1 if kind == ResultKind.INVOICE else -1


class BalanceChecker:
    def __init__(self, config: ReconciliationConfig) -> None:
        self.config = config

    def check(
        self,
        statement: Sequence[CanonicalTransaction],
        accounting: Sequence[CanonicalTransaction],
        recorded_payments: Sequence[CanonicalTransaction],
        balances: StatementBalances | None,
        results: Sequence[MatchResult],
    ) -> BalanceCheckResult:
        balances = balances or StatementBalances()
        invoices = money_sum(
            t.amount for t in statement if t.transaction_type == TransactionType.INVOICE
        )
        credits = money_sum(
            t.amount for t in statement if t.transaction_type == TransactionType.CREDIT_NOTE
        )
        payments = money_sum(
            t.amount for t in statement if t.transaction_type == TransactionType.PAYMENT
        )
        opening, closing = balances.opening_balance, balances.closing_balance
        result = BalanceCheckResult(
            status=BalanceStatus.BALANCE_CHECK_NOT_POSSIBLE,
            opening_balance=opening,
            total_invoices=invoices,
            total_credit_notes=credits,
            total_payments=payments,
            supplier_closing_balance=closing,
            running_balance_breaks=self._running_breaks(statement, opening),
        )
        tolerance = self.config.amount_tolerance
        open_item = False

        if closing is None:
            result.reason = (
                "Statement balance cannot be calculated because the closing balance was not "
                "found. Enter it on the Supplier Statement page to enable the balance check."
            )
            if opening is not None:
                result.expected_closing_balance = opening + invoices - credits - payments
        elif opening is None:
            activity = invoices - credits - payments
            if abs(closing - activity) <= tolerance:
                open_item = True
                result.status = BalanceStatus.BALANCED
                result.expected_closing_balance = activity
                result.difference = closing - activity
                result.reason = (
                    "No opening balance was found. The listed items add up to the closing "
                    "balance, so the statement was treated as an open-item statement."
                )
            else:
                result.reason = (
                    "Statement balance cannot be calculated because the opening balance was "
                    "not found. Enter it on the Supplier Statement page to enable the check."
                )
        else:
            expected = opening + invoices - credits - payments
            result.expected_closing_balance = expected
            result.difference = closing - expected
            if abs(result.difference) <= tolerance:
                result.status = BalanceStatus.BALANCED
                result.reason = (
                    "Opening balance plus statement activity equals the closing balance."
                )
            else:
                result.status = BalanceStatus.BALANCE_MISMATCH
                result.reason = (
                    f"Opening balance plus statement activity gives "
                    f"{format_money(expected)}, but the supplier's closing balance is "
                    f"{format_money(closing)} (difference {format_money(result.difference)}). "
                    "Some statement lines may not have been extracted - check the extracted data."
                )

        self._compare_with_accounting(
            result, accounting, recorded_payments, results, statement, open_item
        )
        return result

    # -------------------------------------------------------------- helpers
    def _running_breaks(
        self, statement: Sequence[CanonicalTransaction], opening: Decimal | None
    ) -> list[str]:
        breaks: list[str] = []
        previous = opening
        for txn in statement:
            if txn.balance is None:
                previous = None
                continue
            if previous is not None:
                expected = previous + txn.signed_amount
                if abs(txn.balance - expected) > self.config.amount_tolerance:
                    breaks.append(
                        f"Row {txn.source_row} ({txn.display_reference}): running balance "
                        f"{format_money(txn.balance)}, expected {format_money(expected)}."
                    )
                    if len(breaks) >= MAX_RUNNING_BREAKS:
                        break
            previous = txn.balance
        return breaks

    def _compare_with_accounting(
        self,
        result: BalanceCheckResult,
        accounting: Sequence[CanonicalTransaction],
        recorded_payments: Sequence[CanonicalTransaction],
        results: Sequence[MatchResult],
        statement: Sequence[CanonicalTransaction],
        open_item: bool,
    ) -> None:
        docs = [t for t in accounting if t.is_document]
        payments = [*recorded_payments, *(t for t in accounting if not t.is_document)]
        start = ZERO if open_item else result.opening_balance
        if start is None or result.supplier_closing_balance is None:
            return
        replay = (
            start + money_sum(t.signed_amount for t in docs) - money_sum(p.amount for p in payments)
        )
        result.accounting_balance = replay
        result.accounting_balance_basis = (
            "Statement opening balance + accounting bills - credit notes - recorded payments"
            if not open_item
            else "Accounting bills - credit notes - recorded payments (open-item statement)"
        )
        total = result.supplier_closing_balance - replay
        result.statement_vs_accounting_difference = total

        explained = self._explain(results, statement, accounting, recorded_payments)
        if result.difference is not None and result.difference != 0:
            explained[CATEGORY_STATEMENT_ARITHMETIC] = result.difference
        result.explained_difference = {k: v for k, v in explained.items() if v != 0}
        result.unexplained_difference = total - money_sum(result.explained_difference.values())

    @staticmethod
    def _explain(
        results: Sequence[MatchResult],
        statement: Sequence[CanonicalTransaction],
        accounting: Sequence[CanonicalTransaction],
        recorded_payments: Sequence[CanonicalTransaction],
    ) -> dict[str, Decimal]:
        """Contribution of each result to (supplier closing - accounting replay)."""
        by_id = {t.transaction_id: t for t in (*statement, *accounting, *recorded_payments)}
        totals: dict[str, Decimal] = {
            name: ZERO
            for name in (
                CATEGORY_MISSING, CATEGORY_ACCOUNTING_ONLY, CATEGORY_AMOUNTS,
                CATEGORY_DUPLICATES, CATEGORY_PAYMENTS, CATEGORY_UNRESOLVED,
            )
        }  # fmt: skip
        for r in results:
            if "OVERPAID" in r.issues or r.kind == ResultKind.BALANCE:
                continue  # allocation findings do not move balances
            stmt = by_id.get(r.statement_txn_id or "")
            acct = by_id.get(r.accounting_txn_id or "")
            if r.kind == ResultKind.PAYMENT:
                # Payments reduce balances: statement-only lowers the supplier side.
                recorded = money_sum(
                    by_id[i].amount for i in (r.accounting_txn_id, *r.related_txn_ids) if i in by_id
                )
                value = -(stmt.amount if stmt else ZERO) + recorded
                category = CATEGORY_PAYMENTS
            else:
                sign = _sign(r.kind)
                value = sign * ((stmt.amount if stmt else ZERO) - (acct.amount if acct else ZERO))
                if r.status == MatchStatus.DUPLICATE:
                    category = CATEGORY_DUPLICATES
                elif stmt and acct:
                    category = CATEGORY_AMOUNTS
                elif r.status == MatchStatus.MISSING_IN_ACCOUNTING:
                    category = CATEGORY_MISSING
                elif r.status == MatchStatus.NOT_ON_SUPPLIER_STATEMENT:
                    category = CATEGORY_ACCOUNTING_ONLY
                else:
                    category = CATEGORY_UNRESOLVED
            totals[category] += value
        return totals
