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
    BalanceCheckResult,
    BalanceStatus,
    MatchMethod,
    MatchResult,
    MatchStatus,
    ReconciliationConfig,
    ReconciliationRun,
    ReconciliationSummary,
    ResultKind,
    ReviewStatus,
)
from src.models.transaction import CanonicalTransaction, StatementBalances, TransactionType
from src.reconciliation.confidence import ConfidenceScorer
from src.reconciliation.duplicate_detector import DuplicateDetector, DuplicateFinding
from src.reconciliation.matcher import InvoiceMatcher, MatchPair
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
                    results.append(self._duplicate_result(finding))
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
        results += [self._pair_result(p) for p in matching.pairs]

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
                results.append(self._duplicate_result(finding))
            else:
                results.append(
                    self._missing_result(txn, matching.ambiguous.get(txn.transaction_id, []))
                )
        for txn in matching.unmatched_accounting:
            finding = possible_duplicates.get(txn.transaction_id)
            if finding is not None:
                results.append(self._duplicate_result(finding))
            else:
                results.append(self._accounting_only_result(txn, period_end))

        run = ReconciliationRun(
            results=results,
            balance=BalanceCheckResult(
                status=BalanceStatus.BALANCE_CHECK_NOT_POSSIBLE,
                reason="Balance check has not been evaluated.",
            ),
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

    def _pair_result(self, pair: MatchPair) -> MatchResult:
        stmt, acct = pair.statement, pair.accounting
        difference = pair.amount_difference
        days = pair.days_difference
        within = abs(difference) <= self.config.amount_tolerance
        exact = difference == 0 and (days in (None, 0))
        if pair.method in (MatchMethod.FUZZY_REFERENCE, MatchMethod.AMOUNT_DATE_PROXIMITY):
            status = MatchStatus.REFERENCE_MISMATCH
        elif exact:
            status = MatchStatus.MATCHED
        elif within:
            status = MatchStatus.MATCHED_WITH_TOLERANCE
        else:
            status = MatchStatus.UNRESOLVED
        reason = (
            f"{describe(stmt)} paired with accounting {acct.display_reference} "
            f"({pair.method.value.replace('_', ' ').lower()})."
        )
        return MatchResult(
            status=status,
            kind=_KIND[stmt.transaction_type],
            statement_txn_id=stmt.transaction_id,
            accounting_txn_id=acct.transaction_id,
            matching_reason=reason,
            statement_amount=stmt.amount,
            accounting_amount=acct.amount,
            amount_difference=difference,
            days_difference=days,
            **self._base(pair.method, pair.confidence),
        )

    def _duplicate_result(self, finding: DuplicateFinding) -> MatchResult:
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

    def _missing_result(self, txn: CanonicalTransaction, ambiguous: list[str]) -> MatchResult:
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

    def _accounting_only_result(
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
        counts = Counter(r.status.value for r in run.results)
        return ReconciliationSummary(
            statement_transactions=len(statement),
            accounting_transactions=len(accounting),
            payment_records=len(payments),
            status_counts=dict(counts),
            pending_reviews=sum(r.review_status == ReviewStatus.PENDING for r in run.results),
            exceptions=sum(r.is_exception for r in run.results),
        )
