#!/usr/bin/env python3
"""
Crypto TA — Full-pipeline 기술적 분석 for cryptocurrencies.

Reuses the margin-ta 6-layer pipeline with crypto-native market regime
(Fear & Greed + BTC Dominance) instead of VIX/Breadth.

Usage:
  python scripts/crypto_ta.py BTC-USD --save --chart

Supported symbols: Any yfinance crypto pair (BTC-USD, ETH-USD, SOL-USD, etc.)
"""
import sys
import os
import argparse
from datetime import datetime

# Add script directory to path for layer module imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layer1_data import fetch_yfinance_data, fetch_tradingview_data
from layer2_indicators import compute_all_indicators, compute_derived
from layer3_liquidity import build_liquidity_package, liquidity_confluence, liquidity_levels
from layer3_signals import (
    find_horizontal_sr,
    find_dynamic_sr,
    find_fibonacci_levels,
    calculate_entry_score,
    compile_levels,
)
from layer4_pricing import (
    determine_entry_strategies,
    calculate_targets,
    calculate_risk_reward,
)
from layer5_output import print_margin_analysis, generate_chart_png, generate_tradingview_link
from layer1_crypto_market import (
    fetch_fear_greed,
    fetch_btc_dominance,
    combine_crypto_regimes,
)


# ═══════════════════════════════════════════════════════
# TradingView symbol mapping for crypto
# ═══════════════════════════════════════════════════════
CRYPTO_TV_MAP = {
    "BTC-USD": "BTCUSD",
    "ETH-USD": "ETHUSD",
    "SOL-USD": "SOLUSD",
    "XRP-USD": "XRPUSD",
    "DOGE-USD": "DOGEUSD",
    "ADA-USD": "ADAUSD",
    "AVAX-USD": "AVAXUSD",
    "DOT-USD": "DOTUSD",
    "LINK-USD": "LINKUSD",
    "MATIC-USD": "MATICUSD",
}


def _tv_symbol(yahoo_symbol: str) -> str:
    """Map yfinance symbol to TradingView symbol for crypto."""
    return CRYPTO_TV_MAP.get(yahoo_symbol.upper(), yahoo_symbol.replace("-", ""))


def main():
    parser = argparse.ArgumentParser(
        description="Crypto TA — 풀파이프라인 암호화폐 기술적 분석",
    )
    parser.add_argument("symbol", type=str, help="Ticker symbol (e.g. BTC-USD, ETH-USD)")
    parser.add_argument("--save", action="store_true", help="결과를 JSON 파일로 저장")
    parser.add_argument("--chart", action="store_true", help="차트 PNG 생성")
    parser.add_argument("--quiet", action="store_true", help="콘솔 출력 억제 (배치 모드)")
    parser.add_argument("--json", action="store_true", help="결과 JSON을 stdout으로 출력")
    parser.add_argument("--no-tv", action="store_true", help="TradingView 교차검증 스킵")
    parser.add_argument("--no-market", action="store_true", help="Fear & Greed / BTC.D 조회 스킵")
    args = parser.parse_args()

    symbol = args.symbol.upper().strip()

    # Validate symbol format
    if "-" not in symbol:
        print(f"⚠️  '{symbol}' → '{symbol}-USD'로 자동 변환합니다.")
        symbol = f"{symbol}-USD"

    from paths import data_dir as _data_dir
    data_dir = _data_dir()
    from paths import charts_dir as _charts_dir
    chart_dir = _charts_dir()
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(chart_dir, exist_ok=True)

    if not args.quiet:
        print(f"\n  🔍 {symbol} 분석 시작...\n")

    # ═══════════════════════════════════════════════════════
    # LAYER 1: DATA
    # ═══════════════════════════════════════════════════════
    if not args.quiet:
        print("  [1/5] 데이터 수집 중...")

    data = fetch_yfinance_data(symbol)
    df = data.get("df")
    info = data.get("info_summary", {})
    warnings = data.get("warnings", [])

    if df is None or df.empty:
        print(f"  ❌ {symbol}: 데이터 없음 (yfinance 조회 실패)")
        if data.get("warnings"):
            for w in data["warnings"]:
                print(f"     ⚠️  {w}")
        sys.exit(1)

    # Regular close (last daily candle close)
    regular_close = float(df["Close"].iloc[-1])

    # Current price: use regular close (no session quote needed for 24/7 crypto)
    current_price = regular_close

    if not args.quiet:
        print(f"    ✅ {len(df)} 거래일 (기준가: ${current_price:,.2f})")

    # TradingView cross-check
    tv_data = {}
    tv_symbol = _tv_symbol(symbol)
    if not args.no_tv:
        try:
            tv_data = fetch_tradingview_data(tv_symbol, exchange="")
            if not tv_data.get("error"):
                if not args.quiet:
                    print(f"    ✅ TradingView 데이터 확보")
            else:
                warnings.append(f"TradingView: {tv_data.get('error')}")
        except Exception as e:
            warnings.append(f"TradingView 조회 실패: {e}")

    # Market regime: Fear & Greed + BTC Dominance
    market_regime = {}
    market_breadth = {}
    combined_market = {}

    if not args.no_market:
        try:
            fg_cache = os.path.join(data_dir, "fear_greed_cache.json")
            market_regime = fetch_fear_greed(cache_path=fg_cache)
            btc_dom_cache = os.path.join(data_dir, "btc_dominance_cache.json")
            market_breadth = fetch_btc_dominance(cache_path=btc_dom_cache)
            combined_market = combine_crypto_regimes(market_regime, market_breadth)
        except Exception as e:
            warnings.append(f"Crypto market regime 조회 실패: {e}")

    # ═══════════════════════════════════════════════════════
    # LAYER 2: INDICATORS
    # ═══════════════════════════════════════════════════════
    if not args.quiet:
        print("  [2/5] 기술적 지표 계산 중...")

    try:
        df = compute_all_indicators(df)
        derived = compute_derived(df)
        if not args.quiet:
            print("    ✅ ta(bukosabino) + TA-Lib 지표 계산 완료")
    except Exception as e:
        warnings.append(f"지표 계산 실패: {e}")
        derived = {}
        if not args.quiet:
            print(f"    ⚠️  지표 계산 일부 실패: {e}")

    # ═══════════════════════════════════════════════════════
    # LAYER 3: SIGNALS
    # ═══════════════════════════════════════════════════════
    if not args.quiet:
        print("  [3/5] 지지/저항 탐지 & 진입 신호 분석 중...")

    entry_score = {"score": 0, "verdict": "분석 불가", "details": []}
    all_levels = {"supports": [], "resistances": [], "current_price": current_price}
    fib_data = {"levels": {}, "confluence": []}
    liquidity = {}

    try:
        horz_supports, horz_resistances = find_horizontal_sr(df, window=5, wick_ratio=1.5)
        dynamic_levels = find_dynamic_sr(df, current_price)
        fib_data = find_fibonacci_levels(df, current_price, lookback_days=252)

        # Base S/R levels
        base_levels = compile_levels(
            horz_supports, horz_resistances, dynamic_levels, fib_data, current_price
        )

        # Liquidity: AVWAP + Volume Profile
        liquidity = build_liquidity_package(df, current_price)
        liquidity["confluence"] = liquidity_confluence(liquidity, base_levels, current_price)

        # 통합 S/R
        all_levels = compile_levels(
            horz_supports,
            horz_resistances,
            dynamic_levels,
            fib_data,
            current_price,
            extra_levels=liquidity_levels(liquidity),
        )

        # Entry Score
        entry_score = calculate_entry_score(
            df,
            current_price,
            all_levels["supports"],
            liquidity=liquidity,
            market={
                "vix": market_regime,
                "breadth": market_breadth,
                "combined": combined_market,
            },
            flow=None,
            tradingview=tv_data,
            fib_data=fib_data,
        )

        if not args.quiet:
            print(f"    ✅ Entry Score: {entry_score['score']}/100 {entry_score['verdict']}")

    except Exception as e:
        warnings.append(f"신호 분석 실패: {e}")
        if not args.quiet:
            print(f"    ❌ 신호 분석 실패: {e}")

    # ═══════════════════════════════════════════════════════
    # LAYER 4: PRICING
    # ═══════════════════════════════════════════════════════
    if not args.quiet:
        print("  [4/5] 진입 전략 & 가격 산출 중...")

    try:
        strategies = determine_entry_strategies(
            current_price,
            entry_score["score"],
            all_levels["supports"],
            all_levels["resistances"],
            df,
            market_regime=combined_market,
        )
        targets = calculate_targets(
            current_price,
            all_levels["resistances"],
            df,
        )
        for s in strategies:
            s["targets_rr"] = calculate_risk_reward(current_price, s, targets)

        if not args.quiet:
            print(f"    ✅ {len(strategies)}개 진입 전략 산출")
    except Exception as e:
        strategies = []
        targets = []
        warnings.append(f"진입 전략 산출 실패: {e}")
        if not args.quiet:
            print(f"    ❌ 진입 전략 산출 실패: {e}")

    # ═══════════════════════════════════════════════════════
    # LAYER 5: OUTPUT
    # ═══════════════════════════════════════════════════════
    if not args.quiet:
        print("  [5/5] 결과 출력 중...\n")

    # Get exchange from info or default to CRYPTO
    exchange = info.get("exchange", "CRYPTO")
    if exchange in ("NMS", "NGM", "NCM"):
        exchange = "NASDAQ"

    # Build result data for console output
    result_data = {
        "info": {
            "symbol": symbol,
            "name": info.get("shortName") or info.get("longName", symbol),
            "sector": "Cryptocurrency",
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta"),
            "exchange": exchange,
            "regular_close": regular_close,
            "previous_close": regular_close,
        },
        "current_price": current_price,
        "prices": {
            "current": current_price,
            "regular_close": regular_close,
            "atr": None,
            "atr_pct": None,
            "source": "yfinance_daily_close",
        },
        "fibonacci": fib_data,
        "liquidity": liquidity,
        "market": {
            "vix": market_regime,
            "breadth": market_breadth,
            "combined": combined_market,
        },
        "levels": all_levels,
        "signals": {
            "entry_score": entry_score,
            "all_levels": all_levels,
            "supports": all_levels["supports"],
            "resistances": all_levels["resistances"],
        },
        "pricing": {
            "strategies": strategies,
            "targets": targets,
        },
        "tradingview": tv_data,
        "session_quote": {},
        "derived": derived,
        "warnings": warnings,
    }

    # Compute ATR
    try:
        if "ATRr_14" in df.columns:
            atr_val = float(df["ATRr_14"].iloc[-1])
            result_data["prices"]["atr"] = round(atr_val, 2)
            result_data["prices"]["atr_pct"] = round(atr_val / current_price * 100, 2)
    except Exception:
        pass

    if not args.quiet:
        print_margin_analysis(result_data)

    # TradingView link
    tv_link = f"https://www.tradingview.com/chart/?symbol={tv_symbol}"
    if not args.quiet:
        print(f"  🔗 TradingView: {tv_link}")

    # Chart PNG
    chart_path = None
    if args.chart:
        chart_path = generate_chart_png(
            df, symbol, current_price, all_levels, chart_dir, fib_data=fib_data
        )
        if chart_path:
            print(f"  📈 차트 저장: {chart_path}")
            print(f"  MEDIA:{chart_path}")

    # ═══════════════════════════════════════════════════════
    # SAVE
    # ═══════════════════════════════════════════════════════
    if args.save:
        import json as _json

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(data_dir, f"{symbol}_{ts}.json")
        # Clean non-serializable data
        save_data = {
            "symbol": symbol,
            "analyzed_at": datetime.now().isoformat(),
            "current_price": current_price,
            "entry_score": entry_score["score"],
            "verdict": entry_score.get("verdict", ""),
            "signal_details": [
                {"name": name, "points": pts}
                for name, pts in entry_score.get("details", [])
            ],
            "levels": {
                "supports": all_levels["supports"][:5],
                "resistances": all_levels["resistances"][:5],
            },
            "fibonacci_confluence": fib_data.get("confluence", []),
            "strategies": [{"name": s.get("strategy", ""), "entry": s.get("entry"),
                          "stop": s.get("stop"), "condition": s.get("condition", "")}
                         for s in strategies],
            "targets": targets,
            "fear_greed": {
                "value": market_regime.get("value"),
                "classification": market_regime.get("classification"),
            },
            "btc_dominance": market_breadth.get("btc_dominance_pct"),
            "warnings": warnings,
        }
        with open(save_path, "w") as f:
            _json.dump(save_data, f, indent=2, default=str)
        if not args.quiet:
            print(f"  💾 JSON 저장: {save_path}")

    # JSON stdout (for piping/scripts)
    if args.json:
        import json as _json
        print(_json.dumps({
            "symbol": symbol,
            "price": current_price,
            "entry_score": entry_score["score"],
            "verdict": entry_score.get("verdict", ""),
            "strategies": len(strategies),
            "targets": [{"level": t["level"], "price": t["price"]} for t in targets],
            "warnings": warnings,
        }, indent=2, default=str))

    if not args.quiet:
        print(f"\n  ✅ {symbol} 분석 완료")


if __name__ == "__main__":
    main()
