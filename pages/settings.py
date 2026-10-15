"""Application settings: default matching rules, integrations, data management."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st
from pydantic import ValidationError

from src.accounting import api_providers
from src.config import get_settings
from src.models.reconciliation import ReconciliationConfig
from ui.components import page_header
from ui.state import current_reconciliation, flash, reconciliation_service, set_current

service = reconciliation_service()
settings = get_settings()
page_header("Settings", "Defaults used for new reconciliation runs.", show_context=False)

config = service.default_config()
st.subheader("Matching rules")
with st.form("defaults"):
    left, middle, right = st.columns(3)
    amount_tolerance = left.number_input(
        "Amount tolerance",
        min_value=0.0,
        step=0.01,
        value=float(config.amount_tolerance),
        format="%.2f",
    )
    date_tolerance = middle.number_input(
        "Date tolerance (days)", 0, 365, config.date_tolerance_days
    )
    payment_tolerance = right.number_input(
        "Payment date tolerance (days)", 0, 365, config.payment_date_tolerance_days
    )
    fuzzy = left.slider("Fuzzy reference threshold", 50, 100, config.fuzzy_match_threshold)
    auto_accept = middle.slider(
        "Auto-accept confidence",
        1,
        100,
        config.auto_accept_threshold,
        help="Matches below this confidence always require review.",
    )
    st.markdown("**Confidence bands** (lower bounds)")
    a, b, c = st.columns(3)
    very_strong = a.number_input("Very strong", 1, 100, config.very_strong_threshold)
    strong = b.number_input("Strong", 1, 100, config.strong_threshold)
    possible = c.number_input("Possible (below = unresolved)", 1, 100, config.possible_threshold)
    if st.form_submit_button("Save defaults", type="primary"):
        try:
            new_config = ReconciliationConfig(
                amount_tolerance=Decimal(str(round(amount_tolerance, 2))),
                date_tolerance_days=int(date_tolerance),
                payment_date_tolerance_days=int(payment_tolerance),
                fuzzy_match_threshold=int(fuzzy),
                auto_accept_threshold=int(auto_accept),
                very_strong_threshold=int(very_strong),
                strong_threshold=int(strong),
                possible_threshold=int(possible),
            )
        except ValidationError as exc:
            st.error("; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()))
        else:
            service.save_default_config(new_config)
            flash("Default matching rules saved. They apply to reconciliations run from now on.")
            st.rerun()  # fmt: skip

st.subheader("Integrations")
for provider in api_providers():
    st.info(provider.status_message(), icon=":material/link_off:")
st.caption(
    "Credentials are read only from environment variables (see .env.example). They are never "
    "stored in the database or shown in the application."
)

st.subheader("Data storage")
st.markdown(
    f"- Local data folder: `{settings.data_dir}`\n"
    f"- Maximum upload size: **{settings.max_upload_mb} MB**\n"
    "- Uploaded documents are stored locally and are excluded from version control."
)

recon = current_reconciliation()
if recon is not None:
    with st.expander("Delete the current reconciliation", icon=":material/delete:"):
        st.warning(
            f"This permanently deletes reconciliation #{recon.id} ({recon.supplier.name}), its "
            "uploaded files, imported transactions, results and audit trail."
        )
        sure = st.checkbox("I understand this cannot be undone")
        if st.button("Delete reconciliation", disabled=not sure):
            service.delete(recon.id)
            set_current(None)
            flash("Reconciliation deleted.", "info")
            st.rerun()
