"""Date parsing for inconsistent real-world formats.

Ambiguous numeric dates (``03/04/2026``) are resolved with ``dayfirst``; callers
should infer it per column with :func:`infer_dayfirst` rather than per value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta

_EXCEL_EPOCH = date(1899, 12, 30)
_MIN_YEAR, _MAX_YEAR = 1990, 2100

_TEXT_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y", "%d %B %Y", "%d-%B-%Y", "%d %B, %Y",
    "%b %d %Y", "%b %d, %Y", "%B %d %Y", "%B %d, %Y", "%d%b%Y", "%d/%b/%Y", "%d.%b.%Y",
)  # fmt: skip
_DAYFIRST_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y")
_MONTHFIRST_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y", "%m.%d.%y")
_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$")
_TIME_SUFFIX = re.compile(r"[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_ORDINAL = re.compile(r"(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)


def _valid(value: date) -> date | None:
    return value if _MIN_YEAR <= value.year <= _MAX_YEAR else None


def excel_serial_to_date(serial: float) -> date | None:
    """Convert an Excel serial day number (1900 date system)."""
    if not 20000 <= serial <= 80000:  # 1954 .. 2119 - avoids treating amounts as dates
        return None
    return _valid(_EXCEL_EPOCH + timedelta(days=int(serial)))


def parse_date(value: object, *, dayfirst: bool | None = True) -> date | None:
    """Parse a date from strings, datetimes or Excel serials. Returns None if unsure."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return _valid(value.date())
    if isinstance(value, date):
        return _valid(value)
    if isinstance(value, int | float):
        return excel_serial_to_date(float(value))

    text = " ".join(str(value).split())
    if not text:
        return None
    if re.fullmatch(r"\d{5}(\.0+)?", text):  # Excel serial stored as text, e.g. "46280"
        return excel_serial_to_date(float(text))
    text = _TIME_SUFFIX.sub("", text)
    text = _ORDINAL.sub(r"\1", text).replace("Sept", "Sep").replace("sept", "sep")

    numeric = _NUMERIC_DATE.match(text)
    if numeric:
        first, second = int(numeric.group(1)), int(numeric.group(2))
        if first > 12:
            formats = _DAYFIRST_FORMATS
        elif second > 12:
            formats = _MONTHFIRST_FORMATS
        else:
            formats = _MONTHFIRST_FORMATS if dayfirst is False else _DAYFIRST_FORMATS
        for fmt in formats:
            try:
                return _valid(datetime.strptime(text, fmt).date())
            except ValueError:
                continue
        return None

    for fmt in _TEXT_FORMATS:
        try:
            return _valid(datetime.strptime(text, fmt).date())
        except ValueError:
            continue
    return None


def infer_dayfirst(values: Iterable[object]) -> bool | None:
    """Infer day/month order from a column of values.

    Returns True (DD/MM), False (MM/DD) or None when no value disambiguates.
    """
    dayfirst_votes = monthfirst_votes = 0
    for value in values:
        if not isinstance(value, str):
            continue
        match = _NUMERIC_DATE.match(value.strip())
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12 >= second:
            dayfirst_votes += 1
        elif second > 12 >= first:
            monthfirst_votes += 1
    if dayfirst_votes and not monthfirst_votes:
        return True
    if monthfirst_votes and not dayfirst_votes:
        return False
    return None


def looks_like_date(value: object) -> bool:
    return parse_date(value, dayfirst=True) is not None


def days_between(first: date | None, second: date | None) -> int | None:
    """Absolute difference in days, or None when either date is missing."""
    if first is None or second is None:
        return None
    return abs((first - second).days)


def format_date(value: date | None, blank: str = "") -> str:
    return value.strftime("%d-%b-%Y") if value else blank


def is_ambiguous_numeric_date(value: object) -> bool:
    """True for numeric dates like 03/04/2026 where day and month could be swapped."""
    if not isinstance(value, str):
        return False
    match = _NUMERIC_DATE.match(value.strip())
    if not match:
        return False
    first, second = int(match.group(1)), int(match.group(2))
    return first <= 12 and second <= 12 and first != second


MONTH_FIRST_CURRENCIES = frozenset({"USD", "CAD"})
