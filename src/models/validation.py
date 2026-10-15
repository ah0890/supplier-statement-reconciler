"""Import validation results shown to the user before reconciliation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from src.models.transaction import CanonicalTransaction, StatementBalances


class IssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class RowIssue(BaseModel):
    """A problem with one source row (``row`` is 1-based as the user sees it)."""

    row: int | None = None
    severity: IssueSeverity
    field: str | None = None
    message: str


class ValidationReport(BaseModel):
    records_read: int = 0
    valid: int = 0
    skipped: int = 0
    issues: list[RowIssue] = Field(default_factory=list)

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.WARNING)

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.ERROR)

    @property
    def error_rows(self) -> int:
        return len({i.row for i in self.issues if i.severity == IssueSeverity.ERROR})

    def add(
        self,
        severity: IssueSeverity,
        message: str,
        *,
        row: int | None = None,
        field: str | None = None,
    ) -> None:
        self.issues.append(RowIssue(row=row, severity=severity, field=field, message=message))


class ImportResult(BaseModel):
    """Transactions produced from one file plus everything learnt while reading it."""

    transactions: list[CanonicalTransaction] = Field(default_factory=list)
    balances: StatementBalances = Field(default_factory=StatementBalances)
    report: ValidationReport = Field(default_factory=ValidationReport)
    excluded_other_suppliers: int = 0

    @property
    def has_blocking_errors(self) -> bool:
        """True when nothing usable was imported."""
        return not self.transactions
