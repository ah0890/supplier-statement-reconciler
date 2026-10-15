"""Import service: storage, mapping, persistence and audit trail."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from src.config import get_settings
from src.database.repository import Repository
from src.models.transaction import TransactionSource
from src.parsers.columns import ColumnMapping
from src.services.import_service import FileRole, ImportService, ImportServiceError
from src.services.reconciliation_service import (
    NewReconciliation,
    ReconciliationService,
    ReconciliationServiceError,
)

STATEMENT = b"""Date,Invoice No,Details,Debit,Credit,Balance
01/09/2026,,Opening balance,,,500.00
03/09/2026,INV-1001,Widgets,1200.00,,1700.00
04/09/2026,INV-1002,Bolts,bad,,
10/09/2026,PMT-1,Payment,,500.00,1200.00
"""

BILLS = b"""Supplier,Bill No,Bill Date,Total
Acme Supplies Ltd,INV 1001,03/09/2026,1200.00
Other Co,Z-9,03/09/2026,10.00
"""


@pytest.fixture
def services(database, tmp_path):
    settings = replace(get_settings(), upload_dir=tmp_path / "uploads", max_upload_mb=1)
    recon_service = ReconciliationService(database)
    recon_id = recon_service.create(
        NewReconciliation(
            supplier_name="Acme Supplies Ltd",
            currency="GBP",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
        )
    )
    return ImportService(database, settings), recon_service, recon_id, settings


def test_statement_upload_persists_transactions_and_balances(services, database):
    importer, recon_service, recon_id, settings = services
    outcome = importer.upload(recon_id, FileRole.STATEMENT, "../../stmt.csv", STATEMENT)
    assert outcome.persisted
    assert len(outcome.result.transactions) == 2
    assert outcome.result.report.errors == 1

    stored = list((settings.upload_dir / f"reconciliation_{recon_id}" / "statement").iterdir())
    assert len(stored) == 1 and stored[0].name.endswith("_stmt.csv")

    recon = recon_service.get(recon_id)
    assert recon.opening_balance == Decimal("500.00")
    assert recon.closing_balance == Decimal("1200.00")

    with database.session() as session:
        repo = Repository(session)
        txns = repo.get_transactions(recon_id, TransactionSource.STATEMENT)
        assert txns[0].invoice_number_normalized == "INV1001"
        events = [e.event_type for e in repo.get_audit_events(recon_id)]
    assert events == ["RECONCILIATION_CREATED", "FILE_UPLOADED", "MAPPING_APPLIED"]


def test_errors_must_be_acknowledged(services):
    importer, _, recon_id, _ = services
    importer.upload(recon_id, FileRole.STATEMENT, "stmt.csv", STATEMENT)
    summary = importer.validation_status(recon_id, FileRole.STATEMENT)
    assert ImportService.blocking_errors(summary)
    importer.acknowledge_errors(recon_id, FileRole.STATEMENT)
    assert not ImportService.blocking_errors(
        importer.validation_status(recon_id, FileRole.STATEMENT)
    )


def test_accounting_upload_filters_other_suppliers(services):
    importer, _, recon_id, _ = services
    outcome = importer.upload(recon_id, FileRole.ACCOUNTING, "bills.csv", BILLS)
    assert [t.invoice_number for t in outcome.result.transactions] == ["INV 1001"]
    assert outcome.result.excluded_other_suppliers == 1


def test_remapping_replaces_transactions(services, database):
    importer, _, recon_id, _ = services
    importer.upload(recon_id, FileRole.ACCOUNTING, "bills.csv", BILLS)
    mapping = ColumnMapping(
        fields={"invoice_number": "Bill No", "date": "Bill Date", "amount": "Total"}
    )
    outcome = importer.apply_mapping(recon_id, FileRole.ACCOUNTING, mapping)
    # Without a supplier column every row belongs to the reconciliation supplier.
    assert len(outcome.result.transactions) == 2
    with database.session() as session:
        assert Repository(session).count_transactions(recon_id, TransactionSource.ACCOUNTING) == 2


def test_incomplete_mapping_persists_nothing(services, database):
    importer, _, recon_id, _ = services
    importer.upload(recon_id, FileRole.ACCOUNTING, "bills.csv", BILLS)
    outcome = importer.apply_mapping(
        recon_id, FileRole.ACCOUNTING, ColumnMapping(fields={"date": "Bill Date"})
    )
    assert not outcome.mapping_complete
    with database.session() as session:
        assert Repository(session).count_transactions(recon_id, TransactionSource.ACCOUNTING) == 0


def test_rejected_uploads(services):
    importer, _, recon_id, _ = services
    with pytest.raises(ImportServiceError, match="not accepted"):
        importer.upload(recon_id, FileRole.ACCOUNTING, "bills.pdf", b"%PDF-1.4")
    with pytest.raises(ImportServiceError, match="maximum"):
        importer.upload(recon_id, FileRole.STATEMENT, "big.csv", b"a," * 600_000)


def test_manual_balances_survive_reimport(services):
    importer, recon_service, recon_id, _ = services
    importer.upload(recon_id, FileRole.STATEMENT, "stmt.csv", STATEMENT)
    importer.set_balances(recon_id, Decimal("400.00"), Decimal("1200.00"))
    importer.apply_mapping(recon_id, FileRole.STATEMENT)
    recon = recon_service.get(recon_id)
    assert recon.opening_balance == Decimal("400.00")
    assert recon.opening_balance_source == "manual"


def test_delete_reconciliation_removes_uploaded_files(services):
    importer, recon_service, recon_id, settings = services
    importer.upload(recon_id, FileRole.STATEMENT, "stmt.csv", STATEMENT)
    recon_service.delete(recon_id)
    folder = settings.upload_dir / f"reconciliation_{recon_id}" / "statement"
    assert not any(folder.iterdir())
    assert recon_service.get(recon_id) is None


def test_create_validation(services):
    _, recon_service, _, _ = services
    with pytest.raises(ReconciliationServiceError):
        recon_service.create(NewReconciliation(supplier_name=" ", currency="GBP"))
    with pytest.raises(ReconciliationServiceError):
        recon_service.create(
            NewReconciliation(
                supplier_name="X",
                currency="GBP",
                period_start=date(2026, 10, 1),
                period_end=date(2026, 9, 1),
            )
        )


def test_previous_suppliers_are_listed(services):
    _, recon_service, _, _ = services
    suppliers = recon_service.suppliers()
    assert [s.name for s in suppliers] == ["Acme Supplies Ltd"]
    assert suppliers[0].normalized_name == "ACME SUPPLIES"
