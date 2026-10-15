"""Assess a matched pair for amount, date and reference differences.

One pair gets one primary status, chosen by priority, while *all* detected
issues are kept in ``issues`` and explained in the reason:

1. ``AMOUNT_MISMATCH``    - amounts differ by more than the amount tolerance
2. ``REFERENCE_MISMATCH`` - paired by fuzzy reference or amount/date proximity
3. ``DATE_MISMATCH``      - dates differ by more than the date tolerance
4. ``MATCHED_WITH_TOLERANCE`` - small differences within tolerance, or the
   references agree only after removing prefixes / leading zeros
5. ``MATCHED``            - identical amount, same (or missing) date, same
   normalised reference
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from src.models.reconciliation import MatchMethod, MatchStatus, ReconciliationConfig
from src.reconciliation.matcher import MatchPair
from src.utils.dates import format_date
from src.utils.money import format_money


class AmountCheck(StrEnum):
    EXACT_MATCH = "EXACT_MATCH"
    MATCH_WITHIN_TOLERANCE = "MATCH_WITHIN_TOLERANCE"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"


class DateCheck(StrEnum):
    SAME_DATE = "SAME_DATE"
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
    DATE_MISMATCH = "DATE_MISMATCH"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass
class MismatchAssessment:
    status: MatchStatus
    amount_check: AmountCheck
    date_check: DateCheck
    issues: list[str] = field(default_factory=list)
    reason: str = ""


class MismatchDetector:
    def __init__(self, config: ReconciliationConfig) -> None:
        self.config = config

    def check_amount(self, difference: Decimal) -> AmountCheck:
        if difference == 0:
            return AmountCheck.EXACT_MATCH
        if abs(difference) <= self.config.amount_tolerance:
            return AmountCheck.MATCH_WITHIN_TOLERANCE
        return AmountCheck.AMOUNT_MISMATCH

    def check_date(self, days: int | None) -> DateCheck:
        if days is None:
            return DateCheck.NOT_AVAILABLE
        if days == 0:
            return DateCheck.SAME_DATE
        if days <= self.config.date_tolerance_days:
            return DateCheck.WITHIN_TOLERANCE
        return DateCheck.DATE_MISMATCH

    def assess(self, pair: MatchPair) -> MismatchAssessment:
        stmt, acct = pair.statement, pair.accounting
        amount_check = self.check_amount(pair.amount_difference)
        date_check = self.check_date(pair.days_difference)
        reference_uncertain = pair.method in (
            MatchMethod.FUZZY_REFERENCE,
            MatchMethod.AMOUNT_DATE_PROXIMITY,
        )
        reference_format_differs = (
            not reference_uncertain
            and stmt.invoice_number_normalized != acct.invoice_number_normalized
        )

        issues: list[str] = []
        sentences: list[str] = []
        if amount_check == AmountCheck.AMOUNT_MISMATCH:
            issues.append("AMOUNT")
            sentences.append(
                f"Amounts differ: statement {format_money(stmt.amount)} vs accounting "
                f"{format_money(acct.amount)} (difference {format_money(pair.amount_difference)})."
            )
        elif amount_check == AmountCheck.MATCH_WITHIN_TOLERANCE:
            sentences.append(
                f"Amounts differ by {format_money(pair.amount_difference)}, within the "
                f"{format_money(self.config.amount_tolerance)} tolerance."
            )
        if reference_uncertain:
            issues.append("REFERENCE")
            if stmt.invoice_number and acct.invoice_number:
                sentences.append(
                    f"References differ: {stmt.invoice_number} vs {acct.invoice_number} "
                    f"(similarity {pair.reference_similarity}%)."
                )
            else:
                sentences.append(
                    "Paired on amount and date only because a reference is missing or "
                    "unrelated - please confirm."
                )
        elif reference_format_differs:
            issues.append("REFERENCE_FORMAT")
            sentences.append(
                f"References agree only after removing prefixes/leading zeros "
                f"({stmt.invoice_number} vs {acct.invoice_number})."
            )
        if date_check == DateCheck.DATE_MISMATCH:
            issues.append("DATE")
            sentences.append(
                f"Dates differ by {pair.days_difference} days "
                f"({format_date(stmt.invoice_date)} vs {format_date(acct.invoice_date)}), beyond "
                f"the {self.config.date_tolerance_days}-day tolerance."
            )
        elif date_check == DateCheck.WITHIN_TOLERANCE:
            sentences.append(f"Dates differ by {pair.days_difference} day(s), within tolerance.")
        elif date_check == DateCheck.NOT_AVAILABLE:
            sentences.append("A date is missing on one side, so dates could not be compared.")

        if "AMOUNT" in issues:
            status = MatchStatus.AMOUNT_MISMATCH
        elif reference_uncertain:
            status = MatchStatus.REFERENCE_MISMATCH
        elif "DATE" in issues:
            status = MatchStatus.DATE_MISMATCH
        elif (
            amount_check == AmountCheck.MATCH_WITHIN_TOLERANCE
            or date_check == DateCheck.WITHIN_TOLERANCE
            or reference_format_differs
        ):
            status = MatchStatus.MATCHED_WITH_TOLERANCE
        else:
            status = MatchStatus.MATCHED

        if status == MatchStatus.MATCHED:
            sentences.insert(0, "Reference, amount and date agree.")
        return MismatchAssessment(status, amount_check, date_check, issues, " ".join(sentences))
