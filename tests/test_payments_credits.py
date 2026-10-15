"""Credit notes, payment matching and allocation."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.models.reconciliation import MatchStatus, ReconciliationConfig, ResultKind
from src.models.transaction import TransactionSource, TransactionType
from src.reconciliation.confidence import ConfidenceScorer
from src.reconciliation.engine import ReconciliationEngine
from src.reconciliation.payments import PaymentReconciler
from tests.conftest import make_txn

D = date(2026, 9, 15)
CN = TransactionType.CREDIT_NOTE
PAY = TransactionType.PAYMENT


def _payment(source, ref, amount, day=D, related=None):
    return make_txn(source, None, amount, day, PAY, reference=ref, related_invoice_number=related)


def _reconciler():
    config = ReconciliationConfig()
    return PaymentReconciler(config, ConfidenceScorer(config))


# --------------------------------------------------------------- credit notes


def test_matched_credit_note_gets_credit_note_status(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("CN-0007", "150.00", D, CN, related_invoice_number="INV-1001")],
        [acct("CN0007", "150.00", D, CN)],
    )
    result = run.results[0]
    assert result.status == MatchStatus.CREDIT_NOTE
    assert result.kind == ResultKind.CREDIT_NOTE
    assert "relates to invoice INV-1001" in result.matching_reason


def test_credit_note_amount_mismatch_is_reported(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("CN-7", "150.00", D, CN)], [acct("CN-7", "100.00", D, CN)]
    )
    assert run.results[0].status == MatchStatus.AMOUNT_MISMATCH
    assert run.results[0].kind == ResultKind.CREDIT_NOTE


def test_credit_note_missing_in_accounting(stmt):
    run = ReconciliationEngine().run([stmt("CN-8", "40.00", D, CN)], [])
    assert run.results[0].status == MatchStatus.MISSING_IN_ACCOUNTING
    assert run.results[0].kind == ResultKind.CREDIT_NOTE
    assert "Credit note CN-8" in run.results[0].matching_reason


def test_credit_note_signed_amount_is_negative(stmt):
    assert stmt("CN-1", "40.00", D, CN).signed_amount == Decimal("-40.00")


# ------------------------------------------------------------------ payments


def test_statement_payment_matched_by_reference():
    results = _reconciler().match(
        [_payment(TransactionSource.STATEMENT, "PMT-55", "1000.00")],
        [_payment(TransactionSource.PAYMENTS, "pmt 55", "1000.00", D + timedelta(days=1))],
    )
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].days_difference == 1


def test_payment_amount_difference_is_payment_mismatch():
    results = _reconciler().match(
        [_payment(TransactionSource.STATEMENT, "PMT-55", "1000.00")],
        [_payment(TransactionSource.PAYMENTS, "PMT-55", "900.00")],
    )
    assert results[0].status == MatchStatus.PAYMENT_MISMATCH
    assert results[0].amount_difference == Decimal("100.00")
    assert results[0].requires_review


def test_payment_matched_by_amount_and_date_requires_review():
    results = _reconciler().match(
        [_payment(TransactionSource.STATEMENT, "BACS", "250.00")],
        [_payment(TransactionSource.PAYMENTS, "Bank transfer 99", "250.00", D + timedelta(days=4))],
    )
    assert results[0].status == MatchStatus.MATCHED_WITH_TOLERANCE
    assert results[0].requires_review


def test_ambiguous_payments_are_not_guessed():
    results = _reconciler().match(
        [_payment(TransactionSource.STATEMENT, None, "250.00")],
        [
            _payment(TransactionSource.PAYMENTS, "A", "250.00"),
            _payment(TransactionSource.PAYMENTS, "B", "250.00", D + timedelta(days=1)),
        ],
    )
    assert sorted(r.status.value for r in results) == ["PAYMENT_MISMATCH"] * 3


def test_recorded_payment_after_period_end_is_timing_difference():
    results = _reconciler().match(
        [], [_payment(TransactionSource.PAYMENTS, "PMT-9", "10.00", date(2026, 10, 2))],
        period_end=date(2026, 9, 30),
    )  # fmt: skip
    assert results[0].status == MatchStatus.PAYMENT_MISMATCH
    assert "AFTER_PERIOD_END" in results[0].issues
    assert "timing difference" in results[0].matching_reason


def test_statement_payments_without_payment_data_are_unverified():
    results = _reconciler().match([_payment(TransactionSource.STATEMENT, "PMT-1", "5.00")], [])
    assert results[0].status == MatchStatus.UNRESOLVED
    assert results[0].issues == ["NO_PAYMENT_DATA"]
    assert "could not be verified" in results[0].matching_reason


# ---------------------------------------------------------------- allocation


def test_outstanding_is_invoice_minus_payments_minus_credits(stmt):
    invoice = stmt("INV-1", "1000.00")
    credit = stmt("CN-1", "100.00", txn_type=CN, related_invoice_number="INV 1")
    payment = _payment(TransactionSource.STATEMENT, "PMT-1", "600.00", related="INV-1")
    allocations, unallocated, overpaid = _reconciler().allocate(
        [invoice, credit], [payment], "STATEMENT"
    )
    assert len(allocations) == 1
    allocation = allocations[0]
    assert allocation.outstanding == Decimal("300.00")
    assert allocation.allocation_status == "PART_PAID"
    assert unallocated == [] and overpaid == []


def test_overpayment_is_flagged(stmt):
    invoice = stmt("INV-1", "100.00")
    payment = _payment(TransactionSource.STATEMENT, "PMT-1", "150.00", related="INV-1")
    allocations, _, overpaid = _reconciler().allocate([invoice], [payment], "STATEMENT")
    assert allocations[0].allocation_status == "OVERPAID"
    assert overpaid[0].status == MatchStatus.PAYMENT_MISMATCH
    assert "overpaid by 50.00" in overpaid[0].matching_reason


def test_unallocated_payments_are_never_assumed(stmt):
    invoice = stmt("INV-1", "100.00")
    payments = [
        _payment(TransactionSource.PAYMENTS, "PMT-1", "100.00"),
        _payment(TransactionSource.PAYMENTS, "PMT-2", "20.00", related="INV-404"),
    ]
    allocations, unallocated, _ = _reconciler().allocate([invoice], payments, "ACCOUNTING")
    assert allocations == []
    assert len(unallocated) == 2
    assert "no invoice allocation" in unallocated[0]
    assert "INV-404" in unallocated[1]


def test_engine_includes_payments_and_allocations(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("INV-1", "100.00"), _payment(TransactionSource.STATEMENT, "PMT-1", "60.00",
                                           related="INV-1")],
        [acct("INV-1", "100.00")],
        [_payment(TransactionSource.PAYMENTS, "PMT-1", "60.00", related="INV-1")],
    )  # fmt: skip
    statuses = sorted(r.status.value for r in run.results)
    assert statuses == ["MATCHED", "MATCHED"]
    assert {a.source for a in run.allocations} == {"STATEMENT", "ACCOUNTING"}
    assert all(a.outstanding == Decimal("40.00") for a in run.allocations)


def test_payment_run_split_across_allocations_matches_combined_total():
    results = _reconciler().match(
        [_payment(TransactionSource.STATEMENT, "PMT-77", "1500.00")],
        [
            _payment(TransactionSource.PAYMENTS, "PMT-77", "1000.00", related="INV-1"),
            _payment(TransactionSource.PAYMENTS, "PMT-77", "500.00", related="INV-2"),
        ],
    )
    assert len(results) == 1
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].accounting_amount == Decimal("1500.00")
    assert len(results[0].related_txn_ids) == 1
    assert "2 recorded payment allocations" in results[0].matching_reason
