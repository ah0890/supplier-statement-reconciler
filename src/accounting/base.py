"""Accounting data provider interface.

The reconciliation engine never knows where accounting data came from. Every
provider - file exports today, Xero / QuickBooks APIs later - returns canonical
transactions through :meth:`AccountingProvider.load`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path

from src.models.transaction import TransactionSource, TransactionType
from src.models.validation import ImportResult, IssueSeverity
from src.parsers.columns import ColumnMapping, detect_mapping, missing_required_fields
from src.parsers.tabular import RawTable
from src.parsers.transformer import TableTransformer, TransformOptions

logger = logging.getLogger(__name__)


class AccountingDataKind(StrEnum):
    BILLS = "BILLS"  # bills / purchase invoices / supplier credit notes
    PAYMENTS = "PAYMENTS"  # supplier payments


class AccountingProviderError(Exception):
    """User-facing error raised by a provider."""


class IntegrationNotAvailableError(AccountingProviderError):
    """Raised by API providers that are not configured or not yet implemented."""


@dataclass
class AccountingQuery:
    """What to fetch. File providers use it for context; API providers for filtering."""

    supplier_name: str | None = None
    supplier_account: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    currency: str | None = None


@dataclass
class AccountingLoadResult:
    result: ImportResult
    provider: str
    table: RawTable | None = None
    mapping: ColumnMapping | None = None
    missing_required: list[str] = field(default_factory=list)

    @property
    def mapping_complete(self) -> bool:
        return not self.missing_required


class AccountingProvider(ABC):
    """Source of accounting-side transactions."""

    name: str = "provider"

    @abstractmethod
    def is_configured(self) -> bool:
        """True when the provider can be used right now."""

    def status_message(self) -> str:
        return "Available" if self.is_configured() else "Not configured"

    @abstractmethod
    def load(
        self,
        query: AccountingQuery,
        kind: AccountingDataKind = AccountingDataKind.BILLS,
        mapping: ColumnMapping | None = None,
    ) -> AccountingLoadResult:
        """Return canonical transactions for ``query``."""


class FileAccountingProvider(AccountingProvider):
    """Base for providers reading an exported file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._table: RawTable | None = None

    def is_configured(self) -> bool:
        return self.path.is_file()

    @abstractmethod
    def _extract_table(self) -> RawTable:
        """Read the export into a table."""

    def table(self) -> RawTable:
        if self._table is None:
            self._table = self._extract_table()
        return self._table

    def load(
        self,
        query: AccountingQuery,
        kind: AccountingDataKind = AccountingDataKind.BILLS,
        mapping: ColumnMapping | None = None,
    ) -> AccountingLoadResult:
        table = self.table()
        mapping = mapping or detect_mapping(table.headers, table.rows)
        is_payments = kind == AccountingDataKind.PAYMENTS
        missing = missing_required_fields(mapping, require_reference=not is_payments)
        if missing:
            result = ImportResult()
            result.report.records_read = len(table.rows)
            result.report.add(
                IssueSeverity.ERROR,
                "Please map the following column(s): " + ", ".join(missing) + ".",
            )
            return AccountingLoadResult(result, self.name, table, mapping, missing)

        options = TransformOptions(
            source=TransactionSource.PAYMENTS if is_payments else TransactionSource.ACCOUNTING,
            source_file=self.path.name,
            expected_currency=query.currency,
            default_supplier=query.supplier_name,
            default_supplier_account=query.supplier_account,
            credit_default=TransactionType.CREDIT_NOTE,
            force_type=TransactionType.PAYMENT if is_payments else None,
            invoice_column_is_allocation=is_payments,
            detect_balances=False,
        )
        result = TableTransformer(mapping, options).transform(table)
        logger.info(
            "%s provider loaded %s: %d transactions, %d issues",
            self.name, kind.value, len(result.transactions), len(result.report.issues),
        )  # fmt: skip
        return AccountingLoadResult(result, self.name, table, mapping, missing)
