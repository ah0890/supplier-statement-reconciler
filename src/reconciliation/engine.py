"""Reconciliation engine - orchestrates the matching components.

The engine is pure business logic: it receives canonical transactions and a
configuration and returns a :class:`ReconciliationRun`. It has no knowledge of
files, databases or the UI.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Sequence
from datetime import date

from src.models.reconciliation import (
    BalanceStatus,
    MatchMethod,
    MatchResult,
    MatchStatus,
    ReconciliationConfig,
    ReconciliationOutcome,
    ReconciliationRun,
    ReconciliationSummary,
    ResultKind,
    ReviewStatus,
)
from src.models.transaction import CanonicalTransaction, StatementBalances, TransactionType
from src.reconciliation.balance_checker import BalanceChecker
from src.reconciliation.confidence import ConfidenceScorer
from src.reconciliation.duplicate_detector import DuplicateDetector, DuplicateFinding
from src.reconciliation.matcher import InvoiceMatcher, MatchPair
from src.reconciliation.mismatch_detector import MismatchDetector
from src.reconciliation.payments import PaymentReconciler
from src.utils.dates import format_date
from src.utils.money import format_money

logger = logging.getLogger(__name__)

_KIND = {
    TransactionType.INVOICE: ResultKind.INVOICE,
    TransactionType.CREDIT_NOTE: ResultKind.CREDIT_NOTE,
    TransactionType.PAYMENT: ResultKind.PAYMENT,
}


def describe(txn: CanonicalTransaction) -> str:
    noun = {"INVOICE": "Invoice", "CREDIT_NOTE": "Credit note", "PAYMENT": "Payment"}[
        txn.transaction_type.value
    ]
    return f"{noun} {txn.display_reference}"


class ReconciliationEngine:
    def __init__(self, config: ReconciliationConfig | None = None) -> None:
        self.config = config or ReconciliationConfig()
        self.scorer = ConfidenceScorer(self.config)
        self.matcher = InvoiceMatcher(self.config, self.scorer)
        self.duplicates = DuplicateDetector(self.config)
        self.mismatches = MismatchDetector(self.config)
        self.payments = PaymentReconciler(self.config, self.scorer)
        self.balance_checker = BalanceChecker(self.config)

    def run(
        self,
        statement: Sequence[CanonicalTransaction],
        accounting: Sequence[CanonicalTransaction],
        payments: Sequence[CanonicalTransaction] = (),
        *,
        balances: StatementBalances | None = None,
        period_end: date | None = None,
    ) -> ReconciliationRun:
        started = time.perf_counter()
        statement_docs = [t for t in statement if t.is_document]
        accounting_docs = [t for t in accounting if t.is_document]

        # 1. Duplicates within each side; near-certain copies leave the matching pool.
        results: list[MatchResult] = []
        possible_duplicates: dict[str, DuplicateFinding] = {}
        pools = []
        for docs in (statement_docs, accounting_docs):
            excluded: set[str] = set()
            for finding in self.duplicates.find_reference_duplicates(docs):
                if finding.exclude_from_matching:
                    excluded.add(finding.duplicate.transaction_id)
                    results.append(self.duplicate_result(finding))
                else:
                    possible_duplicates.setdefault(finding.duplicate.transaction_id, finding)
                    possible_duplicates.setdefault(
                        finding.original.transaction_id,
                        DuplicateFinding(
                            finding.original, finding.duplicate, finding.confidence,
                            finding.reason,
                        ),
                    )  # fmt: skip
            pools.append([d for d in docs if d.transaction_id not in excluded])

        # 2. Layered matching.
        matching = self.matcher.match(pools[0], pools[1])
        results += [self.build_pair_result(p) for p in matching.pairs]

        # 3. Unmatched records: possible duplicates first, then missing / accounting-only.
        for side_unmatched, pool in (
            (matching.unmatched_statement, pools[0]),
            (matching.unmatched_accounting, pools[1]),
        ):
            for finding in self.duplicates.find_near_duplicates(side_unmatched, pool):
                possible_duplicates.setdefault(finding.duplicate.transaction_id, finding)

        for txn in matching.unmatched_statement:
            finding = possible_duplicates.get(txn.transaction_id)
            if finding is not None:
                results.append(self.duplicate_result(finding))
            else:
                results.append(
                    self.missing_result(txn, matching.ambiguous.get(txn.transaction_id, []))
                )
        for txn in matching.unmatched_accounting:
            finding = possible_duplicates.get(txn.transaction_id)
            if finding is not None:
                results.append(self.duplicate_result(finding))
            else:
                results.append(self.accounting_only_result(txn, period_end))

        # 4. Payments and allocation.
        statement_payments = [t for t in statement if not t.is_document]
        recorded_payments = [*payments, *(t for t in accounting if not t.is_document)]
        results += self.payments.match(statement_payments, recorded_payments, period_end=period_end)
        allocations, unallocated = [], []
        for source, docs, side_payments in (
            ("STATEMENT", statement_docs, statement_payments),
            ("ACCOUNTING", accounting_docs, recorded_payments),
        ):
            side_allocations, side_unallocated, overpaid = self.payments.allocate(
                docs, side_payments, source, report_unallocated=source == "ACCOUNTING"
            )
            allocations += side_allocations
            unallocated += side_unallocated
            results += overpaid

        # 5. Balances.
        balance = self.balance_checker.check(statement, accounting, payments, balances, results)
        if balance.status == BalanceStatus.BALANCE_MISMATCH:
            results.append(
                MatchResult(
                    status=MatchStatus.BALANCE_MISMATCH,
                    kind=ResultKind.BALANCE,
                    statement_amount=balance.supplier_closing_balance,
                    amount_difference=balance.difference,
                    matching_reason=balance.reason,
                    issues=["STATEMENT_ARITHMETIC"],
                    requires_review=True,
                    review_status=ReviewStatus.PENDING,
                )
            )

        run = ReconciliationRun(
            results=results,
            allocations=allocations,
            unallocated_payments=unallocated,
            balance=balance,
            config=self.config,
        )
        run.summary = self._summarise(run, statement, accounting, payments)
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Reconciliation run: %d statement, %d accounting -> %d results in %d ms",
            len(statement), len(accounting), len(results), run.duration_ms,
        )  # fmt: skip
        return run

    # --------------------------------------------------------------- results
    def _base(self, method: MatchMethod, confidence: int) -> dict:
        requires_review = self.scorer.requires_review(confidence, method)
        return {
            "matching_method": method,
            "confidence_score": confidence,
            "confidence_band": self.scorer.band(confidence),
            "requires_review": requires_review,
            "review_status": ReviewStatus.PENDING if requires_review else ReviewStatus.NOT_REQUIRED,
        }

    def build_pair_result(self, pair: MatchPair) -> MatchResult:
        """Result for a matched pair (also used when a user confirms a match manually)."""
        stmt, acct = pair.statement, pair.accounting
        assessment = self.mismatches.assess(pair)
        method_text = pair.method.value.replace("_", " ").lower()
        reason = (
            f"{describe(stmt)} matched to accounting {acct.display_reference} by "
            f"{method_text}. {assessment.reason}"
        )
        status = assessment.status
        if stmt.transaction_type == TransactionType.CREDIT_NOTE:
            related = stmt.related_invoice_number or acct.related_invoice_number
            if related:
                reason += f" The credit relates to invoice {related}."
            if status in (MatchStatus.MATCHED, MatchStatus.MATCHED_WITH_TOLERANCE):
                status = MatchStatus.CREDIT_NOTE
        return MatchResult(
            status=status,
            kind=_KIND[stmt.transaction_type],
            statement_txn_id=stmt.transaction_id,
            accounting_txn_id=acct.transaction_id,
            matching_reason=reason,
            issues=assessment.issues,
            statement_amount=stmt.amount,
            accounting_amount=acct.amount,
            amount_difference=pair.amount_difference,
            days_difference=pair.days_difference,
            **self._base(pair.method, pair.confidence),
        )

    def duplicate_result(self, finding: DuplicateFinding) -> MatchResult:
        txn = finding.duplicate
        on_statement = txn.source.value == "STATEMENT"
        requires_review = finding.confidence < self.config.auto_accept_threshold
        return MatchResult(
            status=MatchStatus.DUPLICATE,
            kind=_KIND[txn.transaction_type],
            statement_txn_id=txn.transaction_id if on_statement else None,
            accounting_txn_id=None if on_statement else txn.transaction_id,
            related_txn_ids=[finding.original.transaction_id],
            confidence_score=finding.confidence,
            confidence_band=self.scorer.band(finding.confidence),
            matching_method=MatchMethod.DUPLICATE_CHECK,
            matching_reason=(
                ("On the supplier statement: " if on_statement else "In the accounting records: ")
                + finding.reason
            ),
            statement_amount=txn.amount if on_statement else None,
            accounting_amount=None if on_statement else txn.amount,
            requires_review=requires_review,
            review_status=ReviewStatus.PENDING if requires_review else ReviewStatus.NOT_REQUIRED,
        )

    def missing_result(self, txn: CanonicalTransaction, ambiguous: list[str]) -> MatchResult:
        reason = (
            f"{describe(txn)} dated {format_date(txn.invoice_date, 'n/a')} for "
            f"{format_money(txn.amount)} is on the supplier statement but was not found in "
            "the accounting records."
        )
        status = MatchStatus.MISSING_IN_ACCOUNTING
        if ambiguous:
            status = MatchStatus.UNRESOLVED
            reason = (
                f"{describe(txn)} has {len(ambiguous)} equally plausible accounting "
                "candidates with the same amount and a similar date - it was not matched "
                "automatically. Please review."
            )
        return MatchResult(
            status=status,
            kind=_KIND[txn.transaction_type],
            statement_txn_id=txn.transaction_id,
            related_txn_ids=ambiguous,
            matching_reason=reason,
            statement_amount=txn.amount,
            confidence_score=0,
            requires_review=bool(ambiguous),
            review_status=ReviewStatus.PENDING if ambiguous else ReviewStatus.NOT_REQUIRED,
        )

    def accounting_only_result(
        self, txn: CanonicalTransaction, period_end: date | None
    ) -> MatchResult:
        reason = (
            f"{describe(txn)} dated {format_date(txn.invoice_date, 'n/a')} for "
            f"{format_money(txn.amount)} is in the accounting records but not on the "
            "supplier statement."
        )
        issues = []
        if period_end and txn.invoice_date and txn.invoice_date > period_end:
            reason += " It is dated after the statement period end - likely a timing difference."
            issues.append("AFTER_PERIOD_END")
        return MatchResult(
            status=MatchStatus.NOT_ON_SUPPLIER_STATEMENT,
            kind=_KIND[txn.transaction_type],
            accounting_txn_id=txn.transaction_id,
            matching_reason=reason,
            accounting_amount=txn.amount,
            issues=issues,
        )

    # --------------------------------------------------------------- summary
    def _summarise(
        self,
        run: ReconciliationRun,
        statement: Sequence[CanonicalTransaction],
        accounting: Sequence[CanonicalTransaction],
        payments: Sequence[CanonicalTransaction],
    ) -> ReconciliationSummary:
        return summarise(run, len(statement), len(accounting), len(payments))


def summarise(
    run: ReconciliationRun, statement_count: int, accounting_count: int, payment_count: int
) -> ReconciliationSummary:
    """Counts and a *suggested* outcome. The user always makes the final decision."""
    counts = Counter(r.status.value for r in run.results)
    pending = sum(r.review_status == ReviewStatus.PENDING for r in run.results)
    exceptions = sum(r.is_exception for r in run.results)
    outcome, why = suggest_outcome(run, exceptions, pending)
    return ReconciliationSummary(
        statement_transactions=statement_count,
        accounting_transactions=accounting_count,
        payment_records=payment_count,
        status_counts=dict(counts),
        pending_reviews=pending,
        exceptions=exceptions,
        suggested_outcome=outcome,
        suggested_outcome_reason=why,
    )


def suggest_outcome(
    run: ReconciliationRun, exceptions: int, pending: int
) -> tuple[ReconciliationOutcome, str]:
    balance = run.balance
    tolerance = run.config.amount_tolerance
    unexplained = balance.unexplained_difference
    if balance.status == BalanceStatus.BALANCE_MISMATCH:
        return (
            ReconciliationOutcome.NOT_RECONCILED,
            "The supplier statement does not add up to its closing balance.",
        )
    if pending:
        return (
            ReconciliationOutcome.NOT_RECONCILED,
            f"{pending} item(s) still need a review decision.",
        )
    if unexplained is not None and abs(unexplained) > tolerance:
        return (
            ReconciliationOutcome.NOT_RECONCILED,
            f"{format_money(unexplained)} of the difference between the statement and the "
            "accounting records is not explained by the identified exceptions.",
        )
    if exceptions == 0:
        note = (
            ""
            if balance.status == BalanceStatus.BALANCED
            else " (the balance check was not possible - please confirm balances manually)"
        )
        return ReconciliationOutcome.RECONCILED, "All transactions matched" + note + "."
    return (
        ReconciliationOutcome.RECONCILED_WITH_EXCEPTIONS,
        f"{exceptions} exception(s) identified; the statement difference is fully explained "
        "by them."
        if unexplained is not None
        else f"{exceptions} exception(s) identified; balances could not be compared.",
    )
