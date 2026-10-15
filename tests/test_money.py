"""Money parsing must be exact and must refuse to guess."""

from decimal import Decimal

import pytest

from src.utils.money import (
    format_money,
    infer_decimal_comma,
    money_sum,
    parse_amount,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.56", "1234.56"),
        ("£1,234.56", "1234.56"),
        ("GBP 1,234.56", "1234.56"),
        ("$ 99", "99.00"),
        ("(250.00)", "-250.00"),
        ("250.00-", "-250.00"),
        ("-250.00", "-250.00"),
        ("1,500.00 CR", "-1500.00"),
        ("1,500.00 DR", "1500.00"),
        ("1.234,56", "1234.56"),
        ("12,5", "12.50"),
        ("1 234.56", "1234.56"),
        ("1 234,56", "1234.56"),
        ("0", "0.00"),
        ("0.005", "0.01"),
        ("10.004", "10.00"),
        ("1'234.50", "1234.50"),
        ("1.234.567", "1234567.00"),
    ],
)
def test_parse_amount_handles_real_world_formats(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "  ", "-", "—", "n/a", None, "abc", "12abc", "1,23,4", "1..2"])
def test_parse_amount_returns_none_for_unparseable(raw):
    assert parse_amount(raw) is None


def test_float_input_does_not_leak_binary_error():
    assert parse_amount(0.1) + parse_amount(0.2) == Decimal("0.30")
    assert parse_amount(float("nan")) is None


def test_bool_is_not_money():
    assert parse_amount(True) is None


def test_forced_decimal_comma():
    assert parse_amount("1.234", decimal_comma=True) == Decimal("1234.00")
    assert parse_amount("1,234", decimal_comma=False) == Decimal("1234.00")
    assert parse_amount("1,23", decimal_comma=True) == Decimal("1.23")


def test_infer_decimal_comma():
    assert infer_decimal_comma(["1.234,56", "99,00", ""]) is True
    assert infer_decimal_comma(["1,234.56", "99.00"]) is False
    assert infer_decimal_comma(["100", "200"]) is None


def test_money_sum_and_format():
    assert money_sum([Decimal("1.10"), None, Decimal("2.20")]) == Decimal("3.30")
    assert format_money(Decimal("-1234.5")) == "-1,234.50"
    assert format_money(Decimal("10"), "GBP") == "GBP 10.00"
    assert format_money(None, blank="-") == "-"
