#!/usr/bin/env python3
"""
Update cached Market Breadth regime for margin-ta.

Default universe: data/nightly_tickers.json combined S&P 500 + NASDAQ 100 list.
This is intended to run once after the US close, not inside each ticker analysis.
"""
import argparse
import json
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from paths import data_dir as _data_dir  # noqa: E402

TICKERS_FILE = os.path.join(_data_dir(), "nightly_tickers.json")
OUTPUT_FILE = os.path.join(_data_dir(), "market_breadth_cache.json")

sys.path.insert(0, SCRIPT_DIR)
from layer1_market import compute_market_breadth  # noqa: E402


def load_tickers(path: str) -> list[str]:
    with open(path) as f:
        data = json.load(f)
    return data.get("combined", [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Update margin-ta market breadth cache")
    parser.add_argument("--tickers-file", default=TICKERS_FILE)
    parser.add_argument("--output", default=OUTPUT_FILE)
    parser.add_argument("--period", default="1y")
    args = parser.parse_args()

    tickers = load_tickers(args.tickers_file)
    if not tickers:
        print(f"ERROR: no tickers found in {args.tickers_file}", file=sys.stderr)
        return 1

    breadth = compute_market_breadth(tickers, period=args.period)
    payload = {
        "fetched_at": datetime.now().isoformat(),
        "data": breadth,
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(json.dumps(breadth, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
