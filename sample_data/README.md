# Sample data — DEMO DATA ONLY

Every file in this folder is **synthetic and fictional**. It was produced by
`scripts/generate_demo_data.py`. "Brightline Industrial Supplies Ltd", "Demo
Client Ltd", "Northgate Packaging Co" and all references and amounts are invented.
Nothing here comes from a real company or a real accounting system.

Regenerate these files at any time with:

```bash
python scripts/generate_demo_data.py                 # 40 invoices (default)
python scripts/generate_demo_data.py --count 200     # larger dataset
```

| File | What it represents |
|---|---|
| `supplier_statement.pdf` | Two-page supplier statement laid out in text columns (no grid lines), with a letterhead on every page, repeated headers, a brought-forward balance, a closing balance and an ageing summary |
| `supplier_statement.csv` / `.xlsx` | The same statement as CSV and Excel, each with letterhead rows above the table |
| `accounting_bills.csv` / `.xlsx` | Xero-style bills export (`ContactName, InvoiceNumber, InvoiceDate, …, Type, Status`) that also contains rows for another supplier |
| `payments.csv` | Supplier payments with invoice allocations, including a payment run split over two invoices |
| `demo_manifest.json` | Which references belong to which test scenario (used by the end-to-end test) |

## Scenarios included

Exact matches · invoices missing in accounting · accounting-only bills (one dated
after the statement date) · a bill entered twice · amounts keyed net instead of gross ·
dates beyond tolerance · reference formatting differences (`INV/24117`, `inv 24117`) ·
a transposed-digit typo · a difference within tolerance · a matched and a missing credit
note · a payment matched by reference · a payment run split across allocations · a
payment matched only by amount and date · a payment not yet on the statement · a
statement line with no reference and two equally plausible candidates.

**Do not place real client documents in this folder.** For local testing with real
documents, use `client_data/` or `private/`. Git ignores both.
