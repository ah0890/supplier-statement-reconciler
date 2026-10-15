"""Payment matching and invoice allocation.

* Statement payments are compared with recorded payments (payments export, or
  payment rows in the accounting export): first by reference, then by amount
  and a payment-date window - only when exactly one candidate exists.
* Allocation uses *explicit* links only (a payment's "applied to" invoice, a
  credit note's related invoice). Payments without an allocation are reported
  as unallocated; they are never assigned to invoices by guesswork.
* Outstanding = invoice amount - allocated payments - allocated credits.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from src.models.reconciliation import (
    MatchMethod,
    MatchResult,
    MatchStatus,
    PaymentAllocation,
    ReconciliationConfig,
    ResultKind,
    ReviewStatus,
)
from src.models.transaction import CanonicalTransaction, TransactionType
from src.reconciliation.confidence import ConfidenceScorer
from src.utils.dates import days_between, format_date
from src.utils.money import ZERO, format_money, money_sum
from src.utils.normalization import normalize_invoice_number


def payment_reference(txn: CanonicalTransaction) -> str | None:
    return normalize_invoice_number(txn.reference or txn.invoice_number)


class PaymentReconciler:
    def __init__(self, config: ReconciliationConfig, scorer: ConfidenceScorer) -> None:
        self.config = config
        self.scorer = scorer

    # ------------------------------------------------------------- matching
    def match(
        self,
        statement_payments: Sequence[CanonicalTransaction],
        recorded_payments: Sequence[CanonicalTransaction],
        *,
        period_end: date | None = None,
    ) -> list[MatchResult]:
        if not recorded_payments:
            return [self._unverifiable(p) for p in statement_payments]

        results: list[MatchResult] = []
        by_ref: dict[str, list[CanonicalTransaction]] = defaultdict(list)
        for payment in recorded_payments:
            ref = payment_reference(payment)
            if ref:
                by_ref[ref].append(payment)
        used: set[str] = set()
        remaining: list[CanonicalTransaction] = []

        # Level 1: same payment reference
        for stmt in statement_payments:
            ref = payment_reference(stmt)
            candidates = [c for c in by_ref.get(ref or "", []) if c.transaction_id not in used]
            if not candidates:
                remaining.append(stmt)
                continue
            best = min(candidates, key=lambda c: abs(stmt.amount - c.amount))
            combined = money_sum(c.amount for c in candidates)
            tolerance = self.config.amount_tolerance
            if (
                len(candidates) > 1
                and abs(stmt.amount - best.amount) > tolerance
                and abs(stmt.amount - combined) <= tolerance
            ):
                # A payment run recorded as one row per allocated invoice.
                used.update(c.transaction_id for c in candidates)
                results.append(self._pair_group(stmt, candidates, combined))
                continue
            used.add(best.transaction_id)
            results.append(self._pair(stmt, best, MatchMethod.PAYMENT_REFERENCE))

        # Level 2: amount + date window, unique candidates only
        unmatched_statement: list[CanonicalTransaction] = []
        available = [p for p in recorded_payments if p.transaction_id not in used]
        proposals: dict[str, list[CanonicalTransaction]] = {}
        contenders: dict[str, int] = defaultdict(int)
        for stmt in remaining:
            candidates = [
                c for c in available
                if abs(stmt.amount - c.amount) <= self.config.amount_tolerance
                and (days := days_between(stmt.invoice_date, c.invoice_date)) is not None
                and days <= self.config.payment_date_tolerance_days
            ]  # fmt: skip
            proposals[stmt.transaction_id] = candidates
            for c in candidates:
                contenders[c.transaction_id] += 1
        for stmt in remaining:
            candidates = proposals[stmt.transaction_id]
            if len(candidates) == 1 and contenders[candidates[0].transaction_id] == 1:
                used.add(candidates[0].transaction_id)
                results.append(self._pair(stmt, candidates[0], MatchMethod.PAYMENT_AMOUNT_DATE))
            else:
                unmatched_statement.append(stmt)

        for stmt in unmatched_statement:
            results.append(
                MatchResult(
                    status=MatchStatus.PAYMENT_MISMATCH,
                    kind=ResultKind.PAYMENT,
                    statement_txn_id=stmt.transaction_id,
                    statement_amount=stmt.amount,
                    issues=["NOT_IN_PAYMENT_RECORDS"],
                    matching_reason=(
                        f"Payment {stmt.display_reference} of {format_money(stmt.amount)} dated "
                        f"{format_date(stmt.invoice_date, 'n/a')} is shown on the supplier "
                        "statement but was not found in the payment records."
                    ),
                    requires_review=True,
                    review_status=ReviewStatus.PENDING,
                )
            )
        for payment in recorded_payments:
            if payment.transaction_id in used:
                continue
            reason = (
                f"Payment {payment.display_reference} of {format_money(payment.amount)} dated "
                f"{format_date(payment.invoice_date, 'n/a')} is in the payment records but not "
                "on the supplier statement."
            )
            issues = ["NOT_ON_STATEMENT"]
            if period_end and payment.invoice_date and payment.invoice_date > period_end:
                reason += " It was made after the statement date - likely a timing difference."
                issues.append("AFTER_PERIOD_END")
            results.append(
                MatchResult(
                    status=MatchStatus.PAYMENT_MISMATCH,
                    kind=ResultKind.PAYMENT,
                    accounting_txn_id=payment.transaction_id,
                    accounting_amount=payment.amount,
                    issues=issues,
                    matching_reason=reason,
                )
            )
        return results

    def _unverifiable(self, stmt: CanonicalTransaction) -> MatchResult:
        return MatchResult(
            status=MatchStatus.UNRESOLVED,
            kind=ResultKind.PAYMENT,
            statement_txn_id=stmt.transaction_id,
            statement_amount=stmt.amount,
            issues=["NO_PAYMENT_DATA"],
            matching_reason=(
                f"Payment {stmt.display_reference} of {format_money(stmt.amount)} is shown on the "
                "supplier statement. No payment records were imported, so it could not be "
                "verified. Upload a payments export to check it."
            ),
        )

    def _pair_group(
        self,
        stmt: CanonicalTransaction,
        rows: Sequence[CanonicalTransaction],
        combined: Decimal,
    ) -> MatchResult:
        days = days_between(stmt.invoice_date, rows[0].invoice_date)
        difference = stmt.amount - combined
        return MatchResult(
            status=MatchStatus.MATCHED if difference == 0 else MatchStatus.MATCHED_WITH_TOLERANCE,
            kind=ResultKind.PAYMENT,
            statement_txn_id=stmt.transaction_id,
            accounting_txn_id=rows[0].transaction_id,
            related_txn_ids=[r.transaction_id for r in rows[1:]],
            statement_amount=stmt.amount,
            accounting_amount=combined,
            amount_difference=difference,
            days_difference=days,
            confidence_score=self.scorer.score(
                MatchMethod.PAYMENT_REFERENCE, amount_diff=difference, days_diff=days
            ),
            confidence_band=self.scorer.band(100),
            matching_method=MatchMethod.PAYMENT_REFERENCE,
            matching_reason=(
                f"Payment {stmt.display_reference} of {format_money(stmt.amount)} matched to "
                f"{len(rows)} recorded payment allocations with the same reference "
                f"(total {format_money(combined)})."
            ),
        )

    def _pair(
        self, stmt: CanonicalTransaction, rec: CanonicalTransaction, method: MatchMethod
    ) -> MatchResult:
        difference = stmt.amount - rec.amount
        days = days_between(stmt.invoice_date, rec.invoice_date)
        confidence = self.scorer.score(method, amount_diff=difference, days_diff=days)
        amount_ok = abs(difference) <= self.config.amount_tolerance
        if not amount_ok:
            status = MatchStatus.PAYMENT_MISMATCH
            reason = (
                f"Payment {stmt.display_reference}: statement shows {format_money(stmt.amount)}"
                f" but the payment records show {format_money(rec.amount)} "
                f"(difference {format_money(difference)})."
            )
        else:
            status = (
                MatchStatus.MATCHED
                if difference == 0 and method == MatchMethod.PAYMENT_REFERENCE
                else MatchStatus.MATCHED_WITH_TOLERANCE
            )
            how = "reference" if method == MatchMethod.PAYMENT_REFERENCE else "amount and date"
            reason = (
                f"Payment {stmt.display_reference} matched to recorded payment "
                f"{rec.display_reference} by {how}"
                + (f"; dates differ by {days} day(s)." if days else ".")
            )
        requires_review = not amount_ok or self.scorer.requires_review(confidence, method)
        return MatchResult(
            status=status,
            kind=ResultKind.PAYMENT,
            statement_txn_id=stmt.transaction_id,
            accounting_txn_id=rec.transaction_id,
            statement_amount=stmt.amount,
            accounting_amount=rec.amount,
            amount_difference=difference,
            days_difference=days,
            confidence_score=confidence,
            confidence_band=self.scorer.band(confidence),
            matching_method=method,
            matching_reason=reason,
            issues=[] if amount_ok else ["AMOUNT"],
            requires_review=requires_review,
            review_status=ReviewStatus.PENDING if requires_review else ReviewStatus.NOT_REQUIRED,
        )

    # ----------------------------------------------------------- allocation
    def allocate(
        self,
        documents: Sequence[CanonicalTransaction],
        payments: Sequence[CanonicalTransaction],
        source: str,
        *,
        report_unallocated: bool = True,
    ) -> tuple[list[PaymentAllocation], list[str], list[MatchResult]]:
        """Return (allocations, unallocated payment descriptions, overpayment results).

        Supplier statements rarely show allocations, so callers can switch off the
        unallocated list for the statement side.
        """
        invoices = {
            t.invoice_number_normalized: t
            for t in documents
            if t.transaction_type == TransactionType.INVOICE and t.invoice_number_normalized
        }
        paid: dict[str, Decimal] = defaultdict(lambda: ZERO)
        credited: dict[str, Decimal] = defaultdict(lambda: ZERO)
        unallocated: list[str] = []

        for payment in payments:
            target = normalize_invoice_number(payment.related_invoice_number)
            if target and target in invoices:
                paid[target] += payment.amount
            elif not report_unallocated:
                continue
            elif target:
                unallocated.append(
                    f"{source}: payment {payment.display_reference} "
                    f"({format_money(payment.amount)}) is allocated to invoice "
                    f"{payment.related_invoice_number}, which was not found."
                )
            else:
                unallocated.append(
                    f"{source}: payment {payment.display_reference} "
                    f"({format_money(payment.amount)}) has no invoice allocation."
                )
        for credit in documents:
            if credit.transaction_type != TransactionType.CREDIT_NOTE:
                continue
            target = normalize_invoice_number(credit.related_invoice_number)
            if target and target in invoices:
                credited[target] += credit.amount

        allocations: list[PaymentAllocation] = []
        overpaid: list[MatchResult] = []
        for ref in sorted(set(paid) | set(credited)):
            invoice = invoices[ref]
            outstanding = invoice.amount - paid[ref] - credited[ref]
            tolerance = self.config.amount_tolerance
            if abs(outstanding) <= tolerance:
                status = "FULLY_PAID"
            elif outstanding < 0:
                status = "OVERPAID"
            else:
                status = "PART_PAID"
            allocations.append(
                PaymentAllocation(
                    invoice_number=invoice.invoice_number or ref,
                    invoice_amount=invoice.amount,
                    payments_allocated=paid[ref],
                    credits_allocated=credited[ref],
                    outstanding=outstanding,
                    allocation_status=status,
                    source=source,
                )
            )
            if status == "OVERPAID":
                on_statement = source == "STATEMENT"
                overpaid.append(
                    MatchResult(
                        status=MatchStatus.PAYMENT_MISMATCH,
                        kind=ResultKind.INVOICE,
                        statement_txn_id=invoice.transaction_id if on_statement else None,
                        accounting_txn_id=None if on_statement else invoice.transaction_id,
                        statement_amount=invoice.amount if on_statement else None,
                        accounting_amount=None if on_statement else invoice.amount,
                        amount_difference=outstanding,
                        issues=["OVERPAID"],
                        matching_reason=(
                            f"Invoice {invoice.display_reference} ({format_money(invoice.amount)})"
                            f" has {format_money(paid[ref])} of payments and "
                            f"{format_money(credited[ref])} of credits allocated - overpaid by "
                            f"{format_money(-outstanding)}."
                        ),
                        requires_review=True,
                        review_status=ReviewStatus.PENDING,
                    )
                )
        return allocations, unallocated, overpaid
