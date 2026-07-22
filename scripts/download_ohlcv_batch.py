#!/usr/bin/env python3
"""
download_ohlcv_batch.py — S&P 500 + NASDAQ 100 전종목 OHLCV 일괄 다운로드

margin-ta 야간 스캔 전에 1회 실행하여 2년치 OHLCV + info_summary를 캐시해둔다.
이후 scan_nightly.py가 --ohlcv-cache-dir로 캐시를 읽어 yfinance 호출을 생략한다.

Usage:
    python3 download_ohlcv_batch.py [--date YYYY-MM-DD] [--delay 0.3] [--batch-size 50]
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, date

import yfinance as yf
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from paths import data_dir as _data_dir  # noqa: E402
TICKERS_FILE = os.path.join(_data_dir(), "nightly_tickers.json")
CACHE_BASE = os.path.join(_data_dir(), "ohlcv_cache")


def load_tickers():
    if not os.path.exists(TICKERS_FILE):
        # Same watchlist bootstrap the scanner uses (S&P500 + NASDAQ100).
        sys.path.insert(0, SCRIPT_DIR)
        from scan_nightly import build_tickers_file

        build_tickers_file()
    with open(TICKERS_FILE) as f:
        data = json.load(f)
    tickers = data.get("combined", [])
    if not tickers:
        print("ERROR: No tickers in file", file=sys.stderr)
        sys.exit(1)
    return tickers


def fetch_one_ticker(symbol):
    """Download 2-year OHLCV + info for a single ticker. Returns dict or None."""
    try:
        t = yf.Ticker(symbol)
        df = t.history(period="2y", interval="1d")
        if df.empty:
            return None

        # 거래량 0 행 제거
        df = df[df.Volume > 0].copy()
        if len(df) < 20:
            return None  # 너무 적은 데이터 → 신규상장 등

        # OHLCV → records for JSON
        ohlcv_records = []
        for idx, row in df.iterrows():
            ohlcv_records.append({
                "date": idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx),
                "open": float(row.Open),
                "high": float(row.High),
                "low": float(row.Low),
                "close": float(row.Close),
                "volume": int(row.Volume),
            })

        # info summary
        info = t.info
        info_summary = {
            "name": info.get("shortName") or info.get("longName", symbol),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta"),
            "short_float_pct": info.get("shortPercentOfFloat"),
            "avg_volume_10d": info.get("averageVolume"),
            "avg_volume_50d": info.get("averageVolume50days"),
            "exchange": info.get("exchange", ""),
            "currency": info.get("currency", "USD"),
            "previous_close": info.get("previousClose"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        }

        return {
            "symbol": symbol,
            "cached_at": datetime.now().isoformat(),
            "trading_days": len(ohlcv_records),
            "ohlcv": ohlcv_records,
            "info_summary": info_summary,
        }
    except Exception as e:
        # silent skip — errors reported at end
        return None


def clean_old_caches(cache_date_dir, keep_days=7):
    """Delete cache directories older than keep_days."""
    if not os.path.isdir(CACHE_BASE):
        return
    cutoff = date.today()
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=keep_days)

    for entry in os.listdir(CACHE_BASE):
        entry_path = os.path.join(CACHE_BASE, entry)
        if not os.path.isdir(entry_path):
            continue
        # entry is date string "YYYY-MM-DD"
        if entry == cache_date_dir:
            continue  # don't delete today's
        try:
            entry_date = date.fromisoformat(entry)
            if entry_date < cutoff:
                import shutil
                shutil.rmtree(entry_path)
                print(f"  🗑  오래된 캐시 삭제: {entry}", file=sys.stderr)
        except (ValueError, OSError):
            pass


def main():
    parser = argparse.ArgumentParser(description="Batch OHLCV downloader for margin-ta")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"),
                        help="Cache date key (default: today)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Delay between ticker downloads (seconds)")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Progress report interval")
    parser.add_argument("--keep-days", type=int, default=7,
                        help="Auto-delete caches older than N days (0=never)")
    args = parser.parse_args()

    cache_date_dir = args.date
    cache_dir = os.path.join(CACHE_BASE, cache_date_dir)

    # 기존 캐시 있으면 스킵
    if os.path.exists(cache_dir):
        existing = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        if existing:
            print(f"✅ {cache_date_dir} 캐시 이미 존재 ({len(existing)}개). 스킵.", file=sys.stderr)
            # Still clean old caches
            if args.keep_days > 0:
                clean_old_caches(cache_date_dir, args.keep_days)
            return 0

    os.makedirs(cache_dir, exist_ok=True)

    tickers = load_tickers()
    print(f"📥 {len(tickers)}종목 OHLCV 다운로드 시작... ({cache_date_dir})", file=sys.stderr)

    success = 0
    errors = 0
    t_start = time.time()

    for i, sym in enumerate(tickers):
        if i > 0 and i % args.batch_size == 0:
            elapsed = time.time() - t_start
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (len(tickers) - i) / rate if rate > 0 else 0
            print(f"  [{i}/{len(tickers)}] {success} ok, {errors} err, "
                  f"{elapsed:.0f}s elapsed, ~{remaining:.0f}s remaining",
                  file=sys.stderr)

        data = fetch_one_ticker(sym)
        if data:
            cache_file = os.path.join(cache_dir, f"{sym}.json")
            with open(cache_file, "w") as f:
                json.dump(data, f, ensure_ascii=False)
            success += 1
        else:
            errors += 1

        time.sleep(args.delay)

    elapsed = time.time() - t_start
    print(f"✅ 완료: {success} 저장, {errors} 실패, {elapsed:.0f}s", file=sys.stderr)

    # Manifest file
    manifest = {
        "date": cache_date_dir,
        "downloaded_at": datetime.now().isoformat(),
        "total_tickers": len(tickers),
        "success": success,
        "errors": errors,
        "elapsed_seconds": elapsed,
    }
    manifest_path = os.path.join(cache_dir, "_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # Clean old caches
    if args.keep_days > 0:
        clean_old_caches(cache_date_dir, args.keep_days)

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
