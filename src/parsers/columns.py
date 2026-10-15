"""Automatic column detection and user-correctable column mappings.

Detection works on header text first (synonyms, then fuzzy similarity) and is
then sanity-checked against the column content: a column called "Date" whose
values are not dates gets a low confidence so that the UI asks the user.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from src.utils.dates import infer_dayfirst, looks_like_date
from src.utils.money import infer_decimal_comma, parse_amount


class ColumnField(StrEnum):
    """Canonical fields a source column can be mapped to."""

    INVOICE_NUMBER = "invoice_number"
    REFERENCE = "reference"
    DATE = "date"
    DUE_DATE = "due_date"
    DESCRIPTION = "description"
    TRANSACTION_TYPE = "transaction_type"
    DEBIT = "debit"
    CREDIT = "credit"
    AMOUNT = "amount"
    PAYMENT_AMOUNT = "payment_amount"
    TAX = "tax"
    SUBTOTAL = "subtotal"
    BALANCE = "balance"
    OUTSTANDING = "outstanding_amount"
    CURRENCY = "currency"
    SUPPLIER_NAME = "supplier_name"
    SUPPLIER_ACCOUNT = "supplier_account"
    RELATED_INVOICE = "related_invoice_number"
    STATUS = "status"


FIELD_LABELS: dict[ColumnField, str] = {
    ColumnField.INVOICE_NUMBER: "Invoice number",
    ColumnField.REFERENCE: "Reference",
    ColumnField.DATE: "Date",
    ColumnField.DUE_DATE: "Due date",
    ColumnField.DESCRIPTION: "Description",
    ColumnField.TRANSACTION_TYPE: "Transaction type",
    ColumnField.DEBIT: "Debit",
    ColumnField.CREDIT: "Credit",
    ColumnField.AMOUNT: "Amount (gross)",
    ColumnField.PAYMENT_AMOUNT: "Payment amount",
    ColumnField.TAX: "Tax / VAT",
    ColumnField.SUBTOTAL: "Net / subtotal",
    ColumnField.BALANCE: "Running balance",
    ColumnField.OUTSTANDING: "Outstanding / amount due",
    ColumnField.CURRENCY: "Currency",
    ColumnField.SUPPLIER_NAME: "Supplier name",
    ColumnField.SUPPLIER_ACCOUNT: "Supplier account",
    ColumnField.RELATED_INVOICE: "Allocated / related invoice",
    ColumnField.STATUS: "Status",
}

MONEY_FIELDS = frozenset(
    {
        ColumnField.DEBIT, ColumnField.CREDIT, ColumnField.AMOUNT, ColumnField.PAYMENT_AMOUNT,
        ColumnField.TAX, ColumnField.SUBTOTAL, ColumnField.BALANCE, ColumnField.OUTSTANDING,
    }
)  # fmt: skip
DATE_FIELDS = frozenset({ColumnField.DATE, ColumnField.DUE_DATE})
AMOUNT_SOURCE_FIELDS = (
    ColumnField.AMOUNT,
    ColumnField.DEBIT,
    ColumnField.CREDIT,
    ColumnField.PAYMENT_AMOUNT,
)

SYNONYMS: dict[ColumnField, tuple[str, ...]] = {
    ColumnField.INVOICE_NUMBER: (
        "invoice number", "invoice no", "invoice num", "inv no", "inv number", "invoice",
        "invoice ref", "invoice reference", "invoicenumber", "document number", "document no",
        "doc no", "doc number", "bill number", "bill no", "num", "invoice id", "bill ref",
        "supplier invoice", "supplier invoice number", "vendor invoice", "number", "no",
        "document", "doc ref", "invoice credit no",
    ),
    ColumnField.REFERENCE: (
        "reference", "ref", "ref no", "transaction reference", "trans ref", "our ref",
        "your ref", "payment reference", "document ref", "cheque no", "transaction no",
        "transaction id", "txn ref",
    ),
    ColumnField.DATE: (
        "date", "invoice date", "transaction date", "trans date", "document date", "doc date",
        "posting date", "bill date", "issue date", "txn date", "invoicedate", "tax date",
        "payment date", "entry date",
    ),
    ColumnField.DUE_DATE: ("due date", "due", "payment due", "duedate", "date due"),
    ColumnField.DESCRIPTION: (
        "description", "details", "narrative", "particulars", "memo", "transaction details",
        "line description", "notes", "comment", "comments",
    ),
    ColumnField.TRANSACTION_TYPE: (
        "type", "transaction type", "trans type", "document type", "doc type", "txn type",
        "entry type", "tran type",
    ),
    ColumnField.DEBIT: ("debit", "debits", "dr", "debit amount", "charges", "charge"),
    ColumnField.CREDIT: (
        "credit", "credits", "cr", "credit amount", "payments credits", "payments and credits",
    ),
    ColumnField.AMOUNT: (
        "amount", "total", "gross", "gross amount", "invoice total", "total amount", "value",
        "invoice amount", "inv amount", "total incl vat", "total inc vat", "amount inc vat",
        "amount incl vat", "amount incl tax", "grand total", "total gross", "document total",
    ),
    ColumnField.PAYMENT_AMOUNT: ("payment", "payments", "amount paid", "paid", "payment amount",
                            "amountpaid"),
    ColumnField.TAX: ("tax", "vat", "gst", "tax amount", "vat amount", "taxtotal", "tax total",
                 "sales tax"),
    ColumnField.SUBTOTAL: ("subtotal", "sub total", "net", "net amount", "amount ex vat",
                      "amount excl vat", "excl tax", "net total"),
    ColumnField.BALANCE: ("balance", "running balance", "cumulative balance", "bal",
                     "account balance"),
    ColumnField.OUTSTANDING: (
        "outstanding", "amount due", "amountdue", "open balance", "balance due", "due amount",
        "remaining", "unpaid", "open amount", "amount outstanding", "o s",
    ),
    ColumnField.CURRENCY: ("currency", "curr", "ccy", "currency code", "currencycode"),
    ColumnField.SUPPLIER_NAME: (
        "supplier", "supplier name", "vendor", "vendor name", "contact", "contact name",
        "contactname", "payee", "creditor", "name",
    ),
    ColumnField.SUPPLIER_ACCOUNT: (
        "account number", "account no", "supplier account", "vendor id", "supplier code",
        "vendor number", "supplier id", "account ref", "acct no",
    ),
    ColumnField.RELATED_INVOICE: (
        "applied to", "allocated to", "related invoice", "original invoice", "against invoice",
        "allocation", "invoice applied", "for invoice", "invoices paid",
    ),
    ColumnField.STATUS: ("status", "invoice status", "bill status"),
}  # fmt: skip

_HEADER_CLEAN = re.compile(r"[^a-z0-9]+")
_EXACT_INDEX: dict[str, ColumnField] = {}
for _field, _names in SYNONYMS.items():
    for _name in _names:
        _EXACT_INDEX.setdefault(_name, _field)


def normalize_header(header: object) -> str:
    """'Invoice No.' -> 'invoice no'; '#' becomes 'no'."""
    text = str(header or "").lower().replace("#", " no ").replace("&", " and ")
    return " ".join(_HEADER_CLEAN.sub(" ", text).split())


def score_header(header: object) -> tuple[ColumnField | None, int]:
    """Return the most likely field for a header and a 0-100 confidence."""
    norm = normalize_header(header)
    if not norm:
        return None, 0
    if norm in _EXACT_INDEX:
        return _EXACT_INDEX[norm], 100
    best_field, best_score = None, 0
    for field, names in SYNONYMS.items():
        for name in names:
            if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", norm):
                score = 80 + min(15, len(name))  # longer synonym inside header => stronger
            else:
                score = int(fuzz.ratio(norm, name))
                if score < 88:
                    continue
                score -= 8
            if score > best_score:
                best_field, best_score = field, score
    return best_field, min(best_score, 95)


class ColumnMapping(BaseModel):
    """Mapping of canonical fields to source column headers."""

    fields: dict[str, str] = Field(default_factory=dict)
    confidence: dict[str, int] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)
    dayfirst: bool | None = None
    decimal_comma: bool | None = None

    def column_for(self, field: ColumnField) -> str | None:
        return self.fields.get(field.value)

    def has(self, field: ColumnField) -> bool:
        return bool(self.fields.get(field.value))

    def uncertain_fields(self, threshold: int = 80) -> list[str]:
        return [f for f, c in self.confidence.items() if f in self.fields and c < threshold]


def _content_check(field: ColumnField, values: Sequence[str]) -> tuple[float, str | None]:
    sample = [v for v in values if v and v.strip()][:200]
    if not sample:
        return 0.0, "column is empty"
    if field in DATE_FIELDS:
        ratio = sum(looks_like_date(v) for v in sample) / len(sample)
        return ratio, None if ratio >= 0.6 else "values do not look like dates"
    if field in MONEY_FIELDS:
        ratio = sum(parse_amount(v) is not None for v in sample) / len(sample)
        return ratio, None if ratio >= 0.6 else "values do not look like amounts"
    return 1.0, None


def detect_mapping(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> ColumnMapping:
    """Detect a column mapping from headers and (sample) rows."""
    candidates: list[tuple[int, int, ColumnField]] = []
    for index, header in enumerate(headers):
        field, score = score_header(header)
        if field is not None and score >= 70:
            candidates.append((score, index, field))
        # Secondary candidates: allow a header to serve another field it matches exactly
        norm = normalize_header(header)
        for other, names in SYNONYMS.items():
            if other != field and norm in names:
                candidates.append((90, index, other))

    mapping = ColumnMapping()
    used_columns: set[int] = set()
    for score, index, field in sorted(candidates, key=lambda c: (-c[0], c[1])):
        if field.value in mapping.fields or index in used_columns:
            continue
        values = [row[index] if index < len(row) else "" for row in rows]
        ratio, problem = _content_check(field, values)
        confidence = score
        if problem:
            confidence = max(10, score - 45)
            mapping.notes[field.value] = f"'{headers[index]}': {problem}"
        elif ratio < 0.9 and (field in DATE_FIELDS or field in MONEY_FIELDS):
            confidence = max(10, score - 15)
        mapping.fields[field.value] = headers[index]
        mapping.confidence[field.value] = confidence
        used_columns.add(index)

    _fill_by_content(headers, rows, mapping, used_columns)

    date_values: list[str] = []
    for field in DATE_FIELDS:
        column = mapping.column_for(field)
        if column is not None:
            date_values.extend(_column_values(headers, rows, column))
    mapping.dayfirst = infer_dayfirst(date_values)
    money_values: list[str] = []
    for field in MONEY_FIELDS:
        column = mapping.column_for(field)
        if column is not None:
            money_values.extend(_column_values(headers, rows, column))
    mapping.decimal_comma = infer_decimal_comma(money_values)
    return mapping


def _column_values(headers: Sequence[str], rows: Sequence[Sequence[str]], column: str):
    index = list(headers).index(column)
    return [row[index] if index < len(row) else "" for row in rows]


def _fill_by_content(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    mapping: ColumnMapping,
    used: set[int],
) -> None:
    """Guess date/amount columns from content when headers were not recognised."""
    if mapping.has(ColumnField.DATE) and any(mapping.has(f) for f in AMOUNT_SOURCE_FIELDS):
        return
    for index, header in enumerate(headers):
        if index in used:
            continue
        values = [row[index] if index < len(row) else "" for row in rows]
        if not mapping.has(ColumnField.DATE):
            ratio, problem = _content_check(ColumnField.DATE, values)
            if problem is None and ratio >= 0.8:
                mapping.fields[ColumnField.DATE.value] = header
                mapping.confidence[ColumnField.DATE.value] = 55
                mapping.notes[ColumnField.DATE.value] = f"'{header}' guessed from its content"
                used.add(index)
                continue
        if not any(mapping.has(f) for f in AMOUNT_SOURCE_FIELDS):
            ratio, problem = _content_check(ColumnField.AMOUNT, values)
            if problem is None and ratio >= 0.8:
                mapping.fields[ColumnField.AMOUNT.value] = header
                mapping.confidence[ColumnField.AMOUNT.value] = 50
                mapping.notes[ColumnField.AMOUNT.value] = f"'{header}' guessed from its content"
                used.add(index)


def header_row_score(cells: Sequence[object]) -> int:
    """How many cells in a row look like known column headers."""
    hits = 0
    seen: set[ColumnField] = set()
    for cell in cells:
        if looks_like_date(cell) or parse_amount(cell) is not None:
            continue
        field, score = score_header(cell)
        if field is not None and score >= 80 and field not in seen:
            hits += 1
            seen.add(field)
    return hits


def locate_header_row(grid: Sequence[Sequence[str]], search_rows: int = 40) -> int | None:
    """Index of the most header-like row among the first rows, or None."""
    best_index, best_score = None, 1
    for index, row in enumerate(grid[:search_rows]):
        score = header_row_score(row)
        if score > best_score:
            best_index, best_score = index, score
    return best_index


def missing_required_fields(mapping: ColumnMapping, *, require_reference: bool = True) -> list[str]:
    """User-facing descriptions of required fields that are not mapped."""
    missing: list[str] = []
    if not mapping.has(ColumnField.DATE):
        missing.append("Date")
    if not any(mapping.has(f) for f in AMOUNT_SOURCE_FIELDS):
        missing.append("Amount (or Debit / Credit)")
    if require_reference and not (
        mapping.has(ColumnField.INVOICE_NUMBER) or mapping.has(ColumnField.REFERENCE)
    ):
        missing.append("Invoice number (or Reference)")
    return missing
