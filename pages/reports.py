"""Reports and exports."""

from __future__ import annotations

import streamlit as st

from src.services.export_service import (
    SHEETS,
    ExportService,
    build_export_context,
    export_filename,
)
from src.services.report_service import ReportService
from src.services.result_views import summary_messages
from ui.components import (
    dataframe,
    empty_state,
    kpi_row,
    money,
    page_header,
    require_reconciliation,
    status_badge,
    workflow_steps,
)
from ui.state import reconciliation_service

page_header("Reports", "Download the reconciliation for your files or your client.")
recon = require_reconciliation()
workflow_steps(
    "Report", done=("Details", "Statement", "Accounting", "Mapping", "Reconcile", "Exceptions")
)

context = build_export_context(reconciliation_service(), recon.id)
if context is None:
    empty_state("Nothing to report yet", "Run the reconciliation to generate reports.")
    st.stop()

summary, balance = context.run.summary, context.run.balance
outcome = recon.outcome or summary.suggested_outcome.value
st.markdown(
    "Overall status: " + status_badge(outcome) + ("" if recon.outcome else " (suggested)"),
    unsafe_allow_html=True,
)
if recon.workflow_status == "DATA_CHANGED":
    st.warning("Data changed since the last run - re-run the reconciliation before reporting.")
if summary.pending_reviews:
    st.info(f"{summary.pending_reviews} item(s) are still awaiting review.")

kpi_row(
    [
        ("Matched", summary.matched, f"of {summary.statement_transactions} statement lines",
         "green"),
        ("Exceptions", summary.exceptions, "listed in the report",
         "red" if summary.exceptions else "green"),
        ("Supplier closing balance", money(balance.supplier_closing_balance, recon.currency), "",
         "blue"),
        ("Unexplained difference", money(balance.unexplained_difference, recon.currency), "",
         "red" if balance.unexplained_difference else "green"),
    ]
)  # fmt: skip
for line in summary_messages(summary.status_counts, summary.pending_reviews):
    st.markdown(f"- {line}")

st.subheader("Downloads")
exporter, reporter = ExportService(), ReportService()
left, middle, right = st.columns(3)
with left, st.container(border=True):
    st.markdown("**Client report (PDF)**")
    st.caption("Summary, balance reconciliation and every exception with its reason.")
    st.download_button(
        "Download PDF report",
        data=reporter.pdf_bytes(context),
        file_name=export_filename(context, "pdf"),
        mime="application/pdf",
        type="primary",
        icon=":material/picture_as_pdf:",
    )
with middle, st.container(border=True):
    st.markdown("**Excel workbook**")
    st.caption("Summary, " + ", ".join(name for name, _ in SHEETS) + ", Balance Check, Audit.")
    st.download_button(
        "Download Excel workbook",
        data=exporter.workbook_bytes(context),
        file_name=export_filename(context, "xlsx"),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        icon=":material/table:",
    )
with right, st.container(border=True):
    st.markdown("**CSV (all results)**")
    st.caption("One row per result, for further analysis or import into other tools.")
    st.download_button(
        "Download CSV",
        data=exporter.csv_bytes(context.rows),
        file_name=export_filename(context, "csv"),
        mime="text/csv",
        icon=":material/download:",
    )

st.subheader("Audit trail")
st.caption("What was imported, which mapping and settings were used, and every decision taken.")
dataframe(context.audit, height=320)
