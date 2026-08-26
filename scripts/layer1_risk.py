"""리스크 레이어 수집: yfinance 배치 + FRED best-effort. Spec #2 §1/§4."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import yfinance as yf
from config import BUFFETT_GDP_OVERRIDE, FRED_SERIES, RISK_TICKERS, SECTOR_UNIVERSE
from fred_client import fetch_fred_series
from layer1_market import load_market_breadth


def _history_close(symbol: str, period: str = "10y") -> pd.Series | None:
    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d")
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df:
        return None
    close = df["Close"].dropna()
    return close if len(close) else None


def download_closes(symbols: list[str], period: str = "10y") -> dict[str, pd.Series]:
    """yfinance 종가 배치. 실패 심볼은 조용히 제외."""
    out: dict[str, pd.Series] = {}
    for sym in symbols:
        s = _history_close(sym, period=period)
        if s is not None:
            out[sym] = s
    return out


def fetch_risk_data(cache_path: str | None = None, max_age_hours: int = 12,
                    include_kr: bool = True,
                    breadth_cache_path: str | None = None) -> dict:
    """시장/섹터 위험 원천 데이터 수집. 부분 실패는 unavailable로 흡수, 예외 없음."""
    unavailable: list[str] = []

    ticker_syms = list(RISK_TICKERS.values())
    raw = download_closes(ticker_syms)
    tickers = {name: raw[sym] for name, sym in RISK_TICKERS.items() if sym in raw}
    for name, sym in RISK_TICKERS.items():
        if sym not in raw:
            unavailable.append(f"ticker:{name}({sym})")

    sector_syms = SECTOR_UNIVERSE["us"] + SECTOR_UNIVERSE["semi"]
    if include_kr:
        sector_syms = sector_syms + SECTOR_UNIVERSE["kr"]
    sectors = download_closes(sector_syms)
    for sym in sector_syms:
        if sym not in sectors:
            unavailable.append(f"sector:{sym}")

    fred: dict[str, dict] = {}
    for key, series_id in FRED_SERIES.items():
        res = fetch_fred_series(series_id)
        fred[key] = {"last": res["last"], "error": res["error"]}
        if res["last"] is None:
            unavailable.append(f"fred:{key}")
    # 버핏지표 GDP 수동 오버라이드 (FRED egress 차단 대비)
    if fred.get("buffett_gdp", {}).get("last") is None and BUFFETT_GDP_OVERRIDE is not None:
        fred["buffett_gdp"] = {"last": BUFFETT_GDP_OVERRIDE, "error": None}
        if "fred:buffett_gdp" in unavailable:
            unavailable.remove("fred:buffett_gdp")

    breadth: dict | None = None
    if breadth_cache_path:
        try:
            breadth = load_market_breadth(breadth_cache_path, max_age_hours=168)
        except Exception:
            breadth = {"regime": "unavailable", "risk_mode": "normal",
                       "size_multiplier": 1.0, "warnings": ["breadth load failed"]}
    if isinstance(breadth, dict) and breadth.get("regime") == "unavailable":
        unavailable.append("breadth:stale_or_missing")

    return {
        "tickers": tickers,
        "sectors": sectors,
        "fred": fred,
        "breadth": breadth,
        "unavailable": unavailable,
        "as_of": datetime.now().isoformat(),
    }
