"""Accounting providers and upload validation."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

from src.accounting import get_file_provider
from src.accounting.base import (
    AccountingDataKind,
    AccountingProviderError,
    AccountingQuery,
    IntegrationNotAvailableError,
)
from src.accounting.quickbooks_provider import QuickBooksProvider
from src.accounting.xero_provider import XeroProvider
from src.models.transaction import TransactionSource, TransactionType
from src.utils.validation import (
    UploadValidationError,
    safe_join,
    sanitize_filename,
    validate_upload,
)

XERO_STYLE = """ContactName,InvoiceNumber,InvoiceDate,DueDate,SubTotal,TaxTotal,Total,AmountDue,Currency,Type,Status
Acme Supplies Ltd,INV-1001,03/09/2026,03/10/2026,1000.00,200.00,1200.00,1200.00,GBP,ACCPAY,AUTHORISED
Acme Supplies Ltd,CN-0007,05/09/2026,,125.00,25.00,150.00,0.00,GBP,ACCPAYCREDIT,PAID
Other Vendor plc,X-1,06/09/2026,,10.00,2.00,12.00,12.00,GBP,ACCPAY,AUTHORISED
"""

QUICKBOOKS_STYLE = """Transaction Type,Date,Num,Name,Memo,Amount,Open Balance
Bill,09/03/2026,INV-1001,Acme Supplies Ltd,Widgets,"1,200.00","1,200.00"
Vendor Credit,09/05/2026,CN-0007,Acme Supplies Ltd,Damaged,-150.00,0.00
Bill,09/25/2026,INV-1004,Acme Supplies Ltd,Gaskets,99.99,99.99
"""

PAYMENTS = """Payment Date,Payment Reference,Invoice Number,Amount Paid
20/09/2026,PMT-55,INV-0998,"1,000.00"
21/09/2026,PMT-56,,250.00
"""


def _load(tmp_path, name, text, kind=AccountingDataKind.BILLS):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return get_file_provider(path).load(AccountingQuery(currency="GBP"), kind)


def test_xero_style_export(tmp_path):
    loaded = _load(tmp_path, "xero.csv", XERO_STYLE)
    assert loaded.mapping_complete
    txns = loaded.result.transactions
    assert len(txns) == 3
    first = txns[0]
    assert first.source == TransactionSource.ACCOUNTING
    assert first.supplier_name == "Acme Supplies Ltd"
    assert first.amount == Decimal("1200.00")
    assert first.subtotal == Decimal("1000.00")
    assert first.tax == Decimal("200.00")
    assert first.outstanding_amount == Decimal("1200.00")
    assert first.due_date == date(2026, 10, 3)
    assert txns[1].transaction_type == TransactionType.CREDIT_NOTE
    assert txns[1].status == "PAID"


def test_quickbooks_style_export_month_first_dates(tmp_path):
    loaded = _load(tmp_path, "qb.csv", QUICKBOOKS_STYLE)
    txns = loaded.result.transactions
    assert loaded.mapping.dayfirst is False
    assert txns[0].invoice_date == date(2026, 9, 3)
    assert txns[1].transaction_type == TransactionType.CREDIT_NOTE
    assert txns[1].amount == Decimal("150.00")
    assert txns[2].invoice_number == "INV-1004"


def test_payments_export_maps_invoice_to_allocation(tmp_path):
    loaded = _load(tmp_path, "payments.csv", PAYMENTS, AccountingDataKind.PAYMENTS)
    txns = loaded.result.transactions
    assert all(t.transaction_type == TransactionType.PAYMENT for t in txns)
    assert all(t.source == TransactionSource.PAYMENTS for t in txns)
    assert txns[0].related_invoice_number == "INV-0998"
    assert txns[0].reference == "PMT-55"
    assert txns[0].invoice_number is None
    assert txns[1].related_invoice_number is None  # unallocated - never assumed


def test_excel_accounting_export(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Bill No", "Bill Date", "Supplier", "Gross"])
    sheet.append(["INV-1", date(2026, 9, 1), "Acme", 100])
    sheet.append(["INV-2", date(2026, 9, 2), "Acme", 250.5])
    path = tmp_path / "bills.xlsx"
    workbook.save(path)
    loaded = get_file_provider(path).load(AccountingQuery())
    assert [t.amount for t in loaded.result.transactions] == [Decimal("100.00"), Decimal("250.50")]


def test_accounting_missing_invoice_column(tmp_path):
    loaded = _load(tmp_path, "bad.csv", "Date,Amount\n01/09/2026,10\n")
    assert not loaded.mapping_complete
    assert "Invoice number (or Reference)" in loaded.missing_required


def test_unsupported_accounting_file():
    with pytest.raises(AccountingProviderError):
        get_file_provider("bills.pdf")


def test_api_providers_report_not_configured(monkeypatch):
    for var in ("XERO_CLIENT_ID", "XERO_CLIENT_SECRET", "XERO_REDIRECT_URI"):
        monkeypatch.delenv(var, raising=False)
    xero = XeroProvider()
    assert not xero.is_configured()
    assert xero.status_message() == "Xero integration: Not configured"
    with pytest.raises(IntegrationNotAvailableError):
        xero.load(AccountingQuery())


def test_api_provider_with_credentials_still_returns_no_fake_data(monkeypatch):
    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "id")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("QUICKBOOKS_REDIRECT_URI", "http://localhost/callback")
    provider = QuickBooksProvider()
    assert provider.is_configured()
    assert "not implemented" in provider.status_message()
    with pytest.raises(IntegrationNotAvailableError):
        provider.load(AccountingQuery())


# ------------------------------------------------------------- upload checks

CSV_EXT = frozenset({".csv", ".xlsx", ".pdf"})


def _xlsx_bytes(extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_validate_upload_accepts_valid_files():
    assert (
        validate_upload("a.CSV", b"a,b\n1,2", allowed_extensions=CSV_EXT, max_bytes=100) == ".csv"
    )
    assert validate_upload("a.pdf", b"%PDF-1.7 ...", allowed_extensions=CSV_EXT, max_bytes=100)
    assert validate_upload("a.xlsx", _xlsx_bytes(), allowed_extensions=CSV_EXT, max_bytes=10_000)


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("a.exe", b"MZ...", "not accepted"),
        ("a.csv", b"MZ\x90\x00", "Executable"),
        ("a.csv", b"", "empty"),
        ("a.csv", b"x" * 101, "maximum"),
        ("a.pdf", b"not a pdf", "valid PDF"),
        ("a.xlsx", b"a,b,c", "not a valid .xlsx"),
        ("a.csv", b"a\x00b", "CSV"),
        ("a.sh", b"#!/bin/sh", "not accepted"),
    ],
)
def test_validate_upload_rejects_bad_files(name, data, message):
    with pytest.raises(UploadValidationError, match=message):
        validate_upload(name, data, allowed_extensions=CSV_EXT, max_bytes=100)


def test_validate_upload_rejects_macro_workbooks():
    data = _xlsx_bytes({"xl/vbaProject.bin": b"macro"})
    with pytest.raises(UploadValidationError, match="macros"):
        validate_upload("a.xlsx", data, allowed_extensions=CSV_EXT, max_bytes=10_000)


def test_validate_upload_rejects_zip_bomb():
    data = _xlsx_bytes({"xl/big.xml": b"0" * 5_000_000})
    with pytest.raises(UploadValidationError, match="too large"):
        validate_upload("a.xlsx", data, allowed_extensions=CSV_EXT, max_bytes=1_000_000)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Windows\\system32\\evil.csv", "evil.csv"),
        ("statement (Sept) £.pdf", "statement _Sept_ .pdf"),
        ("..", "upload"),
        ("", "upload"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_safe_join_blocks_traversal(tmp_path):
    assert safe_join(tmp_path, "a", "b.csv").is_relative_to(tmp_path.resolve())
    with pytest.raises(UploadValidationError):
        safe_join(tmp_path, "..", "outside.csv")
