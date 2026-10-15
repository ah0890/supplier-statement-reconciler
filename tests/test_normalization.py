"""Invoice and supplier normalisation rules."""

from decimal import Decimal

import pytest

from src.models.transaction import CanonicalTransaction, TransactionSource
from src.models.validation import IssueSeverity, ValidationReport
from src.services.normalization_service import NormalizationContext, normalize_transactions
from src.utils.normalization import (
    invoice_core,
    normalize_invoice_number,
    normalize_supplier_name,
    reference_similarity,
    supplier_names_match,
)


@pytest.mark.parametrize(
    "raw",
    [
        "INV-00123",
        "INV00123",
        "inv 00123",
        "INV/00123",
        " Inv_00123 ",
        "INV.00123",
        "INV#00123",
        "Invoice No: INV-00123",
    ],
)
def test_equivalent_invoice_formats_normalise_identically(raw):
    assert normalize_invoice_number(raw) == "INV00123"


def test_leading_zeros_and_letters_are_preserved():
    assert normalize_invoice_number("INV-0123") != normalize_invoice_number("INV-123")
    assert normalize_invoice_number("INV-123A") != normalize_invoice_number("INV-123")
    assert normalize_invoice_number("CN-123") != normalize_invoice_number("INV-123")


@pytest.mark.parametrize("raw", [None, "", "   ", "--"])
def test_blank_invoice_numbers(raw):
    assert normalize_invoice_number(raw) is None


def test_unusual_references_keep_meaningful_characters():
    assert normalize_invoice_number("2026/ABC/0045") == "2026ABC0045"
    assert normalize_invoice_number("PO+123") == "PO+123"


def test_invoice_core():
    assert invoice_core("INV00123") == "123"
    assert invoice_core("00123") == "123"
    assert invoice_core("ABC") is None
    assert invoice_core(None) is None


@pytest.mark.parametrize(
    "raw", ["ABC Ltd", "ABC LIMITED", "ABC LTD.", "abc  ltd", "The ABC Limited"]
)
def test_supplier_legal_suffixes(raw):
    assert normalize_supplier_name(raw) == "ABC"


def test_supplier_normalisation_is_not_aggressive():
    assert normalize_supplier_name("Smith & Sons Ltd") == "SMITH AND SONS"
    assert normalize_supplier_name("Ltd") == "LTD"  # never empty
    assert normalize_supplier_name("Acme Co Ltd") == "ACME"
    assert normalize_supplier_name("Acme Trading Ltd") != normalize_supplier_name("Acme Ltd")
    assert normalize_supplier_name("Bob's Plumbing Ltd T/A Bob Pipes") == "BOB S PLUMBING"


def test_supplier_match():
    assert supplier_names_match("ABC Ltd", "ABC LIMITED")
    assert supplier_names_match("Acme Supplies Ltd", "ACME SUPPLIES")
    assert not supplier_names_match("Acme Supplies", "Apex Supplies")
    assert not supplier_names_match(None, "ABC")


def test_reference_similarity():
    assert reference_similarity("INV10025", "INV10052") >= 75
    assert reference_similarity("INV10025", "CN999") < 50
    assert reference_similarity(None, "x") == 0


def _txn(name, invoice="INV-1", account=None):
    return CanonicalTransaction(
        source=TransactionSource.ACCOUNTING,
        supplier_name=name,
        supplier_account=account,
        invoice_number=invoice,
        amount=Decimal("1"),
    )


def test_normalize_transactions_filters_other_suppliers():
    report = ValidationReport()
    txns = [_txn("Acme Supplies Ltd"), _txn("ACME SUPPLIES LIMITED", "INV 2"), _txn("Other plc")]
    kept, excluded = normalize_transactions(
        txns, NormalizationContext("Acme Supplies", currency="GBP"), report
    )
    assert len(kept) == 2 and excluded == 1
    assert kept[1].invoice_number_normalized == "INV2"
    assert kept[0].currency == "GBP"
    assert any("Other plc" in i.message for i in report.issues)


def test_normalize_transactions_matches_on_account_number():
    report = ValidationReport()
    txns = [_txn("Acme (old name)", account="ACC-9"), _txn("Someone else", account="X")]
    kept, _ = normalize_transactions(
        txns, NormalizationContext("Acme Supplies", supplier_account="acc-9"), report
    )
    assert [t.supplier_name for t in kept] == ["Acme (old name)"]


def test_no_matching_supplier_is_an_error():
    report = ValidationReport()
    kept, _ = normalize_transactions(
        [_txn("Foo Ltd"), _txn("Bar Ltd")], NormalizationContext("Acme"), report
    )
    assert kept == []
    assert report.errors == 1


def test_single_supplier_mismatch_warns_but_keeps_rows():
    report = ValidationReport()
    kept, _ = normalize_transactions([_txn("Acme Group")], NormalizationContext("Apex"), report)
    assert len(kept) == 1
    assert report.issues[0].severity == IssueSeverity.WARNING


def test_accounting_rows_without_account_column_are_filtered_by_name(tmp_path):
    from src.accounting import get_file_provider
    from src.accounting.base import AccountingQuery

    path = tmp_path / "bills.csv"
    path.write_text(
        "Supplier,Bill No,Date,Total\nAcme Ltd,A-1,01/09/2026,10\nOther plc,B-1,01/09/2026,20\n",
        encoding="utf-8",
    )
    loaded = get_file_provider(path).load(
        AccountingQuery(supplier_name="Acme", supplier_account="X1")
    )
    report = loaded.result.report
    kept, excluded = normalize_transactions(
        loaded.result.transactions, NormalizationContext("Acme", supplier_account="X1"), report
    )
    assert [t.invoice_number for t in kept] == ["A-1"]
    assert excluded == 1
