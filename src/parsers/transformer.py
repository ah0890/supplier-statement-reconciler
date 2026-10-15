"""Convert mapped table rows into canonical transactions.

Shared by statement parsers and accounting providers so that every source is
interpreted by the same, tested rules. The transformer:

* skips blank rows, repeated headers, totals/subtotals and ageing summaries;
* captures opening / closing balance rows instead of treating them as invoices;
* works out amount and direction from Amount, Debit/Credit or Payment columns;
* classifies invoices, credit notes and payments from type columns, keywords,
  reference patterns and finally the debit/credit side;
* records every assumption or problem as a row-level validation issue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from src.models.transaction import (
    CanonicalTransaction,
    StatementBalances,
    TransactionSource,
    TransactionType,
)
from src.models.validation import ImportResult, IssueSeverity, ValidationReport
from src.parsers.columns import ColumnField, ColumnMapping, normalize_header
from src.parsers.tabular import RawTable
from src.utils.dates import parse_date
from src.utils.money import ZERO, parse_amount

_OPENING = re.compile(
    r"\b(opening balance|balance b ?/ ?f(wd)?|brought forward|b ?/ ?fwd|balance forward|"
    r"previous balance|opening)\b",
    re.IGNORECASE,
)
_CLOSING = re.compile(
    r"\b(closing balance|balance c ?/ ?f(wd)?|carried forward|c ?/ ?fwd|total due|amount due|"
    r"balance due|total outstanding|amount now due|closing|balance outstanding)\b",
    re.IGNORECASE,
)
_TOTAL = re.compile(
    r"^\s*(sub ?-?totals?|page totals?|grand totals?|totals?|statement totals?|total for|"
    r"total this page|continued)\b",
    re.IGNORECASE,
)
_AGEING = re.compile(
    r"\b(current|30 days|60 days|90 days|over 90|90\+|120\+|1-30|31-60|61-90|ageing|aging|"
    r"days overdue)\b",
    re.IGNORECASE,
)

_WORDS = re.compile(r"[A-Za-z]{3,}")
_MONEY_LIKE = re.compile(r"-?[\d,]+\.\d{2}")

_CREDIT_TYPE = re.compile(
    r"\b(credit ?notes?|cr ?notes?|crn|cn|credit memo|vendor credit|accpaycredit|"
    r"purchase credit|supplier credit|credit)\b",
    re.IGNORECASE,
)
_PAYMENT_TYPE = re.compile(
    r"\b(payments?|pmt|paid|receipts?|remittance|bacs|cheque|check|chq|bill payment|"
    r"direct debit|dd|transfer|faster payment|chaps|wire|ach|bank)\b",
    re.IGNORECASE,
)
_INVOICE_TYPE = re.compile(
    r"\b(invoices?|inv|bills?|accpay|purchase invoice|tax invoice|charge|debit note|sale|"
    r"sales)\b",
    re.IGNORECASE,
)
_CREDIT_DESC = re.compile(r"\b(credit ?note|credit memo|crn|refund)\b", re.IGNORECASE)
_PAYMENT_DESC = re.compile(
    r"\b(payment|paid|remittance|bacs|cheque|chq|receipt|direct debit|faster payment|"
    r"bank transfer|chaps)\b",
    re.IGNORECASE,
)
_CREDIT_REF = re.compile(r"^(CN|CRN|CR|CM|CRED)[\s\-/#.]*\d", re.IGNORECASE)
_PAYMENT_REF = re.compile(r"^(PMT|PAY|BACS|CHQ|RCPT|REM|DD|FP)[\s\-/#.]*\d", re.IGNORECASE)


@dataclass
class TransformOptions:
    source: TransactionSource
    source_file: str | None = None
    expected_currency: str | None = None
    default_supplier: str | None = None
    default_supplier_account: str | None = None
    # How to classify a credit-side row with no other evidence.
    credit_default: TransactionType = TransactionType.PAYMENT
    # Force every row to one type (e.g. a payments export).
    force_type: TransactionType | None = None
    # In a payments export the "invoice" column is the allocation.
    invoice_column_is_allocation: bool = False
    detect_balances: bool = True


@dataclass
class _Cells:
    """Accessor for mapped cells of one row."""

    row: list[str]
    index: dict[str, int]

    def get(self, field: ColumnField) -> str:
        position = self.index.get(field.value)
        if position is None or position >= len(self.row):
            return ""
        return self.row[position].strip()


class TableTransformer:
    """Applies a column mapping to a :class:`RawTable`."""

    def __init__(self, mapping: ColumnMapping, options: TransformOptions) -> None:
        self.mapping = mapping
        self.options = options

    def transform(self, table: RawTable) -> ImportResult:
        report = ValidationReport()
        balances = StatementBalances()
        transactions: list[CanonicalTransaction] = []
        index = {
            field: table.headers.index(column)
            for field, column in self.mapping.fields.items()
            if column in table.headers
        }
        header_signature = {normalize_header(h) for h in table.headers if h}
        self._in_ageing_section = False

        for warning in table.warnings:
            report.add(IssueSeverity.WARNING, warning)

        for row, label, row_number in zip(
            table.rows, table.row_labels, table.source_rows, strict=True
        ):
            if not any(cell.strip() for cell in row):
                continue
            report.records_read += 1
            if self._is_repeated_header(row, header_signature):
                report.skipped += 1
                continue

            cells = _Cells(row, index)
            outcome = self._transform_row(cells, row, label, row_number, table, report, balances)
            if outcome is None:
                report.skipped += 1
            else:
                transactions.append(outcome)

        self._derive_balances(transactions, balances)
        report.valid = len(transactions)
        if not transactions:
            report.add(
                IssueSeverity.ERROR,
                "Unable to identify transaction rows. Check that the correct columns are "
                "mapped for Date and Amount.",
            )
        return ImportResult(transactions=transactions, balances=balances, report=report)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _is_repeated_header(row: list[str], signature: set[str]) -> bool:
        values = [normalize_header(c) for c in row if c.strip()]
        if len(values) < 2:
            return False
        return sum(v in signature for v in values) / len(values) >= 0.6

    def _parse_money(
        self, text: str, label: str, field: str, report: ValidationReport, row_number: int
    ) -> tuple[Decimal | None, bool]:
        """Return (value, ok). ok=False means a non-empty value could not be parsed."""
        if not text:
            return None, True
        value = parse_amount(text, decimal_comma=self.mapping.decimal_comma)
        if value is None:
            report.add(
                IssueSeverity.ERROR,
                f"{label}: amount '{text}' in {field} could not be parsed - row excluded.",
                row=row_number,
                field=field,
            )
            return None, False
        return value, True

    @staticmethod
    def _money_cells_contain_words(cells: _Cells) -> bool:
        for field in (ColumnField.AMOUNT, ColumnField.DEBIT, ColumnField.CREDIT,
                      ColumnField.PAYMENT_AMOUNT, ColumnField.BALANCE):  # fmt: skip
            text = cells.get(field)
            if text and _WORDS.search(text) and parse_amount(text) is None:
                return True
        return False

    def _row_text(self, row: list[str]) -> str:
        return " ".join(c for c in row if c and parse_amount(c) is None)

    def _transform_row(
        self,
        cells: _Cells,
        row: list[str],
        label: str,
        row_number: int,
        table: RawTable,
        report: ValidationReport,
        balances: StatementBalances,
    ) -> CanonicalTransaction | None:
        opts = self.options
        raw_date = cells.get(ColumnField.DATE)
        txn_date = parse_date(raw_date, dayfirst=self.mapping.dayfirst)
        row_text = self._row_text(row)
        invoice_text = cells.get(ColumnField.INVOICE_NUMBER)
        reference_text = cells.get(ColumnField.REFERENCE)

        # --- balance, total and summary rows ---------------------------------
        if opts.detect_balances and not invoice_text:
            if _OPENING.search(row_text):
                balances.opening_balance = self._balance_value(cells, row)
                balances.opening_source = "detected"
                return None
            if _CLOSING.search(row_text):
                balances.closing_balance = self._balance_value(cells, row)
                balances.closing_source = "detected"
                return None
        first_text = next((c for c in row if c.strip()), "")
        if _TOTAL.match(first_text) or (not txn_date and _TOTAL.search(row_text)):
            return None
        if not txn_date and _AGEING.search(row_text):
            # Ageing summaries ("Current / 30 days / 60 days ...") and the value rows
            # beneath them are not transactions.
            self._in_ageing_section = True
            return None
        if not txn_date and (self._in_ageing_section or _MONEY_LIKE.fullmatch(invoice_text)):
            return None

        # --- free-text lines (addresses, notes, remittance text) ----------------
        if not txn_date and self._money_cells_contain_words(cells):
            report.add(
                IssueSeverity.INFO, f"{label}: text line without a date skipped.", row=row_number
            )
            return None

        # --- amounts -----------------------------------------------------------
        problems = False
        values: dict[ColumnField, Decimal | None] = {}
        for field in (
            ColumnField.AMOUNT, ColumnField.DEBIT, ColumnField.CREDIT,
            ColumnField.PAYMENT_AMOUNT, ColumnField.TAX, ColumnField.SUBTOTAL,
            ColumnField.BALANCE, ColumnField.OUTSTANDING,
        ):  # fmt: skip
            value, ok = self._parse_money(cells.get(field), label, field.value, report, row_number)
            values[field] = value
            problems |= not ok
        if problems:
            return None

        amount, side = self._amount_and_side(values)
        if amount is None:
            if txn_date or invoice_text:
                report.add(
                    IssueSeverity.WARNING,
                    f"{label}: no amount found - row skipped.",
                    row=row_number,
                )
            return None
        if raw_date and txn_date is None:
            report.add(
                IssueSeverity.WARNING,
                f"{label}: date '{raw_date}' could not be read.",
                row=row_number,
                field="date",
            )
        elif not raw_date:
            report.add(IssueSeverity.WARNING, f"{label}: no date on this row.", row=row_number)

        # --- classification ----------------------------------------------------
        txn_type, type_note = self._classify(cells, side, invoice_text or reference_text)
        if type_note:
            report.add(IssueSeverity.INFO, f"{label}: {type_note}", row=row_number)

        related = cells.get(ColumnField.RELATED_INVOICE) or None
        invoice_number: str | None = invoice_text or None
        reference: str | None = reference_text or None
        if opts.invoice_column_is_allocation and invoice_number:
            related = related or invoice_number
            invoice_number = None
        if txn_type == TransactionType.PAYMENT and not opts.invoice_column_is_allocation:
            reference = reference or invoice_number
            invoice_number = None
        elif invoice_number is None and txn_type != TransactionType.PAYMENT:
            invoice_number = reference

        if txn_type != TransactionType.PAYMENT and not invoice_number:
            report.add(
                IssueSeverity.WARNING,
                f"{label}: blank invoice number - will be matched on amount and date only.",
                row=row_number,
                field="invoice_number",
            )
        if amount == ZERO:
            report.add(IssueSeverity.WARNING, f"{label}: zero amount.", row=row_number)

        currency = cells.get(ColumnField.CURRENCY).upper() or opts.expected_currency
        if opts.expected_currency and currency and currency != opts.expected_currency.upper():
            report.add(
                IssueSeverity.WARNING,
                f"{label}: currency {currency} differs from reconciliation currency "
                f"{opts.expected_currency}.",
                row=row_number,
                field="currency",
            )

        raw = {h: v for h, v in zip(table.headers, row, strict=False) if v}
        return CanonicalTransaction(
            source=opts.source,
            transaction_type=txn_type,
            supplier_name=cells.get(ColumnField.SUPPLIER_NAME) or opts.default_supplier,
            supplier_account=cells.get(ColumnField.SUPPLIER_ACCOUNT)
            or opts.default_supplier_account,
            invoice_number=invoice_number,
            reference=reference,
            related_invoice_number=related,
            invoice_date=txn_date,
            due_date=parse_date(cells.get(ColumnField.DUE_DATE), dayfirst=self.mapping.dayfirst),
            description=cells.get(ColumnField.DESCRIPTION) or None,
            subtotal=_abs(values[ColumnField.SUBTOTAL]),
            tax=_abs(values[ColumnField.TAX]),
            amount=amount,
            currency=currency or None,
            debit=values[ColumnField.DEBIT],
            credit=values[ColumnField.CREDIT],
            payment_amount=values[ColumnField.PAYMENT_AMOUNT],
            outstanding_amount=values[ColumnField.OUTSTANDING],
            balance=values[ColumnField.BALANCE],
            status=cells.get(ColumnField.STATUS) or None,
            source_file=opts.source_file,
            source_row=row_number,
            raw_data=raw,
        )

    def _balance_value(self, cells: _Cells, row: list[str]) -> Decimal | None:
        decimal_comma = self.mapping.decimal_comma
        for field in (ColumnField.BALANCE, ColumnField.AMOUNT, ColumnField.OUTSTANDING):
            value = parse_amount(cells.get(field), decimal_comma=decimal_comma)
            if value is not None:
                return value
        debit = parse_amount(cells.get(ColumnField.DEBIT), decimal_comma=decimal_comma)
        credit = parse_amount(cells.get(ColumnField.CREDIT), decimal_comma=decimal_comma)
        if debit is not None or credit is not None:
            return (debit or ZERO) - (credit or ZERO)
        for cell in reversed(row):  # last number on the line, e.g. "Closing balance ... 1,234.00"
            value = parse_amount(cell, decimal_comma=decimal_comma)
            if value is not None:
                return value
        return None

    @staticmethod
    def _amount_and_side(values: dict[ColumnField, Decimal | None]):
        """Return (positive amount, 'debit'|'credit') or (None, None)."""
        debit, credit = values[ColumnField.DEBIT], values[ColumnField.CREDIT]
        if debit or credit:
            net = (debit or ZERO) - (credit or ZERO)
            if net == ZERO and debit:
                return abs(debit), "debit"
            return abs(net), "debit" if net > 0 else "credit"
        amount = values[ColumnField.AMOUNT]
        if amount is not None:
            return abs(amount), "credit" if amount < 0 else "debit"
        payment = values[ColumnField.PAYMENT_AMOUNT]
        if payment is not None:
            return abs(payment), "credit"
        if debit is not None or credit is not None:  # explicit zero
            return ZERO, "debit"
        return None, None

    def _classify(self, cells: _Cells, side: str, reference: str):
        """Return (type, note). The note explains inferred classifications."""
        opts = self.options
        if opts.force_type is not None:
            return opts.force_type, None

        type_text = cells.get(ColumnField.TRANSACTION_TYPE)
        description = cells.get(ColumnField.DESCRIPTION)
        explicit: TransactionType | None = None
        if type_text:
            if _CREDIT_TYPE.search(type_text):
                explicit = TransactionType.CREDIT_NOTE
            elif _PAYMENT_TYPE.search(type_text):
                explicit = TransactionType.PAYMENT
            elif _INVOICE_TYPE.search(type_text):
                explicit = TransactionType.INVOICE

        if explicit is None:
            if _CREDIT_REF.match(reference) or _CREDIT_DESC.search(description):
                explicit = TransactionType.CREDIT_NOTE
            elif _PAYMENT_REF.match(reference) or _PAYMENT_DESC.search(description):
                explicit = TransactionType.PAYMENT

        if explicit is not None:
            if explicit == TransactionType.INVOICE and side == "credit":
                return (
                    TransactionType.CREDIT_NOTE,
                    "invoice-type row with a negative/credit amount treated as a credit note.",
                )
            return explicit, None

        if side == "debit":
            return TransactionType.INVOICE, None
        return (
            opts.credit_default,
            f"credit entry without a type was classified as "
            f"{opts.credit_default.value.replace('_', ' ').lower()}.",
        )

    def _derive_balances(
        self, transactions: list[CanonicalTransaction], balances: StatementBalances
    ) -> None:
        if not self.options.detect_balances or not transactions:
            return
        first, last = transactions[0], transactions[-1]
        if balances.opening_balance is None and first.balance is not None:
            balances.opening_balance = first.balance - first.signed_amount
            balances.opening_source = "derived"
        if balances.closing_balance is None and last.balance is not None:
            balances.closing_balance = last.balance
            balances.closing_source = "derived"


def _abs(value: Decimal | None) -> Decimal | None:
    return abs(value) if value is not None else None
