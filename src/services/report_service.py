"""Client-facing PDF reconciliation report (reportlab)."""

from __future__ import annotations

import io
from collections.abc import Sequence
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.models.reconciliation import MatchStatus
from src.services.export_service import ExportContext, balance_items, summary_items
from src.services.result_views import EXCEPTION_CATEGORIES, ResultRow, summary_messages
from src.utils.dates import format_date
from src.utils.money import format_money

NAVY = colors.HexColor("#1F4E79")
MUTED = colors.HexColor("#5B6675")
BORDER = colors.HexColor("#E2E7EF")
STRIPE = colors.HexColor("#F6F8FB")
OUTCOME_COLOURS = {
    "RECONCILED": colors.HexColor("#1E7B4D"),
    "RECONCILED_WITH_EXCEPTIONS": colors.HexColor("#9A6200"),
    "NOT_RECONCILED": colors.HexColor("#B42318"),
}
MAX_ROWS_PER_CATEGORY = 150


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], textColor=NAVY, alignment=0,
                                fontSize=18, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], textColor=NAVY, fontSize=12,
                             spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontSize=9, leading=12),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontSize=7.5, leading=9.5,
                                textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base["BodyText"], fontSize=7.5, leading=9),
        "num": ParagraphStyle("n", parent=base["BodyText"], fontSize=7.5, leading=9,
                              alignment=TA_RIGHT),
    }  # fmt: skip


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        return format_money(value)
    return str(value)


def _grid(data: list[list], widths: Sequence[float], header: bool = True) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE]),
        ]
    table.setStyle(TableStyle(style))
    return table


class ReportService:
    def pdf_bytes(self, context: ExportContext) -> bytes:
        styles = _styles()
        buffer = io.BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
            title=f"Supplier reconciliation - {context.supplier}",
        )  # fmt: skip
        width = document.width
        story: list = []
        recon, summary = context.recon, context.run.summary

        story.append(Paragraph("Supplier Statement Reconciliation", styles["title"]))
        if recon.is_demo:
            story.append(Paragraph("<b>DEMO DATA</b> - synthetic records for demonstration "
                                   "only.", styles["small"]))  # fmt: skip
        items = summary_items(context)
        header_rows = [
            [
                Paragraph(f"<b>{escape(k)}</b>", styles["cell"]),
                Paragraph(escape(_fmt(v)), styles["cell"]),
            ]
            for k, v in items[:6]
        ]
        story.append(_grid(header_rows, [55 * mm, width / 2 - 55 * mm], header=False))

        outcome = recon.outcome or summary.suggested_outcome.value
        colour = OUTCOME_COLOURS.get(outcome, NAVY)
        label = outcome.replace("_", " ").title() + ("" if recon.outcome else " (suggested)")
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f'<font color="#{colour.hexval()[2:]}"><b>'
            f"Overall status: {escape(label)}</b></font>", styles["body"]))  # fmt: skip
        reason = recon.outcome_note if recon.outcome else summary.suggested_outcome_reason
        if reason:
            story.append(Paragraph(escape(reason), styles["small"]))

        story.append(Paragraph("Summary", styles["h2"]))
        counts = [["Measure", "Count"]] + [[k, _fmt(v)] for k, v in items[7:]]
        findings = [Paragraph("• " + escape(line), styles["body"]) for line in
                    summary_messages(summary.status_counts, summary.pending_reviews)]  # fmt: skip
        story.append(Table(
            [[_grid(counts, [60 * mm, 22 * mm]), findings]],
            colWidths=[90 * mm, width - 90 * mm],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]),
        ))  # fmt: skip

        balance_rows = [["Item", "Amount / detail"]] + [
            [Paragraph(escape(k), styles["cell"]), Paragraph(escape(_fmt(v)), styles["cell"])]
            for k, v in balance_items(context)
        ]
        story.append(
            KeepTogether(
                [
                    Paragraph("Balance reconciliation", styles["h2"]),
                    _grid(balance_rows, [80 * mm, width - 80 * mm]),
                ]
            )
        )

        by_status: dict[MatchStatus, list[ResultRow]] = {}
        for row in context.rows:
            by_status.setdefault(row.status, []).append(row)
        for category in EXCEPTION_CATEGORIES:
            rows = [r for s in category.statuses for r in by_status.get(s, [])]
            if not rows:
                continue
            heading = [
                Paragraph(f"{category.label} ({len(rows)})", styles["h2"]),
                Paragraph(escape(category.description), styles["small"]),
                Spacer(1, 3),
            ]
            table = self._exception_table(rows[:MAX_ROWS_PER_CATEGORY], width, styles)
            # Keep the heading with the start of its table; long tables still split.
            story.append(KeepTogether([*heading, table]) if len(rows) <= 12 else heading[0])
            if len(rows) > 12:
                story.extend([*heading[1:], table])
            if len(rows) > MAX_ROWS_PER_CATEGORY:
                story.append(Paragraph(
                    f"{len(rows) - MAX_ROWS_PER_CATEGORY} further item(s) are listed in the "
                    "Excel export.", styles["small"]))  # fmt: skip

        story.append(Spacer(1, 10))
        story.append(Paragraph(
            "This report is an automated first-pass reconciliation. Matches below the "
            "auto-accept confidence and all exceptions should be reviewed by a person before "
            "any accounting entries are made.", styles["small"]))  # fmt: skip

        def footer(canvas, doc) -> None:
            canvas.saveState()
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(MUTED)
            canvas.drawString(doc.leftMargin, 8 * mm,
                              f"{context.supplier} · generated "
                              f"{context.generated_at.strftime('%d-%b-%Y %H:%M')}")  # fmt: skip
            canvas.drawRightString(doc.leftMargin + doc.width, 8 * mm, f"Page {doc.page}")
            canvas.restoreState()

        document.build(story, onFirstPage=footer, onLaterPages=footer)
        return buffer.getvalue()

    @staticmethod
    def _exception_table(rows: Sequence[ResultRow], width: float, styles) -> Table:
        widths = [32 * mm, 20 * mm, 24 * mm, 20 * mm, 24 * mm, 22 * mm]
        widths.append(width - sum(widths))
        data = [["Reference", "Stmt date", "Stmt amount", "Acct date", "Acct amount",
                 "Difference", "Reason"]]  # fmt: skip
        for r in rows:
            data.append([
                Paragraph(escape(r.reference), styles["cell"]),
                format_date(r.statement_date, "—"),
                Paragraph(_fmt(r.statement_amount), styles["num"]),
                format_date(r.accounting_date, "—"),
                Paragraph(_fmt(r.accounting_amount), styles["num"]),
                Paragraph(_fmt(r.amount_difference), styles["num"]),
                Paragraph(escape(r.reason), styles["cell"]),
            ])  # fmt: skip
        return _grid(data, widths)
