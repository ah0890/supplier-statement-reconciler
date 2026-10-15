"""Supplier Statement Reconciler - Streamlit entry point.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from src.config import get_settings
from src.logging_config import setup_logging
from ui.theme import apply_theme

settings = get_settings()
setup_logging(settings.log_level, settings.log_dir)

st.set_page_config(
    page_title="Supplier Statement Reconciler",
    page_icon=":material/fact_check:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()

pages = {
    "Overview": [
        st.Page("pages/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True),
    ],
}

navigation = st.navigation(pages)
navigation.run()
