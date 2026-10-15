"""Layered invoice / credit-note matching.

Levels run in order across *all* remaining records, so a weaker method can
never claim a record that a stronger method would have matched:

1. Exact supplier + normalised invoice number
2. Reference "core" (significant digits) + amount within tolerance
3. Reference core + date within tolerance
4. Fuzzy reference similarity (RapidFuzz) among candidates filtered by amount
   or date window, corroborated by an agreeing amount (or a near-identical
   reference with an agreeing date)
5. Supplier + amount + date proximity, only when the pairing is unambiguous

Candidate lookups use dictionaries, a sorted amount index and a date index,
so matching stays close to linear for thousands of records.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from rapidfuzz import fuzz

from src.models.reconciliation import MatchMethod, ReconciliationConfig
from src.models.transaction import CanonicalTransaction, TransactionType
from src.reconciliation.confidence import ConfidenceScorer
from src.utils.dates import days_between
from src.utils.normalization import invoice_core, supplier_names_match

MIN_CORE_LENGTH = 3
NEAR_IDENTICAL_SIMILARITY = 95


@dataclass
class MatchPair:
    statement: CanonicalTransaction
    accounting: CanonicalTransaction
    method: MatchMethod
    confidence: int
    reference_similarity: int = 100

    @property
    def amount_difference(self) -> Decimal:
        return self.statement.amount - self.accounting.amount

    @property
    def days_difference(self) -> int | None:
        return days_between(self.statement.invoice_date, self.accounting.invoice_date)


@dataclass
class MatchingOutput:
    pairs: list[MatchPair] = field(default_factory=list)
    unmatched_statement: list[CanonicalTransaction] = field(default_factory=list)
    unmatched_accounting: list[CanonicalTransaction] = field(default_factory=list)
    # statement txn id -> ids of equally plausible accounting candidates (level 5)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)


class _AccountingIndex:
    """Indexes over the not-yet-matched accounting documents."""

    def __init__(self, docs: Iterable[CanonicalTransaction]) -> None:
        self.by_id: dict[str, CanonicalTransaction] = {}
        self.by_ref: dict[tuple[TransactionType, str], list[str]] = defaultdict(list)
        self.by_core: dict[tuple[TransactionType, str], list[str]] = defaultdict(list)
        self.by_date: dict[tuple[TransactionType, date], list[str]] = defaultdict(list)
        amounts: dict[TransactionType, list[tuple[Decimal, str]]] = defaultdict(list)
        for doc in docs:
            self.by_id[doc.transaction_id] = doc
            kind = doc.transaction_type
            if doc.invoice_number_normalized:
                self.by_ref[(kind, doc.invoice_number_normalized)].append(doc.transaction_id)
                core = invoice_core(doc.invoice_number_normalized)
                if core and len(core) >= MIN_CORE_LENGTH:
                    self.by_core[(kind, core)].append(doc.transaction_id)
            if doc.invoice_date:
                self.by_date[(kind, doc.invoice_date)].append(doc.transaction_id)
            amounts[kind].append((doc.amount, doc.transaction_id))
        self.amounts = {kind: sorted(values) for kind, values in amounts.items()}
        self.amount_keys = {kind: [v[0] for v in values] for kind, values in self.amounts.items()}
        self.matched: set[str] = set()

    def available(self, ids: Iterable[str]) -> list[CanonicalTransaction]:
        return [self.by_id[i] for i in ids if i not in self.matched]

    def near_amount(
        self, kind: TransactionType, amount: Decimal, tolerance: Decimal
    ) -> list[CanonicalTransaction]:
        keys = self.amount_keys.get(kind, [])
        start = bisect.bisect_left(keys, amount - tolerance)
        end = bisect.bisect_right(keys, amount + tolerance)
        return self.available(txn_id for _, txn_id in self.amounts.get(kind, [])[start:end])

    def near_date(
        self, kind: TransactionType, day: date, window: int
    ) -> list[CanonicalTransaction]:
        ids: list[str] = []
        for offset in range(-window, window + 1):
            ids.extend(self.by_date.get((kind, day + timedelta(days=offset)), []))
        return self.available(ids)


class InvoiceMatcher:
    """Pairs statement documents with accounting documents."""

    def __init__(self, config: ReconciliationConfig, scorer: ConfidenceScorer | None = None):
        self.config = config
        self.scorer = scorer or ConfidenceScorer(config)

    # ----------------------------------------------------------------- public
    def match(
        self,
        statement_docs: list[CanonicalTransaction],
        accounting_docs: list[CanonicalTransaction],
    ) -> MatchingOutput:
        index = _AccountingIndex(accounting_docs)
        output = MatchingOutput()
        remaining = list(statement_docs)

        for level in (
            self._level_exact_reference,
            self._level_core_amount,
            self._level_core_date,
            self._level_fuzzy_reference,
        ):
            proposals = [p for s in remaining for p in level(s, index)]
            remaining = self._assign(proposals, remaining, index, output)

        proposals = []
        for stmt in remaining:
            candidates = self._level_amount_date(stmt, index)
            if len(candidates) == 1:
                proposals.extend(candidates)
            elif len(candidates) > 1:
                output.ambiguous[stmt.transaction_id] = [
                    c.accounting.transaction_id for c in candidates
                ]
        proposals = self._drop_contested(proposals, output)
        remaining = self._assign(proposals, remaining, index, output)

        output.unmatched_statement = remaining
        output.unmatched_accounting = [
            doc for doc in accounting_docs if doc.transaction_id not in index.matched
        ]
        return output

    # ---------------------------------------------------------------- helpers
    def _compatible(self, stmt: CanonicalTransaction, acct: CanonicalTransaction) -> bool:
        if stmt.transaction_type != acct.transaction_type:
            return False
        if stmt.currency and acct.currency and stmt.currency != acct.currency:
            return False
        if (
            stmt.supplier_name_normalized
            and acct.supplier_name_normalized
            and stmt.supplier_name_normalized != acct.supplier_name_normalized
        ):
            return supplier_names_match(stmt.supplier_name, acct.supplier_name)
        return True

    def _amount_ok(self, stmt: CanonicalTransaction, acct: CanonicalTransaction) -> bool:
        return abs(stmt.amount - acct.amount) <= self.config.amount_tolerance

    def _date_ok(self, stmt: CanonicalTransaction, acct: CanonicalTransaction) -> bool:
        days = days_between(stmt.invoice_date, acct.invoice_date)
        return days is not None and days <= self.config.date_tolerance_days

    def _pair(
        self,
        stmt: CanonicalTransaction,
        acct: CanonicalTransaction,
        method: MatchMethod,
        similarity: int = 100,
        unrelated: bool = False,
    ) -> MatchPair:
        confidence = self.scorer.score(
            method,
            amount_diff=stmt.amount - acct.amount,
            days_diff=days_between(stmt.invoice_date, acct.invoice_date),
            base_amount=max(stmt.amount, acct.amount),
            reference_similarity=similarity,
            unrelated_references=unrelated,
        )
        return MatchPair(stmt, acct, method, confidence, similarity)

    @staticmethod
    def _quality(pair: MatchPair) -> tuple:
        days = pair.days_difference
        return (
            -pair.confidence,
            abs(pair.amount_difference),
            days if days is not None else 10_000,
            pair.statement.source_row or 0,
        )

    def _assign(
        self,
        proposals: list[MatchPair],
        remaining: list[CanonicalTransaction],
        index: _AccountingIndex,
        output: MatchingOutput,
    ) -> list[CanonicalTransaction]:
        """Greedy assignment, best proposals first; each record used at most once."""
        used_statement: set[str] = set()
        for pair in sorted(proposals, key=self._quality):
            stmt_id, acct_id = pair.statement.transaction_id, pair.accounting.transaction_id
            if stmt_id in used_statement or acct_id in index.matched:
                continue
            if pair.confidence < self.config.possible_threshold:
                continue
            used_statement.add(stmt_id)
            index.matched.add(acct_id)
            output.pairs.append(pair)
        return [s for s in remaining if s.transaction_id not in used_statement]

    # ----------------------------------------------------------------- levels
    def _level_exact_reference(self, stmt, index: _AccountingIndex) -> list[MatchPair]:
        ref = stmt.invoice_number_normalized
        if not ref:
            return []
        candidates = index.available(index.by_ref.get((stmt.transaction_type, ref), []))
        return [
            self._pair(stmt, acct, MatchMethod.EXACT_REFERENCE)
            for acct in candidates
            if self._compatible(stmt, acct)
        ]

    def _core_candidates(self, stmt, index: _AccountingIndex) -> list[CanonicalTransaction]:
        core = invoice_core(stmt.invoice_number_normalized)
        if not core or len(core) < MIN_CORE_LENGTH:
            return []
        return [
            acct
            for acct in index.available(index.by_core.get((stmt.transaction_type, core), []))
            if self._compatible(stmt, acct)
        ]

    def _level_core_amount(self, stmt, index: _AccountingIndex) -> list[MatchPair]:
        return [
            self._pair(stmt, acct, MatchMethod.REFERENCE_AMOUNT, self._similarity(stmt, acct))
            for acct in self._core_candidates(stmt, index)
            if self._amount_ok(stmt, acct)
        ]

    def _level_core_date(self, stmt, index: _AccountingIndex) -> list[MatchPair]:
        return [
            self._pair(stmt, acct, MatchMethod.REFERENCE_DATE, self._similarity(stmt, acct))
            for acct in self._core_candidates(stmt, index)
            if self._date_ok(stmt, acct)
        ]

    @staticmethod
    def _similarity(stmt: CanonicalTransaction, acct: CanonicalTransaction) -> int:
        a, b = stmt.invoice_number_normalized, acct.invoice_number_normalized
        return round(fuzz.ratio(a, b)) if a and b else 0

    def _window_candidates(self, stmt, index: _AccountingIndex) -> list[CanonicalTransaction]:
        candidates = {
            c.transaction_id: c
            for c in index.near_amount(
                stmt.transaction_type, stmt.amount, self.config.amount_tolerance
            )
        }
        if stmt.invoice_date:
            for c in index.near_date(
                stmt.transaction_type, stmt.invoice_date, self.config.date_tolerance_days
            ):
                candidates.setdefault(c.transaction_id, c)
        return [c for c in candidates.values() if self._compatible(stmt, c)]

    def _level_fuzzy_reference(self, stmt, index: _AccountingIndex) -> list[MatchPair]:
        if not stmt.invoice_number_normalized:
            return []
        pairs = []
        for acct in self._window_candidates(stmt, index):
            similarity = self._similarity(stmt, acct)
            if similarity < self.config.fuzzy_match_threshold:
                continue
            # Sequential invoice numbers are always "similar", so similarity alone is not
            # evidence: the amount must agree, or the reference must be near-identical.
            corroborated = self._amount_ok(stmt, acct) or (
                similarity >= NEAR_IDENTICAL_SIMILARITY and self._date_ok(stmt, acct)
            )
            if corroborated:
                pairs.append(self._pair(stmt, acct, MatchMethod.FUZZY_REFERENCE, similarity))
        return pairs

    def _level_amount_date(self, stmt, index: _AccountingIndex) -> list[MatchPair]:
        if not stmt.invoice_date:
            return []
        pairs = []
        for acct in index.near_amount(
            stmt.transaction_type, stmt.amount, self.config.amount_tolerance
        ):
            if not self._compatible(stmt, acct) or not self._date_ok(stmt, acct):
                continue
            similarity = self._similarity(stmt, acct)
            both_have_refs = bool(stmt.invoice_number_normalized and acct.invoice_number_normalized)
            pairs.append(
                self._pair(
                    stmt,
                    acct,
                    MatchMethod.AMOUNT_DATE_PROXIMITY,
                    similarity,
                    unrelated=both_have_refs and similarity < 50,
                )
            )
        return pairs

    @staticmethod
    def _drop_contested(proposals: list[MatchPair], output: MatchingOutput) -> list[MatchPair]:
        """Level 5 pairs are only kept when the accounting record has one contender."""
        contenders: dict[str, list[MatchPair]] = defaultdict(list)
        for pair in proposals:
            contenders[pair.accounting.transaction_id].append(pair)
        kept = []
        for pairs in contenders.values():
            if len(pairs) == 1:
                kept.append(pairs[0])
            else:
                for pair in pairs:
                    output.ambiguous[pair.statement.transaction_id] = [
                        pair.accounting.transaction_id
                    ]
        return kept
