"""Duplicate detection rules."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.models.reconciliation import MatchStatus, ReconciliationConfig, ReviewStatus
from src.reconciliation.duplicate_detector import DuplicateDetector
from src.reconciliation.engine import ReconciliationEngine

D = date(2026, 9, 10)


def _detector():
    return DuplicateDetector(ReconciliationConfig())


def test_exact_duplicate_bill_is_detected_and_excluded(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("INV-500", "750.00", D)],
        [acct("INV-500", "750.00", D, source_row=2), acct("INV 500", "750.00", D, source_row=7)],
    )
    statuses = sorted(r.status for r in run.results)
    assert statuses == [MatchStatus.DUPLICATE, MatchStatus.MATCHED]
    duplicate = next(r for r in run.results if r.status == MatchStatus.DUPLICATE)
    assert duplicate.confidence_score == 98
    assert duplicate.accounting_amount == Decimal("750.00")
    assert duplicate.review_status == ReviewStatus.NOT_REQUIRED
    assert "entered twice" in duplicate.matching_reason


def test_same_reference_dates_apart_has_lower_confidence(acct):
    findings = _detector().find_reference_duplicates(
        [acct("INV-1", "10.00", D), acct("INV-1", "10.00", D + timedelta(days=2))]
    )
    assert findings[0].confidence == 92
    findings = _detector().find_reference_duplicates(
        [acct("INV-1", "10.00", D), acct("INV-1", "10.00", D + timedelta(days=40))]
    )
    assert findings[0].confidence == 85


def test_same_reference_different_amount_is_only_possible_duplicate(stmt, acct):
    finding = _detector().find_reference_duplicates(
        [acct("INV-1", "100.00", D), acct("INV-1", "120.00", D)]
    )[0]
    assert finding.confidence == 65
    assert not finding.exclude_from_matching

    # The copy matching the statement amount is matched; the other is a possible duplicate.
    run = ReconciliationEngine().run(
        [stmt("INV-1", "120.00", D)],
        [acct("INV-1", "100.00", D, source_row=1), acct("INV-1", "120.00", D, source_row=2)],
    )
    by_status = {r.status: r for r in run.results}
    assert by_status[MatchStatus.MATCHED].accounting_amount == Decimal("120.00")
    duplicate = by_status[MatchStatus.DUPLICATE]
    assert duplicate.accounting_amount == Decimal("100.00")
    assert duplicate.requires_review


def test_similar_records_with_different_references_are_not_auto_duplicates(acct):
    findings = _detector().find_reference_duplicates(
        [acct("INV-1", "100.00", D), acct("INV-2", "100.00", D)]
    )
    assert findings == []


def test_near_duplicate_with_similar_reference(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("INV-20451", "300.00", D)],
        [
            acct("INV-20451", "300.00", D, source_row=1),
            acct("INV-204511", "300.00", D, source_row=2),
        ],
    )
    duplicate = next(r for r in run.results if r.status == MatchStatus.DUPLICATE)
    assert duplicate.confidence_score == 75
    assert duplicate.requires_review


def test_unrelated_references_with_same_amount_are_not_duplicates(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("INV-1", "300.00", D)],
        [acct("INV-1", "300.00", D, source_row=1), acct("ZZ-998877", "300.00", D, source_row=2)],
    )
    statuses = sorted(r.status.value for r in run.results)
    assert statuses == ["MATCHED", "NOT_ON_SUPPLIER_STATEMENT"]


def test_statement_side_duplicates(stmt, acct):
    run = ReconciliationEngine().run(
        [stmt("INV-9", "40.00", D, source_row=3), stmt("INV-9", "40.00", D, source_row=4)],
        [acct("INV-9", "40.00", D)],
    )
    duplicate = next(r for r in run.results if r.status == MatchStatus.DUPLICATE)
    assert duplicate.statement_txn_id is not None
    assert "supplier statement" in duplicate.matching_reason


def test_blank_reference_duplicates_are_possible_only(acct):
    findings = _detector().find_near_duplicates(
        [acct(None, "55.00", D, source_row=2)],
        [acct("INV-55", "55.00", D, source_row=1), acct(None, "55.00", D, source_row=2)],
    )
    assert findings[0].confidence == 60


def test_duplicates_ignore_other_suppliers(acct):
    findings = _detector().find_reference_duplicates(
        [acct("INV-1", "10.00", D, supplier_name="Acme Ltd"),
         acct("INV-1", "10.00", D, supplier_name="Zenith Ltd")]
    )  # fmt: skip
    assert findings == []
