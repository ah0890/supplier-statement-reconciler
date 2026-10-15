"""PDF supplier statements.

Supplier statements come in many layouts, so several extraction strategies are
tried and the one yielding the most transaction-like rows wins:

1. **Ruled tables** - ``pdfplumber.extract_tables`` (works when the statement
   draws grid lines).
2. **Column layout** - word positions are grouped into lines and assigned to
   columns using the x-positions of the header row (works for the very common
   "text aligned in columns" statements without grid lines). Headers repeated
   on later pages re-anchor the columns.
3. **PyMuPDF fallback** - if pdfplumber cannot read the file, word positions are
   taken from PyMuPDF and fed to the same column-layout algorithm.

Scanned (image-only) PDFs contain no text; they are rejected with a clear
message because OCR is out of scope for the MVP.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from src.parsers.base import BaseStatementParser, StatementParserError
from src.parsers.columns import header_row_score
from src.parsers.tabular import RawTable, build_table
from src.utils.dates import looks_like_date
from src.utils.money import parse_amount

logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 300


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class _Column:
    name: str
    x0: float
    x1: float

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2


def group_lines(words: Sequence[Word], y_tolerance: float = 3.0) -> list[list[Word]]:
    """Group words into visual lines by their vertical position."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (round(w.top, 1), w.x0)):
        if lines and abs(lines[-1][0].top - word.top) <= y_tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def split_phrases(line: Sequence[Word], gap: float) -> list[list[Word]]:
    """Split a line into phrases wherever the horizontal gap exceeds ``gap``."""
    phrases: list[list[Word]] = []
    for word in line:
        if phrases and word.x0 - phrases[-1][-1].x1 <= gap:
            phrases[-1].append(word)
        else:
            phrases.append([word])
    return phrases


def _phrase_text(phrase: Sequence[Word]) -> str:
    return " ".join(w.text for w in phrase)


def _gap_threshold(words: Sequence[Word]) -> float:
    heights = sorted(w.height for w in words if w.height > 0)
    median = heights[len(heights) // 2] if heights else 10.0
    return max(3.5, median * 0.55)


def _assign(phrase: Sequence[Word], columns: Sequence[_Column]) -> int:
    x0, x1 = phrase[0].x0, phrase[-1].x1
    best_index, best_overlap = -1, 0.0
    for index, column in enumerate(columns):
        overlap = min(x1, column.x1) - max(x0, column.x0)
        if overlap > best_overlap:
            best_index, best_overlap = index, overlap
    if best_index >= 0:
        return best_index
    centre = (x0 + x1) / 2
    return min(range(len(columns)), key=lambda i: abs(columns[i].centre - centre))


def _widen_columns(columns: list[_Column], page_width: float) -> list[_Column]:
    """Extend column spans halfway towards their neighbours so values are captured."""
    widened = []
    for index, column in enumerate(columns):
        left = 0.0 if index == 0 else (columns[index - 1].x1 + column.x0) / 2
        right = page_width if index == len(columns) - 1 else (column.x1 + columns[index + 1].x0) / 2
        widened.append(_Column(column.name, left, right))
    return widened


def layout_grid(
    pages: Sequence[tuple[Sequence[Word], float]],
) -> tuple[list[list[str]], list[str]]:
    """Build a grid from positioned words using header-anchored columns."""
    grid: list[list[str]] = []
    labels: list[str] = []
    columns: list[_Column] | None = None
    header_names: list[str] | None = None

    for page_number, (words, page_width) in enumerate(pages, start=1):
        if not words:
            continue
        gap = _gap_threshold(words)
        for line_number, line in enumerate(group_lines(words), start=1):
            phrases = split_phrases(line, gap)
            texts = [_phrase_text(p) for p in phrases]
            if header_row_score(texts) >= 3:
                raw_columns = [
                    _Column(t, p[0].x0, p[-1].x1) for t, p in zip(texts, phrases, strict=True)
                ]
                columns = _widen_columns(raw_columns, page_width)
                if header_names is None:
                    header_names = texts
                    grid.append(texts)
                    labels.append(f"page {page_number}, line {line_number}")
                continue  # repeated header on later pages
            if columns is None:
                continue
            cells = [""] * len(columns)
            for phrase in phrases:
                index = _assign(phrase, columns)
                text = _phrase_text(phrase)
                cells[index] = f"{cells[index]} {text}".strip()
            grid.append(cells)
            labels.append(f"page {page_number}, line {line_number}")
    return grid, labels


def _transaction_like_rows(grid: Sequence[Sequence[str]]) -> int:
    count = 0
    for row in grid:
        has_date = any(looks_like_date(c) for c in row if c)
        has_amount = any(parse_amount(c) is not None and not looks_like_date(c) for c in row if c)
        count += has_date and has_amount
    return count


class PDFStatementParser(BaseStatementParser):
    name = "PDF"
    extensions = frozenset({".pdf"})

    def extract_table(self, path: Path) -> RawTable:
        candidates: list[RawTable] = []
        try:
            candidates.extend(self._extract_with_pdfplumber(path))
        except StatementParserError:
            raise
        except Exception as exc:  # pdfminer raises a variety of exception types
            logger.warning("pdfplumber failed (%s); trying PyMuPDF", type(exc).__name__)
            if "password" in str(exc).lower() or "encrypt" in type(exc).__name__.lower():
                raise StatementParserError(
                    "This PDF is password protected. Please upload an unprotected copy."
                ) from exc

        if not candidates or all(_transaction_like_rows(c.rows) == 0 for c in candidates):
            fallback = self._extract_with_pymupdf(path)
            if fallback is not None:
                candidates.append(fallback)

        if not candidates:
            raise StatementParserError(
                "Unable to identify transaction rows in this PDF. Try a CSV or Excel version "
                "of the statement, or check that the PDF is not a scanned image."
            )
        best = max(
            candidates,
            key=lambda t: (_transaction_like_rows(t.rows), t.header_detected, -len(t.headers)),
        )
        if _transaction_like_rows(best.rows) == 0:
            best.warnings.append(
                "No rows containing both a date and an amount were found. Extraction may be "
                "incomplete - please review the extracted data carefully."
            )
        logger.info("PDF extraction chose method '%s' (%d rows)", best.method, len(best.rows))
        return best

    # ----------------------------------------------------------------- pdfplumber
    def _extract_with_pdfplumber(self, path: Path) -> list[RawTable]:
        ruled_grid: list[list[str]] = []
        ruled_labels: list[str] = []
        layout_pages: list[tuple[list[Word], float]] = []
        total_words = 0

        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                raise StatementParserError("The PDF has no pages.")
            if page_count > MAX_PDF_PAGES:
                raise StatementParserError(
                    f"The PDF has {page_count} pages; the maximum supported is {MAX_PDF_PAGES}."
                )
            for page_number, page in enumerate(pdf.pages, start=1):
                for table in page.extract_tables() or []:
                    for row_number, row in enumerate(table, start=1):
                        ruled_grid.append([c or "" for c in row])
                        ruled_labels.append(f"page {page_number}, table row {row_number}")
                raw_words = page.extract_words(x_tolerance=1.5, y_tolerance=3)
                words = [
                    Word(w["text"], float(w["x0"]), float(w["x1"]), float(w["top"]),
                         float(w["bottom"]))
                    for w in raw_words
                ]  # fmt: skip
                total_words += len(words)
                layout_pages.append((words, float(page.width)))

        if total_words == 0:
            raise StatementParserError(
                "This PDF contains no selectable text - it is probably a scanned image. "
                "OCR is not supported yet; please ask the supplier for a digital PDF, "
                "CSV or Excel statement."
            )

        candidates = []
        if ruled_grid:
            table = build_table(ruled_grid, row_labels=ruled_labels, method="pdf:ruled-tables")
            table.page_count = page_count
            candidates.append(table)
        grid, labels = layout_grid(layout_pages)
        if grid:
            table = build_table(grid, row_labels=labels, header_index=0, method="pdf:column-layout")
            table.page_count = page_count
            candidates.append(table)
        return candidates

    # ------------------------------------------------------------------- PyMuPDF
    def _extract_with_pymupdf(self, path: Path) -> RawTable | None:
        try:
            import pymupdf
        except ImportError:  # optional dependency
            logger.info("PyMuPDF not installed; no fallback extraction available")
            return None
        try:
            with pymupdf.open(path) as document:
                if document.needs_pass:
                    raise StatementParserError(
                        "This PDF is password protected. Please upload an unprotected copy."
                    )
                pages = []
                for page in document:
                    words = [Word(w[4], w[0], w[2], w[1], w[3]) for w in page.get_text("words")]
                    pages.append((words, float(page.rect.width)))
                page_count = document.page_count
        except StatementParserError:
            raise
        except Exception as exc:
            logger.warning("PyMuPDF failed: %s", type(exc).__name__)
            raise StatementParserError(
                "The PDF could not be read. It may be damaged - please try another copy."
            ) from exc

        if not any(words for words, _ in pages):
            raise StatementParserError(
                "This PDF contains no selectable text - it is probably a scanned image. "
                "OCR is not supported yet."
            )
        grid, labels = layout_grid(pages)
        if not grid:
            return None
        table = build_table(grid, row_labels=labels, header_index=0, method="pdf:pymupdf-layout")
        table.page_count = page_count
        return table
