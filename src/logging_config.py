"""Logging setup with redaction of secrets.

Financial documents are sensitive: application code logs counts, identifiers
and outcomes, never full rows or credentials. As a second line of defence the
``RedactingFilter`` masks configured secret values and ``key=value`` pairs that
look like credentials.
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_ENV_VARS = (
    "XERO_CLIENT_SECRET",
    "XERO_CLIENT_ID",
    "QUICKBOOKS_CLIENT_SECRET",
    "QUICKBOOKS_CLIENT_ID",
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|client[_-]?secret|authorization)"
    r"(\s*[=:]\s*)(\S+)"
)
_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = False


class RedactingFilter(logging.Filter):
    """Masks secrets in log records before they are emitted."""

    def __init__(self) -> None:
        super().__init__()
        self._secrets = [
            value for name in _SECRET_ENV_VARS if len(value := os.getenv(name, "").strip()) >= 4
        ]

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return _CREDENTIAL_PATTERN.sub(r"\1\2***", text)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self.redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Configure root logging once (idempotent - safe under Streamlit reruns)."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter(_LOG_FORMAT)
    redactor = RedactingFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redactor)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "reconciler.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)

    # Third-party libraries are noisy at DEBUG/INFO.
    for noisy in ("pdfminer", "fontTools", "PIL", "urllib3", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
