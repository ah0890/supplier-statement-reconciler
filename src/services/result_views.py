"""Join results with their transactions into flat rows.

Shared by the UI, the Excel/CSV exports and the client report so that every
output shows the same information.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.models.reconciliation import MatchResult, MatchStatus, ResultKind
from src.models.transaction import CanonicalTransaction


@dataclass(frozen=True)
class ExceptionCategory:
    key: str
    label: str
    statuses: frozenset[MatchStatus]
    description: str


EXCEPTION_CATEGORIES: tuple[ExceptionCategory, ...] = (
    ExceptionCategory("missing", "Missing invoices", frozenset({MatchStatus.MISSING_IN_ACCOUNTING}),
                      "On the supplier statement but not in the accounting records."),
    ExceptionCategory("accounting_only", "Not on statement",
                      frozenset({MatchStatus.NOT_ON_SUPPLIER_STATEMENT}),
                      "In the accounting records but not on the supplier statement."),
    ExceptionCategory("duplicates", "Duplicates", frozenset({MatchStatus.DUPLICATE}),
                      "Records that appear more than once."),
    ExceptionCategory("amounts", "Amount differences", frozenset({MatchStatus.AMOUNT_MISMATCH}),
                      "Same document, different amounts."),
    ExceptionCategory("dates", "Date differences", frozenset({MatchStatus.DATE_MISMATCH}),
                      "Same document, dates beyond tolerance."),
    ExceptionCategory("references", "Reference differences",
                      frozenset({MatchStatus.REFERENCE_MISMATCH}),
                      "Matched on similar references or amount/date - needs confirmation."),
    ExceptionCategory("payments", "Payment issues", frozenset({MatchStatus.PAYMENT_MISMATCH}),
                      "Payments that differ, are missing, or over-allocate an invoice."),
    ExceptionCategory("balance", "Balance issues", frozenset({MatchStatus.BALANCE_MISMATCH}),
                      "The statement does not add up to its closing balance."),
    ExceptionCategory("unresolved", "Unresolved", frozenset({MatchStatus.UNRESOLVED}),
                      "Items that could not be matched or verified automatically."),
)  # fmt: skip

KIND_LABELS = {
    ResultKind.INVOICE: "Invoice",
    ResultKind.CREDIT_NOTE: "Credit note",
    ResultKind.PAYMENT: "Payment",
    ResultKind.BALANCE: "Balance",
}


@dataclass
class ResultRow:
    result_id: str
    status: MatchStatus
    kind: ResultKind
    reference: str
    supplier: str | None
    statement_reference: str | None
    statement_date: date | None
    statement_due_date: date | None
    statement_amount: Decimal | None
    statement_description: str | None
    accounting_reference: str | None
    accounting_date: date | None
    accounting_amount: Decimal | None
    accounting_description: str | None
    amount_difference: Decimal | None
    days_difference: int | None
    confidence: int
    confidence_band: str
    method: str
    reason: str
    review_status: str
    requires_review: bool
    review_note: str | None

    @property
    def status_label(self) -> str:
        return self.status.label

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    def as_record(self) -> dict[str, object]:
        """Column order used for tables and exports."""
        return {
            "Status": self.status_label,
            "Type": self.kind_label,
            "Reference": self.reference,
            "Supplier": self.supplier or "",
            "Statement Ref": self.statement_reference or "",
            "Statement Date": self.statement_date,
            "Statement Amount": self.statement_amount,
            "Accounting Ref": self.accounting_reference or "",
            "Accounting Date": self.accounting_date,
            "Accounting Amount": self.accounting_amount,
            "Difference": self.amount_difference,
            "Days Difference": self.days_difference,
            "Due Date": self.statement_due_date,
            "Description": self.statement_description or self.accounting_description or "",
            "Confidence": self.confidence,
            "Confidence Band": self.confidence_band.replace("_", " ").title(),
            "Matching Method": self.method.replace("_", " ").title(),
            "Reason": self.reason,
            "Review": self.review_status.replace("_", " ").title(),
            "Review Note": self.review_note or "",
        }


def build_rows(
    results: Sequence[MatchResult], lookup: dict[str, CanonicalTransaction]
) -> list[ResultRow]:
    rows: list[ResultRow] = []
    for r in results:
        stmt = lookup.get(r.statement_txn_id or "")
        acct = lookup.get(r.accounting_txn_id or "")
        primary = stmt or acct
        reference = primary.display_reference if primary else "Statement balance"
        rows.append(
            ResultRow(
                result_id=r.result_id,
                status=r.status,
                kind=r.kind,
                reference=reference,
                supplier=(stmt.supplier_name if stmt else None)
                or (acct.supplier_name if acct else None),
                statement_reference=stmt.display_reference if stmt else None,
                statement_date=stmt.invoice_date if stmt else None,
                statement_due_date=stmt.due_date if stmt else None,
                statement_amount=r.statement_amount,
                statement_description=stmt.description if stmt else None,
                accounting_reference=acct.display_reference if acct else None,
                accounting_date=acct.invoice_date if acct else None,
                accounting_amount=r.accounting_amount,
                accounting_description=acct.description if acct else None,
                amount_difference=r.amount_difference,
                days_difference=r.days_difference,
                confidence=r.confidence_score,
                confidence_band=r.confidence_band.value,
                method=r.matching_method.value,
                reason=r.matching_reason,
                review_status=r.review_status.value,
                requires_review=r.requires_review,
                review_note=r.review_note,
            )
        )
    return rows


def rows_for_statuses(rows: Sequence[ResultRow], statuses: set[MatchStatus]) -> list[ResultRow]:
    return [row for row in rows if row.status in statuses]


def summary_messages(status_counts: dict[str, int], pending_reviews: int) -> list[str]:
    """Plain-language outcome lines, e.g. "4 invoices are missing from accounting."."""

    def count(status: MatchStatus) -> int:
        return status_counts.get(status.value, 0)

    matched = (
        count(MatchStatus.MATCHED)
        + count(MatchStatus.MATCHED_WITH_TOLERANCE)
        + count(MatchStatus.CREDIT_NOTE)
    )
    lines = [f"{matched} transaction(s) matched."]
    templates = (
        (MatchStatus.MISSING_IN_ACCOUNTING,
         "{n} item(s) on the statement are missing from accounting."),
        (MatchStatus.NOT_ON_SUPPLIER_STATEMENT,
         "{n} accounting item(s) are not on the supplier statement."),
        (MatchStatus.DUPLICATE, "{n} possible duplicate(s) detected."),
        (MatchStatus.AMOUNT_MISMATCH, "{n} amount mismatch(es) require review."),
        (MatchStatus.DATE_MISMATCH, "{n} date difference(s) beyond tolerance."),
        (MatchStatus.REFERENCE_MISMATCH,
         "{n} match(es) rely on similar references and need confirmation."),
        (MatchStatus.PAYMENT_MISMATCH, "{n} payment issue(s) found."),
        (MatchStatus.BALANCE_MISMATCH, "The statement does not add up to its closing balance."),
        (MatchStatus.UNRESOLVED, "{n} item(s) could not be resolved automatically."),
    )  # fmt: skip
    for status, template in templates:
        if count(status):
            lines.append(template.format(n=count(status)))
    if pending_reviews:
        lines.append(f"{pending_reviews} item(s) are waiting for your review decision.")
    return lines
