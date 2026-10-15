"""Service access and session state for Streamlit pages."""

from __future__ import annotations

import streamlit as st

from src.config import get_settings
from src.database.database import get_database
from src.database.models import ReconciliationRow
from src.services.import_service import ImportService
from src.services.reconciliation_service import ReconciliationService

CURRENT_KEY = "current_reconciliation_id"


@st.cache_resource
def reconciliation_service() -> ReconciliationService:
    return ReconciliationService(get_database())


@st.cache_resource
def import_service() -> ImportService:
    return ImportService(get_database(), get_settings())


def current_id() -> int | None:
    return st.session_state.get(CURRENT_KEY)


def set_current(reconciliation_id: int | None) -> None:
    st.session_state[CURRENT_KEY] = reconciliation_id


def current_reconciliation() -> ReconciliationRow | None:
    reconciliation_id = current_id()
    if reconciliation_id is None:
        return None
    recon = reconciliation_service().get(reconciliation_id)
    if recon is None:
        set_current(None)
    return recon


def flash(message: str, kind: str = "success") -> None:
    """Show a message after the next rerun."""
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("_flash", []):
        getattr(st, kind)(message)


@st.cache_resource
def review_service():
    from src.services.review_service import ReviewService

    return ReviewService(get_database(), reconciliation_service())
