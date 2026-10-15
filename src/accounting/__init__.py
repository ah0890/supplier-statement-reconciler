"""Accounting data providers."""

from __future__ import annotations

from pathlib import Path

from src.accounting.base import AccountingProvider, AccountingProviderError
from src.accounting.csv_provider import CSVAccountingProvider
from src.accounting.excel_provider import ExcelAccountingProvider
from src.accounting.quickbooks_provider import QuickBooksProvider
from src.accounting.xero_provider import XeroProvider


def get_file_provider(path: Path, sheet_name: str | None = None) -> AccountingProvider:
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return CSVAccountingProvider(path)
    if suffix == ".xlsx":
        return ExcelAccountingProvider(path, sheet_name)
    raise AccountingProviderError(
        f"Unsupported accounting file type '{suffix}'. Please upload a CSV or XLSX export."
    )


def api_providers() -> list[AccountingProvider]:
    """Future API integrations, for status display."""
    return [XeroProvider(), QuickBooksProvider()]


__all__ = [
    "AccountingProvider",
    "AccountingProviderError",
    "CSVAccountingProvider",
    "ExcelAccountingProvider",
    "QuickBooksProvider",
    "XeroProvider",
    "api_providers",
    "get_file_provider",
]
