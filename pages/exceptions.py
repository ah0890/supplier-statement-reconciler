"""Exception management: filter, investigate side by side and review."""

from __future__ import annotations

import streamlit as st

from src.models.reconciliation import MatchStatus, ReviewStatus
from src.models.transaction import CanonicalTransaction
from src.services.reconciliation_service import ReconciliationServiceError
from src.services.result_views import EXCEPTION_CATEGORIES, KIND_LABELS, build_rows
from src.services.review_service import ReviewAction, action_label, available_actions
from src.utils.dates import format_date
from ui.components import (
    badge,
    dataframe,
    empty_state,
    money,
    page_header,
    require_reconciliation,
    side_panel,
    status_badge,
    workflow_steps,
)
from ui.state import flash, reconciliation_service, review_service

REVIEW_QUEUE = "review"
ALL = "all"

service = reconciliation_service()
page_header("Exceptions", "Everything that needs a human decision, in one place.")
recon = require_reconciliation()
workflow_steps("Exceptions", done=("Details", "Statement", "Accounting", "Mapping", "Reconcile"))

stored = service.stored_run(recon.id)
if stored is None:
    empty_state("No results yet", "Run the reconciliation first.")
    st.page_link("pages/reconciliation.py", label="Go to Reconciliation", icon=":material/rule:")
    st.stop()

data = service.load_data(recon.id)
lookup = data.lookup()
results_by_id = {r.result_id: r for r in stored.results}
rows = build_rows(stored.results, lookup)
exception_rows = [row for row in rows if row.status.is_exception]
pending_rows = [row for row in rows if row.review_status == ReviewStatus.PENDING.value]

# ------------------------------------------------------------------ filters
counts = {c.key: sum(row.status in c.statuses for row in rows) for c in EXCEPTION_CATEGORIES}
choices = {
    REVIEW_QUEUE: f"Review queue ({len(pending_rows)})",
    ALL: f"All exceptions ({len(exception_rows)})",
    **{c.key: f"{c.label} ({counts[c.key]})" for c in EXCEPTION_CATEGORIES},
}
default = REVIEW_QUEUE if pending_rows else ALL
category = (
    st.pills(
        "Category",
        list(choices),
        format_func=choices.get,
        default=default,
        key="exception_category",
    )
    or default
)

if category == REVIEW_QUEUE:
    selected_rows = pending_rows
    st.caption("Suggested matches and findings that need your decision before finalising.")
elif category == ALL:
    selected_rows = exception_rows
else:
    definition = next(c for c in EXCEPTION_CATEGORIES if c.key == category)
    selected_rows = [row for row in rows if row.status in definition.statuses]
    st.caption(definition.description)

left, middle, right = st.columns([3, 2, 2])
search = left.text_input("Search", placeholder="Invoice number, description, amount…")
kinds = middle.multiselect("Type", sorted({row.kind for row in selected_rows}),
                           format_func=KIND_LABELS.get, placeholder="All types")  # fmt: skip
max_confidence = right.slider("Confidence up to", 0, 100, 100)
filtered = [
    row
    for row in selected_rows
    if (not kinds or row.kind in kinds)
    and row.confidence <= max_confidence
    and (not search or search.lower() in " ".join(map(str, row.as_record().values())).lower())
]

if not filtered:
    empty_state("Nothing here", "No items match this category and filter.")
    st.stop()

columns = ("Status", "Type", "Reference", "Statement Date", "Statement Amount",
           "Accounting Ref", "Accounting Date", "Accounting Amount", "Difference",
           "Days Difference", "Confidence", "Review")  # fmt: skip
st.caption(f"{len(filtered)} item(s). Select a row to investigate it.")
selection = dataframe(
    [{k: row.as_record()[k] for k in columns} for row in filtered],
    height=min(420, 38 + 35 * len(filtered)),
    key=f"table_{category}",
    selectable=True,
)
selected_indexes = selection.selection.rows if selection else []
if not selected_indexes:
    st.info("Select a row above to see the statement and accounting records side by side.")
    st.stop()

row = filtered[selected_indexes[0]]
result = results_by_id[row.result_id]

# -------------------------------------------------------- side-by-side view
st.divider()
st.markdown(
    f"#### {row.reference} &nbsp; {status_badge(row.status.value)} "
    + badge(KIND_LABELS[row.kind], "blue"),
    unsafe_allow_html=True,
)


def record_fields(txn: CanonicalTransaction | None) -> list[tuple[str, str]]:
    if txn is None:
        return [("Record", "Not present")]
    return [
        ("Invoice / reference", txn.display_reference),
        ("Date", format_date(txn.invoice_date, "—")),
        ("Due date", format_date(txn.due_date, "—")),
        ("Amount", money(txn.amount, txn.currency)),
        ("Type", txn.transaction_type.value.replace("_", " ").title()),
        ("Description", txn.description or "—"),
        ("Supplier", txn.supplier_name or "—"),
        ("Allocated / related", txn.related_invoice_number or "—"),
        ("Source", f"{txn.source_file or ''} row {txn.source_row or '?'}"),
    ]


statement_txn = lookup.get(result.statement_txn_id or "")
accounting_txn = lookup.get(result.accounting_txn_id or "")
related = [lookup[i] for i in result.related_txn_ids if i in lookup]
highlight = set()
if result.amount_difference:
    highlight.add("Amount")
if "DATE" in result.issues:
    highlight.add("Date")

left, right = st.columns(2)
with left:
    if statement_txn is None and result.status == MatchStatus.DUPLICATE and related:
        side_panel("Possible original (accounting)", record_fields(related[0]))
    else:
        side_panel("Supplier statement", record_fields(statement_txn), highlight)
with right:
    if result.status == MatchStatus.DUPLICATE and statement_txn is not None and related:
        side_panel("Possible original (statement)", record_fields(related[0]))
    elif accounting_txn is None and result.status == MatchStatus.UNRESOLVED and related:
        side_panel("Candidate (accounting)", record_fields(related[0]))
    else:
        side_panel("Accounting", record_fields(accounting_txn), highlight)

st.write("")
band_label = result.confidence_band.value.replace("_", " ").title()
side_panel(
    "Assessment",
    [
        ("Difference", money(result.amount_difference)),
        ("Days difference", "—" if result.days_difference is None else str(result.days_difference)),
        ("Matching method", result.matching_method.value.replace("_", " ").title()),
        ("Confidence", f"{result.confidence_score} ({band_label})"),
        ("Review", result.review_status.value.replace("_", " ").title()),
    ],
    highlight={"Difference"} if result.amount_difference else set(),
)  # fmt: skip
st.markdown(f"**Reason.** {result.matching_reason}")
if result.original_status:
    st.caption(f"Originally: {result.original_status.label}.")
if result.review_note:
    st.caption(f"Reviewer note: {result.review_note}")
if len(related) > 1:
    with st.expander(f"Other candidates ({len(related)})"):
        dataframe(
            [{"Reference": t.display_reference, "Date": t.invoice_date, "Amount": t.amount}
             for t in related]
        )  # fmt: skip

# ------------------------------------------------------------------ review
with st.container(border=True):
    st.markdown("##### Review decision")
    note = st.text_input("Note (optional)", key=f"note_{result.result_id}",
                         placeholder="e.g. Confirmed with supplier on 12 Oct")  # fmt: skip
    buttons = st.columns(3)
    for column, action in zip(buttons, available_actions(result), strict=False):
        kind = "primary" if action == ReviewAction.ACCEPT else "secondary"
        if column.button(action_label(result, action), key=f"{action.value}_{result.result_id}",
                         type=kind):  # fmt: skip
            try:
                review_service().review(recon.id, result.result_id, action, note)
            except ReconciliationServiceError as exc:
                st.error(str(exc))
            else:
                flash(f"{action_label(result, action)} - decision saved to the audit trail.")
                st.rerun()
