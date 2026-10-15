# Security and Data Handling

Supplier statements and accounting exports are confidential financial records. This page
lists the controls in the application and its current limits.

## Deployment model

The MVP is a **single-user application that runs locally** (or on a private server the
client controls). It has **no user authentication**. Do not expose it to the internet
without putting it behind an authenticating reverse proxy or VPN. Multi-user access with
roles is on the roadmap.

## Uploaded files

| Control | Where |
|---|---|
| Extension allow-list per upload slot (statement: PDF/XLSX/CSV; accounting: XLSX/CSV) | `src/utils/validation.py` |
| Size limit (default 25 MB, `SSR_MAX_UPLOAD_MB`), enforced by Streamlit and the app | `.streamlit/config.toml`, `validate_upload` |
| Content signature must match the extension (`%PDF-`, ZIP for XLSX, text for CSV) | `validate_upload` |
| Executables rejected (`MZ`, ELF, Mach-O, `#!`), even when renamed | `validate_upload` |
| Macro-enabled or program-carrying workbooks rejected | `_check_xlsx` |
| Zip-bomb guard (decompressed size and ratio limits) | `_check_xlsx` |
| File names sanitised; stored as `<random>_<safe-name>` | `sanitize_filename` |
| Path traversal blocked: every path is resolved and confined to the upload folder | `safe_join` |
| Atomic writes through a temporary file; owner-only permissions where supported | `ImportService._store` |
| PDFs: page limit, password-protected and image-only files rejected with clear messages | `src/parsers/pdf_parser.py` |

Uploaded files are never executed; spreadsheets are read with `openpyxl` in data-only mode.

## Untrusted content in the interface and exports

- Text from client documents (references, descriptions, file names) and user notes is
  **HTML-escaped** wherever HTML is rendered, and **Markdown-escaped** elsewhere, so a
  crafted statement cannot inject markup, links or remote images.
- Excel and CSV exports neutralise values that start with `= + - @` (**formula
  injection**).
- PDF reports escape all text passed to the report engine.
- Database access goes through SQLAlchemy ORM queries only (no string-built SQL).

## Credentials

- No credentials are needed for the MVP, and none are hard-coded.
- Future Xero / QuickBooks credentials are read **only** from environment variables
  (`.env`, which is git-ignored; see `.env.example`). They are never stored in the
  database or shown in the UI.

## Logging

- Logs record counts, identifiers, durations and error types. They do not record
  invoice numbers, amounts, descriptions or file contents. A test enforces this
  (`tests/test_security.py`).
- A redaction filter masks configured secrets and `key=value` credentials as a
  second line of defence.
- Users see a friendly error with a reference ID; technical details go to
  `data/logs/reconciler.log` (`showErrorDetails = "none"`).

## Data at rest and retention

- Everything is stored under `data/` (SQLite database, uploads, logs). The folder is
  git-ignored.
- Deleting a reconciliation (Settings page) removes its uploaded files, transactions,
  results and audit trail.
- The database is not encrypted by the application. Use disk encryption (BitLocker,
  FileVault) on machines that hold client data.

## Repository hygiene

- `.gitignore` excludes `.env`, databases, logs, `data/`, `client_data/`, `private/`
  and `exports/`.
- `sample_data/` contains only synthetic DEMO files.
- A test fails if a tracked file looks like it contains a secret, or if databases,
  logs or upload folders are committed.
- Dependencies were checked with `pip-audit` (no known vulnerabilities at the time
  of writing). Re-run it before each release:
  `pip install pip-audit && pip-audit -r requirements.txt`.

## Reporting a problem

Please report security issues privately to the repository owner rather than opening a
public issue.
