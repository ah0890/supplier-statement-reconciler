"""Run the reconciliation, review results and the balance check, and finalise."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from src.models.reconciliation import (
    MatchStatus,
    ReconciliationConfig,
    ReconciliationOutcome,
    ReviewStatus,
)
from src.services.reconciliation_service import ReconciliationServiceError
from src.services.result_views import build_rows, summary_messages
from ui.components import (
    bullet_list,
    dataframe,
    esc,
    kpi_row,
    md_escape,
    money,
    page_header,
    require_reconciliation,
    side_panel,
    status_badge,
    workflow_steps,
)
from ui.state import flash, reconciliation_service

service = reconciliation_service()
page_header("Reconciliation", "Match the statement against your records and review the outcome.")
recon = require_reconciliation()
workflow_steps("Reconcile", done=("Details", "Statement", "Accounting", "Mapping"))

readiness = service.readiness(recon.id)
stored = service.stored_run(recon.id)

# ------------------------------------------------------------------ run panel
with st.container(border=True):
    st.markdown("##### Run reconciliation")
    for message in readiness.blocking:
        st.error(message, icon=":material/block:")
    for message in readiness.warnings:
        st.warning(message, icon=":material/info:")
    st.caption(
        f"{readiness.statement_transactions} statement transaction(s) · "
        f"{readiness.accounting_transactions} accounting transaction(s) · "
        f"{readiness.payment_records} payment record(s)"
    )

    config = service.config_for(recon.id)
    with st.expander("Matching rules for this run", expanded=False):
        left, middle, right = st.columns(3)
        amount_tolerance = left.number_input(
            "Amount tolerance", min_value=0.0, value=float(config.amount_tolerance), step=0.01,
            format="%.2f", help="Differences up to this amount count as a match within tolerance.",
        )  # fmt: skip
        date_tolerance = middle.number_input(
            "Date tolerance (days)", min_value=0, max_value=365, value=config.date_tolerance_days
        )
        fuzzy = right.slider(
            "Fuzzy reference threshold", min_value=50, max_value=100,
            value=config.fuzzy_match_threshold,
            help="Minimum similarity (0-100) for references to be suggested as a match.",
        )  # fmt: skip

    reviewed = sum(
        r.review_status in (ReviewStatus.ACCEPTED, ReviewStatus.REJECTED, ReviewStatus.UNRESOLVED)
        for r in (stored.results if stored else [])
    )
    confirm = True
    if reviewed:
        confirm = st.checkbox(
            f"Re-running will discard {reviewed} manual review decision(s). Continue?",
            value=False,
        )
    label = "Re-run reconciliation" if stored else "Run reconciliation"
    if st.button(label, type="primary", disabled=not readiness.can_run or not confirm):
        try:
            run_config = config.model_copy(
                update={
                    "amount_tolerance": Decimal(str(round(amount_tolerance, 2))),
                    "date_tolerance_days": int(date_tolerance),
                    "fuzzy_match_threshold": int(fuzzy),
                }
            )
            ReconciliationConfig.model_validate(run_config.model_dump())
            with st.spinner("Matching transactions…"):
                run = service.run(recon.id, run_config)
        except (ReconciliationServiceError, ValueError) as exc:
            st.error(str(exc))
        else:
            flash(
                "Reconciliation completed. "
                + " ".join(summary_messages(run.summary.status_counts, run.summary.pending_reviews))
            )
            st.rerun()

if stored is None:
    st.stop()

if recon.workflow_status == "DATA_CHANGED":
    st.warning("Data changed since the last run. Re-run the reconciliation to refresh results.")

summary, balance = stored.summary, stored.balance
data = service.load_data(recon.id)
rows = build_rows(stored.results, data.lookup())

# --------------------------------------------------------------- summary
st.subheader("Results")
kpi_row(
    [
        ("Matched", summary.matched, "incl. within tolerance & credit notes", "green"),
        ("Exceptions", summary.exceptions, "need attention",
         "red" if summary.exceptions else "green"),
        ("Pending review", summary.pending_reviews, "decisions needed",
         "amber" if summary.pending_reviews else "green"),
        ("Balance check", balance.status.value.replace("_", " ").title(), "",
         "green" if balance.status.value == "BALANCED" else "red"
         if balance.status.value == "BALANCE_MISMATCH" else "grey"),
    ]
)  # fmt: skip
bullet_list(summary_messages(summary.status_counts, summary.pending_reviews))

filters = st.columns([2, 2, 1])
present = [s for s in MatchStatus if summary.status_counts.get(s.value)]
chosen = filters[0].multiselect("Status", present, format_func=lambda s: s.label,
                                placeholder="All statuses")  # fmt: skip
search = filters[1].text_input("Search reference, description or reason", "")
only_review = filters[2].toggle("Needs review only")
visible = [
    row for row in rows
    if (not chosen or row.status in chosen)
    and (not only_review or row.review_status == ReviewStatus.PENDING.value)
    and (not search or search.lower() in " ".join(
        str(v) for v in row.as_record().values()).lower())
]  # fmt: skip
st.caption(f"Showing {len(visible)} of {len(rows)} result(s).")
dataframe([row.as_record() for row in visible], height=420)

st.page_link(
    "pages/exceptions.py", label="Investigate exceptions side by side", icon=":material/report:"
)

# ---------------------------------------------------------------- balance
st.subheader("Balance check")
st.markdown(status_badge(balance.status.value) + f" &nbsp; {esc(balance.reason)}",
            unsafe_allow_html=True)  # fmt: skip
left, right = st.columns(2)
with left:
    side_panel(
        "Supplier statement",
        [
            ("Opening balance", money(balance.opening_balance)),
            ("+ Invoices", money(balance.total_invoices)),
            ("- Credit notes", money(balance.total_credit_notes)),
            ("- Payments", money(balance.total_payments)),
            ("= Expected closing balance", money(balance.expected_closing_balance)),
            ("Supplier closing balance", money(balance.supplier_closing_balance)),
            ("Difference", money(balance.difference)),
        ],
        highlight={"Difference"} if balance.difference else set(),
    )
with right:
    fields = [
        ("Supplier closing balance", money(balance.supplier_closing_balance)),
        ("Accounting balance", money(balance.accounting_balance)),
        ("Difference", money(balance.statement_vs_accounting_difference)),
    ]
    fields += [(f"  {name}", money(value)) for name, value in balance.explained_difference.items()]
    fields.append(("Unexplained", money(balance.unexplained_difference)))
    side_panel(
        "Statement vs accounting",
        fields,
        highlight={"Unexplained"} if balance.unexplained_difference else set(),
    )
    if balance.accounting_balance_basis:
        st.caption(f"Accounting balance basis: {md_escape(balance.accounting_balance_basis)}.")
if balance.running_balance_breaks:
    with st.expander(f"Running balance breaks ({len(balance.running_balance_breaks)})"):
        bullet_list(balance.running_balance_breaks)
if stored.allocations or stored.unallocated_payments:
    with st.expander("Payment allocation"):
        if stored.allocations:
            dataframe(
                [
                    {"Source": a.source.title(), "Invoice": a.invoice_number,
                     "Invoice Amount": a.invoice_amount, "Payments": a.payments_allocated,
                     "Credits": a.credits_allocated, "Outstanding Amount": a.outstanding,
                     "Status": a.allocation_status.replace("_", " ").title()}
                    for a in stored.allocations
                ]
            )  # fmt: skip
        bullet_list(stored.unallocated_payments)

# --------------------------------------------------------------- finalise
st.subheader("Finalise")
if recon.outcome:
    st.markdown(
        "This reconciliation is marked "
        + status_badge(recon.outcome)
        + (f" — {esc(recon.outcome_note)}" if recon.outcome_note else ""),
        unsafe_allow_html=True,
    )
    if st.button("Reopen"):
        service.reopen(recon.id)
        st.rerun()
else:
    st.markdown(
        "Suggested outcome: "
        + status_badge(summary.suggested_outcome.value)
        + f" — {esc(summary.suggested_outcome_reason)}",
        unsafe_allow_html=True,
    )
    with st.form("finalise"):
        outcomes = list(ReconciliationOutcome)
        outcome = st.radio(
            "Mark this reconciliation as",
            outcomes,
            index=outcomes.index(summary.suggested_outcome),
            format_func=lambda o: o.value.replace("_", " ").title(),
            horizontal=True,
        )
        note = st.text_area("Note for the file (optional)", height=70)
        if st.form_submit_button("Save outcome", type="primary"):
            try:
                service.finalise(recon.id, outcome, note)
            except ReconciliationServiceError as exc:
                st.error(str(exc))
            else:
                flash(f"Reconciliation marked {outcome.value.replace('_', ' ').lower()}.")
                st.rerun()

with st.expander("Audit trail"):
    dataframe(service.audit_trail(recon.id))
