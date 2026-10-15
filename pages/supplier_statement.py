"""Upload and review the supplier statement."""

from __future__ import annotations

import streamlit as st

from src.models.transaction import TransactionType
from src.services.import_service import FileRole
from src.utils.money import format_money, money_sum, parse_amount
from ui.components import page_header, require_reconciliation, workflow_steps
from ui.import_panel import (
    remove_button,
    stored_transactions,
    transactions_table,
    upload_section,
    validation_summary,
)
from ui.state import flash, import_service

page_header("Supplier Statement", "Upload the statement received from the supplier.")
recon = require_reconciliation()
workflow_steps("Statement", done=("Details",))

summary = import_service().validation_status(recon.id, FileRole.STATEMENT)
if summary is None:
    st.markdown(
        "Upload the supplier's statement as **PDF, Excel (.xlsx) or CSV**. Multi-page PDFs, "
        "letterheads, repeated headers, totals and balance lines are handled automatically. "
        "Scanned (image-only) PDFs are not supported yet."
    )
upload_section(
    recon,
    FileRole.STATEMENT,
    "PDF statements must contain selectable text. Excel/CSV statements work best.",
)

if summary is None:
    st.stop()

st.divider()
st.subheader("Extraction result")
st.caption(f"File: {summary.get('file_name')} · Parser: {summary.get('parser') or 'n/a'}")
validation_summary(recon, FileRole.STATEMENT)

transactions = stored_transactions(recon, FileRole.STATEMENT)
if not transactions:
    st.warning(
        "No transactions are imported yet. Please map the columns so that Date, Amount and "
        "Invoice number can be read."
    )
    st.page_link("pages/mapping.py", label="Open Column Mapping", icon=":material/table_view:")
else:
    totals = {
        kind: money_sum(t.amount for t in transactions if t.transaction_type == kind)
        for kind in TransactionType
    }
    st.markdown(
        f"**{len(transactions)}** transactions · Invoices "
        f"**{format_money(totals[TransactionType.INVOICE])}** · Credit notes "
        f"**{format_money(totals[TransactionType.CREDIT_NOTE])}** · Payments "
        f"**{format_money(totals[TransactionType.PAYMENT])}**"
    )
    st.caption("Review the extracted lines. If something looks wrong, adjust the column mapping.")
    transactions_table(transactions)

st.divider()
st.subheader("Statement balances")
sources = {
    "detected": "found on the statement",
    "derived": "derived from the running balance",
    "manual": "entered manually",
    None: "not found",
}
st.caption(
    f"Opening balance: {sources.get(recon.opening_balance_source, '')} · Closing balance: "
    f"{sources.get(recon.closing_balance_source, '')}. Confirm or correct them - they are used "
    "for the balance check."
)
with st.form("balances"):
    left, right = st.columns(2)
    opening_text = left.text_input(
        "Opening balance",
        value="" if recon.opening_balance is None else f"{recon.opening_balance:.2f}",
    )
    closing_text = right.text_input(
        "Closing balance (amount due per supplier)",
        value="" if recon.closing_balance is None else f"{recon.closing_balance:.2f}",
    )
    if st.form_submit_button("Save balances"):
        opening, closing = parse_amount(opening_text), parse_amount(closing_text)
        if (opening_text.strip() and opening is None) or (closing_text.strip() and closing is None):
            st.error("Please enter balances as numbers, e.g. 1234.56")
        else:
            import_service().set_balances(recon.id, opening, closing)
            flash("Statement balances saved.")
            st.rerun()

st.divider()
left, right = st.columns([1, 1])
with left:
    st.page_link("pages/accounting_data.py", label="Next: upload accounting data",
                 icon=":material/arrow_forward:")  # fmt: skip
with right:
    remove_button(recon, FileRole.STATEMENT)
