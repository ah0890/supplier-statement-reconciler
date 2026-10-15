"""Excel (.xlsx) supplier statements."""

from __future__ import annotations

from pathlib import Path

from src.parsers.base import BaseStatementParser, StatementParserError
from src.parsers.readers import FileReadError, read_excel_grid
from src.parsers.tabular import RawTable, build_table


class ExcelStatementParser(BaseStatementParser):
    name = "Excel"
    extensions = frozenset({".xlsx"})

    def __init__(self, sheet_name: str | None = None) -> None:
        self.sheet_name = sheet_name

    def extract_table(self, path: Path) -> RawTable:
        try:
            grid, sheet = read_excel_grid(path, self.sheet_name)
        except FileReadError as exc:
            raise StatementParserError(str(exc)) from exc
        table = build_table(grid, method=f"xlsx:{sheet}")
        table.row_labels = [f"{sheet} row {n}" for n in table.source_rows]
        return table
