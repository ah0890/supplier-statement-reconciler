"""SQLAlchemy ORM tables.

Money is stored as text through ``MoneyType`` so that Decimal values round-trip
exactly (SQLite has no native decimal type).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MoneyType(TypeDecorator):
    """Persist Decimal as a fixed-point string."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return str(Decimal(value))

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(value)


class Base(DeclarativeBase):
    pass


class SupplierRow(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    account_number: Mapped[str | None] = mapped_column(String(100))
    contact_reference: Mapped[str | None] = mapped_column(String(255))
    currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reconciliations: Mapped[list[ReconciliationRow]] = relationship(back_populates="supplier")


class ReconciliationRow(Base):
    __tablename__ = "reconciliations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_name: Mapped[str | None] = mapped_column(String(255))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    supplier_account: Mapped[str | None] = mapped_column(String(100))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    notes: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    # Workflow: DRAFT -> READY -> RECONCILED_RUN -> FINALISED
    workflow_status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    outcome: Mapped[str | None] = mapped_column(String(40))
    outcome_note: Mapped[str | None] = mapped_column(Text)

    opening_balance: Mapped[Decimal | None] = mapped_column(MoneyType)
    closing_balance: Mapped[Decimal | None] = mapped_column(MoneyType)
    opening_balance_source: Mapped[str | None] = mapped_column(String(20))
    closing_balance_source: Mapped[str | None] = mapped_column(String(20))

    config_json: Mapped[dict | None] = mapped_column(JSON)
    balance_json: Mapped[dict | None] = mapped_column(JSON)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    allocations_json: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    finalised_at: Mapped[datetime | None] = mapped_column(DateTime)

    supplier: Mapped[SupplierRow] = relationship(back_populates="reconciliations")
    files: Mapped[list[UploadedFileRow]] = relationship(
        back_populates="reconciliation", cascade="all, delete-orphan"
    )


class UploadedFileRow(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reconciliation_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # STATEMENT / ACCOUNTING / PAYMENTS
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    extension: Mapped[str] = mapped_column(String(10))
    parser: Mapped[str | None] = mapped_column(String(100))
    column_mapping: Mapped[dict | None] = mapped_column(JSON)
    import_options: Mapped[dict | None] = mapped_column(JSON)
    validation_json: Mapped[dict | None] = mapped_column(JSON)
    row_count: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    reconciliation: Mapped[ReconciliationRow] = relationship(back_populates="files")


class _TransactionColumns:
    """Columns shared by statement, accounting and payment tables."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(32), index=True)
    file_id: Mapped[int | None] = mapped_column(Integer)
    transaction_type: Mapped[str] = mapped_column(String(20))
    supplier_name: Mapped[str | None] = mapped_column(String(255))
    supplier_name_normalized: Mapped[str | None] = mapped_column(String(255))
    supplier_account: Mapped[str | None] = mapped_column(String(100))
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_number_normalized: Mapped[str | None] = mapped_column(String(100), index=True)
    reference: Mapped[str | None] = mapped_column(String(255))
    related_invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal | None] = mapped_column(MoneyType)
    tax: Mapped[Decimal | None] = mapped_column(MoneyType)
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    currency: Mapped[str | None] = mapped_column(String(3))
    debit: Mapped[Decimal | None] = mapped_column(MoneyType)
    credit: Mapped[Decimal | None] = mapped_column(MoneyType)
    payment_amount: Mapped[Decimal | None] = mapped_column(MoneyType)
    outstanding_amount: Mapped[Decimal | None] = mapped_column(MoneyType)
    balance: Mapped[Decimal | None] = mapped_column(MoneyType)
    status: Mapped[str | None] = mapped_column(String(50))
    source_file: Mapped[str | None] = mapped_column(String(255))
    source_row: Mapped[int | None] = mapped_column(Integer)
    raw_data: Mapped[dict | None] = mapped_column(JSON)


class StatementTransactionRow(_TransactionColumns, Base):
    __tablename__ = "statement_transactions"
    __table_args__ = (UniqueConstraint("reconciliation_id", "transaction_id"),)
    reconciliation_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )


class AccountingTransactionRow(_TransactionColumns, Base):
    __tablename__ = "accounting_transactions"
    __table_args__ = (UniqueConstraint("reconciliation_id", "transaction_id"),)
    reconciliation_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )


class PaymentRow(_TransactionColumns, Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("reconciliation_id", "transaction_id"),)
    reconciliation_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )


class ReconciliationResultRow(Base):
    __tablename__ = "reconciliation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reconciliation_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )
    result_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    original_status: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    statement_txn_id: Mapped[str | None] = mapped_column(String(32))
    accounting_txn_id: Mapped[str | None] = mapped_column(String(32))
    related_txn_ids: Mapped[list | None] = mapped_column(JSON)
    confidence_score: Mapped[int] = mapped_column(Integer, default=0)
    confidence_band: Mapped[str] = mapped_column(String(20))
    matching_method: Mapped[str] = mapped_column(String(40))
    matching_reason: Mapped[str] = mapped_column(Text, default="")
    issues: Mapped[list | None] = mapped_column(JSON)
    statement_amount: Mapped[Decimal | None] = mapped_column(MoneyType)
    accounting_amount: Mapped[Decimal | None] = mapped_column(MoneyType)
    amount_difference: Mapped[Decimal | None] = mapped_column(MoneyType)
    days_difference: Mapped[int | None] = mapped_column(Integer)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(20))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reconciliation_id: Mapped[int | None] = mapped_column(
        ForeignKey("reconciliations.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    event_type: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)


class AppSettingRow(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
