"""Upload accounting-system exports (bills and, optionally, payments)."""

from __future__ import annotations

import streamlit as st

from src.accounting import api_providers
from src.services.import_service import FileRole
from ui.components import page_header, require_reconciliation, workflow_steps
from ui.import_panel import (
    remove_button,
    stored_transactions,
    transactions_table,
    upload_section,
    validation_summary,
)
from ui.state import import_service

page_header("Accounting Data", "Upload the supplier bills exported from your accounting system.")
recon = require_reconciliation()
workflow_steps("Accounting", done=("Details", "Statement"))

bills_tab, payments_tab, integrations_tab = st.tabs(
    ["Bills & credit notes", "Payments (optional)", "Integrations"]
)

with bills_tab:
    st.markdown(
        "Export **supplier bills / purchase invoices** (and supplier credit notes) as CSV or "
        "Excel - for example Xero *Bills*, QuickBooks *Transaction List by Vendor* or a Sage "
        "purchase ledger export. Exports covering several suppliers are fine: rows for other "
        f"suppliers are excluded using the supplier name **{recon.supplier.name}**"
        + (f" or account **{recon.supplier_account}**." if recon.supplier_account else ".")
    )
    upload_section(recon, FileRole.ACCOUNTING, "CSV or XLSX export of supplier bills.")
    if import_service().validation_status(recon.id, FileRole.ACCOUNTING) is not None:
        validation_summary(recon, FileRole.ACCOUNTING)
        transactions = stored_transactions(recon, FileRole.ACCOUNTING)
        if transactions:
            transactions_table(transactions)
        else:
            st.warning("No bills imported yet - please complete the column mapping.")
            st.page_link("pages/mapping.py", label="Open Column Mapping",
                         icon=":material/table_view:")  # fmt: skip
        remove_button(recon, FileRole.ACCOUNTING)

with payments_tab:
    st.markdown(
        "Optionally upload **supplier payments** (date, reference, amount and - if available - "
        "the invoice each payment was allocated to). This lets the application verify payments "
        "shown on the statement. Payments without an allocation are never assumed to belong to "
        "a particular invoice."
    )
    upload_section(recon, FileRole.PAYMENTS, "CSV or XLSX export of supplier payments.")
    if import_service().validation_status(recon.id, FileRole.PAYMENTS) is not None:
        validation_summary(recon, FileRole.PAYMENTS)
        transactions = stored_transactions(recon, FileRole.PAYMENTS)
        if transactions:
            transactions_table(transactions, height=260)
        remove_button(recon, FileRole.PAYMENTS)

with integrations_tab:
    st.markdown("Direct connections to accounting systems are planned for a future version.")
    for provider in api_providers():
        st.info(provider.status_message(), icon=":material/link_off:")
    st.caption(
        "Until then, export the data as CSV/XLSX from your accounting system and upload it here."
    )

st.divider()
st.page_link(
    "pages/mapping.py", label="Next: check column mapping", icon=":material/arrow_forward:"
)
