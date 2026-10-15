# Real-World Data Requirements

This document lists what a client may provide for a reconciliation, what each item is
used for, and what may be redacted. The application is designed to work with
**whatever subset is available**. Only a supplier statement and an accounting export
are required.

> **Current validation status.** The parsers have been tested against synthetic files
> that reproduce common real-world layouts (see `tests/test_real_world_formats.py`
> and `sample_data/`). They have **not yet been validated against genuine client
> documents**. See [Documents requested from the project owner](#documents-requested-from-the-project-owner).

---

## 1. Supplier information

| Item | Required? | Used for |
|---|---|---|
| Supplier name | **Yes** | Labelling, and picking the right supplier's rows from multi-supplier exports |
| Supplier account number (your account with them) | Recommended | Supplier matching when names differ ("ABC Ltd" vs "ABC Trading Limited") |
| Supplier contact / reference | Optional | Notes and the client report only |
| Currency | **Yes** (defaults to GBP) | Currency checks; flags rows in a different currency |
| Statement period / statement date | Recommended | Identifies timing differences (bills dated after the statement) |

## 2. Supplier documents

### Preferred, in order

1. **Supplier statement as CSV or XLSX.** This is the most reliable format: nothing is lost in extraction.
2. **Supplier statement as a digital PDF** (text can be selected). Multi-page PDFs, letterheads,
   repeated headers, page totals, wrapped descriptions and balance lines are supported.
3. **Scanned PDF.** **Not supported** in this version (OCR is out of scope). Ask the supplier
   for a digital copy or an Excel/CSV export.

### What the statement should show

| Field | Needed? | Notes |
|---|---|---|
| Transaction date | **Yes** | Any common format; day/month order is detected, or confirmed by the user |
| Invoice / document reference | **Yes** for invoices | Blank references fall back to amount + date matching |
| Amount, or Debit and Credit columns | **Yes** | A signed amount, `CR`/`DR` suffixes and `(brackets)` are all understood |
| Description / type | Recommended | Distinguishes invoices, credit notes and payments |
| Running balance | Recommended | Enables a line-by-line check that extraction was complete |
| Opening (brought-forward) balance | Recommended | Needed for the balance check; can be typed in manually |
| Closing balance / amount due | Recommended | Needed for the balance check; can be typed in manually |
| Due date, tax, currency | Optional | Shown in reports |

### Optional supporting documents

Individual invoices, credit notes, payment confirmations and remittance advice are **not
read by the application** in this version. They are only useful for a person investigating
a specific exception.

## 3. Accounting data

| Export | Required? | Typical source |
|---|---|---|
| Supplier bills / purchase invoices (incl. supplier credit notes) | **Yes** | Xero *Bills* export; QuickBooks *Transaction List by Vendor* or *Vendor Balance Detail*; Sage *Supplier Activity*; any AP ledger export |
| Supplier payments | Optional | Payments / bill payments export with **reference, date, amount** and, if possible, **the invoice each payment was allocated to** |
| Supplier master data | Optional | Only needed when the export shows supplier codes instead of names |

Formats: **CSV or XLSX**. Exports may contain many suppliers; rows for other suppliers are
excluded automatically by name or account number. Grouped reports, where the supplier
name appears as a heading row followed by "Total for …", are supported.

Direct **Xero and QuickBooks Online API connections are not implemented yet**. See the
roadmap in the README.

## 4. Configuration

| Setting | Default | When to change |
|---|---|---|
| Amount tolerance | 0.01 | Rounding differences between systems (e.g. VAT rounding per line) |
| Date tolerance | 3 days | Suppliers who date invoices on dispatch while you post on receipt |
| Payment date tolerance | 7 days | Slow bank clearing |
| Fuzzy reference threshold | 80 | Lower if suppliers' references are frequently re-keyed with typos; raise if invoice numbers are short and sequential |
| Confidence bands / auto-accept | 95 / 80 / 60 / 95 | Stricter review policies |

All settings are recorded with every reconciliation run (audit trail).

## 5. Working with incomplete data

| Missing | Effect |
|---|---|
| Opening or closing balance | Balance check shows `BALANCE_CHECK_NOT_POSSIBLE` with the reason; matching still works |
| Payments export | Statement payments are listed as "could not be verified"; invoice matching is unaffected |
| Invoice references on some lines | Those lines are matched by amount and date only, and only when exactly one candidate exists |
| Supplier name column in the export | All rows are treated as belonging to the selected supplier (with a warning if names disagree) |
| Due dates, tax, descriptions | Not needed for matching |

## 6. Privacy and redaction

Provide **anonymised copies** wherever possible. These can be redacted without affecting
reconciliation:

- company names and addresses (replace with "Supplier A" / "Client B")
- bank details, sort codes, IBANs
- contact names, emails, phone numbers
- VAT / tax registration numbers
- account numbers (replace consistently, e.g. `ACC-001` in every file)

**Do not redact** invoice numbers, dates, amounts or balances. They are what is being
reconciled. If invoice numbers are sensitive, replace them **consistently across the
statement and the accounting export** (e.g. `INV-1001` → `X-0001` in both files).

Real documents must never be committed to the repository. Keep them in `client_data/`
or `private/` (both git-ignored). The application stores uploads only under the local
`data/` folder.

## Documents requested from the project owner

Real documents would materially improve parser validation. In priority order:

1. **One anonymised supplier statement PDF** (digital, not scanned), ideally two or more
   pages, showing invoice numbers, dates, amounts and balances.
   *Why:* PDF layouts vary most between suppliers; this is the least proven part.
2. **The matching accounting export (CSV or XLSX)** for the same supplier and period,
   exported from the client's actual system (Xero / QuickBooks / Sage / other).
   *Why:* confirms column detection and sign conventions for that system.
3. **One statement containing a credit note and a payment.**
   *Why:* confirms credit/payment classification wording used by real suppliers.
4. **One payments export with invoice allocations** (optional).
   *Why:* confirms payment-run handling and allocation columns.
5. **One deliberately messy example** if available: a statement in a foreign currency or
   locale (e.g. `1.234,56` amounts), or an Excel statement with merged cells.

To check a document locally without uploading it anywhere:

```bash
python scripts/analyze_document.py client_data/statement.pdf
python scripts/analyze_document.py client_data/bills.csv --role accounting --supplier "Supplier A"
```

The script prints the detected layout, column mapping, transactions, balance arithmetic
and any issues, so parser improvements can target the real structure without building
the parser around a single document.
