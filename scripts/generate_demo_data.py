"""Generate DEMO supplier statement, accounting export and payments files.

Usage:
    python scripts/generate_demo_data.py                  # 40 invoices -> sample_data/
    python scripts/generate_demo_data.py --count 200 --seed 7 --output-dir demo_out

All data is synthetic and fictional, and clearly labelled as DEMO data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.demo_data import generate, write_files  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--count",
        type=int,
        default=40,
        help="number of September invoices (minimum 25, default 40)",
    )
    parser.add_argument("--seed", type=int, default=2026, help="random seed (default 2026)")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "sample_data",
                        help="destination folder (default: sample_data/)")  # fmt: skip
    args = parser.parse_args(argv)

    try:
        data = generate(args.count, args.seed)
    except ValueError as exc:
        parser.error(str(exc))
    paths = write_files(data, args.output_dir)
    print(f"DEMO data written to {args.output_dir.resolve()}")
    for name, path in paths.items():
        print(f"  {name:<16} {path.name}")
    print(
        f"  {len(data.statement)} statement lines, {len(data.bills)} accounting rows, "
        f"{len(data.payments)} payment rows; closing balance {data.closing_balance:,.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
