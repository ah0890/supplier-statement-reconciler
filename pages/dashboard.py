"""Dashboard: headline figures for the current reconciliation and the portfolio."""

from __future__ import annotations

import streamlit as st

from src.models.reconciliation import BalanceStatus, MatchStatus
from src.services.result_views import summary_messages
from src.utils.dates import format_date
from ui.charts import difference_breakdown, status_breakdown
from ui.components import (
    badge,
    dataframe,
    empty_state,
    kpi_row,
    money,
    page_header,
    status_badge,
)
from ui.state import current_reconciliation, reconciliation_service, set_current

service = reconciliation_service()
page_header("Dashboard", "First-pass reconciliation of supplier statements against your books.")

reconciliations = service.list()
if not reconciliations:
    empty_state(
        "No reconciliations yet",
        "Create a reconciliation, upload a supplier statement and your accounting export, and "
        "the application will match them and highlight what needs attention.",
    )
    st.page_link("pages/new_reconciliation.py", label="Create your first reconciliation",
                 icon=":material/add_circle:")  # fmt: skip
    st.stop()

recon = current_reconciliation()
if recon is None:
    st.info("Select a reconciliation in the sidebar, or open one from the list below.")
else:
    stored = service.stored_run(recon.id)
    if stored is None:
        readiness = service.readiness(recon.id)
        st.subheader("Next steps")
        if readiness.blocking:
            for step in readiness.blocking:
                st.markdown(f"- {step}")
        else:
            st.success("Both files are imported. You can run the reconciliation now.")
            st.page_link(
                "pages/reconciliation.py", label="Go to Reconciliation", icon=":material/rule:"
            )
        kpi_row(
            [
                ("Statement transactions", readiness.statement_transactions, "imported", "blue"),
                ("Accounting transactions", readiness.accounting_transactions, "imported", "blue"),
                ("Payment records", readiness.payment_records, "optional", "grey"),
                ("Status", "Not run", "reconciliation pending", "grey"),
            ]
        )
    else:
        summary, balance = stored.summary, stored.balance
        if recon.workflow_status == "DATA_CHANGED":
            st.warning("Data changed since the last run - re-run the reconciliation to refresh.")

        overall = recon.outcome or summary.suggested_outcome.value
        overall_text = overall.replace("_", " ").title()
        st.markdown(
            f"**Overall status:** {status_badge(overall)} "
            + ("" if recon.outcome else badge("suggested", "grey")),
            unsafe_allow_html=True,
        )
        st.caption(summary.suggested_outcome_reason)

        missing = summary.count(MatchStatus.MISSING_IN_ACCOUNTING)
        exceptions_accent = "red" if summary.exceptions else "green"
        kpi_row(
            [
                ("Statement transactions", summary.statement_transactions, "on supplier statement",
                 "blue"),
                ("Accounting transactions", summary.accounting_transactions, "in accounting export",
                 "blue"),
                ("Matched", summary.matched, "incl. within tolerance", "green"),
                ("Missing", missing, "not in accounting", "red" if missing else "green"),
            ]
        )  # fmt: skip
        st.write("")
        kpi_row(
            [
                ("Duplicates", summary.count(MatchStatus.DUPLICATE), "possible duplicates",
                 "red" if summary.count(MatchStatus.DUPLICATE) else "green"),
                ("Amount mismatches", summary.count(MatchStatus.AMOUNT_MISMATCH), "need review",
                 "red" if summary.count(MatchStatus.AMOUNT_MISMATCH) else "green"),
                ("Date mismatches", summary.count(MatchStatus.DATE_MISMATCH), "beyond tolerance",
                 "amber" if summary.count(MatchStatus.DATE_MISMATCH) else "green"),
                ("Unresolved", summary.count(MatchStatus.UNRESOLVED), "need attention",
                 "grey"),
            ]
        )  # fmt: skip
        st.write("")
        difference = balance.statement_vs_accounting_difference
        kpi_row(
            [
                ("Statement balance", money(balance.supplier_closing_balance, recon.currency),
                 "supplier closing balance", "blue"),
                ("Accounting balance", money(balance.accounting_balance, recon.currency),
                 "replayed from your records", "blue"),
                ("Difference", money(difference, recon.currency),
                 "statement - accounting", "red" if difference else "green"),
                ("Overall status", overall_text, f"{summary.exceptions} exception(s)",
                 exceptions_accent),
            ]
        )  # fmt: skip

        st.write("")
        left, right = st.columns(2)
        with left:
            st.markdown("##### How the transactions resolved")
            st.plotly_chart(status_breakdown(summary.status_counts), width="stretch",
                            config={"displayModeBar": False})  # fmt: skip
        with right:
            st.markdown("##### What explains the balance difference")
            if balance.explained_difference or balance.unexplained_difference:
                st.plotly_chart(
                    difference_breakdown(balance.explained_difference,
                                         balance.unexplained_difference),
                    width="stretch", config={"displayModeBar": False},
                )  # fmt: skip
                st.caption("Blue: statement higher than accounting. Red: statement lower.")
            elif balance.status == BalanceStatus.BALANCE_CHECK_NOT_POSSIBLE:
                empty_state("Balance comparison not possible", balance.reason)
            else:
                empty_state("No difference", "The statement agrees with the accounting records.")

        with st.container(border=True):
            st.markdown("##### Summary")
            for line in summary_messages(summary.status_counts, summary.pending_reviews):
                st.markdown(f"- {line}")
            st.page_link("pages/reconciliation.py", label="Open reconciliation results",
                         icon=":material/rule:")  # fmt: skip

st.divider()
st.subheader("All reconciliations")
records = [
    {
        "ID": r.id,
        "Client": r.client_name or "",
        "Supplier": r.supplier.name,
        "Period end": r.period_end,
        "Currency": r.currency,
        "Status": (r.outcome or "").replace("_", " ").title()
        or ("Reconciled - review" if r.last_run_at else "In progress"),
        "Created": r.created_at.date(),
        "Demo": "Yes" if r.is_demo else "",
    }
    for r in reconciliations
]
dataframe(records)
open_id = st.selectbox(
    "Open reconciliation",
    [r.id for r in reconciliations],
    format_func=lambda i: next(
        f"#{r.id} - {r.supplier.name} ({format_date(r.period_end, 'no period')})"
        for r in reconciliations
        if r.id == i
    ),
    index=None,
    placeholder="Choose…",
)
if open_id is not None and (recon is None or open_id != recon.id):
    set_current(open_id)
    st.rerun()
