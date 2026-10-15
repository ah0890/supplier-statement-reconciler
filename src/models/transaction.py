"""Canonical transaction model shared by every import source.

Whether data comes from a PDF statement, a CSV export or (in future) the Xero
or QuickBooks APIs, it is converted into ``CanonicalTransaction`` before it
reaches the reconciliation engine.

Sign convention: ``amount`` is always the positive document value. Its effect
on the balance owed to the supplier is given by ``signed_amount``: invoices
increase it, credit notes and payments reduce it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionType(StrEnum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    PAYMENT = "PAYMENT"


class TransactionSource(StrEnum):
    STATEMENT = "STATEMENT"
    ACCOUNTING = "ACCOUNTING"
    PAYMENTS = "PAYMENTS"


def new_transaction_id() -> str:
    return uuid4().hex[:16]


class CanonicalTransaction(BaseModel):
    """One normalised financial transaction."""

    model_config = ConfigDict(str_strip_whitespace=True)

    transaction_id: str = Field(default_factory=new_transaction_id)
    source: TransactionSource
    transaction_type: TransactionType = TransactionType.INVOICE

    supplier_name: str | None = None
    supplier_name_normalized: str | None = None
    supplier_account: str | None = None

    invoice_number: str | None = None
    invoice_number_normalized: str | None = None
    reference: str | None = None
    related_invoice_number: str | None = None

    invoice_date: date | None = None
    due_date: date | None = None
    description: str | None = None

    subtotal: Decimal | None = None
    tax: Decimal | None = None
    amount: Decimal = Field(ge=0)
    currency: str | None = None

    debit: Decimal | None = None
    credit: Decimal | None = None
    payment_amount: Decimal | None = None
    outstanding_amount: Decimal | None = None
    balance: Decimal | None = None
    status: str | None = None

    source_file: str | None = None
    source_row: int | None = None
    raw_data: dict[str, str] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @property
    def signed_amount(self) -> Decimal:
        """Effect on the amount owed to the supplier."""
        return self.amount if self.transaction_type == TransactionType.INVOICE else -self.amount

    @property
    def is_document(self) -> bool:
        """Invoices and credit notes are documents; payments are settlements."""
        return self.transaction_type != TransactionType.PAYMENT

    @property
    def display_reference(self) -> str:
        return self.invoice_number or self.reference or "(no reference)"


class StatementBalances(BaseModel):
    """Balances found on (or entered for) a supplier statement."""

    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    opening_source: str | None = None  # "detected", "derived" or "manual"
    closing_source: str | None = None
