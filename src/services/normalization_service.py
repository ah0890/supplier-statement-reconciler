"""Apply normalisation and scoping rules to imported transactions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from src.models.transaction import CanonicalTransaction
from src.models.validation import IssueSeverity, ValidationReport
from src.utils.normalization import (
    normalize_invoice_number,
    normalize_supplier_name,
    supplier_names_match,
)


@dataclass
class NormalizationContext:
    supplier_name: str
    supplier_account: str | None = None
    currency: str | None = None


def normalize_transactions(
    transactions: list[CanonicalTransaction],
    context: NormalizationContext,
    report: ValidationReport,
    *,
    filter_supplier: bool = True,
) -> tuple[list[CanonicalTransaction], int]:
    """Fill normalised fields and keep only the reconciliation's supplier.

    Returns (kept transactions, number excluded because they belong to other suppliers).
    """
    for txn in transactions:
        txn.invoice_number_normalized = normalize_invoice_number(txn.invoice_number)
        txn.supplier_name = txn.supplier_name or context.supplier_name
        txn.supplier_name_normalized = normalize_supplier_name(txn.supplier_name)
        if not txn.currency and context.currency:
            txn.currency = context.currency

    if not filter_supplier:
        return transactions, 0
    return _filter_supplier(transactions, context, report)


def _belongs(txn: CanonicalTransaction, context: NormalizationContext) -> bool:
    if (
        context.supplier_account
        and txn.supplier_account
        and txn.supplier_account.strip().upper() == context.supplier_account.strip().upper()
    ):
        return True
    return supplier_names_match(txn.supplier_name, context.supplier_name)


def _filter_supplier(
    transactions: list[CanonicalTransaction],
    context: NormalizationContext,
    report: ValidationReport,
) -> tuple[list[CanonicalTransaction], int]:
    names = Counter(t.supplier_name_normalized for t in transactions if t.supplier_name_normalized)
    if len(names) <= 1:
        if names and not all(_belongs(t, context) for t in transactions):
            found = next(iter(names))
            report.add(
                IssueSeverity.WARNING,
                f"The file's supplier name ('{found}') does not match the reconciliation "
                f"supplier ('{context.supplier_name}'). All rows were kept - please confirm "
                "this is the right export.",
            )
        return transactions, 0

    kept = [t for t in transactions if _belongs(t, context)]
    excluded = len(transactions) - len(kept)
    if not kept:
        listed = ", ".join(name for name, _ in names.most_common(8))
        report.add(
            IssueSeverity.ERROR,
            f"No rows match supplier '{context.supplier_name}'. Suppliers found in the file: "
            f"{listed}. Check the supplier name on the reconciliation or the column mapping.",
        )
        return [], excluded
    if excluded:
        others = Counter(
            t.supplier_name or "(blank)" for t in transactions if not _belongs(t, context)
        )
        summary = ", ".join(f"{name} ({count})" for name, count in others.most_common(5))
        report.add(
            IssueSeverity.INFO,
            f"{excluded} row(s) for other suppliers were excluded: {summary}.",
        )
    return kept, excluded
