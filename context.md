# Project Context

Handover notes for anyone continuing work on **Supplier Statement Reconciler**: the
current state, the rules the project follows, and what to do next.

## What this is

A Streamlit application that reconciles a supplier statement (PDF, XLSX or CSV) against an
accounting export (CSV or XLSX). It reports missing invoices, duplicates, amount, date and
reference differences, credit notes, payment issues, accounting-only items and balance
differences, with a review workflow and Excel, CSV and PDF reports.

- Repository: `github.com/ah0890/supplier-statement-reconciler` (branch `main`)
- Released: `v0.1.0`. CI (GitHub Actions) runs ruff, mypy and pytest on every push.
- Tests: 294 passing, about 94% coverage of `src/`.

## Current phase: real-world document testing

**Status (2026-10-16):** the owner supplied 42 test files in `client_data/` (14 statements,
10 accounting exports, 8 payment exports and 10 deliberately bad files). They are realistic
*fictional* test documents (the PDF footer says so), not genuine client statements. All
supplier statements now parse completely, every bad file is rejected with a clear message,
and all nine supplier reconciliations explain their balance difference with 0.00
unexplained. Fixes made, each covered by synthetic tests in
`tests/test_real_world_formats.py`: page carry-forward balances, content-aware column
choice (type vs number columns, gross vs net "Amount"), German/French vocabulary,
consistent-column CSV delimiter detection, umlaut transliteration in supplier names,
credit/adjustment/discount classification, Excel serial dates stored as text,
cross-currency reference warnings, and an error for document lines with no amount.

The application is complete and works on synthetic data. **The next work uses real
supplier statements only. Do not use the demo data** (`sample_data/`, the
"Load demo reconciliation" button or `scripts/generate_demo_data.py`) for this phase.

### Where real documents go

```text
client_data/              ← git-ignored; never committed
├── statements/           supplier statements (PDF, XLSX, CSV)
├── accounting/           matching accounting exports (CSV, XLSX)
└── payments/             optional payments exports (CSV, XLSX)
```

Use the same base name for files that belong together, e.g.
`statements/acme_2026-09.pdf` and `accounting/acme_2026-09.csv`.

### How to test a real document

1. Run a diagnostic, which only prints results and stores nothing:
   ```bash
   python scripts/analyze_document.py client_data/statements/<file>.pdf
   python scripts/analyze_document.py client_data/accounting/<file>.csv --role accounting --supplier "<Supplier name>"
   ```
2. Check the output: extraction method, header detected, column mapping and confidence,
   transaction counts by type, opening/closing balance and the arithmetic check, issues.
3. Then run the full workflow in the app: `streamlit run app.py` → New Reconciliation →
   upload the statement and the export → Column Mapping → Reconciliation.
4. Record each finding (wrong column, missed row, misclassified type, wrong date order)
   and fix it **generically**. Add synonyms or rules, and never build the parser around a
   single supplier's layout.
5. For every fix, add a **synthetic** test that reproduces the layout. Never copy real
   values, names or files into `tests/`.

### What to look for first

- PDFs: was a header row found, and did the extraction choose `pdf:ruled-tables` or
  `pdf:column-layout`? Are all pages and lines present? Running-balance breaks indicate
  missed lines.
- Do the opening and closing balances match the document?
- Does the date order (DD/MM vs MM/DD) and the number format (`1,234.56` vs `1.234,56`)
  match the document?
- Are credit notes and payments classified correctly?
- Accounting exports: are other suppliers excluded, are credit notes detected, and are
  signs right?

## Rules that must be kept

- **Git identity:** use the configured identity (`Ahaulakh0890`). Do not change
  `user.name` or `user.email`, do not run `git init`, and do not change the remote.
- **No AI attribution** in commits, code, docs or the UI (no co-author lines).
- **Commit in small, meaningful steps** (`feat:`, `fix:`, `test:`, `docs:` …), run
  `ruff check .`, `ruff format --check .`, `mypy` and `pytest` first, then push to `main`.
- **Never commit real documents** or anything under `data/`, `client_data/` or `private/`.
  Check `git status` before every commit.
- **Money is `Decimal`**; never use floats for amounts.
- **Business logic stays in `src/`**; `pages/` and `ui/` are presentation only.
- **No fake features:** Xero and QuickBooks report "Not configured" until they are
  really implemented.
- **Never silently accept uncertain matches.** Fuzzy and proximity matches always need review.
- **Don't claim real-world validation** until it has actually been done with real files.

## Where things are

| Area | Location |
|---|---|
| Entry point / navigation | `app.py` |
| Pages | `pages/` |
| UI helpers (theme, components, charts) | `ui/` |
| Column detection (header synonyms) | `src/parsers/columns.py` |
| Row interpretation (types, balances, skipped rows) | `src/parsers/transformer.py` |
| PDF extraction | `src/parsers/pdf_parser.py` |
| Matching engine | `src/reconciliation/` |
| Import workflow | `src/services/import_service.py` |
| Settings / defaults | `src/config.py`, Settings page |
| Diagnostic script | `scripts/analyze_document.py` |
| Documentation | `docs/` (start with `REAL_WORLD_DATA_REQUIREMENTS.md` and `RECONCILIATION_LOGIC.md`) |

## Environment

- Windows, Python 3.13 in `.venv` (the local Python 3.12 install is broken; 3.12+ is the
  minimum supported version).
- Setup: `pip install -r requirements-dev.txt`
- Run: `streamlit run app.py` → <http://localhost:8501>
- Runtime data (database, uploads, logs) lives in `data/`, which is git-ignored. Delete it to
  start with an empty application.
- Playwright is installed in `.venv` only, for browser checks; it is not a project dependency.

## Known limitations

- Scanned (image-only) PDFs are not supported (no OCR).
- Single-user, local app with no authentication.
- PyMuPDF is AGPL. It is an optional fallback; see `docs/FREE_RESOURCES.md` before any
  hosted or commercial use.
- Re-running a reconciliation discards earlier manual review decisions (the UI warns first).
- No licence file has been chosen yet (the owner's decision).

## Next steps

1. Test the real statements placed in `client_data/` (this phase).
2. Fix parsing gaps they reveal, with synthetic regression tests.
3. Xero API integration (`src/accounting/xero_provider.py`), then QuickBooks.
