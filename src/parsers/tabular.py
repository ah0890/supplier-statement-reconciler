"""Turn a grid of cells into a header + rows table."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from src.parsers.columns import locate_header_row
from src.utils.dates import looks_like_date
from src.utils.money import parse_amount


@dataclass
class RawTable:
    """Extracted table before mapping. All cells are strings."""

    headers: list[str]
    rows: list[list[str]]
    row_labels: list[str]
    source_rows: list[int]
    method: str = ""
    warnings: list[str] = field(default_factory=list)
    page_count: int = 1
    header_detected: bool = True

    def column_values(self, header: str) -> list[str]:
        index = self.headers.index(header)
        return [row[index] for row in self.rows]

    def preview(self, limit: int = 200) -> list[dict[str, str]]:
        return [dict(zip(self.headers, row, strict=False)) for row in self.rows[:limit]]


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _unique_headers(raw: Sequence[str]) -> tuple[list[str], list[str]]:
    headers: list[str] = []
    duplicates: list[str] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(raw):
        name = clean_cell(value) or f"Column {index + 1}"
        if name in seen:
            seen[name] += 1
            duplicates.append(name)
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers.append(name)
    return headers, duplicates


def _is_data_cell(cell: str) -> bool:
    return bool(cell) and (looks_like_date(cell) or parse_amount(cell) is not None)


def _text_header_fallback(grid: Sequence[Sequence[str]]) -> int | None:
    """Treat the first non-empty row as the header when it is all text and the next
    row contains data (handles headers in other languages or unusual wording)."""
    non_empty = [i for i, row in enumerate(grid) if any(row)]
    if len(non_empty) < 2:
        return None
    first, second = grid[non_empty[0]], grid[non_empty[1]]
    filled = [c for c in first if c]
    first_is_text = len(filled) >= 2 and not any(_is_data_cell(c) for c in filled)
    if first_is_text and any(_is_data_cell(c) for c in second):
        return non_empty[0]
    return None


def build_table(
    grid: Sequence[Sequence[object]],
    *,
    row_labels: Sequence[str] | None = None,
    header_index: int | None = None,
    method: str = "",
) -> RawTable:
    """Locate the header row and normalise the grid into a rectangular table."""
    cleaned = [[clean_cell(c) for c in row] for row in grid]
    labels = list(row_labels) if row_labels else [f"row {i + 1}" for i in range(len(cleaned))]
    width = max((len(r) for r in cleaned), default=0)
    warnings: list[str] = []

    if header_index is None:
        header_index = locate_header_row(cleaned)
    if header_index is None:
        header_index = _text_header_fallback(cleaned)

    if header_index is None:
        headers = [f"Column {i + 1}" for i in range(width)]
        body_start = 0
        header_detected = False
        warnings.append("No header row was recognised. Please map the columns manually.")
    else:
        headers, duplicates = _unique_headers(cleaned[header_index] + [""] * width)
        headers = headers[:width]
        body_start = header_index + 1
        header_detected = True
        if duplicates:
            warnings.append(
                "Duplicate column names found: "
                + ", ".join(sorted(set(duplicates)))
                + ". They were renamed with a suffix - please check the mapping."
            )

    rows, kept_labels, source_rows = [], [], []
    for offset, row in enumerate(cleaned[body_start:], start=body_start):
        padded = row + [""] * (width - len(row))
        rows.append(padded[:width])
        kept_labels.append(labels[offset])
        source_rows.append(offset + 1)

    # Drop columns that are completely empty and unnamed (common in PDF/Excel exports).
    keep = [
        i for i in range(width)
        if not headers[i].startswith("Column ") or any(r[i] for r in rows)
    ]  # fmt: skip
    if len(keep) != width:
        headers = [headers[i] for i in keep]
        rows = [[r[i] for i in keep] for r in rows]

    return RawTable(
        headers=headers,
        rows=rows,
        row_labels=kept_labels,
        source_rows=source_rows,
        method=method,
        warnings=warnings,
        header_detected=header_detected,
    )
