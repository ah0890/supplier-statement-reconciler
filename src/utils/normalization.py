"""Invoice-reference and supplier-name normalisation.

Normalisation is deliberately *controlled*: it removes formatting noise that
never distinguishes two documents (case, whitespace, ``- / _ . # :``
separators, a leading "Invoice No." label) but keeps every letter and digit,
including leading zeros. ``INV-00123``, ``inv 00123`` and ``INV/00123`` all
become ``INV00123``; ``INV-0123`` stays different.

A looser *core* key (significant digits only) exists for candidate lookup in
the matcher, where it is only ever used together with corroborating amount or
date evidence and a lower confidence score.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

_SEPARATORS = re.compile(r"[\s\-_/\\.#:,;]+")
_LABEL_PREFIX = re.compile(
    r"^\s*(invoice\s*(no|number|num|ref)?|inv\s*(no|number)|bill\s*(no|number)|ref(erence)?|"
    r"doc(ument)?\s*(no|number))\s*[.:#]+\s*",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"\d+")

_LEGAL_SUFFIXES = (
    "LIMITED", "LTD", "PLC", "LLC", "LLP", "LP", "INC", "INCORPORATED", "CORP",
    "CORPORATION", "CO", "COMPANY", "GMBH", "AG", "SA", "SARL", "BV", "NV", "PTY",
    "PVT", "PRIVATE", "PTE", "SRL", "SPA", "AB", "AS", "OY", "KG",
)  # fmt: skip
_SUPPLIER_PUNCT = re.compile(r"[^\w\s]")


def normalize_invoice_number(value: str | None) -> str | None:
    """Canonical form of an invoice reference, or None if blank."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = _LABEL_PREFIX.sub("", text)
    normalised = _SEPARATORS.sub("", text).upper()
    return normalised or None


def invoice_core(normalized: str | None) -> str | None:
    """Significant digits of a reference (``INV00123`` -> ``123``) for candidate lookup."""
    if not normalized:
        return None
    digits = "".join(_DIGITS.findall(normalized)).lstrip("0")
    return digits or None


def normalize_supplier_name(value: str | None) -> str | None:
    """``ABC Ltd.`` / ``ABC LIMITED`` / ``abc  ltd`` -> ``ABC``.

    Only trailing legal-form words and a leading "The" are removed; distinctive
    words are never dropped.
    """
    if value is None:
        return None
    text = str(value).upper().replace("&", " AND ")
    text = re.sub(r"\bT/A\b.*$", "", text)  # "trading as ..." suffix
    text = _SUPPLIER_PUNCT.sub(" ", text)
    words = text.split()
    if words[:1] == ["THE"] and len(words) > 1:
        words = words[1:]
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words) or None


def supplier_names_match(first: str | None, second: str | None, threshold: int = 92) -> bool:
    """True when two supplier names very probably refer to the same supplier."""
    a, b = normalize_supplier_name(first), normalize_supplier_name(second)
    if not a or not b:
        return False
    return a == b or fuzz.token_sort_ratio(a, b) >= threshold


def reference_similarity(first: str | None, second: str | None) -> int:
    """0-100 similarity between two normalised references."""
    if not first or not second:
        return 0
    return round(fuzz.ratio(first, second))
