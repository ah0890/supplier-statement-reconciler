"""Manual review of questionable results: accept, reject or mark unresolved.

Uncertain matches are never finalised automatically. Every decision keeps the
original status, records who-what-when in the audit trail and refreshes the
summary and balance breakdown.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from src.database.database import Database
from src.database.models import utcnow
from src.database.repository import Repository
from src.models.reconciliation import (
    MatchMethod,
    MatchResult,
    MatchStatus,
    ReconciliationRun,
    ResultKind,
    ReviewStatus,
)
from src.reconciliation.balance_checker import BalanceChecker
from src.reconciliation.engine import ReconciliationEngine, summarise
from src.reconciliation.matcher import MatchPair
from src.services.reconciliation_service import ReconciliationService, ReconciliationServiceError

logger = logging.getLogger(__name__)


class ReviewAction(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    UNRESOLVED = "UNRESOLVED"


def available_actions(result: MatchResult) -> list[ReviewAction]:
    """Actions that make sense for a result."""
    if result.kind == ResultKind.BALANCE:
        return [ReviewAction.ACCEPT, ReviewAction.UNRESOLVED]
    paired = bool(result.statement_txn_id and result.accounting_txn_id)
    if paired or result.status == MatchStatus.DUPLICATE:
        return [ReviewAction.ACCEPT, ReviewAction.REJECT, ReviewAction.UNRESOLVED]
    return [ReviewAction.ACCEPT, ReviewAction.UNRESOLVED]


def action_label(result: MatchResult, action: ReviewAction) -> str:
    paired = bool(result.statement_txn_id and result.accounting_txn_id)
    if action == ReviewAction.ACCEPT:
        if paired:
            return "Accept match"
        if result.status == MatchStatus.DUPLICATE:
            return "Confirm duplicate"
        return "Acknowledge"
    if action == ReviewAction.REJECT:
        return "Reject match" if paired else "Not a duplicate"
    return "Mark unresolved"


class ReviewService:
    def __init__(self, database: Database, reconciliations: ReconciliationService) -> None:
        self.database = database
        self.reconciliations = reconciliations

    def review(
        self,
        reconciliation_id: int,
        result_id: str,
        action: ReviewAction,
        note: str | None = None,
    ) -> None:
        stored = self.reconciliations.stored_run(reconciliation_id)
        if stored is None:
            raise ReconciliationServiceError("Run the reconciliation first.")
        result = next((r for r in stored.results if r.result_id == result_id), None)
        if result is None:
            raise ReconciliationServiceError("Result not found - it may have been re-run.")
        if action not in available_actions(result):
            raise ReconciliationServiceError("This action is not available for this item.")

        data = self.reconciliations.load_data(reconciliation_id)
        lookup = data.lookup()
        engine = ReconciliationEngine(stored.config)
        note = (note or "").strip() or None
        previous = result.status
        new_results: list[MatchResult] = []

        if action == ReviewAction.ACCEPT:
            updated = self._accept(result, lookup, engine)
        elif action == ReviewAction.REJECT:
            recon = self.reconciliations.get(reconciliation_id)
            period_end = recon.period_end if recon else None
            updated, new_results = self._reject(result, lookup, engine, period_end)
        else:
            updated = result.model_copy(update={"status": MatchStatus.UNRESOLVED})
            updated.review_status = ReviewStatus.UNRESOLVED

        updated.original_status = result.original_status or previous
        updated.result_id = result.result_id
        updated.requires_review = False
        updated.review_note = note
        updated.reviewed_at = utcnow()
        if updated.review_status in (ReviewStatus.PENDING, ReviewStatus.NOT_REQUIRED):
            updated.review_status = (
                ReviewStatus.REJECTED if action == ReviewAction.REJECT else ReviewStatus.ACCEPTED
            )

        results = [updated if r.result_id == result_id else r for r in stored.results]
        results += new_results
        balance = BalanceChecker(stored.config).check(
            data.statement,
            data.accounting,
            data.payments,
            data.balances,
            [r for r in results if r.kind != ResultKind.BALANCE],
        )
        run = ReconciliationRun(
            results=results,
            balance=balance,
            allocations=stored.allocations,
            unallocated_payments=stored.unallocated_payments,
            config=stored.config,
        )
        summary = summarise(
            run,
            stored.summary.statement_transactions,
            stored.summary.accounting_transactions,
            stored.summary.payment_records,
        )

        with self.database.session() as session:
            repo = Repository(session)
            recon = repo.get_reconciliation(reconciliation_id)
            if recon is None:
                raise ReconciliationServiceError("Reconciliation not found.")
            repo.update_result(reconciliation_id, updated)
            for extra in new_results:
                repo.add_result(reconciliation_id, extra)
            recon.summary_json = summary.model_dump(mode="json")
            recon.balance_json = balance.model_dump(mode="json")
            if recon.outcome:
                recon.outcome, recon.finalised_at = None, None  # decision changed the picture
            repo.add_audit_event(
                reconciliation_id,
                f"REVIEW_{action.value}",
                f"{action_label(result, action)}: {previous.label} -> {updated.status.label}.",
                {"result_id": result_id, "note": note,
                 "statement_txn": result.statement_txn_id,
                 "accounting_txn": result.accounting_txn_id},
            )  # fmt: skip
        logger.info("Review %s on result %s (reconciliation %s)",
                    action.value, result_id, reconciliation_id)  # fmt: skip

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _accept(result: MatchResult, lookup, engine: ReconciliationEngine) -> MatchResult:
        stmt = lookup.get(result.statement_txn_id or "")
        acct = lookup.get(result.accounting_txn_id or "")
        if stmt and acct and result.kind != ResultKind.PAYMENT:
            pair = MatchPair(stmt, acct, MatchMethod.MANUAL, result.confidence_score, 100)
            updated = engine.build_pair_result(pair)
            updated.confidence_score = result.confidence_score
            updated.confidence_band = result.confidence_band
            updated.matching_method = result.matching_method
            updated.matching_reason = (
                "Match confirmed by reviewer. " + updated.matching_reason.split(". ", 1)[-1]
            )
            if "REFERENCE_FORMAT" in updated.issues or (
                stmt.invoice_number_normalized != acct.invoice_number_normalized
            ):
                updated.issues = sorted({*updated.issues, "REFERENCE_CONFIRMED"} - {"REFERENCE"})
            updated.review_status = ReviewStatus.ACCEPTED
            return updated
        if stmt and acct and result.status == MatchStatus.PAYMENT_MISMATCH:
            updated = result.model_copy()
        elif stmt and acct:
            updated = result.model_copy(update={"status": MatchStatus.MATCHED})
        else:
            updated = result.model_copy()
        updated.review_status = ReviewStatus.ACCEPTED
        return updated

    @staticmethod
    def _reject(result: MatchResult, lookup, engine: ReconciliationEngine, period_end):
        stmt = lookup.get(result.statement_txn_id or "")
        acct = lookup.get(result.accounting_txn_id or "")
        if result.status == MatchStatus.DUPLICATE:
            txn = stmt or acct
            assert txn is not None
            if stmt:
                updated = engine.missing_result(txn, [])
            else:
                updated = engine.accounting_only_result(txn, period_end)
            updated.matching_reason = "Reviewer confirmed this is not a duplicate. " + (
                updated.matching_reason
            )
            updated.review_status = ReviewStatus.REJECTED
            return updated, []

        if result.kind == ResultKind.PAYMENT:
            updated = result.model_copy(
                update={
                    "status": MatchStatus.PAYMENT_MISMATCH,
                    "accounting_txn_id": None,
                    "accounting_amount": None,
                    "amount_difference": None,
                    "days_difference": None,
                    "matching_method": MatchMethod.MANUAL,
                    "matching_reason": "Reviewer rejected the suggested payment match. The "
                    "statement payment was not found in the payment records.",
                }
            )
            extra = MatchResult(
                status=MatchStatus.PAYMENT_MISMATCH,
                kind=ResultKind.PAYMENT,
                accounting_txn_id=acct.transaction_id if acct else None,
                accounting_amount=acct.amount if acct else None,
                matching_method=MatchMethod.MANUAL,
                matching_reason="Reviewer rejected the suggested payment match. This recorded "
                "payment is not on the supplier statement.",
                review_status=ReviewStatus.REJECTED,
            )
        else:
            assert stmt is not None and acct is not None
            updated = engine.missing_result(stmt, [])
            extra = engine.accounting_only_result(acct, period_end)
            updated.matching_reason = "Reviewer rejected the suggested match. " + (
                updated.matching_reason
            )
            extra.matching_reason = "Reviewer rejected the suggested match. " + (
                extra.matching_reason
            )
            extra.review_status = ReviewStatus.REJECTED
            extra.original_status = result.status
        updated.review_status = ReviewStatus.REJECTED
        return updated, [extra]
