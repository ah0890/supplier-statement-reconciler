# Free and Open-Source Resources

Everything the application needs is free and open source. No paid services are used.

## Runtime libraries (`requirements.txt`)

| Library | What it does here | Licence | Source / docs |
|---|---|---|---|
| **Python 3.12+** | Language runtime | PSF License | https://www.python.org |
| **Streamlit** | Web user interface | Apache-2.0 | https://github.com/streamlit/streamlit · https://docs.streamlit.io |
| **pandas** | Table handling in the UI (data grids) | BSD-3-Clause | https://pandas.pydata.org |
| **SQLAlchemy** | ORM and database access layer | MIT | https://www.sqlalchemy.org |
| **SQLite** | Local embedded database (bundled with Python) | Public domain | https://sqlite.org |
| **openpyxl** | Reading XLSX uploads; writing the Excel export | MIT | https://openpyxl.readthedocs.io |
| **pdfplumber** (with pdfminer.six) | PDF text, word positions and ruled-table extraction | MIT | https://github.com/jsvine/pdfplumber |
| **PyMuPDF** | Fallback PDF text extraction when pdfplumber cannot read a file | **AGPL-3.0** or commercial (Artifex) – see note | https://github.com/pymupdf/PyMuPDF |
| **RapidFuzz** | Fuzzy reference and supplier-name similarity | MIT | https://github.com/rapidfuzz/RapidFuzz |
| **Pydantic** | Domain models and validation | MIT | https://docs.pydantic.dev |
| **python-dotenv** | Loads optional settings from `.env` | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| **Plotly** | Two dashboard charts | MIT | https://plotly.com/python/ |
| **ReportLab** (open-source edition) | PDF client report; demo statement PDFs | BSD-style ReportLab licence | https://www.reportlab.com/opensource/ |

### Note on PyMuPDF (AGPL)

PyMuPDF is dual-licensed: AGPL-3.0, or a commercial licence from Artifex. The AGPL
requires that if you **distribute** the software, or **offer it to users over a
network**, you make the complete corresponding source available under the AGPL.

- Using this tool **internally** (locally, or on a private server for your own staff)
  is normally unproblematic.
- Offering it as a **hosted service to third parties** under a closed-source licence
  would require either AGPL compliance or a commercial licence.

PyMuPDF is an **optional fallback**. The import is guarded, so if PyMuPDF is uninstalled,
PDF parsing uses pdfplumber alone (`pip uninstall PyMuPDF`). Review this before any
commercial hosting.

## Development tools (`requirements-dev.txt`)

| Tool | Purpose | Licence |
|---|---|---|
| **pytest** | Test runner | MIT |
| **pytest-cov** | Coverage reporting | MIT |
| **Ruff** | Linting and formatting | MIT |
| **mypy** (+ `types-openpyxl`, `types-reportlab`) | Static type checking | MIT / Apache-2.0 |
| **pip-audit** (optional, not pinned) | Dependency vulnerability scan | Apache-2.0 |
| **GitHub Actions** | CI: lint, types and tests on every push | Free for public repositories |

## Dependency policy

- Add a dependency only when it removes real complexity. Prefer the standard library
  (`csv`, `decimal`, `zipfile`, `logging`).
- Pin compatible version ranges in `requirements.txt`; run the test suite and
  `pip-audit` before upgrading.
- Record the licence of every new runtime dependency in this file.
