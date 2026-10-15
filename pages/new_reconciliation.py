"""Create a reconciliation."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from src.config import SUPPORTED_CURRENCIES
from src.services.reconciliation_service import NewReconciliation, ReconciliationServiceError
from ui.components import page_header, workflow_steps
from ui.state import flash, reconciliation_service, set_current

page_header(
    "New Reconciliation",
    "Enter the supplier and statement details. Everything except the supplier name is optional.",
    show_context=False,
)
workflow_steps("Details")

today = date.today()
period_end_default = today.replace(day=1) - timedelta(days=1)

with st.form("new_reconciliation", border=True):
    left, right = st.columns(2)
    with left:
        client = st.text_input("Client / company", placeholder="e.g. Northwind Trading Ltd",
                               help="The business whose books are being reconciled.")  # fmt: skip
        supplier = st.text_input("Supplier name *", placeholder="e.g. Acme Supplies Ltd")
        account = st.text_input("Supplier account / reference",
                                help="Your account number with the supplier, if known. Used to "
                                "pick the right supplier from multi-supplier exports.")  # fmt: skip
    with right:
        currency = st.selectbox("Currency", SUPPORTED_CURRENCIES, index=0)
        start = st.date_input("Statement period start", value=period_end_default.replace(day=1),
                              format="DD/MM/YYYY")  # fmt: skip
        end = st.date_input("Statement period end / statement date", value=period_end_default,
                            format="DD/MM/YYYY")  # fmt: skip
    notes = st.text_area("Notes (optional)", height=80)
    submitted = st.form_submit_button("Create reconciliation", type="primary")

if submitted:
    try:
        new_id = reconciliation_service().create(
            NewReconciliation(
                supplier_name=supplier,
                currency=currency,
                client_name=client,
                supplier_account=account,
                period_start=start,
                period_end=end,
                notes=notes,
            )
        )
    except ReconciliationServiceError as exc:
        st.error(str(exc))
    else:
        set_current(new_id)
        flash(f"Reconciliation #{new_id} created. Next: upload the supplier statement.")
        st.switch_page("pages/supplier_statement.py")

st.caption(
    "Tip: you can create a fully worked demo reconciliation from the Dashboard to see the "
    "workflow end to end."
)
