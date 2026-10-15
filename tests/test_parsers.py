"""Statement parsing: CSV, Excel, PDF, column detection and messy rows."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook

from src.models.transaction import TransactionType
from src.models.validation import IssueSeverity
from src.parsers.base import StatementParserError, get_statement_parser
from src.parsers.columns import (
    ColumnField,
    ColumnMapping,
    detect_mapping,
    locate_header_row,
    missing_required_fields,
    score_header,
)
from src.parsers.csv_parser import CSVStatementParser
from src.parsers.excel_parser import ExcelStatementParser
from src.parsers.pdf_parser import PDFStatementParser
from src.utils.dates import infer_dayfirst, parse_date
from tests.pdf_helpers import image_only_pdf, layout_statement_pdf, ruled_statement_pdf, sample_rows

# --------------------------------------------------------------------- dates


@pytest.mark.parametrize(
    ("raw", "dayfirst", "expected"),
    [
        ("01/10/2026", True, date(2026, 10, 1)),
        ("01/10/2026", False, date(2026, 1, 10)),
        ("25/10/2026", False, date(2026, 10, 25)),  # unambiguous despite preference
        ("2026-10-01", True, date(2026, 10, 1)),
        ("2026-10-01 00:00:00", True, date(2026, 10, 1)),
        ("01-Oct-2026", True, date(2026, 10, 1)),
        ("1 October 2026", True, date(2026, 10, 1)),
        ("Oct 1, 2026", True, date(2026, 10, 1)),
        ("1st Oct 2026", True, date(2026, 10, 1)),
        ("01.10.26", True, date(2026, 10, 1)),
        ("20261001", True, date(2026, 10, 1)),
        (46296, True, date(2026, 10, 1)),
        (datetime(2026, 10, 1, 13, 5), True, date(2026, 10, 1)),
    ],
)
def test_parse_date_formats(raw, dayfirst, expected):
    assert parse_date(raw, dayfirst=dayfirst) == expected


@pytest.mark.parametrize("raw", ["", "not a date", "32/13/2026", "1234.56", 1500, None])
def test_parse_date_rejects_invalid(raw):
    assert parse_date(raw) is None


def test_infer_dayfirst():
    assert infer_dayfirst(["01/02/2026", "25/02/2026"]) is True
    assert infer_dayfirst(["02/25/2026"]) is False
    assert infer_dayfirst(["01/02/2026"]) is None


# ------------------------------------------------------------ column detection


@pytest.mark.parametrize(
    ("header", "field"),
    [
        ("Invoice No.", ColumnField.INVOICE_NUMBER),
        ("Inv #", ColumnField.INVOICE_NUMBER),
        ("InvoiceNumber", ColumnField.INVOICE_NUMBER),
        ("Transaction Date", ColumnField.DATE),
        ("Debit (GBP)", ColumnField.DEBIT),
        ("Total Incl. VAT", ColumnField.AMOUNT),
        ("Running Balance", ColumnField.BALANCE),
        ("AmountDue", ColumnField.OUTSTANDING),
        ("ContactName", ColumnField.SUPPLIER_NAME),
    ],
)
def test_score_header_recognises_synonyms(header, field):
    detected, score = score_header(header)
    assert detected == field
    assert score >= 80


def test_detect_mapping_flags_columns_whose_content_disagrees():
    headers = ["Date", "Ref", "Amount"]
    rows = [["hello", "INV-1", "10.00"], ["world", "INV-2", "20.00"]]
    mapping = detect_mapping(headers, rows)
    assert mapping.confidence["date"] < 60
    assert "date" in mapping.notes


def test_detect_mapping_guesses_from_content_without_headers():
    headers = ["Column 1", "Column 2", "Column 3"]
    rows = [["01/09/2026", "INV-1", "10.00"], ["02/09/2026", "INV-2", "20.00"]]
    mapping = detect_mapping(headers, rows)
    assert mapping.column_for(ColumnField.DATE) == "Column 1"
    assert mapping.column_for(ColumnField.AMOUNT) in {"Column 2", "Column 3"}
    assert mapping.confidence["date"] < 80  # guessed => user should confirm


def test_locate_header_row_skips_letterhead():
    grid = [
        ["ACME SUPPLIES LTD"],
        ["Statement of account"],
        [""],
        ["Date", "Invoice No", "Details", "Debit", "Credit", "Balance"],
        ["01/09/2026", "INV-1", "x", "10.00", "", "10.00"],
    ]
    assert locate_header_row(grid) == 3


def test_missing_required_fields():
    mapping = ColumnMapping(fields={"description": "Details"})
    missing = missing_required_fields(mapping)
    assert "Date" in missing
    assert "Amount (or Debit / Credit)" in missing
    assert "Invoice number (or Reference)" in missing


# ------------------------------------------------------------------------ CSV

MESSY_CSV = """ACME SUPPLIES LTD
Statement of Account,,,,,
Customer: DEMO CLIENT,,,,,
,,,,,
Date,Invoice No.,Description,Debit,Credit,Balance
01/09/2026,,Balance brought forward,,,"1,000.00"
03/09/2026,INV-1001,Widgets,"1,200.00",,"2,200.00"
,,,,,
05/09/2026,CN-0007,Credit note - damaged goods,,150.00,"2,050.00"
Date,Invoice No.,Description,Debit,Credit,Balance
14/09/2026,INV-1002,Bolts,300.50,,"2,350.50"
20/09/2026,PMT-55,Payment received,,"1,000.00","1,350.50"
22/09/2026,INV-1003,Nuts,abc,,
Page total,,,"1,500.50","1,150.00",
,,Closing balance,,,"1,350.50"
Current,30 days,60 days,90 days,,
"1,350.50",0.00,0.00,0.00,,
"""


@pytest.fixture
def messy_csv(tmp_path):
    path = tmp_path / "statement.csv"
    path.write_text(MESSY_CSV, encoding="utf-8")
    return path


def test_csv_parser_handles_messy_statement(messy_csv):
    parsed = CSVStatementParser().parse(messy_csv, expected_currency="GBP")
    assert parsed.mapping_complete
    txns = parsed.result.transactions
    refs = [t.invoice_number or t.reference for t in txns]
    assert refs == ["INV-1001", "CN-0007", "INV-1002", "PMT-55"]

    types = {t.invoice_number or t.reference: t.transaction_type for t in txns}
    assert types["INV-1001"] == TransactionType.INVOICE
    assert types["CN-0007"] == TransactionType.CREDIT_NOTE
    assert types["PMT-55"] == TransactionType.PAYMENT

    balances = parsed.result.balances
    assert balances.opening_balance == Decimal("1000.00")
    assert balances.closing_balance == Decimal("1350.50")
    assert balances.opening_source == "detected"

    first = txns[0]
    assert first.amount == Decimal("1200.00")
    assert first.invoice_date == date(2026, 9, 3)
    assert first.currency == "GBP"
    assert first.raw_data["Invoice No."] == "INV-1001"


def test_csv_parser_reports_malformed_amounts(messy_csv):
    report = CSVStatementParser().parse(messy_csv).result.report
    errors = [i for i in report.issues if i.severity == IssueSeverity.ERROR]
    assert len(errors) == 1
    assert "abc" in errors[0].message
    assert errors[0].row == 13


def test_csv_semicolon_european_format(tmp_path):
    path = tmp_path / "eu.csv"
    path.write_text(
        "Datum;Rechnung;Amount\n"  # partially foreign headers
        "01.09.2026;R-100;1.234,56\n"
        "02.09.2026;R-101;-99,00\n",
        encoding="cp1252",
    )
    parser = CSVStatementParser()
    table = parser.extract_table(path)
    mapping = detect_mapping(table.headers, table.rows)
    mapping.fields["invoice_number"] = "Rechnung"  # user corrects the mapping
    parsed = parser.parse(path, mapping=mapping, table=table)
    amounts = [t.amount for t in parsed.result.transactions]
    assert amounts == [Decimal("1234.56"), Decimal("99.00")]
    # A negative amount against a document number is a credit, not a payment.
    assert parsed.result.transactions[1].transaction_type == TransactionType.CREDIT_NOTE


def test_csv_signed_amount_column_and_cr_suffix(tmp_path):
    path = tmp_path / "signed.csv"
    path.write_text(
        "Date,Reference,Type,Amount\n"
        "2026-09-01,INV-1,Invoice,500.00\n"
        "2026-09-02,CRN-2,,200.00 CR\n"
        "2026-09-03,INV-3,Invoice,-50.00\n",
        encoding="utf-8",
    )
    txns = CSVStatementParser().parse(path).result.transactions
    assert [t.transaction_type for t in txns] == [
        TransactionType.INVOICE,
        TransactionType.CREDIT_NOTE,
        TransactionType.CREDIT_NOTE,  # invoice with negative amount
    ]
    assert txns[0].invoice_number == "INV-1"  # reference used as invoice number


def test_empty_csv_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(StatementParserError, match="empty"):
        CSVStatementParser().parse(path)


def test_binary_file_with_csv_extension_is_rejected(tmp_path):
    path = tmp_path / "fake.csv"
    path.write_bytes(b"PK\x03\x04\x00\x00binary")
    with pytest.raises(StatementParserError):
        CSVStatementParser().parse(path)


def test_missing_columns_block_import(tmp_path):
    path = tmp_path / "nocols.csv"
    path.write_text("Name,Notes\nfoo,bar\n", encoding="utf-8")
    parsed = CSVStatementParser().parse(path)
    assert not parsed.mapping_complete
    assert parsed.result.transactions == []
    assert "Please map" in parsed.result.report.issues[0].message


def test_blank_invoice_number_and_zero_amount_warn(tmp_path):
    path = tmp_path / "blank.csv"
    path.write_text(
        "Date,Invoice Number,Amount\n01/09/2026,,100.00\n02/09/2026,INV-9,0.00\n",
        encoding="utf-8",
    )
    report = CSVStatementParser().parse(path).result.report
    messages = " ".join(i.message for i in report.issues)
    assert "blank invoice number" in messages
    assert "zero amount" in messages


def test_currency_mismatch_warning(tmp_path):
    path = tmp_path / "ccy.csv"
    path.write_text("Date,Invoice,Amount,Currency\n01/09/2026,INV-1,10.00,USD\n", encoding="utf-8")
    parsed = CSVStatementParser().parse(path, expected_currency="GBP")
    assert parsed.result.transactions[0].currency == "USD"
    assert any("currency USD" in i.message for i in parsed.result.report.issues)


# ---------------------------------------------------------------------- Excel


def test_excel_parser_with_letterhead_and_real_dates(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Notes"
    sheet["A1"] = "Nothing to see here"
    data = workbook.create_sheet("Statement")
    data.append(["Acme Supplies Ltd - Statement"])
    data.append([])
    data.append(["Invoice Date", "Inv #", "Gross Amount", "Running Balance"])
    data.append([datetime(2026, 9, 3), "INV-1001", 1200.0, 1200.0])
    data.append([datetime(2026, 9, 4), "INV-1002", 0.1 + 0.2, 1200.3])
    data.append([None, None, None, None])
    data.append([datetime(2026, 9, 30), "Closing balance", None, 1200.3])
    path = tmp_path / "statement.xlsx"
    workbook.save(path)

    parsed = ExcelStatementParser().parse(path)
    txns = parsed.result.transactions
    assert [t.invoice_number for t in txns] == ["INV-1001", "INV-1002"]
    assert txns[0].invoice_date == date(2026, 9, 3)
    assert txns[1].amount == Decimal("0.30")
    assert parsed.table.method == "xlsx:Statement"
    assert parsed.result.balances.opening_balance == Decimal("0.00")  # derived from balance


def test_corrupt_excel_is_rejected(tmp_path):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(StatementParserError, match="could not be opened"):
        ExcelStatementParser().parse(path)


# ------------------------------------------------------------------------ PDF


def _check_pdf_result(parsed, expected_rows):
    txns = parsed.result.transactions
    invoices = [t for t in txns if t.transaction_type == TransactionType.INVOICE]
    payments = [t for t in txns if t.transaction_type == TransactionType.PAYMENT]
    assert len(invoices) == expected_rows
    assert len(payments) == 1
    assert invoices[0].invoice_number == "INV-1001"
    assert invoices[0].amount == Decimal("100.07")
    assert parsed.result.balances.opening_balance == Decimal("1000.00")


def test_pdf_ruled_multi_page(tmp_path):
    rows = sample_rows(40)
    path = ruled_statement_pdf(tmp_path / "ruled.pdf", rows, rows_per_page=15)
    parsed = PDFStatementParser().parse(path)
    assert parsed.table.page_count >= 3
    _check_pdf_result(parsed, 40)


def test_pdf_column_layout_multi_page_with_repeated_headers(tmp_path):
    rows = sample_rows(45)
    path = layout_statement_pdf(tmp_path / "layout.pdf", rows, rows_per_page=20)
    parsed = PDFStatementParser().parse(path)
    assert parsed.table.method == "pdf:column-layout"
    assert parsed.table.page_count == 3
    _check_pdf_result(parsed, 45)
    last_invoice = [t for t in parsed.result.transactions if t.invoice_number][-1]
    assert last_invoice.invoice_number == "INV-1045"
    assert "page 3" in " ".join(parsed.table.row_labels)


def test_pdf_without_text_is_rejected(tmp_path):
    path = image_only_pdf(tmp_path / "scan.pdf")
    with pytest.raises(StatementParserError, match="scanned"):
        PDFStatementParser().parse(path)


def test_corrupt_pdf_is_rejected(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\n garbage")
    with pytest.raises(StatementParserError):
        PDFStatementParser().parse(path)


def test_parser_factory(tmp_path):
    assert isinstance(get_statement_parser("a.PDF"), PDFStatementParser)
    assert isinstance(get_statement_parser("a.xlsx"), ExcelStatementParser)
    assert isinstance(get_statement_parser("a.csv"), CSVStatementParser)
    with pytest.raises(StatementParserError, match="Unsupported"):
        get_statement_parser("a.exe")
