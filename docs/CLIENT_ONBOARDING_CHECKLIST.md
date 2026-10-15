# Client Onboarding Checklist

Use this checklist when starting supplier reconciliations for a new client. Tick items as
they are collected. Items marked *(optional)* improve results but are not required.

## 1. Client information

- [ ] Client / company name (as it should appear on reports)
- [ ] Contact person for reconciliation queries
- [ ] Accounting system in use (Xero / QuickBooks Online / Sage / other) and version
- [ ] Reporting currency
- [ ] Agreed frequency (monthly, quarterly, ad hoc) and which suppliers are in scope
- [ ] Agreed delivery: Excel workbook, PDF report or both

## 2. Supplier information (per supplier)

- [ ] Supplier name exactly as it appears in the accounting system
- [ ] Client's account number with the supplier *(recommended for multi-supplier exports)*
- [ ] Supplier contact for statement queries *(optional)*
- [ ] Supplier currency
- [ ] Any known quirks: invoice number format, payment terms, regular credit notes

## 3. Supplier statement requirements

- [ ] Statement covers the agreed period, with the statement date visible
- [ ] Format: **CSV or XLSX preferred**; digital PDF accepted; **scanned PDFs not supported**
- [ ] Shows invoice/document numbers, dates and amounts (or debit and credit columns)
- [ ] Shows the opening (brought-forward) balance and closing balance / amount due
- [ ] Shows payments and credit notes received in the period
- [ ] Running balance column present *(optional, recommended)*

## 4. Accounting export requirements

- [ ] Supplier bills / purchase invoices for the period, **including supplier credit notes**
- [ ] Export includes: invoice number, invoice date, total (gross) amount
- [ ] Export includes supplier name or account *(required if it contains several suppliers)*
- [ ] Due date, tax, amount due/outstanding, status *(optional)*
- [ ] Period covered: at least the statement period, plus about two weeks after it, to
      identify timing differences
- [ ] Export format CSV or XLSX (not PDF)

| System | Suggested export |
|---|---|
| Xero | Business → Bills to pay → filter supplier/period → Export |
| QuickBooks Online | Reports → *Transaction List by Vendor* or *Vendor Balance Detail* → Export to Excel |
| Sage | Supplier activity / purchase ledger transactions → Export CSV |

## 5. Payment data *(optional)*

- [ ] Supplier payments for the period: date, reference, amount
- [ ] Invoice allocation for each payment, if available (one row per invoice paid is fine)
- [ ] Payments made in the last days of the period (expected timing differences)

## 6. Credit notes

- [ ] Confirm credit notes are included in the bills export (or provide them separately)
- [ ] Related invoice numbers for credit notes, where available *(optional)*

## 7. Currency

- [ ] Statement and accounting export in the same currency
- [ ] Foreign-currency suppliers: agree which currency is reconciled (transaction currency
      recommended); mixed-currency rows are flagged, never matched across currencies
- [ ] Number format used (e.g. `1,234.56` vs `1.234,56`). It is detected automatically and
      can be confirmed on the mapping screen

## 8. Tolerance configuration

- [ ] Amount tolerance (default **0.01**; increase only for known rounding differences)
- [ ] Date tolerance (default **3 days**; increase if the supplier dates on dispatch)
- [ ] Payment date tolerance (default **7 days**)
- [ ] Fuzzy reference threshold (default **80**)
- [ ] Review policy: all matches below 95 confidence are reviewed (default)

## 9. Expected workflow

1. Create the reconciliation (supplier, account, period, currency).
2. Upload the supplier statement; confirm the extracted lines and balances.
3. Upload the bills export (and payments export if available).
4. Check the column mapping; resolve or acknowledge validation errors.
5. Run the reconciliation.
6. Work through the review queue and exceptions; add notes.
7. Mark the outcome (Reconciled / Reconciled with exceptions / Not reconciled).
8. Export the PDF report and Excel workbook for the client file.

## 10. Privacy and redaction

- [ ] Client has agreed how documents are transferred (secure portal or encrypted email; not
      plain email attachments if avoidable)
- [ ] Documents are stored only on the reconciliation machine, under `data/` or
      `client_data/` (never in the source repository)
- [ ] For testing or demonstrations, use **anonymised** copies: redact company names,
      addresses, bank details, contact details and tax numbers. Keep invoice numbers, dates
      and amounts, or replace them consistently in every file
- [ ] Agree a retention period; delete reconciliations and their files when it ends
      (Settings → Delete reconciliation)
- [ ] Machine uses disk encryption and a screen lock
