"""Security behaviour: escaping untrusted text, safe logging, secrets hygiene."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import replace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src import config
from src.config import PROJECT_ROOT, get_settings
from src.database import database as database_module
from src.models.reconciliation import ReconciliationOutcome
from src.services.import_service import FileRole, ImportService
from src.services.reconciliation_service import NewReconciliation, ReconciliationService
from ui.components import md_escape
from ui.state import CURRENT_KEY

PAYLOAD = '<img src=x onerror="alert(1)">'
STATEMENT = f"""Date,Invoice No,Details,Debit,Credit,Balance
01/09/2026,,Opening balance,,,0.00
03/09/2026,INV-7731,"{PAYLOAD.replace('"', "'")} ![t](http://evil.example/p.png)",4321.09,,4321.09
30/09/2026,,Closing balance,,,4321.09
""".encode()
BILLS = b"Bill No,Bill Date,Total\nINV-9001,03/09/2026,55.00\n"


def test_md_escape_neutralises_links_images_and_html():
    escaped = md_escape("![x](http://evil/p.png) [a](b) <b>x</b> *bold*")
    assert "![" not in escaped and "](" not in escaped
    assert "<b>" not in escaped
    assert "*bold*" not in escaped


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


def test_user_and_document_text_is_escaped_in_pages(app_env):
    from ui.state import import_service, reconciliation_service

    service = reconciliation_service()
    recon_id = service.create(NewReconciliation(supplier_name="Acme", currency="GBP"))
    import_service().upload(recon_id, FileRole.STATEMENT, "s.csv", STATEMENT)
    import_service().upload(recon_id, FileRole.ACCOUNTING, "b.csv", BILLS)
    service.run(recon_id)
    service.finalise(recon_id, ReconciliationOutcome.NOT_RECONCILED, "<script>alert('x')</script>")

    app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30)
    app.session_state[CURRENT_KEY] = recon_id
    app.run()
    app.switch_page("pages/reconciliation.py").run()
    assert not app.exception
    rendered = " ".join(m.value for m in app.markdown)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_logs_contain_no_financial_details(database, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    settings = replace(get_settings(), upload_dir=tmp_path / "uploads")
    service = ReconciliationService(database)
    importer = ImportService(database, settings)
    recon_id = service.create(NewReconciliation(supplier_name="Acme", currency="GBP"))
    importer.upload(recon_id, FileRole.STATEMENT, "s.csv", STATEMENT)
    importer.upload(recon_id, FileRole.ACCOUNTING, "b.csv", BILLS)
    service.run(recon_id)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert logged  # something was logged
    for sensitive in ("INV-7731", "4321.09", "4,321.09", "INV-9001", "evil.example"):
        assert sensitive not in logged


def test_no_secrets_or_private_files_tracked():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert ".env" not in tracked
    assert not [f for f in tracked if f.endswith((".db", ".sqlite", ".sqlite3", ".log"))]
    assert not [f for f in tracked if f.startswith(("data/uploads", "client_data/", "private/"))]

    secret = re.compile(
        r"(?i)(client[_-]?secret|api[_-]?key|password|token)[ \t]*[=:][ \t]*['\"]?[A-Za-z0-9/+_-]{16,}"
    )
    for name in tracked:
        if name.startswith("tests/") or not name.endswith((".py", ".toml", ".md", ".txt", ".yml",
                                                            ".example", ".json", ".csv")):  # fmt: skip
            continue
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8", errors="ignore")
        assert not secret.search(text), f"possible secret in {name}"
