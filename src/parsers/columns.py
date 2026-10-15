"""Automatic column detection and user-correctable column mappings.

Detection works on header text first (synonyms, then fuzzy similarity) and is
then sanity-checked against the column content: a column called "Date" whose
values are not dates gets a low confidence so that the UI asks the user.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal
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

# Common abbreviations and non-English (German, French, Dutch, Spanish) statement headers.
# Headers are compared after accents are removed ("Währung" -> "wahrung").
_ADDITIONAL_SYNONYMS: dict[ColumnField, tuple[str, ...]] = {
    ColumnField.INVOICE_NUMBER: (
        "belegnummer", "belegnr", "beleg nr", "rechnungsnummer", "rechnungsnr", "rechnung nr",
        "numero de facture", "no facture", "n facture", "facture", "factuurnummer",
        "numero factura",
    ),
    ColumnField.DATE: (
        "belegdatum", "datum", "buchungsdatum", "rechnungsdatum", "date de facture",
        "date piece", "factuurdatum", "fecha", "txn date",
    ),
    ColumnField.DUE_DATE: ("falligkeit", "falligkeitsdatum", "echeance", "date d echeance",
                           "vervaldatum"),
    ColumnField.DESCRIPTION: ("buchungstext", "text", "beschreibung", "libelle", "designation",
                              "omschrijving", "concepto"),
    ColumnField.TRANSACTION_TYPE: ("belegart", "buchungsart", "type de piece"),
    ColumnField.DEBIT: ("soll", "debe"),
    ColumnField.CREDIT: ("haben", "haber", "paid credited", "paid or credited"),
    ColumnField.AMOUNT: ("betrag", "montant", "bedrag", "importe", "invoice amt", "inv amt",
                         "amt", "gross amt"),
    ColumnField.BALANCE: ("saldo", "solde", "running bal", "balance due to date"),
    ColumnField.CURRENCY: ("wahrung", "devise", "valuta", "moneda"),
    ColumnField.TAX: ("mwst", "ust", "tva", "btw", "iva"),
    ColumnField.REFERENCE: ("referenz", "referentie"),
    ColumnField.SUPPLIER_NAME: ("lieferant", "fournisseur", "leverancier", "proveedor"),
}  # fmt: skip
for _field, _extra in _ADDITIONAL_SYNONYMS.items():
    SYNONYMS[_field] = SYNONYMS[_field] + _extra

_HEADER_CLEAN = re.compile(r"[^a-z0-9]+")
_EXACT_INDEX: dict[str, ColumnField] = {}
for _field, _names in SYNONYMS.items():
    for _name in _names:
        _EXACT_INDEX.setdefault(_name, _field)


def normalize_header(header: object) -> str:
    """'Invoice No.' -> 'invoice no'; '#' becomes 'no'."""
    text = unicodedata.normalize("NFKD", str(header or "")).encode("ascii", "ignore").decode()
    text = text.lower().replace("#", " no ").replace("&", " and ")
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


# Values typical of a document-type column (INV / PAY / CN / B/FWD ...), not of references.
_TYPE_VALUE = re.compile(
    r"^(inv|invoice|bill|pay|payment|pmt|rcpt|receipt|cr|crn|cn|c/n|credit|credit note|dr|"
    r"adj|adjustment|b ?/ ?fwd|b ?/ ?f|pi|pc|pp|pa|si|sc|re|gs|za|accpay|accpaycredit|"
    r"vendor credit|bill payment|rechnung|gutschrift|zahlung)$",
    re.IGNORECASE,
)


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
    if field in (ColumnField.INVOICE_NUMBER, ColumnField.REFERENCE) and len(sample) >= 4:
        distinct = len({v.strip().upper() for v in sample}) / len(sample)
        typeish = sum(bool(_TYPE_VALUE.match(v.strip())) for v in sample) / len(sample)
        if typeish >= 0.6 or distinct < 0.3:
            return distinct, "values repeat like document types rather than references"
    return 1.0, None


def _looks_like_type_column(values: Sequence[str]) -> bool:
    sample = [v.strip() for v in values if v and v.strip()][:200]
    return len(sample) >= 3 and sum(bool(_TYPE_VALUE.match(v)) for v in sample) / len(sample) >= 0.6


def _candidates(headers: Sequence[str], rows: Sequence[Sequence[str]]):
    """All plausible (confidence, header score, column index, field, note) assignments."""
    found = []
    for index, header in enumerate(headers):
        own_name = normalize_header(header)
        values = [  # repeated header rows (multi-page files) are not content
            v for row in rows
            if (v := row[index] if index < len(row) else "") and normalize_header(v) != own_name
        ]  # fmt: skip
        fields: dict[ColumnField, int] = {}
        field, score = score_header(header)
        if field is not None and score >= 70:
            fields[field] = score
        norm = normalize_header(header)
        for other, names in SYNONYMS.items():  # a header may serve another field exactly
            if other not in fields and norm in names:
                fields[other] = 90
        if _looks_like_type_column(values):
            fields[ColumnField.TRANSACTION_TYPE] = max(
                fields.get(ColumnField.TRANSACTION_TYPE, 0), 92
            )
        for candidate, header_score in fields.items():
            ratio, problem = _content_check(candidate, values)
            confidence = header_score
            if problem:
                confidence = max(10, header_score - 45)
            elif ratio < 0.9 and (candidate in DATE_FIELDS or candidate in MONEY_FIELDS):
                confidence = max(10, header_score - 15)
            found.append((confidence, header_score, index, candidate, problem))
    return found


def _balance_agreement(
    headers: Sequence[str], rows: Sequence[Sequence[str]], amount_col: str, balance_col: str
) -> float:
    """Share of rows where the change in running balance equals the amount column."""
    balances = [parse_amount(v) for v in _column_values(headers, rows, balance_col)]
    amounts = [parse_amount(v) for v in _column_values(headers, rows, amount_col)]
    hits = tries = 0
    previous = None
    for balance, amount in zip(balances, amounts, strict=True):
        if balance is not None and previous is not None and amount is not None:
            tries += 1
            hits += abs(abs(balance - previous) - abs(amount)) <= Decimal("0.01")
        if balance is not None:
            previous = balance
    return hits / tries if tries else 0.0


def _prefer_balance_consistent_amount(
    headers: Sequence[str], rows: Sequence[Sequence[str]], mapping: ColumnMapping
) -> None:
    """When several columns could be 'the amount' (e.g. net and gross both called
    'Amount'), choose the one whose values move the running balance."""
    amount_col = mapping.column_for(ColumnField.AMOUNT)
    balance_col = mapping.column_for(ColumnField.BALANCE)
    if not amount_col or not balance_col:
        return
    mapped = set(mapping.fields.values())
    options = [
        h for h in headers
        if h == amount_col
        or (h not in mapped and score_header(h)[0] in (ColumnField.AMOUNT, ColumnField.SUBTOTAL))
    ]  # fmt: skip
    if len(options) < 2:
        return
    scores = {h: _balance_agreement(headers, rows, h, balance_col) for h in options}
    best = max(options, key=lambda h: scores[h])
    if best != amount_col and scores[best] >= 0.6 and scores[best] > scores[amount_col] + 0.2:
        mapping.fields[ColumnField.AMOUNT.value] = best
        mapping.confidence[ColumnField.AMOUNT.value] = 85
        mapping.notes[ColumnField.AMOUNT.value] = (
            f"'{best}' chosen because it agrees with the running balance"
        )
        if not mapping.has(ColumnField.SUBTOTAL):
            mapping.fields[ColumnField.SUBTOTAL.value] = amount_col
            mapping.confidence[ColumnField.SUBTOTAL.value] = 70


def detect_mapping(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> ColumnMapping:
    """Detect a column mapping from headers and (sample) rows.

    Header evidence is combined with content checks *before* columns are assigned,
    so a column whose content contradicts its name loses to a better candidate.
    """
    mapping = ColumnMapping()
    used_columns: set[int] = set()
    for confidence, _, index, field, problem in sorted(
        _candidates(headers, rows), key=lambda c: (-c[0], -c[1], c[2])
    ):
        if field.value in mapping.fields or index in used_columns:
            continue
        if problem:
            mapping.notes[field.value] = f"'{headers[index]}': {problem}"
        mapping.fields[field.value] = headers[index]
        mapping.confidence[field.value] = confidence
        used_columns.add(index)

    _fill_by_content(headers, rows, mapping, used_columns)
    _prefer_balance_consistent_amount(headers, rows, mapping)

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
