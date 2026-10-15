"""Repository: the only module that issues queries.

Services talk to the repository using Pydantic domain models; ORM rows never
leak into the reconciliation engine.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.database.models import (
    AccountingTransactionRow,
    AppSettingRow,
    AuditEventRow,
    PaymentRow,
    ReconciliationResultRow,
    ReconciliationRow,
    StatementTransactionRow,
    SupplierRow,
    UploadedFileRow,
    utcnow,
)
from src.models.reconciliation import (
    ConfidenceBand,
    MatchMethod,
    MatchResult,
    MatchStatus,
    ResultKind,
    ReviewStatus,
)
from src.models.transaction import CanonicalTransaction, TransactionSource, TransactionType

TxnTable = type[StatementTransactionRow] | type[AccountingTransactionRow] | type[PaymentRow]
_TXN_TABLES: dict[TransactionSource, TxnTable] = {
    TransactionSource.STATEMENT: StatementTransactionRow,
    TransactionSource.ACCOUNTING: AccountingTransactionRow,
    TransactionSource.PAYMENTS: PaymentRow,
}
_TXN_FIELDS = (
    "transaction_id", "supplier_name", "supplier_name_normalized", "supplier_account",
    "invoice_number", "invoice_number_normalized", "reference", "related_invoice_number",
    "invoice_date", "due_date", "description", "subtotal", "tax", "amount", "currency",
    "debit", "credit", "payment_amount", "outstanding_amount", "balance", "status",
    "source_file", "source_row",
)  # fmt: skip


def _txn_to_row(txn: CanonicalTransaction, reconciliation_id: int, file_id: int | None):
    table = _TXN_TABLES[txn.source]
    values = {name: getattr(txn, name) for name in _TXN_FIELDS}
    return table(
        reconciliation_id=reconciliation_id,
        file_id=file_id,
        transaction_type=txn.transaction_type.value,
        raw_data=txn.raw_data or None,
        **values,
    )


def _row_to_txn(row: Any, source: TransactionSource) -> CanonicalTransaction:
    values = {name: getattr(row, name) for name in _TXN_FIELDS}
    return CanonicalTransaction(
        source=source,
        transaction_type=TransactionType(row.transaction_type),
        raw_data=row.raw_data or {},
        **values,
    )


def _result_to_row(result: MatchResult, reconciliation_id: int) -> ReconciliationResultRow:
    return ReconciliationResultRow(
        reconciliation_id=reconciliation_id,
        result_id=result.result_id,
        status=result.status.value,
        original_status=result.original_status.value if result.original_status else None,
        kind=result.kind.value,
        statement_txn_id=result.statement_txn_id,
        accounting_txn_id=result.accounting_txn_id,
        related_txn_ids=result.related_txn_ids or None,
        confidence_score=result.confidence_score,
        confidence_band=result.confidence_band.value,
        matching_method=result.matching_method.value,
        matching_reason=result.matching_reason,
        issues=result.issues or None,
        statement_amount=result.statement_amount,
        accounting_amount=result.accounting_amount,
        amount_difference=result.amount_difference,
        days_difference=result.days_difference,
        requires_review=result.requires_review,
        review_status=result.review_status.value,
        review_note=result.review_note,
        reviewed_at=result.reviewed_at,
    )


def _row_to_result(row: ReconciliationResultRow) -> MatchResult:
    return MatchResult(
        result_id=row.result_id,
        status=MatchStatus(row.status),
        original_status=MatchStatus(row.original_status) if row.original_status else None,
        kind=ResultKind(row.kind),
        statement_txn_id=row.statement_txn_id,
        accounting_txn_id=row.accounting_txn_id,
        related_txn_ids=row.related_txn_ids or [],
        confidence_score=row.confidence_score,
        confidence_band=ConfidenceBand(row.confidence_band),
        matching_method=MatchMethod(row.matching_method),
        matching_reason=row.matching_reason or "",
        issues=row.issues or [],
        statement_amount=row.statement_amount,
        accounting_amount=row.accounting_amount,
        amount_difference=row.amount_difference,
        days_difference=row.days_difference,
        requires_review=row.requires_review,
        review_status=ReviewStatus(row.review_status),
        review_note=row.review_note,
        reviewed_at=row.reviewed_at,
    )


class Repository:
    """Data access for one unit of work (one SQLAlchemy session)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ----------------------------------------------------------------- suppliers
    def get_or_create_supplier(
        self,
        name: str,
        normalized_name: str,
        *,
        account_number: str | None = None,
        currency: str | None = None,
        contact_reference: str | None = None,
    ) -> SupplierRow:
        query = select(SupplierRow).where(SupplierRow.normalized_name == normalized_name)
        if account_number:
            query = query.where(SupplierRow.account_number == account_number)
        supplier = self.session.scalars(query).first()
        if supplier is None:
            supplier = SupplierRow(
                name=name,
                normalized_name=normalized_name,
                account_number=account_number,
                currency=currency,
                contact_reference=contact_reference,
            )
            self.session.add(supplier)
            self.session.flush()
        return supplier

    def list_suppliers(self) -> Sequence[SupplierRow]:
        return self.session.scalars(select(SupplierRow).order_by(SupplierRow.name)).all()

    # ----------------------------------------------------------- reconciliations
    def create_reconciliation(
        self,
        supplier: SupplierRow,
        *,
        client_name: str | None,
        supplier_account: str | None,
        period_start: date | None,
        period_end: date | None,
        currency: str,
        notes: str | None = None,
        is_demo: bool = False,
    ) -> ReconciliationRow:
        recon = ReconciliationRow(
            supplier=supplier,
            client_name=client_name,
            supplier_account=supplier_account,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            notes=notes,
            is_demo=is_demo,
        )
        self.session.add(recon)
        self.session.flush()
        return recon

    def get_reconciliation(self, reconciliation_id: int) -> ReconciliationRow | None:
        return self.session.get(ReconciliationRow, reconciliation_id)

    def list_reconciliations(self) -> Sequence[ReconciliationRow]:
        query = select(ReconciliationRow).order_by(ReconciliationRow.created_at.desc())
        return self.session.scalars(query).all()

    def delete_reconciliation(self, reconciliation_id: int) -> None:
        recon = self.get_reconciliation(reconciliation_id)
        if recon is not None:
            for table in (*_TXN_TABLES.values(), ReconciliationResultRow, AuditEventRow):
                self.session.execute(
                    delete(table).where(table.reconciliation_id == reconciliation_id)
                )
            self.session.delete(recon)
            self.session.flush()

    # --------------------------------------------------------------------- files
    def add_uploaded_file(self, reconciliation_id: int, **values: Any) -> UploadedFileRow:
        role = values["role"]
        for existing in self.list_files(reconciliation_id, role=role):
            existing.is_active = False
        row = UploadedFileRow(reconciliation_id=reconciliation_id, **values)
        self.session.add(row)
        self.session.flush()
        return row

    def list_files(
        self, reconciliation_id: int, *, role: str | None = None, active_only: bool = True
    ) -> Sequence[UploadedFileRow]:
        query = select(UploadedFileRow).where(
            UploadedFileRow.reconciliation_id == reconciliation_id
        )
        if role:
            query = query.where(UploadedFileRow.role == role)
        if active_only:
            query = query.where(UploadedFileRow.is_active.is_(True))
        return self.session.scalars(query.order_by(UploadedFileRow.uploaded_at)).all()

    def get_active_file(self, reconciliation_id: int, role: str) -> UploadedFileRow | None:
        files = self.list_files(reconciliation_id, role=role)
        return files[-1] if files else None

    # -------------------------------------------------------------- transactions
    def replace_transactions(
        self,
        reconciliation_id: int,
        source: TransactionSource,
        transactions: Iterable[CanonicalTransaction],
        file_id: int | None = None,
    ) -> int:
        table = _TXN_TABLES[source]
        self.session.execute(delete(table).where(table.reconciliation_id == reconciliation_id))
        rows = [_txn_to_row(t, reconciliation_id, file_id) for t in transactions]
        self.session.add_all(rows)
        self.session.flush()
        return len(rows)

    def get_transactions(
        self, reconciliation_id: int, source: TransactionSource
    ) -> list[CanonicalTransaction]:
        table = _TXN_TABLES[source]
        query = (
            select(table)
            .where(table.reconciliation_id == reconciliation_id)
            .order_by(table.source_row, table.id)
        )
        return [_row_to_txn(row, source) for row in self.session.scalars(query)]

    def count_transactions(self, reconciliation_id: int, source: TransactionSource) -> int:
        table = _TXN_TABLES[source]
        query = select(func.count()).where(table.reconciliation_id == reconciliation_id)
        return int(self.session.scalar(query) or 0)

    # ------------------------------------------------------------------- results
    def replace_results(self, reconciliation_id: int, results: Iterable[MatchResult]) -> None:
        self.session.execute(
            delete(ReconciliationResultRow).where(
                ReconciliationResultRow.reconciliation_id == reconciliation_id
            )
        )
        self.session.add_all(_result_to_row(r, reconciliation_id) for r in results)
        self.session.flush()

    def get_results(self, reconciliation_id: int) -> list[MatchResult]:
        query = (
            select(ReconciliationResultRow)
            .where(ReconciliationResultRow.reconciliation_id == reconciliation_id)
            .order_by(ReconciliationResultRow.id)
        )
        return [_row_to_result(row) for row in self.session.scalars(query)]

    def update_result(self, reconciliation_id: int, result: MatchResult) -> None:
        row = self.session.scalars(
            select(ReconciliationResultRow).where(
                ReconciliationResultRow.reconciliation_id == reconciliation_id,
                ReconciliationResultRow.result_id == result.result_id,
            )
        ).first()
        if row is None:
            raise KeyError(f"Result {result.result_id} not found")
        updated = _result_to_row(result, reconciliation_id)
        for column in ReconciliationResultRow.__table__.columns:
            if column.key != "id":
                setattr(row, column.key, getattr(updated, column.key))
        self.session.flush()

    def add_result(self, reconciliation_id: int, result: MatchResult) -> None:
        self.session.add(_result_to_row(result, reconciliation_id))
        self.session.flush()

    # --------------------------------------------------------------------- audit
    def add_audit_event(
        self,
        reconciliation_id: int | None,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEventRow(
                reconciliation_id=reconciliation_id,
                event_type=event_type,
                message=message,
                details=_jsonable(details) if details else None,
            )
        )
        self.session.flush()

    def get_audit_events(self, reconciliation_id: int) -> Sequence[AuditEventRow]:
        query = (
            select(AuditEventRow)
            .where(AuditEventRow.reconciliation_id == reconciliation_id)
            .order_by(AuditEventRow.created_at, AuditEventRow.id)
        )
        return self.session.scalars(query).all()

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str) -> str | None:
        row = self.session.get(AppSettingRow, key)
        return row.value if row else None

    def set_setting(self, key: str, value: str) -> None:
        row = self.session.get(AppSettingRow, key)
        if row is None:
            self.session.add(AppSettingRow(key=key, value=value))
        else:
            row.value = value
        self.session.flush()

    def touch(self, recon: ReconciliationRow) -> None:
        recon.updated_at = utcnow()


def _jsonable(value: Any) -> Any:
    """Convert Decimals/dates so values can be stored in JSON columns."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value
