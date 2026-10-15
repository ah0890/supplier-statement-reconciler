# Changelog

## 0.1.0 — first release

- Supplier statement import: PDF (ruled tables, column-aligned multi-page text, PyMuPDF
  fallback), XLSX and CSV, with automatic column detection and manual mapping
- Accounting import: CSV/XLSX bills, credit notes and payments (Xero, QuickBooks and Sage
  layouts), supplier scoping for multi-supplier exports
- Validation summary with row-level issues and explicit acknowledgement of errors
- Controlled invoice and supplier normalisation
- Layered matching engine with confidence scoring and configurable tolerances
- Duplicate, amount, date and reference mismatch detection
- Credit notes, payment matching (including payment runs) and explicit-only allocation
- Statement balance reconciliation with a fully explained statement-vs-accounting difference
- Exception management with side-by-side investigation and accept / reject / unresolved review
- Excel workbook, CSV and PDF client report; audit trail
- Synthetic demo data generator and one-click demo reconciliation
- Document diagnostic script for real-world document testing
- Security hardening: upload validation, path confinement, escaping, formula-injection
  protection, safe logging
- Xero and QuickBooks provider interfaces prepared (not yet connected)
