# Demo Guide

A 10–15 minute demonstration for a prospective client (bookkeeper, accountant or AP team).
All demo data is **synthetic**. Say so at the start; the app also shows a DEMO banner.

## Before the meeting

```bash
.venv\Scripts\activate            # Windows (macOS/Linux: source .venv/bin/activate)
python scripts/generate_demo_data.py
streamlit run app.py
```

Open http://localhost:8501. For a clean start, use an empty data folder:
`set SSR_DATA_DIR=demo_data` (Windows) before `streamlit run app.py`.

Optionally open `sample_data/supplier_statement.pdf` in a PDF viewer, to show the
starting document.

## Script

**1. Set the scene (1 min).** "Every month you get statements from suppliers and tick them off
against the bills in Xero/QuickBooks by hand. On a 40-line statement that's an hour or two, and
the interesting part is the handful of lines that don't agree. This tool does the first pass
and shows you only those."

**2. Start the app.** Show the empty dashboard.

**3. Create the reconciliation.** Either:
- *Quick path:* **Dashboard → Load demo reconciliation** (generates the files and imports them), or
- *Full path:* **New Reconciliation** → supplier "Brightline Industrial Supplies Ltd", account
  `BIS-00417`, period 1–30 Sep 2026, GBP.

**4. Upload the supplier statement** (full path): `sample_data/supplier_statement.pdf`.
Point out:
- a two-page PDF with a letterhead on every page, repeated headers and an ageing summary;
- 46 transactions extracted, opening balance 3,250.00 and closing balance 197,790.40 detected;
- the validation summary: one warning, a line with no invoice number.

**5. Upload the accounting export**: `sample_data/accounting_bills.csv` (Xero-style), then
`sample_data/payments.csv` on the *Payments* tab. Point out: "4 rows for other suppliers were
excluded". The export contains another supplier's bills, as real exports do.

**6. Map columns.** Open **Column Mapping**. Everything was detected automatically, with
confidence badges. Show that the user can override any column, the date order and the
number format.

**7. Run the reconciliation.** **Reconciliation → Run**. Read out the summary lines:
"36 transactions matched. 4 items missing from accounting. 2 possible duplicates…"

**8. Review the dashboard.** KPIs, then the two charts:
- *How the transactions resolved*;
- *What explains the balance difference*: the 8,755.86 difference broken down by cause,
  with **0.00 unexplained**. This is usually the moment that lands with accountants.

**9. Open Exceptions.** Walk through the categories:
- **Missing invoices**: INV-24107, INV-24113, INV-24125 and credit note CN-5013 are not in the books.
- **Duplicates**: INV-24106 entered twice (98% confidence).
- **Amount differences**: two bills keyed net instead of gross.
- **Not on statement**: INV-24146 dated 2 October, flagged as a likely timing difference.

**10. Investigate a mismatched invoice.** Select an **Amount differences** row: statement
on the left, accounting on the right, difference highlighted, plus the method, confidence and
plain-English reason.

**11. Manual review.** Open the **Review queue**. Select the *Reference mismatch*
INV-24121 ↔ INV-24112 (transposed digits, same amount). Explain that fuzzy matches are never
accepted automatically. Click **Accept match** with a note. Show that the decision appears
in the audit trail.

**12. Export the report.** **Reports**: download the PDF report and the Excel workbook. Show
the workbook tabs (Summary, Missing, Duplicates, Amount Mismatches, … Balance Check, Audit
Trail) with frozen headers and number formatting.

**13. Explain time savings (1 min).** Illustrative only:

| | Manual | With the tool (first pass) |
|---|---|---|
| 40–50 line statement | ~2–3 hours | ~10–20 minutes, mostly reviewing exceptions |

"Your team's time goes into the eight lines that need judgement, not the thirty-six that don't."
These are illustrative estimates, not guaranteed results.

## Likely questions

- **Does it connect to Xero/QuickBooks?** Not yet; it works from standard exports today.
  API connections are on the roadmap (the architecture is ready for them).
- **Scanned statements?** Not in this version. Digital PDF, Excel or CSV are needed.
- **Where is our data stored?** Locally, under `data/`; nothing is sent anywhere.
  See `docs/SECURITY.md`.
- **What if it matches wrongly?** Every match shows its method and confidence. Anything
  uncertain waits for a person, and decisions can be reversed by re-running.
- **Can we try it on our own statement?** Yes. Provide an anonymised statement and
  export (see `docs/REAL_WORLD_DATA_REQUIREMENTS.md`).
