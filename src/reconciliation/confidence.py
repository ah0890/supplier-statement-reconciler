"""Confidence scoring for proposed matches.

The score answers "how sure are we that these two records are the same
document?" - not "do they agree?". Disagreements (amount, date) are reported
through the result status; they only lower confidence slightly because a
large discrepancy can also mean the pairing itself is wrong.

Scores by matching level (before adjustments):

==========================  =====  ==========================================
Level / method              Base   Notes
==========================  =====  ==========================================
1 Exact reference           100    -4 amount outside tolerance, -3 date outside
                                   tolerance, -15 amount differs by >50%,
                                   -10 dates >90 days apart
2 Reference core + amount   90     -3 date outside tolerance
3 Reference core + date     82
4 Fuzzy reference           50-80  scaled by similarity; +8 amount agrees,
                                   +4 date agrees; capped at 89
5 Amount + date proximity   70     -2 per day apart, -5 amount only within
                                   tolerance, -15 if both references exist
                                   and look unrelated; capped at 79
==========================  =====  ==========================================

Fuzzy and proximity matches can never reach the auto-accept threshold, so they
always go to manual review.
"""

from __future__ import annotations

from decimal import Decimal

from src.models.reconciliation import ConfidenceBand, MatchMethod, ReconciliationConfig


class ConfidenceScorer:
    def __init__(self, config: ReconciliationConfig) -> None:
        self.config = config

    def band(self, score: int) -> ConfidenceBand:
        if score >= self.config.very_strong_threshold:
            return ConfidenceBand.VERY_STRONG
        if score >= self.config.strong_threshold:
            return ConfidenceBand.STRONG
        if score >= self.config.possible_threshold:
            return ConfidenceBand.POSSIBLE
        return ConfidenceBand.UNRESOLVED

    def requires_review(self, score: int, method: MatchMethod) -> bool:
        if method in (MatchMethod.FUZZY_REFERENCE, MatchMethod.AMOUNT_DATE_PROXIMITY):
            return True
        return score < self.config.auto_accept_threshold

    def _amount_ok(self, amount_diff: Decimal | None) -> bool:
        return amount_diff is not None and abs(amount_diff) <= self.config.amount_tolerance

    def _date_ok(self, days: int | None) -> bool:
        return days is not None and days <= self.config.date_tolerance_days

    def score(
        self,
        method: MatchMethod,
        *,
        amount_diff: Decimal | None,
        days_diff: int | None,
        base_amount: Decimal | None = None,
        reference_similarity: int = 100,
        unrelated_references: bool = False,
    ) -> int:
        amount_ok, date_ok = self._amount_ok(amount_diff), self._date_ok(days_diff)
        if method == MatchMethod.EXACT_REFERENCE:
            score = 100
            if not amount_ok:
                score -= 4
                if base_amount and amount_diff is not None and abs(amount_diff) > base_amount / 2:
                    score -= 15
            if days_diff is not None and not date_ok:
                score -= 3
                if days_diff > 90:
                    score -= 10
        elif method == MatchMethod.REFERENCE_AMOUNT:
            score = 90 - (0 if date_ok or days_diff is None else 3)
        elif method == MatchMethod.REFERENCE_DATE:
            score = 82
        elif method == MatchMethod.FUZZY_REFERENCE:
            threshold = self.config.fuzzy_match_threshold
            span = max(1, 100 - threshold)
            score = 50 + round((reference_similarity - threshold) * 30 / span)
            score += 8 if amount_ok else 0
            score += 4 if date_ok else 0
            score = min(score, 89)
        elif method == MatchMethod.AMOUNT_DATE_PROXIMITY:
            score = 70 - 2 * (days_diff or 0)
            if amount_diff is not None and amount_diff != 0:
                score -= 5
            if unrelated_references:
                score -= 15
            score = min(score, 79)
        elif method in (MatchMethod.PAYMENT_REFERENCE, MatchMethod.MANUAL):
            score = 100 if amount_ok else 90
        elif method == MatchMethod.PAYMENT_AMOUNT_DATE:
            score = 75 - 2 * (days_diff or 0)
        else:
            score = 0
        return max(0, min(100, score))
