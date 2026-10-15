"""Demo data generator."""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal

import pytest

from src.config import PROJECT_ROOT
from src.services.demo_data import DEMO_SUPPLIER, OTHER_SUPPLIER, generate, write_files


def test_generator_is_deterministic():
    first, second = generate(40, seed=1), generate(40, seed=1)
    assert [line.reference for line in first.statement] == [
        line.reference for line in second.statement
    ]
    assert first.closing_balance == second.closing_balance
    assert generate(40, seed=2).closing_balance != first.closing_balance


def test_statement_arithmetic_is_consistent():
    data = generate(60)
    activity = sum(
        (line.debit or Decimal(0)) - (line.credit or Decimal(0)) for line in data.statement
    )
    assert data.opening_balance + activity == data.closing_balance
    assert data.statement[-1].balance == data.closing_balance


def test_every_scenario_is_present():
    data = generate(25)
    assert all(data.manifest[name] for name in data.manifest), data.manifest
    suppliers = {bill.supplier for bill in data.bills}
    assert suppliers == {DEMO_SUPPLIER, OTHER_SUPPLIER}


def test_minimum_size_enforced():
    with pytest.raises(ValueError):
        generate(10)


def test_write_files(tmp_path):
    paths = write_files(generate(30), tmp_path)
    for path in paths.values():
        assert path.is_file() and path.stat().st_size > 0
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert "DEMO" in manifest["note"]
    assert paths["statement_pdf"].read_bytes().startswith(b"%PDF")
    assert "DEMO DATA" in paths["statement_csv"].read_text(encoding="utf-8")


def test_cli_script(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "generate_demo_data.py"),
         "--count", "30", "--output-dir", str(tmp_path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert "DEMO data written" in completed.stdout
    assert (tmp_path / "supplier_statement.pdf").is_file()
