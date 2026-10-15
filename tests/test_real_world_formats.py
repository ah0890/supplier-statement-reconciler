"""Layouts modelled on common real-world exports and statements.

These fixtures reproduce the *structure* of widely used formats (column
names, grouping, sign conventions, wrapped text). They are synthetic: no real
client documents are used. Real-document validation still requires samples
from the project owner (see docs/REAL_WORLD_DATA_REQUIREMENTS.md).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from src.accounting import get_file_provider
from src.accounting.base import AccountingQuery
from src.config import PROJECT_ROOT
from src.models.transaction import TransactionType
from src.models.validation import ValidationReport
from src.parsers.csv_parser import CSVStatementParser
from src.parsers.pdf_parser import PDFStatementParser
from src.services.normalization_service import NormalizationContext, normalize_transactions

QUICKBOOKS_VENDOR_BALANCE_DETAIL = """Demo Client Ltd
Vendor Balance Detail
All Dates

,Date,Transaction Type,Num,Due Date,Amount,Open Balance,Balance
Acme Supplies Ltd,,,,,,,
,09/03/2026,Bill,INV-1001,10/03/2026,"1,200.00","1,200.00","1,200.00"
,09/05/2026,Vendor Credit,CN-7,,-150.00,-150.00,"1,050.00"
,09/12/2026,Bill,INV-1004,10/12/2026,99.99,99.99,"1,149.99"
Total for Acme Supplies Ltd,,,,,"1,149.99","1,149.99",
Zenith Tools Inc,,,,,,,
,09/04/2026,Bill,Z-500,10/04/2026,500.00,500.00,500.00
Total for Zenith Tools Inc,,,,,500.00,500.00,
TOTAL,,,,,"1,649.99","1,649.99",
"""

SAGE_SUPPLIER_ACTIVITY = """Type,Date,Ref,Details,Debit,Credit,O/S
PI,03/09/2026,INV-1001,Widgets,,"1,200.00","1,200.00"
PC,05/09/2026,CN-7,Damaged goods,150.00,,0.00
PP,20/09/2026,BACS,Payment,"1,000.00",,0.00
"""

SIGNED_STATEMENT = """Statement of account - Acme Supplies Ltd
Date,Document,Narrative,Amount (£),Balance (£)
1st Sep 2026,,Balance B/Fwd,,£500.00
3rd Sep 2026,INV 1001,Widgets for site A,"£1,200.00","£1,700.00"
,,"delivered to rear entrance",,
5th Sep 2026,CRN 7,Returned goods,(150.00),"£1,550.00"
20th Sep 2026,,Payment - thank you,"1,000.00 CR",£550.00
,,Amount Now Due,,£550.00
"""


def test_quickbooks_grouped_report_assigns_suppliers_and_skips_totals(tmp_path):
    path = tmp_path / "vendor_balance_detail.csv"
    path.write_text(QUICKBOOKS_VENDOR_BALANCE_DETAIL, encoding="utf-8")
    loaded = get_file_provider(path).load(AccountingQuery(currency="USD"))
    txns = loaded.result.transactions
    assert [t.invoice_number for t in txns] == ["INV-1001", "CN-7", "INV-1004", "Z-500"]
    assert txns[0].supplier_name == "Acme Supplies Ltd"
    assert txns[3].supplier_name == "Zenith Tools Inc"
    assert txns[1].transaction_type == TransactionType.CREDIT_NOTE
    assert txns[1].amount == Decimal("150.00")
    assert txns[0].invoice_date == date(2026, 9, 3)  # USD export: month first
    assert any("date order could not be determined" in i.message
               for i in loaded.result.report.issues)  # fmt: skip

    kept, excluded = normalize_transactions(
        txns, NormalizationContext("Acme Supplies"), ValidationReport()
    )
    assert len(kept) == 3 and excluded == 1


def test_sage_type_codes(tmp_path):
    # Supplier ledger from the buyer's side: invoices are credits to the supplier account.
    path = tmp_path / "sage.csv"
    path.write_text(SAGE_SUPPLIER_ACTIVITY, encoding="utf-8")
    txns = get_file_provider(path).load(AccountingQuery()).result.transactions
    assert [t.transaction_type for t in txns] == [
        TransactionType.INVOICE,
        TransactionType.CREDIT_NOTE,
        TransactionType.PAYMENT,
    ]
    assert txns[0].outstanding_amount == Decimal("1200.00")


def test_signed_statement_with_symbols_ordinals_and_wrapped_text(tmp_path):
    path = tmp_path / "statement.csv"
    path.write_text(SIGNED_STATEMENT, encoding="utf-8")
    parsed = CSVStatementParser().parse(path, expected_currency="GBP")
    txns = parsed.result.transactions
    assert [(t.transaction_type, t.amount) for t in txns] == [
        (TransactionType.INVOICE, Decimal("1200.00")),
        (TransactionType.CREDIT_NOTE, Decimal("150.00")),
        (TransactionType.PAYMENT, Decimal("1000.00")),
    ]
    assert txns[0].invoice_number == "INV 1001"
    assert txns[0].description == "Widgets for site A delivered to rear entrance"
    assert txns[0].invoice_date == date(2026, 9, 3)
    assert parsed.result.balances.opening_balance == Decimal("500.00")
    assert parsed.result.balances.closing_balance == Decimal("550.00")


def test_pdf_with_wrapped_descriptions(tmp_path):
    path = tmp_path / "wrapped.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
    pdf.setFont("Helvetica-Bold", 9)
    y = height - 80
    for x, title in ((40, "Date"), (110, "Invoice No"), (200, "Description")):
        pdf.drawString(x, y, title)
    pdf.drawRightString(470, y, "Amount")
    pdf.drawRightString(550, y, "Balance")
    pdf.setFont("Helvetica", 9)
    rows = [
        ("02/09/2026", "INV-2001", "Hydraulic hose assemblies", "450.00", "450.00"),
        ("", "", "incl. fittings and test certificate", "", ""),
        ("09/09/2026", "INV-2002", "Bearings", "120.00", "570.00"),
    ]
    for date_text, ref, desc, amount, balance in rows:
        y -= 14
        for x, value in ((40, date_text), (110, ref), (200, desc)):
            if value:
                pdf.drawString(x, y, value)
        if amount:
            pdf.drawRightString(470, y, amount)
            pdf.drawRightString(550, y, balance)
    pdf.save()

    txns = PDFStatementParser().parse(path).result.transactions
    assert len(txns) == 2
    assert txns[0].description == "Hydraulic hose assemblies incl. fittings and test certificate"


def test_analyze_document_script(tmp_path):
    path = tmp_path / "statement.csv"
    path.write_text(SIGNED_STATEMENT, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "analyze_document.py"), str(path)],
        capture_output=True, text=True,
    )  # fmt: skip
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Column mapping" in completed.stdout
    assert "Arithmetic check: BALANCED" in completed.stdout


def test_ambiguous_dates_default_to_day_first_for_gbp_with_warning(tmp_path):
    path = tmp_path / "bills.csv"
    path.write_text("Bill No,Date,Total\nA-1,04/03/2026,10\n", encoding="utf-8")
    loaded = get_file_provider(path).load(AccountingQuery(currency="GBP"))
    assert loaded.result.transactions[0].invoice_date == date(2026, 3, 4)
    assert loaded.result.report.warnings == 1


def test_due_dates_help_disambiguate_invoice_dates(tmp_path):
    path = tmp_path / "bills.csv"
    path.write_text("Bill No,Date,Due Date,Total\nA-1,04/03/2026,04/30/2026,10\n", encoding="utf-8")
    loaded = get_file_provider(path).load(AccountingQuery(currency="GBP"))
    assert loaded.result.transactions[0].invoice_date == date(2026, 4, 3)
    assert loaded.result.report.warnings == 0
