"""Reconciliation configuration and result models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from src import config


class MatchStatus(StrEnum):
    MATCHED = "MATCHED"
    MATCHED_WITH_TOLERANCE = "MATCHED_WITH_TOLERANCE"
    MISSING_IN_ACCOUNTING = "MISSING_IN_ACCOUNTING"
    NOT_ON_SUPPLIER_STATEMENT = "NOT_ON_SUPPLIER_STATEMENT"
    DUPLICATE = "DUPLICATE"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DATE_MISMATCH = "DATE_MISMATCH"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    CREDIT_NOTE = "CREDIT_NOTE"
    PAYMENT_MISMATCH = "PAYMENT_MISMATCH"
    BALANCE_MISMATCH = "BALANCE_MISMATCH"
    UNRESOLVED = "UNRESOLVED"

    @property
    def label(self) -> str:
        return STATUS_LABELS[self]

    @property
    def is_exception(self) -> bool:
        return self not in OK_STATUSES


STATUS_LABELS: dict[MatchStatus, str] = {
    MatchStatus.MATCHED: "Matched",
    MatchStatus.MATCHED_WITH_TOLERANCE: "Matched within tolerance",
    MatchStatus.MISSING_IN_ACCOUNTING: "Missing in accounting",
    MatchStatus.NOT_ON_SUPPLIER_STATEMENT: "Not on supplier statement",
    MatchStatus.DUPLICATE: "Duplicate",
    MatchStatus.AMOUNT_MISMATCH: "Amount mismatch",
    MatchStatus.DATE_MISMATCH: "Date mismatch",
    MatchStatus.REFERENCE_MISMATCH: "Reference mismatch",
    MatchStatus.CREDIT_NOTE: "Credit note matched",
    MatchStatus.PAYMENT_MISMATCH: "Payment issue",
    MatchStatus.BALANCE_MISMATCH: "Balance mismatch",
    MatchStatus.UNRESOLVED: "Unresolved",
}

OK_STATUSES = frozenset(
    {MatchStatus.MATCHED, MatchStatus.MATCHED_WITH_TOLERANCE, MatchStatus.CREDIT_NOTE}
)


class MatchMethod(StrEnum):
    EXACT_REFERENCE = "EXACT_REFERENCE"  # level 1
    REFERENCE_AMOUNT = "REFERENCE_AMOUNT"  # level 2
    REFERENCE_DATE = "REFERENCE_DATE"  # level 3
    FUZZY_REFERENCE = "FUZZY_REFERENCE"  # level 4
    AMOUNT_DATE_PROXIMITY = "AMOUNT_DATE_PROXIMITY"  # level 5
    PAYMENT_REFERENCE = "PAYMENT_REFERENCE"
    PAYMENT_AMOUNT_DATE = "PAYMENT_AMOUNT_DATE"
    DUPLICATE_CHECK = "DUPLICATE_CHECK"
    MANUAL = "MANUAL"
    NONE = "NONE"


class ConfidenceBand(StrEnum):
    VERY_STRONG = "VERY_STRONG"
    STRONG = "STRONG"
    POSSIBLE = "POSSIBLE"
    UNRESOLVED = "UNRESOLVED"


class ReviewStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"


class ReconciliationOutcome(StrEnum):
    RECONCILED = "RECONCILED"
    RECONCILED_WITH_EXCEPTIONS = "RECONCILED_WITH_EXCEPTIONS"
    NOT_RECONCILED = "NOT_RECONCILED"


class BalanceStatus(StrEnum):
    BALANCED = "BALANCED"
    BALANCE_MISMATCH = "BALANCE_MISMATCH"
    BALANCE_CHECK_NOT_POSSIBLE = "BALANCE_CHECK_NOT_POSSIBLE"


class ResultKind(StrEnum):
    """Which kind of transaction a result concerns."""

    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    PAYMENT = "PAYMENT"
    BALANCE = "BALANCE"


class ReconciliationConfig(BaseModel):
    """User-adjustable matching rules. Persisted with every run for auditability."""

    amount_tolerance: Decimal = config.DEFAULT_AMOUNT_TOLERANCE
    date_tolerance_days: int = Field(default=config.DEFAULT_DATE_TOLERANCE_DAYS, ge=0, le=365)
    payment_date_tolerance_days: int = Field(
        default=config.DEFAULT_PAYMENT_DATE_TOLERANCE_DAYS, ge=0, le=365
    )
    fuzzy_match_threshold: int = Field(default=config.FUZZY_MATCH_THRESHOLD, ge=50, le=100)
    very_strong_threshold: int = Field(default=config.CONFIDENCE_VERY_STRONG, ge=1, le=100)
    strong_threshold: int = Field(default=config.CONFIDENCE_STRONG, ge=1, le=100)
    possible_threshold: int = Field(default=config.CONFIDENCE_POSSIBLE, ge=1, le=100)
    auto_accept_threshold: int = Field(default=config.AUTO_ACCEPT_CONFIDENCE, ge=1, le=100)

    @field_validator("amount_tolerance")
    @classmethod
    def _non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("Amount tolerance cannot be negative.")
        return value

    @model_validator(mode="after")
    def _ordered_thresholds(self) -> ReconciliationConfig:
        if not (self.possible_threshold < self.strong_threshold < self.very_strong_threshold):
            raise ValueError(
                "Confidence thresholds must increase: possible < strong < very strong."
            )
        return self


class MatchResult(BaseModel):
    """One line of the reconciliation outcome."""

    result_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    status: MatchStatus
    original_status: MatchStatus | None = None
    kind: ResultKind = ResultKind.INVOICE
    statement_txn_id: str | None = None
    accounting_txn_id: str | None = None
    related_txn_ids: list[str] = Field(default_factory=list)

    confidence_score: int = Field(default=0, ge=0, le=100)
    confidence_band: ConfidenceBand = ConfidenceBand.UNRESOLVED
    matching_method: MatchMethod = MatchMethod.NONE
    matching_reason: str = ""
    issues: list[str] = Field(default_factory=list)

    statement_amount: Decimal | None = None
    accounting_amount: Decimal | None = None
    amount_difference: Decimal | None = None
    days_difference: int | None = None

    requires_review: bool = False
    review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    review_note: str | None = None
    reviewed_at: datetime | None = None

    @property
    def is_exception(self) -> bool:
        return self.status.is_exception


class BalanceCheckResult(BaseModel):
    status: BalanceStatus
    reason: str = ""
    opening_balance: Decimal | None = None
    total_invoices: Decimal | None = None
    total_credit_notes: Decimal | None = None
    total_payments: Decimal | None = None
    expected_closing_balance: Decimal | None = None
    supplier_closing_balance: Decimal | None = None
    difference: Decimal | None = None
    running_balance_breaks: list[str] = Field(default_factory=list)

    accounting_balance: Decimal | None = None
    accounting_balance_basis: str | None = None
    statement_vs_accounting_difference: Decimal | None = None
    explained_difference: dict[str, Decimal] = Field(default_factory=dict)
    unexplained_difference: Decimal | None = None


class PaymentAllocation(BaseModel):
    """Invoice amount less allocated payments and credits."""

    invoice_number: str
    invoice_amount: Decimal
    payments_allocated: Decimal
    credits_allocated: Decimal
    outstanding: Decimal
    allocation_status: str  # FULLY_PAID, PART_PAID, UNPAID, OVERPAID
    source: str  # STATEMENT or ACCOUNTING


class ReconciliationSummary(BaseModel):
    statement_transactions: int = 0
    accounting_transactions: int = 0
    payment_records: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    pending_reviews: int = 0
    exceptions: int = 0
    suggested_outcome: ReconciliationOutcome = ReconciliationOutcome.NOT_RECONCILED
    suggested_outcome_reason: str = ""

    def count(self, status: MatchStatus) -> int:
        return self.status_counts.get(status.value, 0)

    @property
    def matched(self) -> int:
        return (
            self.count(MatchStatus.MATCHED)
            + self.count(MatchStatus.MATCHED_WITH_TOLERANCE)
            + self.count(MatchStatus.CREDIT_NOTE)
        )


class ReconciliationRun(BaseModel):
    """Everything produced by one execution of the reconciliation engine."""

    results: list[MatchResult] = Field(default_factory=list)
    balance: BalanceCheckResult
    allocations: list[PaymentAllocation] = Field(default_factory=list)
    unallocated_payments: list[str] = Field(default_factory=list)
    summary: ReconciliationSummary = Field(default_factory=ReconciliationSummary)
    config: ReconciliationConfig = Field(default_factory=ReconciliationConfig)
    duration_ms: int = 0
