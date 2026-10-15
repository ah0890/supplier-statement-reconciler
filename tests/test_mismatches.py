"""Amount, date and reference mismatch rules."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.models.reconciliation import MatchStatus, ReconciliationConfig
from src.reconciliation.engine import ReconciliationEngine

D = date(2026, 10, 1)


def _one(statement, accounting, **config):
    run = ReconciliationEngine(ReconciliationConfig(**config)).run(statement, accounting)
    assert len(run.results) == 1, run.results
    return run.results[0]


def test_specification_example_amount_and_date_differences(stmt, acct):
    result = _one(
        [stmt("INV-10025", "10500.00", D)],
        [acct("INV-10025", "10250.00", D + timedelta(days=4))],
    )
    assert result.status == MatchStatus.AMOUNT_MISMATCH
    assert result.statement_amount == Decimal("10500.00")
    assert result.accounting_amount == Decimal("10250.00")
    assert result.amount_difference == Decimal("250.00")
    assert result.days_difference == 4
    assert set(result.issues) == {"AMOUNT", "DATE"}
    assert "difference 250.00" in result.matching_reason
    assert "4 days" in result.matching_reason


def test_exact_match(stmt, acct):
    result = _one([stmt("INV-1", "99.99", D)], [acct("INV-1", "99.99", D)])
    assert result.status == MatchStatus.MATCHED
    assert result.issues == []
    assert "agree" in result.matching_reason


@pytest.mark.parametrize(
    ("statement_amount", "tolerance", "expected"),
    [
        ("100.00", "0.01", MatchStatus.MATCHED),
        ("100.01", "0.01", MatchStatus.MATCHED_WITH_TOLERANCE),
        ("99.99", "0.01", MatchStatus.MATCHED_WITH_TOLERANCE),
        ("100.02", "0.01", MatchStatus.AMOUNT_MISMATCH),
        ("100.50", "1.00", MatchStatus.MATCHED_WITH_TOLERANCE),
        ("100.01", "0", MatchStatus.AMOUNT_MISMATCH),
    ],
)
def test_amount_tolerance_boundaries(stmt, acct, statement_amount, tolerance, expected):
    result = _one(
        [stmt("INV-1", statement_amount, D)],
        [acct("INV-1", "100.00", D)],
        amount_tolerance=Decimal(tolerance),
    )
    assert result.status == expected


@pytest.mark.parametrize(
    ("days", "tolerance", "expected"),
    [
        (0, 3, MatchStatus.MATCHED),
        (3, 3, MatchStatus.MATCHED_WITH_TOLERANCE),
        (4, 3, MatchStatus.DATE_MISMATCH),
        (4, 7, MatchStatus.MATCHED_WITH_TOLERANCE),
        (1, 0, MatchStatus.DATE_MISMATCH),
    ],
)
def test_date_tolerance_boundaries(stmt, acct, days, tolerance, expected):
    result = _one(
        [stmt("INV-1", "10.00", D)],
        [acct("INV-1", "10.00", D + timedelta(days=days))],
        date_tolerance_days=tolerance,
    )
    assert result.status == expected
    assert result.days_difference == days


def test_missing_date_does_not_cause_date_mismatch(stmt, acct):
    result = _one([stmt("INV-1", "10.00", None)], [acct("INV-1", "10.00", D)])
    assert result.status == MatchStatus.MATCHED
    assert result.days_difference is None
    assert "could not be compared" in result.matching_reason


def test_reference_mismatch_for_fuzzy_match(stmt, acct):
    result = _one([stmt("INV-10025", "500.00", D)], [acct("INV-10052", "500.00", D)])
    assert result.status == MatchStatus.REFERENCE_MISMATCH
    assert "References differ: INV-10025 vs INV-10052" in result.matching_reason


def test_reference_format_difference_is_tolerated_not_exact(stmt, acct):
    result = _one([stmt("INV-0045123", "250.00", D)], [acct("45123", "250.00", D)])
    assert result.status == MatchStatus.MATCHED_WITH_TOLERANCE
    assert "REFERENCE_FORMAT" in result.issues


def test_amount_mismatch_has_priority_over_reference(stmt, acct):
    result = _one([stmt("INV-45123", "250.00", D)], [acct("45123", "275.00", D)])
    assert result.status == MatchStatus.AMOUNT_MISMATCH
    assert result.amount_difference == Decimal("-25.00")


def test_negative_difference_sign(stmt, acct):
    result = _one([stmt("INV-1", "90.00", D)], [acct("INV-1", "100.00", D)])
    assert result.amount_difference == Decimal("-10.00")
