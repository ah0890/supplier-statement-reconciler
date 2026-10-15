"""Duplicate detection within one side (statement or accounting).

Not every similar record is a duplicate. Findings carry a confidence and a
reason, and only near-certain copies are taken out of the matching pool:

=====================================================  ===========  ===========
Evidence                                               Confidence   Excluded
=====================================================  ===========  ===========
Same supplier, reference, amount and date              98           yes
Same reference and amount, dates within tolerance      92           yes
Same reference and amount, dates further apart         85           yes
Same reference, different amount (re-issued?)          65           no
Same amount and date, very similar reference           75           no
Same amount and date, one reference blank              60           no
=====================================================  ===========  ===========

"Excluded" copies are reported as DUPLICATE and do not take part in matching
(so a double-entered bill cannot hide a genuine difference). Lower-confidence
findings stay in the pool; they are only reported as DUPLICATE if the record
ends up unmatched.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from src.models.reconciliation import ReconciliationConfig
from src.models.transaction import CanonicalTransaction
from src.utils.dates import days_between, format_date
from src.utils.money import format_money

EXCLUDE_THRESHOLD = 85
NEAR_DUPLICATE_SIMILARITY = 85


@dataclass
class DuplicateFinding:
    duplicate: CanonicalTransaction
    original: CanonicalTransaction
    confidence: int
    reason: str

    @property
    def exclude_from_matching(self) -> bool:
        return self.confidence >= EXCLUDE_THRESHOLD


def _order(txn: CanonicalTransaction) -> tuple:
    return (txn.source_row or 0, txn.transaction_id)


class DuplicateDetector:
    def __init__(self, config: ReconciliationConfig) -> None:
        self.config = config

    def find_reference_duplicates(
        self, docs: Sequence[CanonicalTransaction]
    ) -> list[DuplicateFinding]:
        """Records sharing supplier + document type + normalised reference."""
        groups: dict[tuple, list[CanonicalTransaction]] = defaultdict(list)
        for doc in docs:
            if doc.invoice_number_normalized:
                key = (
                    doc.transaction_type,
                    doc.supplier_name_normalized,
                    doc.invoice_number_normalized,
                )
                groups[key].append(doc)

        findings: list[DuplicateFinding] = []
        for members in groups.values():
            if len(members) < 2:
                continue
            members = sorted(members, key=_order)
            original = members[0]
            for copy in members[1:]:
                findings.append(self._classify_reference_copy(original, copy))
        return findings

    def _classify_reference_copy(
        self, original: CanonicalTransaction, copy: CanonicalTransaction
    ) -> DuplicateFinding:
        ref = copy.display_reference
        days = days_between(original.invoice_date, copy.invoice_date)
        same_amount = original.amount == copy.amount
        if same_amount and days in (0, None):
            confidence = 98
            reason = (
                f"{ref} appears more than once with the same amount ({format_money(copy.amount)})"
                f" and date - very likely entered twice."
            )
        elif same_amount and days is not None and days <= self.config.date_tolerance_days:
            confidence = 92
            reason = (
                f"{ref} appears more than once with the same amount; dates differ by {days} day(s)."
            )
        elif same_amount:
            confidence = 85
            reason = (
                f"{ref} appears more than once with the same amount but dates "
                f"{format_date(original.invoice_date)} and {format_date(copy.invoice_date)}."
            )
        else:
            confidence = 65
            reason = (
                f"{ref} appears more than once with different amounts "
                f"({format_money(original.amount)} and {format_money(copy.amount)}) - possibly "
                "an amended or re-issued document. Please check."
            )
        return DuplicateFinding(copy, original, confidence, reason)

    def find_near_duplicates(
        self,
        candidates: Sequence[CanonicalTransaction],
        pool: Sequence[CanonicalTransaction],
    ) -> list[DuplicateFinding]:
        """Records with different references but the same amount and a similar date."""
        by_amount: dict[tuple, list[CanonicalTransaction]] = defaultdict(list)
        for doc in pool:
            by_amount[(doc.transaction_type, doc.amount)].append(doc)

        findings: list[DuplicateFinding] = []
        flagged: set[str] = set()
        for candidate in sorted(candidates, key=_order):
            best: tuple[int, CanonicalTransaction, str] | None = None
            for other in by_amount.get((candidate.transaction_type, candidate.amount), []):
                if other.transaction_id == candidate.transaction_id:
                    continue
                if other.transaction_id in flagged or _order(other) > _order(candidate):
                    continue  # report only the later record of a pair
                days = days_between(candidate.invoice_date, other.invoice_date)
                if days is None or days > self.config.date_tolerance_days:
                    continue
                if candidate.supplier_name_normalized != other.supplier_name_normalized:
                    continue
                a, b = candidate.invoice_number_normalized, other.invoice_number_normalized
                if a and b:
                    if a == b:
                        continue  # handled by reference duplicates
                    similarity = round(fuzz.ratio(a, b))
                    if similarity < NEAR_DUPLICATE_SIMILARITY:
                        continue
                    confidence = 75
                    why = f"a very similar reference ({other.display_reference})"
                else:
                    confidence = 60
                    why = f"{other.display_reference} (one reference is blank)"
                if best is None or confidence > best[0]:
                    best = (confidence, other, why)
            if best is not None:
                confidence, other, why = best
                flagged.add(candidate.transaction_id)
                findings.append(
                    DuplicateFinding(
                        candidate,
                        other,
                        confidence,
                        f"{candidate.display_reference} has the same amount "
                        f"({format_money(candidate.amount)}) and a similar date as {why} - "
                        "possible duplicate.",
                    )
                )
        return findings
