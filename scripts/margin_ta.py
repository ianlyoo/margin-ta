#!/usr/bin/env python3
"""
Margin TA — 기술적 분석 기반 미수 진입 추천가
Usage: margin_ta.py SYMBOL [--save] [--chart]

미국주식 매집 결정 후 진입 타이밍을 위한 기술적 분석 도구.
6-Layer Pipeline: Data → Indicators → Signals → Pricing → Output
"""
import sys
import os
import argparse
from datetime import datetime

# 스크립트 디렉토리를 path에 추가 (같은 디렉토리의 layer 모듈 import용)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layer1_data import fetch_ohlcv_data, fetch_tradingview_data, resolve_yfinance_candidates
from layer1_flow import fetch_external_flow
from layer1_kr_market import fetch_korea_market_regime
from layer1_market import combine_market_regimes, fetch_vix_regime, load_market_breadth
from layer1_options import fetch_options_analysis, legacy_flow_options
from layer1_ownership import fetch_beneficial_ownership
from layer1_session import fetch_session_quote
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
    build_entry_plans,
    calculate_targets,
    calculate_risk_reward,
)
from layer5_output import print_margin_analysis, generate_chart_png, generate_tradingview_link, format_discord_output
from market_risk import build_market_risk


def _fmt_price(value: float, currency: str = "USD") -> str:
    currency = str(currency or "USD").upper()
    if not isinstance(value, (int, float)):
        return "N/A"
    if currency == "KRW":
        return f"₩{value:,.0f}"
    if currency == "JPY":
        return f"¥{value:,.0f}"
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}"


def load_from_ohlcv_cache(symbol: str, cache_dir: str) -> dict:
    """OHLCV 캐시 JSON에서 데이터를 로드하여 fetch_yfinance_data()와 동일한 형식으로 반환"""
    import json as _json
    import pandas as _pd

    cache_file = os.path.join(cache_dir, f"{symbol}.json")
    if not os.path.exists(cache_file):
        return {"df": None, "info_summary": {}, "warnings": [f"캐시 없음: {cache_file}"]}

    with open(cache_file) as f:
        raw = _json.load(f)

    ohlcv = raw.get("ohlcv", [])
    if not ohlcv:
        return {"df": None, "info_summary": {}, "warnings": ["캐시에 OHLCV 데이터 없음"]}

    # records → DataFrame
    records = []
    for r in ohlcv:
        records.append({
            "Date": r["date"],
            "Open": r["open"],
            "High": r["high"],
            "Low": r["low"],
            "Close": r["close"],
            "Volume": r["volume"],
        })
    df = _pd.DataFrame(records)
    df["Date"] = _pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    # yfinance는 timezone-aware UTC index → 변환
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    info = raw.get("info_summary", {})
    # exchange 정규화 (yfinance는 "NMS", "NYQ" 등으로 반환)
    raw_exchange = info.get("exchange", "").upper()
    if "NASDAQ" in raw_exchange or raw_exchange in ("NMS", "NCM", "NGM"):
        info["exchange"] = "NASDAQ"
    elif "NYSE" in raw_exchange or raw_exchange in ("NYQ", "NYS", "ASE", "PCX"):
        info["exchange"] = "NYSE"
    elif "BATS" in raw_exchange or raw_exchange in ("BTS", "BTZ"):
        info["exchange"] = "NYSE"  # BATS → NYSE로 취급
    elif not info.get("exchange"):
        info["exchange"] = "NASDAQ"

    return {
        "df": df,
        "info_summary": info,
        "warnings": raw.get("warnings", []),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Margin TA — 기술적 분석 미수 진입 추천가",
    )
    parser.add_argument("symbol", type=str, help="Ticker symbol (e.g. AAPL, 005930, 005930.KS)")
    parser.add_argument("--market", choices=["auto", "us", "kr"], default="auto", help="시장 선택. auto는 6자리 숫자/.KS/.KQ를 한국 종목으로 판별")
    parser.add_argument("--save", action="store_true", help="결과를 JSON 파일로 저장")
    parser.add_argument("--chart", action="store_true", help="차트 PNG 생성")
    parser.add_argument("--quiet", action="store_true", help="콘솔 출력 억제 (배치 모드)")
    parser.add_argument("--json", action="store_true", help="결과 JSON을 stdout으로 출력")
    parser.add_argument("--discord", action="store_true", help="Discord 친화적 텍스트 포맷으로 stdout 출력")
    parser.add_argument("--no-tv", action="store_true", help="TradingView 교차검증 스킵")
    parser.add_argument("--no-market", action="store_true", help="VIX/Breadth 시장 레짐 조회 스킵")
    parser.add_argument("--flow", action="store_true", help="DarkFina/ChartExchange 외부 다크풀·옵션·공매도 플로우 조회")
    parser.add_argument("--flow-source", choices=["darkfina", "chartexchange"], default="darkfina", help="외부 플로우 데이터 소스")
    parser.add_argument("--no-options", action="store_true", help="옵션 체인/옵션맵 분석 비활성화")
    parser.add_argument(
        "--options-provider",
        choices=["auto", "public", "darkfina", "yfinance", "alphavantage", "tradier", "polygon", "unusualwhales"],
        default="auto",
        help="옵션 데이터 제공자. 기본 auto: 공개 소스 + 키 존재 시 보강",
    )
    parser.add_argument("--options-cache-dir", type=str, default=None, help="옵션 OI snapshot/cache 디렉토리")
    parser.add_argument("--options-max-expiries", type=int, default=5, help="분석할 옵션 만기 후보 수")
    parser.add_argument("--options-timeout", type=int, default=8, help="옵션 소스별 네트워크 timeout 초")
    parser.add_argument("--ownership", action="store_true", help="SEC EDGAR 13D/G 대량보유자 변동 조회")
    parser.add_argument("--ownership-lookback-days", type=int, default=365, help="13D/G 이벤트 조회 기간. 기본 365일")
    parser.add_argument("--no-session-quote", action="store_true", help="데이마켓/프리마켓 세션 현재가 조회 스킵")
    parser.add_argument("--session-provider", choices=["auto", "kis", "toss", "yfinance"], default="auto", help="세션 현재가 제공자. toss: 토스 단독, auto: 데이마켓 KIS, 프리/애프터 yfinance")
    parser.add_argument("--ohlcv-provider", choices=["auto", "toss", "pykrx", "yfinance"], default="auto", help="OHLCV 제공자. toss: 토스 우선, auto: toss→pykrx→yfinance fallback")
    parser.add_argument("--ohlcv-cache-dir", type=str, default=None,
                        help="OHLCV 캐시 디렉토리 (provider 호출 대신 캐시에서 데이터 로드)")
    args = parser.parse_args()

    symbol = args.symbol.upper().strip()
    
    # Quiet mode: suppress all print() for batch execution
    if args.quiet:
        import builtins
        builtins.print = lambda *a, **kw: None
    
    print(f"\n  🔍 {symbol} 분석 시작...\n")

    # ─── LAYER 1: Data ───
    print("  [1/5] 데이터 수집 중...")
    if args.ohlcv_cache_dir:
        # 캐시 디렉토리에서 OHLCV 로드
        cache_date_dir = args.ohlcv_cache_dir.rstrip("/")
        data = load_from_ohlcv_cache(symbol, cache_date_dir)
        if data["df"] is None or data["df"].empty:
            # 캐시 미스 → provider fallback
            print(f"    ⚠️  캐시 미스, provider fallback...")
            data = fetch_ohlcv_data(symbol, market=args.market, provider=args.ohlcv_provider)
        else:
            print(f"    ✅ 캐시에서 로드 ({len(data['df'])} 거래일)")
    else:
        data = fetch_ohlcv_data(symbol, market=args.market, provider=args.ohlcv_provider)
    if data["df"] is None or data["df"].empty:
        print(f"  ❌ {symbol} 데이터를 가져올 수 없습니다. 티커를 확인해주세요.")
        if data["warnings"]:
            for w in data["warnings"]:
                print(f"    ⚠️  {w}")
        return 1

    df = data["df"]
    data["info_summary"].setdefault("symbol", symbol)
    data["info_summary"].setdefault("name", symbol)
    inferred_market = resolve_yfinance_candidates(symbol, market=args.market)["market"]
    if inferred_market == "KR":
        data["info_summary"].setdefault("market", "KR")
        data["info_summary"].setdefault("currency", "KRW")
        data["info_summary"].setdefault("exchange", "KRX")
    else:
        data["info_summary"].setdefault("market", "US")
        data["info_summary"].setdefault("currency", "USD")
    symbol = str(data["info_summary"].get("symbol") or symbol).upper().strip()
    market_type = str(data["info_summary"].get("market") or "US").upper()
    currency = str(data["info_summary"].get("currency") or "USD").upper()
    is_korean = market_type in {"KR", "KOR", "KOREA"}
    regular_close = float(df.Close.iloc[-1])
    current_price = regular_close
    data["info_summary"]["regular_close"] = regular_close
    data["info_summary"]["previous_close"] = current_price  # 출력 호환용: 분석 기준 현재가
    print(f"    ✅ {len(df)} 거래일 (정규장 기준가: {_fmt_price(regular_close, currency)})")

    exchange = data["info_summary"].get("exchange", "NASDAQ")
    session_quote = fetch_session_quote(
        symbol,
        exchange=exchange,
        regular_close=regular_close,
        provider="off" if args.no_session_quote else args.session_provider,
        market=market_type,
    )
    for w in session_quote.get("warnings", []):
        data["warnings"].append(w)
    if session_quote.get("active_price") and session_quote.get("active_source") != "yfinance_daily":
        current_price = float(session_quote["active_price"])
        data["info_summary"]["previous_close"] = current_price
        if not args.quiet:
            print(
                f"    ✅ 세션 현재가 반영: {_fmt_price(current_price, currency)} "
                f"({session_quote.get('active_source')}, {session_quote.get('session', {}).get('session')})"
            )

    # TradingView 교차검증
    tv_data = None
    if not args.no_tv:
        try:
            tv_data = fetch_tradingview_data(
                data["info_summary"].get("tv_symbol") or symbol,
                exchange,
                screener=data["info_summary"].get("tv_screener"),
            )
            if tv_data.get("error"):
                if not args.quiet:
                    print(f"    ⚠️  TradingView: {tv_data['error']}")
            else:
                if not args.quiet:
                    print(f"    ✅ TradingView 데이터 확보")
        except Exception as e:
            if not args.quiet:
                print(f"    ⚠️  TradingView 연결 실패: {e}")

    # VIX market regime
    market_regime = None
    market_breadth = None
    combined_market = None
    if not args.no_market:
        if is_korean:
            if not args.quiet:
                print("    한국 시장 레짐 (KOSPI) 조회 중...")
            kr_market = fetch_korea_market_regime()
            market_regime = kr_market.get("vix")
            market_breadth = kr_market.get("breadth")
            combined_market = kr_market.get("combined")
            for w in kr_market.get("combined", {}).get("warnings", []):
                data["warnings"].append(w)
            if not args.quiet and combined_market.get("trend_regime"):
                print(f"    ✅ KOSPI {combined_market['trend_label']} / 변동성 {combined_market.get('volatility_20d', '?')}%")
        else:
            from paths import data_dir as _data_dir
            data_dir = _data_dir()
            cache_path = os.path.join(data_dir, "market_regime_cache.json")
            breadth_cache_path = os.path.join(data_dir, "market_breadth_cache.json")
            market_regime = fetch_vix_regime(cache_path=cache_path)
            market_breadth = load_market_breadth(breadth_cache_path)
            combined_market = combine_market_regimes(market_regime, market_breadth)
            for w in market_regime.get("warnings", []):
                data["warnings"].append(w)
            for w in market_breadth.get("warnings", []):
                data["warnings"].append(w)

    # 시장 위험 대시보드 (경량 — 캐시 사용, 실패해도 분석 계속)
    market_risk = None
    sector_risk_for_symbol = None
    if not args.no_market:
        try:
            from paths import data_dir as _data_dir
            data_dir_mr = _data_dir()
            market_risk = build_market_risk(
                include_kr=is_korean,
                cache_path=os.path.join(data_dir_mr, "market_risk_cache.json"),
                cache_only=True,
            )
            sector = str(data["info_summary"].get("sector") or "").lower()
            sector_map = {
                "technology": "XLK", "financial services": "XLF", "healthcare": "XLV",
                "energy": "XLE", "industrials": "XLI", "consumer cyclical": "XLY",
                "consumer defensive": "XLP", "utilities": "XLU", "basic materials": "XLB",
                "communication services": "XLC", "real estate": "XLRE",
            }
            etf = sector_map.get(sector)
            if etf and market_risk and etf in market_risk.get("sector_risk", {}):
                sector_risk_for_symbol = {"etf": etf, **market_risk["sector_risk"][etf]}
        except Exception as e:
            data["warnings"].append(f"시장 위험 계산 실패: {e}")

    flow_data = None
    if args.flow:
        if is_korean:
            data["warnings"].append("한국 종목은 미국 다크풀/공매도 외부 flow overlay를 건너뜀")
        else:
            flow_data = fetch_external_flow(
                symbol,
                price=current_price,
                exchange=exchange,
                source=args.flow_source,
                include_options=False,
            )
            for w in flow_data.get("warnings", []):
                data["warnings"].append(w)

    options_data = None

    ownership_data = None
    if args.ownership:
        if is_korean:
            data["warnings"].append("한국 종목은 SEC 13D/G ownership overlay를 건너뜀")
        else:
            if not args.quiet:
                print("    SEC 13D/G 대량보유자 변동 조회 중...")
            ownership_data = fetch_beneficial_ownership(symbol, lookback_days=args.ownership_lookback_days)
            for w in ownership_data.get("warnings", []):
                data["warnings"].append(w)
            if not args.quiet:
                if ownership_data.get("event_count"):
                    latest_owner = (ownership_data.get("latest_event") or {}).get("reporting_owner") or "holder"
                    print(f"    ✅ 13D/G 이벤트 {ownership_data['event_count']}건 ({latest_owner})")
                else:
                    print(f"    13D/G 최근 이벤트 없음 ({ownership_data.get('status', 'empty')})")

    # ─── LAYER 2: Indicators ───
    print("  [2/5] 기술적 지표 계산 중...")
    try:
        df = compute_all_indicators(df)
        derived = compute_derived(df)
        print(f"    ✅ 기술 지표 계산 완료")
    except Exception as e:
        print(f"    ⚠️  지표 계산 일부 실패: {e}")
        derived = {}

    # ─── LAYER 3: Signals ───
    print("  [3/5] 지지/저항 탐지 & 진입 신호 분석 중...")
    try:
        horz_supports, horz_resistances = find_horizontal_sr(df, window=5, wick_ratio=1.5)
        dynamic_levels = find_dynamic_sr(df, current_price)
        fib_data = find_fibonacci_levels(df, current_price, lookback_days=252)

        # Base S/R levels
        base_levels = compile_levels(
            horz_supports, horz_resistances, dynamic_levels, fib_data, current_price
        )

        # Liquidity levels: AVWAP + Volume Profile
        liquidity = build_liquidity_package(df, current_price)
        liquidity["confluence"] = liquidity_confluence(liquidity, base_levels, current_price)

        # 전체 S/R 레벨 통합
        all_levels = compile_levels(
            horz_supports,
            horz_resistances,
            dynamic_levels,
            fib_data,
            current_price,
            extra_levels=liquidity_levels(liquidity),
        )

        if is_korean and not args.no_options:
            data["warnings"].append("한국 종목은 미국 옵션 체인/옵션맵 overlay를 건너뜀")
        elif not args.no_options:
            if not args.quiet:
                print("    옵션 체인/옵션맵 분석 중...")
            options_cache_dir = args.options_cache_dir or os.path.join(
                __import__("paths").data_dir(), "options_cache"
            )
            try:
                options_data = fetch_options_analysis(
                    symbol,
                    price=current_price,
                    provider=args.options_provider,
                    max_expiries=args.options_max_expiries,
                    timeout=args.options_timeout,
                    cache_dir=options_cache_dir,
                    technical_levels=all_levels,
                    df=df,
                )
                for w in options_data.get("warnings", []):
                    data["warnings"].append(w)
                if flow_data is not None:
                    flow_data["options"] = legacy_flow_options(options_data)
            except Exception as e:
                options_data = {
                    "source": "none",
                    "provider_requested": args.options_provider,
                    "symbol": symbol,
                    "spot": current_price,
                    "selected_expiry": None,
                    "expiry_rankings": [],
                    "chain_quality": {"quality_status": "unavailable", "quality_score": 0},
                    "max_pain": None,
                    "put_call_ratios": {},
                    "walls": {},
                    "options_map": [],
                    "volatility": {},
                    "greeks_exposure": {"status": "unavailable"},
                    "score_overlay": {"enabled": False, "raw_score": 0, "clamped_score": 0, "details": []},
                    "warnings": [f"Options analysis failed: {e}"],
                }
                data["warnings"].extend(options_data["warnings"])
                if flow_data is not None:
                    flow_data["options"] = legacy_flow_options(options_data)

        # Entry Score
        entry_score = calculate_entry_score(
            df,
            current_price,
            all_levels["supports"],
            liquidity=liquidity,
            market={"vix": market_regime, "breadth": market_breadth, "combined": combined_market},
            options=options_data,
            flow=flow_data,
            ownership=ownership_data,
            tradingview=tv_data,
            fib_data=fib_data,
        )

        from layer3_consensus import add_horizon_conflict, build_consensus
        from layer3_horizons import build_horizons
        from layer3_signals import build_sr_tiers, find_monthly_swings, find_weekly_pivots, tier_levels
        from timeframes import resample_ohlcv

        contribs = entry_score.pop("contribs", [])
        consensus = build_consensus(contribs, df)
        horizons = build_horizons(df, consensus)
        add_horizon_conflict(consensus, horizons)
        for name in ("mid", "long"):
            if horizons[name].get("stance") == "insufficient_data":
                data["warnings"].append(f"{name} 호라이즌 데이터 부족 ({horizons[name].get('bars', 0)}봉) — 생략")

        df_weekly = resample_ohlcv(df, "W-FRI")
        df_monthly = resample_ohlcv(df, "ME")
        orig_supports = list(all_levels["supports"])
        orig_resistances = list(all_levels["resistances"])
        all_levels = tier_levels(
            all_levels,
            find_weekly_pivots(df_weekly),
            find_monthly_swings(df_monthly),
            df, current_price,
        )
        sr_tiers = build_sr_tiers(all_levels, current_price)

        print(f"    ✅ Entry Score: {entry_score['score']}/100 {entry_score['verdict']}")
    except Exception as e:
        print(f"    ❌ 신호 분석 실패: {e}")
        return 1

    # ─── LAYER 4: Pricing ───
    print("  [4/5] 진입 전략 & 가격 산출 중...")
    try:
        currency = str(data["info_summary"].get("currency") or "USD").upper()
        # v4.0: 트리거 기반 전략 엔진
        entry_plans = build_entry_plans(
            current_price,
            entry_score["score"],
            orig_supports,
            orig_resistances,
            df,
            market_regime=combined_market,
            ownership=ownership_data,
            options_data=options_data,
            currency=currency,
            consensus=consensus,
            major_supports=sr_tiers["major_supports"],
        )

        # 레거시 호환: strategies 리스트 생성
        strategies = determine_entry_strategies(
            current_price,
            entry_score["score"],
            orig_supports,
            orig_resistances,
            df,
            market_regime=combined_market,
            ownership=ownership_data,
            currency=currency,
        )
        targets = calculate_targets(current_price, orig_resistances, df)

        # 각 전략별 R:R 계산
        for s in strategies:
            if s.get("entry") and s.get("stop"):
                s["targets_rr"] = calculate_risk_reward(s["entry"], s["stop"], targets)

        active = len([s for s in strategies if "Wait" not in s.get("type", "")])
        print(f"    ✅ {active}개 진입 전략 산출")
    except Exception as e:
        print(f"    ❌ 가격 산출 실패: {e}")
        return 1

    # ─── LAYER 5: Output ───
    if not args.quiet:
        print("  [5/5] 결과 출력 중...\n")
    else:
        print("  [5/5] 결과 출력 중...\n")  # will be suppressed by quiet

    result = {
        "symbol": symbol,
        "current_price": current_price,
        "info": data["info_summary"],
        "signals": {
            "entry_score": entry_score,
            "all_levels": all_levels,
            "horizontal": {
                "supports": horz_supports[:5],
                "resistances": horz_resistances[:5],
            },
            "fibonacci": fib_data,
        },
        "liquidity": liquidity,
        "market": {
            "vix": market_regime,
            "breadth": market_breadth,
            "combined": combined_market,
            "risk": ({"score": market_risk["score"], "regime": market_risk["regime"],
                      "alerts": market_risk["alerts"]} if market_risk else None),
        },
        "session_quote": session_quote,
        "options": options_data,
        "flow": flow_data,
        "ownership": ownership_data,
        "pricing": {
            "strategies": strategies,
            "targets": targets,
            "entry_plans": entry_plans,
        },
        "derived": derived,
        "tradingview": tv_data,
        "warnings": data["warnings"],
        "horizons": horizons,
        "consensus": consensus,
        "sr_tiers": sr_tiers,
        "sector_risk_for_symbol": sector_risk_for_symbol,
    }

    # 콘솔 출력
    if not args.quiet:
        print_margin_analysis(result)

    # Chart PNG
    chart_path = None
    if args.chart:
        from paths import charts_dir as _charts_dir
        chart_dir = _charts_dir()
        chart_path = generate_chart_png(
            df,
            symbol,
            current_price,
            all_levels,
            chart_dir,
            fib_data=fib_data,
            options=options_data,
            currency=currency,
        )
        if chart_path:
            print(f"  📈 차트 저장: {chart_path}")
            print(f"  MEDIA:{chart_path}")

    # TradingView 링크
    tv_link = generate_tradingview_link(symbol, exchange)
    if not args.quiet:
        print(f"  🔗 TradingView: {tv_link}")

    # JSON 저장
    if args.save:
        import json

        from paths import data_dir as _data_dir
        data_dir = _data_dir()
        os.makedirs(data_dir, exist_ok=True)
        json_path = os.path.join(
            data_dir, f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        # datetime 변환
        import json as json_module

        class NpEncoder(json_module.JSONEncoder):
            def default(self, obj):
                if hasattr(obj, "isoformat"):
                    return obj.isoformat()
                if hasattr(obj, "item"):
                    return obj.item()
                return str(obj)

        with open(json_path, "w") as f:
            json_module.dump(result, f, cls=NpEncoder, indent=2)
        print(f"  💾 JSON 저장: {json_path}")

    print(f"\n  ✅ {symbol} 분석 완료\n")
    
    # JSON stdout 출력 (배치 모드용)
    if args.json:
        import json as json_module
        import sys
        class NpEncoder(json_module.JSONEncoder):
            def default(self, obj):
                if hasattr(obj, "isoformat"):
                    return obj.isoformat()
                if hasattr(obj, "item"):
                    return obj.item()
                return str(obj)
        sys.stdout.write(json_module.dumps(result, cls=NpEncoder, indent=2) + "\n")

    # Discord 포맷 stdout 출력
    if args.discord:
        import sys
        sys.stdout.write(format_discord_output(result) + "\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
