"""Database and repository behaviour."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.database.repository import Repository
from src.models.reconciliation import (
    MatchMethod,
    MatchResult,
    MatchStatus,
    ReconciliationConfig,
    ReviewStatus,
)
from src.models.transaction import CanonicalTransaction, TransactionSource, TransactionType


def _create_recon(repo: Repository):
    supplier = repo.get_or_create_supplier("Acme Supplies Ltd", "ACME SUPPLIES")
    return repo.create_reconciliation(
        supplier,
        client_name="Demo Client",
        supplier_account="ACC-1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        currency="GBP",
    )


def test_supplier_get_or_create_is_idempotent(database):
    with database.session() as session:
        repo = Repository(session)
        first = repo.get_or_create_supplier("Acme Ltd", "ACME")
        second = repo.get_or_create_supplier("ACME LIMITED", "ACME")
        assert first.id == second.id


def test_transactions_round_trip_with_exact_decimals(database):
    txn = CanonicalTransaction(
        source=TransactionSource.STATEMENT,
        transaction_type=TransactionType.CREDIT_NOTE,
        invoice_number="CN-001",
        invoice_number_normalized="CN001",
        invoice_date=date(2026, 9, 5),
        amount=Decimal("1234.57"),
        tax=Decimal("205.76"),
        raw_data={"Ref": "CN-001"},
    )
    with database.session() as session:
        repo = Repository(session)
        recon = _create_recon(repo)
        repo.replace_transactions(recon.id, TransactionSource.STATEMENT, [txn])
        recon_id = recon.id

    with database.session() as session:
        loaded = Repository(session).get_transactions(recon_id, TransactionSource.STATEMENT)
    assert len(loaded) == 1
    assert loaded[0].amount == Decimal("1234.57")
    assert isinstance(loaded[0].amount, Decimal)
    assert loaded[0].transaction_type == TransactionType.CREDIT_NOTE
    assert loaded[0].signed_amount == Decimal("-1234.57")
    assert loaded[0].raw_data == {"Ref": "CN-001"}


def test_replace_transactions_replaces_previous_import(database):
    def txn(n):
        return CanonicalTransaction(
            source=TransactionSource.ACCOUNTING, invoice_number=n, amount=Decimal("1")
        )

    with database.session() as session:
        repo = Repository(session)
        recon = _create_recon(repo)
        repo.replace_transactions(recon.id, TransactionSource.ACCOUNTING, [txn("A"), txn("B")])
        repo.replace_transactions(recon.id, TransactionSource.ACCOUNTING, [txn("C")])
        assert repo.count_transactions(recon.id, TransactionSource.ACCOUNTING) == 1


def test_results_round_trip_and_update(database):
    result = MatchResult(
        status=MatchStatus.REFERENCE_MISMATCH,
        statement_txn_id="s1",
        accounting_txn_id="a1",
        confidence_score=72,
        matching_method=MatchMethod.FUZZY_REFERENCE,
        amount_difference=Decimal("0.00"),
        requires_review=True,
        review_status=ReviewStatus.PENDING,
    )
    with database.session() as session:
        repo = Repository(session)
        recon = _create_recon(repo)
        repo.replace_results(recon.id, [result])
        result.review_status = ReviewStatus.ACCEPTED
        result.status = MatchStatus.MATCHED
        repo.update_result(recon.id, result)
        loaded = repo.get_results(recon.id)
    assert loaded[0].review_status == ReviewStatus.ACCEPTED
    assert loaded[0].status == MatchStatus.MATCHED
    assert loaded[0].amount_difference == Decimal("0.00")


def test_delete_reconciliation_removes_children(database):
    with database.session() as session:
        repo = Repository(session)
        recon = _create_recon(repo)
        repo.replace_transactions(
            recon.id,
            TransactionSource.STATEMENT,
            [CanonicalTransaction(source=TransactionSource.STATEMENT, amount=Decimal("5"))],
        )
        repo.add_audit_event(recon.id, "TEST", "created", {"amount": Decimal("1.00")})
        recon_id = recon.id
        repo.delete_reconciliation(recon_id)
        assert repo.get_reconciliation(recon_id) is None
        assert repo.count_transactions(recon_id, TransactionSource.STATEMENT) == 0


def test_audit_details_are_json_safe(database):
    with database.session() as session:
        repo = Repository(session)
        recon = _create_recon(repo)
        repo.add_audit_event(
            recon.id, "RUN", "ran", {"tol": Decimal("0.01"), "d": date(2026, 1, 1)}
        )
        event = repo.get_audit_events(recon.id)[0]
    assert event.details == {"tol": "0.01", "d": "2026-01-01"}


def test_settings_store(database):
    with database.session() as session:
        repo = Repository(session)
        repo.set_setting("k", "1")
        repo.set_setting("k", "2")
        assert repo.get_setting("k") == "2"
        assert repo.get_setting("missing") is None


def test_negative_amount_rejected_by_model():
    with pytest.raises(ValidationError):
        CanonicalTransaction(source=TransactionSource.STATEMENT, amount=Decimal("-1"))


def test_config_validates_threshold_order():
    with pytest.raises(ValidationError):
        ReconciliationConfig(possible_threshold=90, strong_threshold=80)
    with pytest.raises(ValidationError):
        ReconciliationConfig(amount_tolerance=Decimal("-0.01"))
