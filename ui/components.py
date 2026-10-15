"""Reusable presentation components."""

from __future__ import annotations

import contextlib
import html
from collections.abc import Sequence
from decimal import Decimal

import pandas as pd
import streamlit as st

from src.database.models import ReconciliationRow
from src.models.reconciliation import MatchStatus
from src.services.reconciliation_service import workflow_label
from src.utils.dates import format_date
from src.utils.money import format_money
from ui.state import current_reconciliation, show_flash

STATUS_TONE: dict[str, str] = {
    MatchStatus.MATCHED.value: "green",
    MatchStatus.MATCHED_WITH_TOLERANCE.value: "green",
    MatchStatus.CREDIT_NOTE.value: "green",
    MatchStatus.MISSING_IN_ACCOUNTING.value: "red",
    MatchStatus.NOT_ON_SUPPLIER_STATEMENT.value: "amber",
    MatchStatus.DUPLICATE.value: "red",
    MatchStatus.AMOUNT_MISMATCH.value: "red",
    MatchStatus.DATE_MISMATCH.value: "amber",
    MatchStatus.REFERENCE_MISMATCH.value: "amber",
    MatchStatus.PAYMENT_MISMATCH.value: "amber",
    MatchStatus.BALANCE_MISMATCH.value: "red",
    MatchStatus.UNRESOLVED.value: "grey",
    "RECONCILED": "green",
    "RECONCILED_WITH_EXCEPTIONS": "amber",
    "NOT_RECONCILED": "red",
    "BALANCED": "green",
    "BALANCE_MISMATCH": "red",
    "BALANCE_CHECK_NOT_POSSIBLE": "grey",
}

WORKFLOW_STEPS = (
    "Details", "Statement", "Accounting", "Mapping", "Reconcile", "Exceptions", "Report",
)  # fmt: skip


def esc(value: object) -> str:
    return html.escape(str(value))


def badge(text: str, tone: str = "grey") -> str:
    return f'<span class="ssr-badge ssr-badge-{tone}">{esc(text)}</span>'


def status_badge(status: str) -> str:
    label = status.replace("_", " ").title()
    with contextlib.suppress(ValueError):
        label = MatchStatus(status).label
    return badge(label, STATUS_TONE.get(status, "grey"))


def page_header(title: str, subtitle: str = "", *, show_context: bool = True) -> None:
    context = ""
    recon = current_reconciliation() if show_context else None
    if recon is not None:
        period = ""
        if recon.period_start or recon.period_end:
            period = (
                f"{format_date(recon.period_start, '…')} – {format_date(recon.period_end, '…')}"
            )
        context = (
            f'<div class="ssr-context"><strong>{esc(recon.supplier.name)}</strong><br>'
            f"{esc(period)} · {esc(recon.currency)}</div>"
        )
    st.markdown(
        f'<div class="ssr-header"><div><h1>{esc(title)}</h1>'
        f'<div class="ssr-sub">{esc(subtitle)}</div></div>{context}</div>',
        unsafe_allow_html=True,
    )
    if recon is not None and recon.is_demo:
        st.markdown(
            '<div class="ssr-demo-banner">DEMO DATA - synthetic records for demonstration '
            "only.</div>",
            unsafe_allow_html=True,
        )
    show_flash()


def workflow_steps(current: str, done: Sequence[str] = ()) -> None:
    parts = []
    for step in WORKFLOW_STEPS:
        css = "ssr-step"
        if step == current:
            css += " ssr-step-current"
        elif step in done:
            css += " ssr-step-done"
        parts.append(f'<span class="{css}">{esc(step)}</span>')
    st.markdown(f'<div class="ssr-steps">{"".join(parts)}</div>', unsafe_allow_html=True)


def kpi(label: str, value: object, note: str = "", accent: str = "blue") -> None:
    st.markdown(
        f'<div class="ssr-card ssr-kpi-accent-{accent}"><div class="ssr-kpi-label">{esc(label)}'
        f'</div><div class="ssr-kpi-value">{esc(value)}</div>'
        f'<div class="ssr-kpi-note">{esc(note)}</div></div>',
        unsafe_allow_html=True,
    )


def kpi_row(items: Sequence[tuple[str, object, str, str]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value, note, accent) in zip(columns, items, strict=True):
        with column:
            kpi(label, value, note, accent)


def empty_state(title: str, body: str) -> None:
    st.markdown(
        f'<div class="ssr-empty"><h4>{esc(title)}</h4><div>{esc(body)}</div></div>',
        unsafe_allow_html=True,
    )


def require_reconciliation() -> ReconciliationRow:
    """Stop the page with guidance when no reconciliation is selected."""
    recon = current_reconciliation()
    if recon is None:
        empty_state(
            "No reconciliation selected",
            "Create a new reconciliation, or pick an existing one in the sidebar.",
        )
        st.page_link("pages/new_reconciliation.py", label="Create a reconciliation",
                     icon=":material/add_circle:")  # fmt: skip
        st.stop()
    return recon


def money(value: Decimal | None, currency: str | None = None, blank: str = "—") -> str:
    return format_money(value, currency, blank=blank)


def side_panel(title: str, fields: Sequence[tuple[str, str]], highlight: set[str] = frozenset()):
    rows = "".join(
        f'<div class="ssr-field"><span>{esc(k)}</span>'
        f'<span class="{"ssr-diff" if k in highlight else ""}">{esc(v)}</span></div>'
        for k, v in fields
    )
    st.markdown(f'<div class="ssr-side"><h5>{esc(title)}</h5>{rows}</div>',
                unsafe_allow_html=True)  # fmt: skip


def money_column(label: str) -> st.column_config.NumberColumn:
    return st.column_config.NumberColumn(label, format="%.2f")


def dataframe(records: Sequence[dict], *, height: int | None = None, key: str | None = None,
              selectable: bool = False):  # fmt: skip
    """Render records with money/date columns formatted consistently."""
    frame = pd.DataFrame(list(records))
    config = {}
    for column in frame.columns:
        lower = column.lower()
        if any(word in lower for word in ("amount", "difference", "balance", "total")) and not (
            lower.startswith("days")
        ):
            frame[column] = frame[column].map(lambda v: float(v) if v is not None else None)
            config[column] = money_column(column)
        elif "date" in lower:
            config[column] = st.column_config.DateColumn(column, format="DD MMM YYYY")
        elif column == "Confidence":
            config[column] = st.column_config.ProgressColumn(
                column, min_value=0, max_value=100, format="%d"
            )
    kwargs = {"hide_index": True, "width": "stretch", "column_config": config}
    if height:
        kwargs["height"] = height
    if selectable:
        return st.dataframe(frame, key=key, on_select="rerun", selection_mode="single-row",
                            **kwargs)  # fmt: skip
    return st.dataframe(frame, **kwargs)


def reconciliation_label(recon: ReconciliationRow) -> str:
    period = format_date(recon.period_end, "") or recon.created_at.strftime("%d-%b-%Y")
    demo = " [DEMO]" if recon.is_demo else ""
    return f"#{recon.id} · {recon.supplier.name} · {period}{demo}"


def workflow_badge(recon: ReconciliationRow) -> str:
    if recon.outcome:
        return status_badge(recon.outcome)
    label = workflow_label(recon)
    tone = "amber" if "re-run" in label else "blue" if "review" in label else "grey"
    return badge(label, tone)
