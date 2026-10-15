"""Supplier Statement Reconciler - Streamlit entry point.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from src.config import get_settings
from src.logging_config import setup_logging
from ui.components import reconciliation_label
from ui.state import current_id, reconciliation_service, set_current
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
    "Reconciliation": [
        st.Page("pages/new_reconciliation.py", title="New Reconciliation",
                icon=":material/add_circle:"),
        st.Page("pages/supplier_statement.py", title="Supplier Statement",
                icon=":material/receipt_long:"),
        st.Page("pages/accounting_data.py", title="Accounting Data",
                icon=":material/account_balance:"),
        st.Page("pages/mapping.py", title="Column Mapping", icon=":material/table_view:"),
        st.Page("pages/reconciliation.py", title="Reconciliation", icon=":material/rule:"),
        st.Page("pages/exceptions.py", title="Exceptions", icon=":material/report:"),
        st.Page("pages/reports.py", title="Reports", icon=":material/summarize:"),
    ],
    "Administration": [
        st.Page("pages/settings.py", title="Settings", icon=":material/settings:"),
    ],
}  # fmt: skip

with st.sidebar:
    st.markdown("### Supplier Statement Reconciler")
    reconciliations = reconciliation_service().list()
    if reconciliations:
        ids = [r.id for r in reconciliations]
        labels = {r.id: reconciliation_label(r) for r in reconciliations}
        selected = current_id()
        if selected not in ids:  # default to the most recent reconciliation
            selected = ids[0]
            set_current(selected)
        index = ids.index(selected)
        choice = st.selectbox(
            "Current reconciliation",
            ids,
            index=index,
            format_func=lambda i: labels[i],
            placeholder="Select a reconciliation",
        )
        if choice != selected:
            set_current(choice)
            st.rerun()
    else:
        st.caption("No reconciliations yet.")

navigation = st.navigation(pages)
navigation.run()
