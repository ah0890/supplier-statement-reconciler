"""Plotly charts. Only charts that answer a question the user actually has.

Colours: status palette for result states (always paired with a text label),
blue/red diverging pair for signed amounts. Grid and axes stay recessive.
"""

from __future__ import annotations

from decimal import Decimal

import plotly.graph_objects as go

from src.models.reconciliation import MatchStatus

GOOD, WARNING, SERIOUS, CRITICAL, NEUTRAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b", "#9aa5b4"
POSITIVE, NEGATIVE = "#2a78d6", "#d03b3b"
INK, MUTED, GRID = "#1B2430", "#52514e", "#E8ECF2"

STATUS_COLOR = {
    MatchStatus.MATCHED: GOOD,
    MatchStatus.MATCHED_WITH_TOLERANCE: GOOD,
    MatchStatus.CREDIT_NOTE: GOOD,
    MatchStatus.DATE_MISMATCH: WARNING,
    MatchStatus.REFERENCE_MISMATCH: WARNING,
    MatchStatus.NOT_ON_SUPPLIER_STATEMENT: SERIOUS,
    MatchStatus.PAYMENT_MISMATCH: SERIOUS,
    MatchStatus.MISSING_IN_ACCOUNTING: CRITICAL,
    MatchStatus.DUPLICATE: CRITICAL,
    MatchStatus.AMOUNT_MISMATCH: CRITICAL,
    MatchStatus.BALANCE_MISMATCH: CRITICAL,
    MatchStatus.UNRESOLVED: NEUTRAL,
}


def _layout(figure: go.Figure, height: int) -> go.Figure:
    figure.update_layout(
        height=height,
        margin={"l": 8, "r": 48, "t": 8, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "sans-serif", "size": 12, "color": INK},
        showlegend=False,
        bargap=0.35,
        hoverlabel={"bgcolor": "white", "font": {"color": INK}},
    )
    figure.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor="#B8C1CC",
                        tickfont={"color": MUTED})  # fmt: skip
    figure.update_yaxes(showgrid=False, tickfont={"color": INK}, autorange="reversed")
    return figure


def status_breakdown(status_counts: dict[str, int]) -> go.Figure:
    """Horizontal bars: how many results ended in each status."""
    ordered = [s for s in MatchStatus if status_counts.get(s.value)]
    labels = [s.label for s in ordered]
    values = [status_counts[s.value] for s in ordered]
    figure = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker={"color": [STATUS_COLOR[s] for s in ordered], "cornerradius": 4},
            text=values,
            textposition="outside",
            textfont={"color": INK},
            cliponaxis=False,
            hovertemplate="%{y}: %{x} item(s)<extra></extra>",
        )
    )
    return _layout(figure, max(180, 34 * len(ordered) + 40))


def difference_breakdown(explained: dict[str, Decimal], unexplained: Decimal | None) -> go.Figure:
    """Signed contribution of each exception category to the balance difference."""
    items = list(explained.items())
    if unexplained:
        items.append(("Unexplained", unexplained))
    values = [float(value) for _, value in items]
    # Values sit in the category labels: text beside negative bars would collide with them.
    labels = [f"{name}  {value:,.2f}" for name, value in zip((n for n, _ in items), values,
                                                             strict=True)]  # fmt: skip
    figure = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker={
                "color": [POSITIVE if v >= 0 else NEGATIVE for v in values],
                "cornerradius": 4,
            },
            hovertemplate="%{y}<extra></extra>",
        )
    )
    return _layout(figure, max(160, 38 * len(items) + 40))
