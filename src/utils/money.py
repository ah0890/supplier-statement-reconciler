"""Money parsing and formatting.

All monetary values are ``decimal.Decimal``. Floats are never used for
financial arithmetic; when a float arrives (e.g. from a spreadsheet cell) it is
converted through its shortest ``repr`` so that ``0.1`` becomes ``Decimal("0.1")``
rather than its binary approximation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MONEY_QUANT = Decimal("0.01")
ZERO = Decimal("0.00")

_CURRENCY_SYMBOLS = "£$€¥₹₨"
_CURRENCY_CODES = re.compile(
    r"(US\$|A\$|C\$|NZ\$|(?<![A-Za-z])(?:GBP|USD|EUR|AUD|CAD|NZD|ZAR|INR|PKR|AED|SGD|CHF|JPY|"
    r"CNY|HKD|SEK|NOK|DKK|Rs\.?)(?![A-Za-z]))",
    re.IGNORECASE,
)
_EMPTY_TOKENS = {"", "-", "–", "—", "nil", "n/a", "na", "none", "null", "nan"}
_WHITESPACE = re.compile(r"[\s   ]+")
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3})+(,\d+)?$")
_EURO_DECIMAL = re.compile(r"^\d+,\d{1,2}$")


def quantize(value: Decimal) -> Decimal:
    """Round a Decimal to two places using commercial (half-up) rounding."""
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _from_number(value: int | float | Decimal) -> Decimal | None:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return Decimal(repr(value))
    if isinstance(value, Decimal) and not value.is_finite():
        return None
    return Decimal(value)


def _strip_sign_markers(text: str) -> tuple[str, bool]:
    """Remove sign markers; return (clean text, is_negative)."""
    negative = False
    upper = text.upper()
    if upper.endswith("CR"):
        negative, text = True, text[:-2]
    elif upper.endswith("DR"):
        text = text[:-2]
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("-"):
        negative, text = True, text[:-1]
    if text.startswith("-"):
        negative, text = True, text[1:]
    elif text.startswith("+"):
        text = text[1:]
    return text, negative


def _normalise_separators(text: str, decimal_comma: bool | None) -> str | None:
    text = text.replace("'", "")
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        if text.rfind(",") > text.rfind("."):  # 1.234,56
            return text.replace(".", "").replace(",", ".") if _THOUSANDS_DOT.match(text) else None
        return text.replace(",", "") if _THOUSANDS_COMMA.match(text) else None  # 1,234.56
    if has_comma:
        if decimal_comma is True or (decimal_comma is None and _EURO_DECIMAL.match(text)):
            return text.replace(",", ".") if text.count(",") == 1 else None
        return text.replace(",", "") if _THOUSANDS_COMMA.match(text) else None
    if has_dot and text.count(".") > 1:
        return text.replace(".", "") if _THOUSANDS_DOT.match(text) else None
    if has_dot and decimal_comma is True and _THOUSANDS_DOT.match(text):
        return text.replace(".", "")  # European "1.234" = one thousand two hundred thirty-four
    return text


def parse_amount(value: object, *, decimal_comma: bool | None = None) -> Decimal | None:
    """Parse a monetary value from messy input.

    Handles thousands separators, currency symbols/codes, parentheses negatives,
    trailing minus, ``CR``/``DR`` suffixes and European decimal commas.
    Returns ``None`` when the value is empty or cannot be parsed safely - it never
    guesses.

    ``decimal_comma`` forces the interpretation of a lone comma; ``None`` infers
    it per value.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        number = _from_number(value)
        return quantize(number) if number is not None else None

    text = _WHITESPACE.sub("", str(value))
    if text.lower() in _EMPTY_TOKENS:
        return None
    text = _CURRENCY_CODES.sub("", text).strip(_CURRENCY_SYMBOLS)
    text, negative = _strip_sign_markers(text)
    text = text.strip(_CURRENCY_SYMBOLS)
    if not text or not re.fullmatch(r"\d[\d.,']*", text):
        return None

    normalised = _normalise_separators(text, decimal_comma)
    if normalised is None:
        return None
    try:
        number = Decimal(normalised)
    except InvalidOperation:
        return None
    return quantize(-number if negative else number)


def infer_decimal_comma(values: Iterable[object]) -> bool | None:
    """Infer whether a column uses European decimal commas (``1.234,56``).

    Returns True/False when the evidence is unambiguous, otherwise ``None``.
    """
    comma_votes = dot_votes = 0
    for value in values:
        if not isinstance(value, str):
            continue
        text = _WHITESPACE.sub("", value).strip("()-+" + _CURRENCY_SYMBOLS)
        if re.search(r"\d,\d{2}$", text):
            comma_votes += 1
        elif re.search(r"\d\.\d{2}$", text):
            dot_votes += 1
    if comma_votes and not dot_votes:
        return True
    if dot_votes and not comma_votes:
        return False
    return None


def money_sum(values: Iterable[Decimal | None]) -> Decimal:
    """Sum Decimals, ignoring ``None``."""
    total = ZERO
    for value in values:
        if value is not None:
            total += value
    return quantize(total)


def format_money(value: Decimal | None, currency: str | None = None, *, blank: str = "") -> str:
    """Format for display: ``1,234.56``; negatives as ``-1,234.56``."""
    if value is None:
        return blank
    text = f"{quantize(value):,.2f}"
    return f"{currency} {text}" if currency else text


def money_to_str(value: Decimal | None) -> str | None:
    """Lossless string representation used for persistence."""
    return None if value is None else str(quantize(value))


def str_to_money(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(value)
