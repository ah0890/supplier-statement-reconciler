"""Centralised application configuration.

All tunables live here. Values may be overridden through environment variables
(see ``.env.example``); nothing in this module requires credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env", override=False)

# ---------------------------------------------------------------------------
# Reconciliation defaults
# ---------------------------------------------------------------------------
DEFAULT_AMOUNT_TOLERANCE = Decimal("0.01")
DEFAULT_DATE_TOLERANCE_DAYS = 3
DEFAULT_PAYMENT_DATE_TOLERANCE_DAYS = 7
FUZZY_MATCH_THRESHOLD = 80

# Confidence bands (lower bounds, inclusive)
CONFIDENCE_VERY_STRONG = 95
CONFIDENCE_STRONG = 80
CONFIDENCE_POSSIBLE = 60

# Matches below this confidence are never finalised without a human decision.
AUTO_ACCEPT_CONFIDENCE = 95

# ---------------------------------------------------------------------------
# Upload security
# ---------------------------------------------------------------------------
STATEMENT_EXTENSIONS = frozenset({".pdf", ".xlsx", ".csv"})
ACCOUNTING_EXTENSIONS = frozenset({".xlsx", ".csv"})
DEFAULT_MAX_UPLOAD_MB = 25

SUPPORTED_CURRENCIES = (
    "GBP", "USD", "EUR", "AUD", "CAD", "NZD", "ZAR", "INR", "PKR", "AED", "SGD", "CHF",
)  # fmt: skip


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_decimal(name: str, default: Decimal) -> Decimal:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return Decimal(raw)
    except InvalidOperation:
        return default


@dataclass(frozen=True)
class AppSettings:
    """Runtime settings resolved from the environment."""

    data_dir: Path
    upload_dir: Path
    log_dir: Path
    database_url: str
    max_upload_mb: int
    log_level: str
    default_amount_tolerance: Decimal
    default_date_tolerance_days: int
    fuzzy_match_threshold: int

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def _resolve_dir(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return process-wide settings. Directories are created on first use."""
    data_dir = _resolve_dir(_env_str("SSR_DATA_DIR", "data"))
    upload_dir = data_dir / "uploads"
    log_dir = data_dir / "logs"
    for directory in (data_dir, upload_dir, log_dir):
        directory.mkdir(parents=True, exist_ok=True)

    default_db = f"sqlite:///{(data_dir / 'reconciler.db').as_posix()}"
    database_url = _env_str("SSR_DATABASE_URL", default_db)

    return AppSettings(
        data_dir=data_dir,
        upload_dir=upload_dir,
        log_dir=log_dir,
        database_url=database_url,
        max_upload_mb=max(1, _env_int("SSR_MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB)),
        log_level=_env_str("SSR_LOG_LEVEL", "INFO").upper(),
        default_amount_tolerance=_env_decimal(
            "SSR_DEFAULT_AMOUNT_TOLERANCE", DEFAULT_AMOUNT_TOLERANCE
        ),
        default_date_tolerance_days=_env_int(
            "SSR_DEFAULT_DATE_TOLERANCE_DAYS", DEFAULT_DATE_TOLERANCE_DAYS
        ),
        fuzzy_match_threshold=_env_int("SSR_FUZZY_MATCH_THRESHOLD", FUZZY_MATCH_THRESHOLD),
    )
