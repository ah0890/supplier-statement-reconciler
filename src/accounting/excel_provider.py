"""Accounting exports in Excel (.xlsx) format."""

from __future__ import annotations

from pathlib import Path

from src.accounting.base import AccountingProviderError, FileAccountingProvider
from src.parsers.readers import FileReadError, read_excel_grid
from src.parsers.tabular import RawTable, build_table


class ExcelAccountingProvider(FileAccountingProvider):
    name = "Excel export"

    def __init__(self, path: Path, sheet_name: str | None = None) -> None:
        super().__init__(path)
        self.sheet_name = sheet_name

    def _extract_table(self) -> RawTable:
        try:
            grid, sheet = read_excel_grid(self.path, self.sheet_name)
        except FileReadError as exc:
            raise AccountingProviderError(str(exc)) from exc
        table = build_table(grid, method=f"xlsx:{sheet}")
        table.row_labels = [f"{sheet} row {n}" for n in table.source_rows]
        return table
