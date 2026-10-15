"""Tests for configuration and safe logging."""

import logging
from decimal import Decimal

from src import config
from src.logging_config import RedactingFilter


def test_reconciliation_defaults_match_specification():
    assert Decimal("0.01") == config.DEFAULT_AMOUNT_TOLERANCE
    assert config.DEFAULT_DATE_TOLERANCE_DAYS == 3
    assert config.FUZZY_MATCH_THRESHOLD == 80
    assert isinstance(config.DEFAULT_AMOUNT_TOLERANCE, Decimal)


def test_settings_create_runtime_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("SSR_DATA_DIR", str(tmp_path / "runtime"))
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.upload_dir.is_dir()
        assert settings.log_dir.is_dir()
        assert settings.database_url.startswith("sqlite:///")
        assert settings.max_upload_bytes == settings.max_upload_mb * 1024 * 1024
    finally:
        config.get_settings.cache_clear()


def test_invalid_env_values_fall_back_to_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("SSR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SSR_DEFAULT_AMOUNT_TOLERANCE", "not-a-number")
    monkeypatch.setenv("SSR_MAX_UPLOAD_MB", "abc")
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.default_amount_tolerance == Decimal("0.01")
        assert settings.max_upload_mb == config.DEFAULT_MAX_UPLOAD_MB
    finally:
        config.get_settings.cache_clear()


def test_redacting_filter_masks_credentials(monkeypatch):
    monkeypatch.setenv("XERO_CLIENT_SECRET", "super-secret-value")
    redactor = RedactingFilter()
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1,
        "token=abc123 and secret super-secret-value used", None, None,
    )  # fmt: skip
    redactor.filter(record)
    message = record.getMessage()
    assert "abc123" not in message
    assert "super-secret-value" not in message
    assert "token=***" in message
