# Architecture

## Principles

1. **Business logic is independent of the UI.** `src/` never imports Streamlit. The engine
   can be called from a script, a test, an API or a scheduled job.
2. **One canonical model.** Every source (PDF, XLSX, CSV, and later Xero/QuickBooks APIs)
   is converted to `CanonicalTransaction` before reconciliation.
3. **Money is `Decimal`.** Floats never take part in financial arithmetic; SQLite stores
   money as exact strings.
4. **Nothing uncertain is silently finalised.** Every match carries a method, a confidence
   score and a reason; weak matches wait for a human decision.
5. **Auditability.** Imports, mappings, settings, runs and review decisions are recorded.

## Layers

```
            ┌────────────────────────── UI (Streamlit) ──────────────────────────┐
            │ app.py · pages/*.py · ui/ (theme, components, charts, state)        │
            └───────────────────────────────┬─────────────────────────────────────┘
                                            │ calls
            ┌───────────────────────────────▼─────────────────────────────────────┐
            │ Services (src/services)                                              │
            │ import_service · normalization_service · reconciliation_service      │
            │ review_service · export_service · report_service · result_views     │
            │ demo_data                                                            │
            └──────┬───────────────────┬──────────────────────────┬───────────────┘
                   │                   │                          │
   ┌───────────────▼──────┐ ┌──────────▼───────────────┐ ┌────────▼─────────────────┐
   │ Parsers / providers  │ │ Reconciliation engine     │ │ Persistence               │
   │ src/parsers          │ │ src/reconciliation        │ │ src/database              │
   │ src/accounting       │ │ engine · matcher ·        │ │ models (SQLAlchemy) ·     │
   │                      │ │ duplicate_detector ·      │ │ repository · database     │
   │                      │ │ mismatch_detector ·       │ │ (SQLite)                  │
   │                      │ │ payments · balance_checker│ │                           │
   │                      │ │ · confidence              │ │                           │
   └──────────┬───────────┘ └───────────┬──────────────┘ └───────────────────────────┘
              └──────── src/models (Pydantic) · src/utils (money, dates, normalisation, validation)
```

## Directory guide

| Path | Responsibility |
|---|---|
| `app.py` | Entry point: logging, theme, navigation, sidebar selector, error boundary |
| `pages/` | One file per screen; presentation only |
| `ui/` | Theme CSS, reusable components, charts, cached service access, import panel |
| `src/config.py` | All defaults and environment-driven settings |
| `src/models/` | `CanonicalTransaction`, reconciliation results/config, validation report, supplier |
| `src/utils/` | Money (Decimal parsing), dates, invoice/supplier normalisation, upload validation |
| `src/parsers/` | Statement parsers (`BaseStatementParser` → CSV/Excel/PDF), column detection, row transformer |
| `src/accounting/` | `AccountingProvider` interface; CSV/Excel providers; Xero/QuickBooks placeholders |
| `src/reconciliation/` | The engine and its components (pure functions of their inputs) |
| `src/database/` | ORM tables, engine/session handling, repository (the only module issuing queries) |
| `src/services/` | Workflows that combine the above for the UI and scripts |
| `scripts/` | Demo data generator; document diagnostic tool |
| `tests/` | Unit, integration, UI smoke, security and end-to-end tests |

## Data flow

```
upload bytes
  └─ validate_upload (extension, size, signature, macros, zip bomb)
  └─ store under data/uploads/reconciliation_<id>/<role>/ (atomic, confined path)
  └─ parser / provider → RawTable (header located, rows as text)
  └─ detect_mapping (synonyms + fuzzy header match + content checks)   ← user may override
  └─ TableTransformer → CanonicalTransaction[] + StatementBalances + ValidationReport
  └─ normalize_transactions (invoice/supplier keys, currency, supplier scoping)
  └─ Repository.replace_transactions + audit event
run
  └─ ReconciliationEngine.run(statement, accounting, payments, balances, period_end)
       1 DuplicateDetector (per side)   – near-certain copies leave the pool
       2 InvoiceMatcher (levels 1–5)    – indexed candidates, greedy best-first
       3 MismatchDetector               – status + issues + explanation
       4 PaymentReconciler              – payment matching, allocation, overpayments
       5 BalanceChecker                 – statement arithmetic, statement vs accounting
       → ReconciliationRun (results, balance, allocations, summary, config)
  └─ persisted with config + file hashes in the audit trail
review → ReviewService (accept / reject / unresolved) → summary + balance refreshed
report → ExportService (XLSX/CSV) · ReportService (PDF)
```

## Database schema (SQLite)

| Table | Purpose |
|---|---|
| `suppliers` | Supplier name, normalised name, account number, currency |
| `reconciliations` | One reconciliation: client, supplier, period, currency, balances (+ source), workflow state, outcome, JSON snapshots of config/summary/balance/allocations |
| `uploaded_files` | Original name, stored path, SHA-256, size, parser, column mapping, validation summary, active flag |
| `statement_transactions` / `accounting_transactions` / `payments` | Canonical transactions per source (money as exact text) |
| `reconciliation_results` | One row per result: status, original status, ids, method, confidence, reason, differences, review state/note |
| `audit_events` | Timestamped events with structured details |
| `app_settings` | Saved default matching rules |

The schema is created automatically (`Base.metadata.create_all`). If the schema changes
after release, add Alembic migrations.

## Extending

- **New statement format:** usually just header synonyms in `src/parsers/columns.py`. For
  a new file type, subclass `BaseStatementParser` and implement `extract_table`.
- **New accounting system API:** subclass `AccountingProvider` (see
  `src/accounting/oauth_provider.py`) and return canonical transactions from `load()`.
  The engine, UI and exports need no changes.
- **New matching rule:** add a level in `InvoiceMatcher.match` and a scoring branch in
  `ConfidenceScorer`; keep the "never silently exact" rule.

## Performance

Matching uses dictionaries keyed by normalised reference and digit core, a bisect-searchable
sorted amount index and a date index, so fuzzy comparison only runs against a small
candidate window. A test reconciles 5,000 × 5,000 records in about one second.
