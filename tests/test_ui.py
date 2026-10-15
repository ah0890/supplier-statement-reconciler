"""UI smoke tests: every page renders without exceptions (Streamlit AppTest)."""

from __future__ import annotations

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src import config
from src.config import PROJECT_ROOT
from src.database import database as database_module
from src.services.import_service import FileRole
from src.services.reconciliation_service import NewReconciliation
from ui.state import CURRENT_KEY

STATEMENT = b"""Date,Invoice No,Details,Debit,Credit,Balance
01/09/2026,,Balance brought forward,,,500.00
03/09/2026,INV-1001,Widgets,"1,200.00",,"1,700.00"
05/09/2026,INV-1002,Bolts,300.00,,2000.00
06/09/2026,INV-1003,Nuts,80.00,,2080.00
10/09/2026,PMT-1,Payment received,,500.00,1580.00
30/09/2026,,Closing balance,,,1580.00
"""
BILLS = b"""Supplier,Bill No,Bill Date,Total
Acme Supplies Ltd,INV-1001,03/09/2026,1200.00
Acme Supplies Ltd,INV-1002,05/09/2026,310.00
Acme Supplies Ltd,INV-1009,07/09/2026,45.00
"""

PAGES = [
    "pages/dashboard.py",
    "pages/new_reconciliation.py",
    "pages/supplier_statement.py",
    "pages/accounting_data.py",
    "pages/mapping.py",
    "pages/reconciliation.py",
    "pages/exceptions.py",
    "pages/reports.py",
    "pages/settings.py",
]


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SSR_DATA_DIR", str(tmp_path / "data"))
    config.get_settings.cache_clear()
    database_module.get_database.cache_clear()
    st.cache_resource.clear()
    yield
    config.get_settings.cache_clear()
    database_module.get_database.cache_clear()
    st.cache_resource.clear()


def _populated_reconciliation(run: bool) -> int:
    from ui.state import import_service, reconciliation_service

    service = reconciliation_service()
    recon_id = service.create(NewReconciliation(supplier_name="Acme Supplies Ltd", currency="GBP"))
    import_service().upload(recon_id, FileRole.STATEMENT, "statement.csv", STATEMENT)
    import_service().upload(recon_id, FileRole.ACCOUNTING, "bills.csv", BILLS)
    if run:
        service.run(recon_id)
    return recon_id


def _render(page: str, recon_id: int | None) -> AppTest:
    app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30)
    if recon_id is not None:
        app.session_state[CURRENT_KEY] = recon_id
    app.run()
    app.switch_page(page)
    app.run()
    return app


@pytest.mark.parametrize("page", PAGES)
def test_pages_render_without_data(app_env, page):
    app = _render(page, None)
    assert not app.exception, app.exception


@pytest.mark.parametrize("page", PAGES)
def test_pages_render_with_reconciliation_results(app_env, page):
    recon_id = _populated_reconciliation(run=True)
    app = _render(page, recon_id)
    assert not app.exception, app.exception


def test_reconciliation_page_before_run(app_env):
    recon_id = _populated_reconciliation(run=False)
    app = _render("pages/reconciliation.py", recon_id)
    assert not app.exception
    assert any(b.label == "Run reconciliation" for b in app.button)


def test_dashboard_shows_results(app_env):
    recon_id = _populated_reconciliation(run=True)
    app = _render("pages/dashboard.py", recon_id)
    markdown = " ".join(m.value for m in app.markdown)
    assert "Statement transactions" in markdown
    assert "missing from accounting" in markdown
