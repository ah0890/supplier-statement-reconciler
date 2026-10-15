"""Accounting exports in CSV format (Xero, QuickBooks, Sage and generic exports)."""

from __future__ import annotations

from src.accounting.base import AccountingProviderError, FileAccountingProvider
from src.parsers.readers import FileReadError, read_csv_grid
from src.parsers.tabular import RawTable, build_table


class CSVAccountingProvider(FileAccountingProvider):
    name = "CSV export"

    def _extract_table(self) -> RawTable:
        try:
            grid = read_csv_grid(self.path)
        except FileReadError as exc:
            raise AccountingProviderError(str(exc)) from exc
        return build_table(grid, method="csv")
