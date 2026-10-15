"""Generate statement PDFs for tests (nothing binary is committed)."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

HEADER = ["Date", "Invoice No", "Description", "Debit", "Credit", "Balance"]


def ruled_statement_pdf(path: Path, rows: list[list[str]], *, rows_per_page: int = 25) -> Path:
    """A statement drawn as a gridded table, repeating the header on every page."""
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    story = []
    for start in range(0, len(rows), rows_per_page):
        table = Table([HEADER, *rows[start : start + rows_per_page]])
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
        story.append(table)
        story.append(_PageBreak())
    doc.build(story[:-1])
    return path


class _PageBreak:  # minimal flowable to avoid importing PageBreak in every test
    def __new__(cls):
        from reportlab.platypus import PageBreak

        return PageBreak()


def layout_statement_pdf(path: Path, rows: list[list[str]], *, rows_per_page: int = 30) -> Path:
    """A statement with text aligned in columns and no grid lines (very common)."""
    x_positions = [40, 110, 200, 380, 450, 520]
    right_aligned = {3, 4, 5}
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4

    def header(y: float) -> None:
        pdf.setFont("Helvetica-Bold", 9)
        for index, (x, title) in enumerate(zip(x_positions, HEADER, strict=True)):
            if index in right_aligned:
                pdf.drawRightString(x + 40, y, title)
            else:
                pdf.drawString(x, y, title)

    for start in range(0, len(rows), rows_per_page):
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(40, height - 50, "SUPPLIER STATEMENT (TEST)")
        pdf.setFont("Helvetica", 9)
        pdf.drawString(40, height - 66, "Account: TEST-001   Statement date: 30/09/2026")
        y = height - 100
        header(y)
        pdf.setFont("Helvetica", 9)
        for row in rows[start : start + rows_per_page]:
            y -= 14
            for index, (x, value) in enumerate(zip(x_positions, row, strict=True)):
                if not value:
                    continue
                if index in right_aligned:
                    pdf.drawRightString(x + 40, y, value)
                else:
                    pdf.drawString(x, y, value)
        pdf.drawString(width / 2 - 20, 30, f"Page {start // rows_per_page + 1}")
        pdf.showPage()
    pdf.save()
    return path


def image_only_pdf(path: Path) -> Path:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.rect(50, 50, 200, 200, fill=1)
    pdf.showPage()
    pdf.save()
    return path


def sample_rows(count: int = 40) -> list[list[str]]:
    rows = [["01/09/2026", "", "Balance brought forward", "", "", "1,000.00"]]
    balance = 1000_00
    for i in range(1, count + 1):
        amount = 100_00 + i * 7
        balance += amount
        rows.append(
            [
                f"{(i % 28) + 1:02d}/09/2026",
                f"INV-{1000 + i}",
                f"Goods supplied {i}",
                f"{amount / 100:,.2f}",
                "",
                f"{balance / 100:,.2f}",
            ]
        )
    balance -= 500_00
    rows.append(["29/09/2026", "PMT-778", "Payment received - thank you", "", "500.00",
                 f"{balance / 100:,.2f}"])  # fmt: skip
    return rows
