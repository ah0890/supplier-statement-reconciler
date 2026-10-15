"""CSV supplier statements."""

from __future__ import annotations

from pathlib import Path

from src.parsers.base import BaseStatementParser, StatementParserError
from src.parsers.readers import FileReadError, read_csv_grid
from src.parsers.tabular import RawTable, build_table


class CSVStatementParser(BaseStatementParser):
    name = "CSV"
    extensions = frozenset({".csv"})

    def extract_table(self, path: Path) -> RawTable:
        try:
            grid = read_csv_grid(path)
        except FileReadError as exc:
            raise StatementParserError(str(exc)) from exc
        return build_table(grid, method="csv")
