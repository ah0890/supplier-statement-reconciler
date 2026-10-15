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

from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from src.accounting import get_file_provider
from src.accounting.base import AccountingQuery
from src.config import PROJECT_ROOT
from src.models.transaction import TransactionType
from src.models.validation import ValidationReport
from src.parsers.columns import ColumnField
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


# --- Layout patterns found during real-document testing (synthetic values) ---------

PAGE_CARRY_FORWARD = """Date,Ref,Details,Debit,Credit,Balance
01/09/2026,,Balance brought forward,,,1000.00
02/09/2026,X-1,Item,100.00,,1100.00
,,Carried forward,,,1100.00
,,Brought forward,,,1100.00
03/09/2026,X-2,Item,50.00,,1150.00
,,Amount now due,,,1150.00
"""

GERMAN_STATEMENT = (
    "Muster Lieferant GmbH;;;;;\n"
    "Kontoauszug;;;;;\n"
    "Belegdatum;Belegnummer;Buchungstext;Soll;Haben;Saldo\n"
    "01.09.2026;;Saldovortrag;;;1.000,00\n"
    "02.09.2026;RE-1;Ware;2.500,50;;3.500,50\n"
    "05.09.2026;ZA-9;Zahlungseingang;;1.000,00;2.500,50\n"
    "05.09.2026;ZA-9;Skonto 2 %;;20,00;2.480,50\n"
    "09.09.2026;GS-3;Gutschrift zu RE-1;;80,50;2.400,00\n"
    ";;Endsaldo;;;2.400,00\n"
)

TYPE_AND_NUMBER_COLUMNS = """Date,Document,Doc No.,Debit,Credit,Balance
2026-09-01,B/FWD,,,,100.00
2026-09-02,INV,AB-1,10.00,,110.00
2026-09-03,INV,AB-2,20.00,,130.00
2026-09-04,PAY,PAY 1,,100.00,30.00
2026-09-05,INV,AB-3,5.00,,35.00
"""


def _parse(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return CSVStatementParser().parse(path)


def test_page_brought_forward_lines_do_not_replace_opening_balance(tmp_path):
    parsed = _parse(tmp_path, "pages.csv", PAGE_CARRY_FORWARD)
    assert parsed.result.balances.opening_balance == Decimal("1000.00")
    assert parsed.result.balances.closing_balance == Decimal("1150.00")
    assert len(parsed.result.transactions) == 2


def test_german_semicolon_statement(tmp_path):
    parsed = _parse(tmp_path, "kontoauszug.csv", GERMAN_STATEMENT, encoding="cp1252")
    txns = parsed.result.transactions
    assert [(t.transaction_type, t.amount) for t in txns] == [
        (TransactionType.INVOICE, Decimal("2500.50")),
        (TransactionType.PAYMENT, Decimal("1000.00")),
        (TransactionType.CREDIT_NOTE, Decimal("20.00")),  # settlement discount
        (TransactionType.CREDIT_NOTE, Decimal("80.50")),
    ]
    assert parsed.result.balances.opening_balance == Decimal("1000.00")
    assert parsed.result.balances.closing_balance == Decimal("2400.00")


def test_type_column_is_not_taken_as_invoice_number(tmp_path):
    parsed = _parse(tmp_path, "types.csv", TYPE_AND_NUMBER_COLUMNS)
    assert parsed.mapping.column_for(ColumnField.INVOICE_NUMBER) == "Doc No."
    assert parsed.mapping.column_for(ColumnField.TRANSACTION_TYPE) == "Document"
    assert parsed.result.balances.opening_balance == Decimal("100.00")
    assert [t.display_reference for t in parsed.result.transactions] == [
        "AB-1", "AB-2", "PAY 1", "AB-3",
    ]  # fmt: skip


def test_gross_amount_column_chosen_by_running_balance(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Date", "Invoice No", "Amount", "VAT", "Amount", "Balance"])
    sheet.append(["2026-09-01", "Balance b/f", None, None, None, 100])
    sheet.append(["2026-09-02", "Q-1", 100, 20, 120, 220])
    sheet.append(["2026-09-03", "Q-2", 50, 10, 60, 280])
    sheet.append(["46268", "Q-3", 10, 2, 12, 292])  # Excel serial date stored as text
    sheet.append(["2026-09-05", "Q-4", -10, -2, -12, 280])
    path = tmp_path / "dup.xlsx"
    workbook.save(path)
    from src.parsers.excel_parser import ExcelStatementParser

    parsed = ExcelStatementParser().parse(path)
    txns = parsed.result.transactions
    assert parsed.mapping.column_for(ColumnField.AMOUNT) == "Amount (2)"
    assert [t.amount for t in txns] == [Decimal("120.00"), Decimal("60.00"), Decimal("12.00"),
                                        Decimal("12.00")]  # fmt: skip
    assert txns[2].invoice_date == date(2026, 9, 3)
    assert txns[3].transaction_type == TransactionType.CREDIT_NOTE  # negative on a document
    assert parsed.result.balances.opening_balance == Decimal("100.00")


def test_credit_and_adjustment_descriptions_and_bfwd_without_slash(tmp_path):
    parsed = _parse(
        tmp_path,
        "misc.csv",
        "Doc Date,Doc Type,Doc No,Details,Invoice Amt,Paid/Credited,Running Bal\n"
        "01/09/2026,BFWD,,,,,500.00\n"
        "02/09/2026,,X-10,Linen,100.00,,600.00\n"
        "03/09/2026,,X-C1,Credit - stained items,,10.00,590.00\n"
        "04/09/2026,,ADJ-7,Short delivery adj,,5.00,585.00\n"
        "05/09/2026,,,Payment rec'd - with thanks,,500.00,85.00\n",
    )
    types = [t.transaction_type for t in parsed.result.transactions]
    assert types == [TransactionType.INVOICE, TransactionType.CREDIT_NOTE,
                     TransactionType.CREDIT_NOTE, TransactionType.PAYMENT]  # fmt: skip
    assert parsed.result.balances.opening_balance == Decimal("500.00")


def test_document_line_without_amount_is_an_error(tmp_path):
    parsed = _parse(
        tmp_path, "noamt.csv", "Date,Invoice No,Amount\n01/09/2026,INV-5,TBC\n02/09/2026,INV-6,\n"
    )
    row_errors = [i for i in parsed.result.report.issues if i.severity.value == "ERROR" and i.row]
    assert len(row_errors) == 2
