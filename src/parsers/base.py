"""Statement parser architecture.

Each parser only has to *extract a table* from its file format. Mapping,
validation and conversion to canonical transactions are shared.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from src.models.transaction import TransactionSource, TransactionType
from src.models.validation import ImportResult, IssueSeverity
from src.parsers.columns import ColumnMapping, detect_mapping, missing_required_fields
from src.parsers.tabular import RawTable
from src.parsers.transformer import TableTransformer, TransformOptions

logger = logging.getLogger(__name__)


class StatementParserError(Exception):
    """Raised with a user-facing explanation when a statement cannot be read."""


@dataclass
class StatementParseResult:
    table: RawTable
    mapping: ColumnMapping
    result: ImportResult
    parser_name: str
    missing_required: list[str] = field(default_factory=list)

    @property
    def mapping_complete(self) -> bool:
        return not self.missing_required


class BaseStatementParser(ABC):
    """Extracts a table from a supplier statement file."""

    name: ClassVar[str] = "base"
    extensions: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def extract_table(self, path: Path) -> RawTable:
        """Return the statement's transaction table (header + rows)."""

    def parse(
        self,
        path: Path,
        *,
        mapping: ColumnMapping | None = None,
        expected_currency: str | None = None,
        supplier_name: str | None = None,
        supplier_account: str | None = None,
        table: RawTable | None = None,
    ) -> StatementParseResult:
        """Extract, map and convert a statement. ``mapping`` overrides detection."""
        table = table or self.extract_table(path)
        mapping = mapping or detect_mapping(table.headers, table.rows)
        missing = missing_required_fields(mapping)
        if missing:
            result = ImportResult()
            result.report.records_read = len(table.rows)
            result.report.add(
                IssueSeverity.ERROR,
                "Please map the following column(s): " + ", ".join(missing) + ".",
            )
        else:
            options = TransformOptions(
                source=TransactionSource.STATEMENT,
                source_file=path.name,
                expected_currency=expected_currency,
                default_supplier=supplier_name,
                default_supplier_account=supplier_account,
                credit_default=TransactionType.PAYMENT,
                detect_balances=True,
            )
            result = TableTransformer(mapping, options).transform(table)
        logger.info(
            "Parsed statement with %s: %d rows, %d transactions, %d issues",
            self.name, len(table.rows), len(result.transactions), len(result.report.issues),
        )  # fmt: skip
        return StatementParseResult(
            table=table,
            mapping=mapping,
            result=result,
            parser_name=self.name,
            missing_required=missing,
        )


def get_statement_parser(path: Path | str) -> BaseStatementParser:
    """Return the parser for a file based on its extension."""
    from src.parsers.csv_parser import CSVStatementParser
    from src.parsers.excel_parser import ExcelStatementParser
    from src.parsers.pdf_parser import PDFStatementParser

    suffix = Path(path).suffix.lower()
    for parser_cls in (PDFStatementParser, ExcelStatementParser, CSVStatementParser):
        if suffix in parser_cls.extensions:
            return parser_cls()
    raise StatementParserError(
        f"Unsupported statement file type '{suffix}'. Please upload a PDF, XLSX or CSV file."
    )
