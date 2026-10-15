"""Matching engine: layered matching, confidence and candidate handling."""

from __future__ import annotations

import time
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.models.reconciliation import (
    ConfidenceBand,
    MatchMethod,
    MatchStatus,
    ReconciliationConfig,
    ReviewStatus,
)
from src.models.transaction import TransactionSource, TransactionType
from src.reconciliation.confidence import ConfidenceScorer
from src.reconciliation.engine import ReconciliationEngine
from src.reconciliation.matcher import InvoiceMatcher
from tests.conftest import make_txn

D = date(2026, 9, 10)


def _match(statement, accounting, **config):
    return InvoiceMatcher(ReconciliationConfig(**config)).match(statement, accounting)


def test_level1_exact_reference_with_formatting_differences(stmt, acct):
    out = _match([stmt("INV-00123", "100.00")], [acct("inv 00123", "100.00")])
    assert len(out.pairs) == 1
    pair = out.pairs[0]
    assert pair.method == MatchMethod.EXACT_REFERENCE
    assert pair.confidence == 100


def test_level2_core_reference_plus_amount(stmt, acct):
    out = _match([stmt("INV-0045123", "250.00")], [acct("45123", "250.00")])
    assert out.pairs[0].method == MatchMethod.REFERENCE_AMOUNT
    assert 80 <= out.pairs[0].confidence < 95


def test_level3_core_reference_plus_date(stmt, acct):
    out = _match([stmt("INV-45123", "250.00", D)], [acct("45123", "260.00", D)])
    assert out.pairs[0].method == MatchMethod.REFERENCE_DATE


def test_level4_fuzzy_reference_requires_review(stmt, acct):
    out = _match([stmt("INV-10025", "500.00", D)], [acct("INV-10052", "500.00", D)])
    pair = out.pairs[0]
    assert pair.method == MatchMethod.FUZZY_REFERENCE
    assert pair.confidence < 90
    engine_result = ReconciliationEngine().run(
        [stmt("INV-10025", "500.00", D)], [acct("INV-10052", "500.00", D)]
    )
    result = engine_result.results[0]
    assert result.status == MatchStatus.REFERENCE_MISMATCH  # never silently "exact"
    assert result.requires_review
    assert result.review_status == ReviewStatus.PENDING


def test_fuzzy_threshold_is_respected(stmt, acct):
    statement = [stmt("INV-10025", "500.00", D)]
    accounting = [acct("INV-10052", "500.00", D)]
    strict = _match(statement, accounting, fuzzy_match_threshold=95)
    assert all(p.method != MatchMethod.FUZZY_REFERENCE for p in strict.pairs)


def test_sequential_invoice_numbers_with_different_amounts_do_not_fuzzy_match(stmt, acct):
    out = _match([stmt("INV-1003", "80.00", D)], [acct("INV-1009", "45.00", D)])
    assert out.pairs == []


def test_level5_amount_and_date_for_blank_reference(stmt, acct):
    out = _match([stmt(None, "321.45", D)], [acct("BILL-77", "321.45", D + timedelta(days=1))])
    assert out.pairs[0].method == MatchMethod.AMOUNT_DATE_PROXIMITY
    assert 60 <= out.pairs[0].confidence < 80


def test_level5_ambiguous_candidates_are_not_matched(stmt, acct):
    out = _match(
        [stmt(None, "100.00", D)],
        [acct("A-1", "100.00", D), acct("B-2", "100.00", D)],
    )
    assert out.pairs == []
    assert len(out.ambiguous[out.unmatched_statement[0].transaction_id]) == 2
    run = ReconciliationEngine().run(
        [stmt(None, "100.00", D)], [acct("A-1", "100.00", D), acct("B-2", "100.00", D)]
    )
    statuses = sorted(r.status.value for r in run.results)
    assert statuses == ["NOT_ON_SUPPLIER_STATEMENT", "NOT_ON_SUPPLIER_STATEMENT", "UNRESOLVED"]


def test_level5_outside_date_tolerance_not_matched(stmt, acct):
    out = _match([stmt(None, "100.00", D)], [acct("A-1", "100.00", D + timedelta(days=10))])
    assert out.pairs == []


def test_stronger_level_wins_over_earlier_fuzzy_candidate(stmt, acct):
    # The first statement row would fuzzy-match INV-1002, but INV-1002 belongs to the
    # second statement row exactly. Level ordering must protect the exact match.
    statement = [stmt("INV-1003", "50.00", D), stmt("INV-1002", "50.00", D)]
    accounting = [acct("INV-1002", "50.00", D)]
    out = _match(statement, accounting)
    assert len(out.pairs) == 1
    assert out.pairs[0].statement.invoice_number == "INV-1002"
    assert out.pairs[0].method == MatchMethod.EXACT_REFERENCE


def test_invoices_never_match_credit_notes(stmt, acct):
    out = _match(
        [stmt("1001", "10.00", D, TransactionType.CREDIT_NOTE)],
        [acct("1001", "10.00", D, TransactionType.INVOICE)],
    )
    assert out.pairs == []


def test_different_currencies_are_not_matched(stmt, acct):
    out = _match([stmt("INV-1", "10.00", currency="USD")], [acct("INV-1", "10.00")])
    assert out.pairs == []


def test_engine_reports_missing_and_accounting_only(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("INV-1", "10.00"), stmt("INV-2", "20.00")],
        [acct("INV-1", "10.00"), acct("INV-9", "90.00", date(2026, 10, 5))],
        period_end=date(2026, 9, 30),
    )
    by_status = {r.status: r for r in run.results}
    assert by_status[MatchStatus.MATCHED].confidence_band == ConfidenceBand.VERY_STRONG
    missing = by_status[MatchStatus.MISSING_IN_ACCOUNTING]
    assert missing.statement_amount == Decimal("20.00")
    assert "not found in the accounting records" in missing.matching_reason
    only = by_status[MatchStatus.NOT_ON_SUPPLIER_STATEMENT]
    assert "AFTER_PERIOD_END" in only.issues
    assert run.summary.count(MatchStatus.MATCHED) == 1
    assert run.summary.exceptions == 2


def test_amount_within_tolerance(stmt, acct):
    run = ReconciliationEngine(ReconciliationConfig(amount_tolerance=Decimal("0.05"))).run(
        [stmt("INV-1", "100.03")], [acct("INV-1", "100.00")]
    )
    result = run.results[0]
    assert result.status == MatchStatus.MATCHED_WITH_TOLERANCE
    assert result.amount_difference == Decimal("0.03")


# ---------------------------------------------------------------- confidence


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (100, ConfidenceBand.VERY_STRONG),
        (95, ConfidenceBand.VERY_STRONG),
        (94, ConfidenceBand.STRONG),
        (80, ConfidenceBand.STRONG),
        (79, ConfidenceBand.POSSIBLE),
        (60, ConfidenceBand.POSSIBLE),
        (59, ConfidenceBand.UNRESOLVED),
    ],
)
def test_confidence_bands(score, band):
    assert ConfidenceScorer(ReconciliationConfig()).band(score) == band


def test_confidence_thresholds_are_configurable():
    scorer = ConfidenceScorer(
        ReconciliationConfig(very_strong_threshold=99, strong_threshold=90, possible_threshold=70)
    )
    assert scorer.band(95) == ConfidenceBand.STRONG
    assert scorer.band(65) == ConfidenceBand.UNRESOLVED


def test_fuzzy_score_never_reaches_auto_accept():
    scorer = ConfidenceScorer(ReconciliationConfig())
    score = scorer.score(
        MatchMethod.FUZZY_REFERENCE, amount_diff=Decimal("0"), days_diff=0, reference_similarity=99
    )
    assert score <= 89
    assert scorer.requires_review(score, MatchMethod.FUZZY_REFERENCE)


def test_exact_reference_with_large_difference_loses_confidence():
    scorer = ConfidenceScorer(ReconciliationConfig())
    score = scorer.score(
        MatchMethod.EXACT_REFERENCE,
        amount_diff=Decimal("900"),
        days_diff=120,
        base_amount=Decimal("1000"),
    )
    assert score < 80


# --------------------------------------------------------------- performance


def test_matching_5000_records_is_fast():
    statement, accounting = [], []
    for i in range(5000):
        day = date(2026, 1, 1) + timedelta(days=i % 300)
        amount = f"{100 + (i * 37) % 9000}.{i % 100:02d}"
        statement.append(make_txn(TransactionSource.STATEMENT, f"INV-{i:06d}", amount, day))
        ref = f"INV{i:06d}" if i % 10 else f"INV-{i:06d}X"  # 10% need fuzzy/other levels
        accounting.append(make_txn(TransactionSource.ACCOUNTING, ref, amount, day))
    started = time.perf_counter()
    run = ReconciliationEngine().run(statement, accounting)
    elapsed = time.perf_counter() - started
    assert elapsed < 10, f"took {elapsed:.1f}s"
    paired = sum(bool(r.statement_txn_id and r.accounting_txn_id) for r in run.results)
    assert paired == 5000
