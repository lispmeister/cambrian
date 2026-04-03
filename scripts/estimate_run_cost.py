"""Estimate model cost for a run given token counts and rates.

Example:
  uv run python scripts/estimate_run_cost.py --input 51877 --output 18651 --in-rate 5 --out-rate 25
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=float, required=True, help="Input tokens")
    parser.add_argument("--output", type=float, required=True, help="Output tokens")
    parser.add_argument(
        "--in-rate",
        type=float,
        required=True,
        help="Input rate in dollars per 1M tokens",
    )
    parser.add_argument(
        "--out-rate",
        type=float,
        required=True,
        help="Output rate in dollars per 1M tokens",
    )
    parser.add_argument(
        "--multiplier",
        type=float,
        default=1.0,
        help="Optional multiplier (e.g., 1.1 for US-only inference)",
    )
    args = parser.parse_args()

    cost = (args.input / 1_000_000.0) * args.in_rate
    cost += (args.output / 1_000_000.0) * args.out_rate
    cost *= args.multiplier

    print(f"Estimated cost: ${cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
