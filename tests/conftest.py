"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.database.database import Database
from src.models.transaction import CanonicalTransaction, TransactionSource, TransactionType


@pytest.fixture
def database(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    yield db
    db.dispose()


def make_txn(
    source: TransactionSource,
    invoice: str | None,
    amount: str,
    txn_date: date | None = date(2026, 9, 1),
    txn_type: TransactionType = TransactionType.INVOICE,
    **extra,
) -> CanonicalTransaction:
    """Build a canonical transaction with normalisation applied."""
    from src.utils.normalization import normalize_invoice_number, normalize_supplier_name

    supplier = extra.pop("supplier_name", "Acme Supplies Ltd")
    return CanonicalTransaction(
        source=source,
        transaction_type=txn_type,
        supplier_name=supplier,
        supplier_name_normalized=normalize_supplier_name(supplier),
        invoice_number=invoice,
        invoice_number_normalized=normalize_invoice_number(invoice),
        invoice_date=txn_date,
        amount=Decimal(amount),
        currency=extra.pop("currency", "GBP"),
        **extra,
    )


@pytest.fixture
def stmt():
    def _make(invoice, amount, txn_date=date(2026, 9, 1), txn_type=TransactionType.INVOICE, **kw):
        return make_txn(TransactionSource.STATEMENT, invoice, amount, txn_date, txn_type, **kw)

    return _make


@pytest.fixture
def acct():
    def _make(invoice, amount, txn_date=date(2026, 9, 1), txn_type=TransactionType.INVOICE, **kw):
        return make_txn(TransactionSource.ACCOUNTING, invoice, amount, txn_date, txn_type, **kw)

    return _make
