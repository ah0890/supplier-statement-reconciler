"""Manual review of uncertain matches."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.config import get_settings
from src.models.reconciliation import MatchStatus, ReviewStatus
from src.services.import_service import FileRole, ImportService
from src.services.reconciliation_service import (
    NewReconciliation,
    ReconciliationService,
    ReconciliationServiceError,
)
from src.services.review_service import ReviewAction, ReviewService, available_actions

STATEMENT = b"""Date,Invoice No,Details,Debit,Credit,Balance
01/09/2026,,Opening balance,,,0.00
03/09/2026,INV-10025,Widgets,500.00,,500.00
04/09/2026,INV-2001,Bolts,100.00,,600.00
30/09/2026,,Closing balance,,,600.00
"""
BILLS = b"""Bill No,Bill Date,Total
INV-10052,03/09/2026,500.00
INV-2001,04/09/2026,100.00
INV-2001,04/09/2026,100.00
"""


@pytest.fixture
def setup(database, tmp_path):
    settings = replace(get_settings(), upload_dir=tmp_path / "uploads")
    reconciliations = ReconciliationService(database)
    importer = ImportService(database, settings)
    recon_id = reconciliations.create(NewReconciliation(supplier_name="Acme", currency="GBP"))
    importer.upload(recon_id, FileRole.STATEMENT, "s.csv", STATEMENT)
    importer.upload(recon_id, FileRole.ACCOUNTING, "b.csv", BILLS)
    reconciliations.run(recon_id)
    return reconciliations, ReviewService(database, reconciliations), recon_id


def _result(reconciliations, recon_id, status):
    return next(r for r in reconciliations.stored_run(recon_id).results if r.status == status)


def test_fuzzy_match_is_pending_until_reviewed(setup):
    reconciliations, _, recon_id = setup
    fuzzy = _result(reconciliations, recon_id, MatchStatus.REFERENCE_MISMATCH)
    assert fuzzy.review_status == ReviewStatus.PENDING
    stored = reconciliations.stored_run(recon_id)
    assert stored.summary.pending_reviews >= 1
    assert stored.summary.suggested_outcome.value == "NOT_RECONCILED"


def test_accept_match(setup):
    reconciliations, reviews, recon_id = setup
    fuzzy = _result(reconciliations, recon_id, MatchStatus.REFERENCE_MISMATCH)
    reviews.review(recon_id, fuzzy.result_id, ReviewAction.ACCEPT, "Typo confirmed by supplier")
    updated = next(
        r for r in reconciliations.stored_run(recon_id).results if r.result_id == fuzzy.result_id
    )
    assert updated.review_status == ReviewStatus.ACCEPTED
    assert updated.status == MatchStatus.MATCHED_WITH_TOLERANCE
    assert updated.original_status == MatchStatus.REFERENCE_MISMATCH
    assert updated.review_note == "Typo confirmed by supplier"
    assert "confirmed by reviewer" in updated.matching_reason.lower()
    events = [e["Event"] for e in reconciliations.audit_trail(recon_id)]
    assert "Review Accept" in events


def test_reject_match_splits_into_missing_and_accounting_only(setup):
    reconciliations, reviews, recon_id = setup
    fuzzy = _result(reconciliations, recon_id, MatchStatus.REFERENCE_MISMATCH)
    before = len(reconciliations.stored_run(recon_id).results)
    reviews.review(recon_id, fuzzy.result_id, ReviewAction.REJECT)
    stored = reconciliations.stored_run(recon_id)
    assert len(stored.results) == before + 1
    assert stored.summary.count(MatchStatus.MISSING_IN_ACCOUNTING) == 1
    assert stored.summary.count(MatchStatus.NOT_ON_SUPPLIER_STATEMENT) == 1
    assert stored.summary.count(MatchStatus.REFERENCE_MISMATCH) == 0
    # Balance breakdown follows the decision.
    assert "Items missing in accounting" in stored.balance.explained_difference


def test_mark_unresolved(setup):
    reconciliations, reviews, recon_id = setup
    fuzzy = _result(reconciliations, recon_id, MatchStatus.REFERENCE_MISMATCH)
    reviews.review(recon_id, fuzzy.result_id, ReviewAction.UNRESOLVED)
    stored = reconciliations.stored_run(recon_id)
    assert stored.summary.count(MatchStatus.UNRESOLVED) == 1


def test_duplicate_can_be_confirmed_or_rejected(setup):
    reconciliations, reviews, recon_id = setup
    duplicate = _result(reconciliations, recon_id, MatchStatus.DUPLICATE)
    assert ReviewAction.REJECT in available_actions(duplicate)
    reviews.review(recon_id, duplicate.result_id, ReviewAction.REJECT, "Two separate deliveries")
    stored = reconciliations.stored_run(recon_id)
    assert stored.summary.count(MatchStatus.DUPLICATE) == 0
    assert stored.summary.count(MatchStatus.NOT_ON_SUPPLIER_STATEMENT) == 1


def test_reject_not_available_for_single_sided_results(setup):
    reconciliations, reviews, recon_id = setup
    fuzzy = _result(reconciliations, recon_id, MatchStatus.REFERENCE_MISMATCH)
    reviews.review(recon_id, fuzzy.result_id, ReviewAction.REJECT)
    missing = _result(reconciliations, recon_id, MatchStatus.MISSING_IN_ACCOUNTING)
    with pytest.raises(ReconciliationServiceError):
        reviews.review(recon_id, missing.result_id, ReviewAction.REJECT)


def test_review_clears_finalised_outcome(setup):
    reconciliations, reviews, recon_id = setup
    from src.models.reconciliation import ReconciliationOutcome

    reconciliations.finalise(recon_id, ReconciliationOutcome.NOT_RECONCILED, "first pass")
    fuzzy = _result(reconciliations, recon_id, MatchStatus.REFERENCE_MISMATCH)
    reviews.review(recon_id, fuzzy.result_id, ReviewAction.ACCEPT)
    assert reconciliations.get(recon_id).outcome is None


def test_finalise_requires_fresh_run(setup, database, tmp_path):
    reconciliations, _, recon_id = setup
    from src.models.reconciliation import ReconciliationOutcome

    importer = ImportService(database, replace(get_settings(), upload_dir=tmp_path / "u2"))
    importer.upload(recon_id, FileRole.ACCOUNTING, "b.csv", BILLS)
    with pytest.raises(ReconciliationServiceError, match="Re-run"):
        reconciliations.finalise(recon_id, ReconciliationOutcome.RECONCILED)
