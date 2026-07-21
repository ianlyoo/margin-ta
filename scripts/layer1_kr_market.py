"""
Layer 1b-KR — Korea Market Regime: KOSPI trend + volatility overlay.

Uses yfinance only (zero additional API keys).  Mirrors the US VIX/Breadth
interface so margin_ta.py and layer3_signals.py can consume it transparently.
"""
import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


KOSPI_SYMBOL = "^KS11"
KOSDAQ_SYMBOL = "^KQ11"
CACHE_DIR_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "data")
CACHE_MAX_AGE_HOURS = 4


def _load_cache(cache_path: str, max_age_hours: int) -> dict | None:
    if not cache_path or not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path) as f:
            payload = json.load(f)
        fetched_at = datetime.fromisoformat(payload.get("fetched_at", ""))
        if datetime.now() - fetched_at <= timedelta(hours=max_age_hours):
            return payload.get("data")
    except Exception:
        return None
    return None


def _save_cache(cache_path: str, data: dict) -> None:
    if not cache_path:
        return
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({"fetched_at": datetime.now().isoformat(), "data": data}, f, indent=2)
    except Exception:
        pass


def _classify_vol(value: float) -> tuple[str, str, float]:
    """Classify annualized volatility regime → (regime, risk_mode, size_multiplier)."""
    if value >= 40:
        return "extreme", "defensive", 0.55
    if value >= 28:
        return "high", "reduce_risk", 0.70
    if value <= 15:
        return "low", "risk_on", 1.10
    return "normal", "normal", 1.00


def fetch_korea_market_regime(
    cache_path: str | None = None,
    max_age_hours: int = CACHE_MAX_AGE_HOURS,
) -> dict:
    """Fetch KOSPI daily data and return a compact Korean market regime package.

    Mirrors the US ``fetch_vix_regime`` / ``combine_market_regimes`` shape so
    the existing score overlay functions consume it without changes.

    Returns
    -------
    dict
        Keys: ``vix`` (mirror), ``breadth`` (mirror), ``combined`` (mirror).
        Each sub-dict carries the same top-level keys as the US equivalent
        (value, regime, trend, risk_mode, size_multiplier, warnings, …).
    """
    cache_path = cache_path or os.path.join(CACHE_DIR_DEFAULT, "kr_market_cache.json")
    cached = _load_cache(cache_path, max_age_hours)
    if cached:
        return cached

    # ── default / unavailable skeleton ──────────────────────────────
    regime_default: dict = {
        "symbol": KOSPI_SYMBOL,
        "value": None,
        "regime": "unavailable",
        "trend": "unknown",
        "risk_mode": "normal",
        "size_multiplier": 1.0,
        "warnings": [],
    }
    result: dict = {
        "vix": {**regime_default, "symbol": KOSPI_SYMBOL},
        "breadth": {**regime_default, "symbol": KOSPI_SYMBOL},
        "combined": {**regime_default, "symbol": KOSPI_SYMBOL},
    }

    try:
        import yfinance as yf

        df = yf.Ticker(KOSPI_SYMBOL).history(period="1y", interval="1d")
        if df.empty or "Close" not in df:
            result["combined"]["warnings"].append("KOSPI 데이터 없음")
            return result

        close = df["Close"].dropna()
        if len(close) < 50:
            result["combined"]["warnings"].append("KOSPI 데이터 부족 (<50 거래일)")
            return result

        # ── trend regime: 50MA vs 200MA ───────────────────────────
        sma50 = close.rolling(50).mean().iloc[-1]
        sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
        sma20 = close.rolling(20).mean().iloc[-1]

        current = float(close.iloc[-1])
        ma50 = float(sma50) if pd.notna(sma50) else current
        ma200 = float(sma200) if sma200 is not None and pd.notna(sma200) else None
        ma20 = float(sma20) if pd.notna(sma20) else current

        if ma200 is not None:
            if ma50 > ma200 * 1.03:
                trend_regime = "strong_bull"
                trend_label = "강한 상승장"
            elif ma50 > ma200:
                trend_regime = "bull"
                trend_label = "상승장"
            elif ma50 < ma200 * 0.97:
                trend_regime = "strong_bear"
                trend_label = "강한 하락장"
            elif ma50 < ma200:
                trend_regime = "bear"
                trend_label = "하락장"
            else:
                trend_regime = "neutral"
                trend_label = "횡보"
        else:
            # Not enough data for 200MA
            if current > ma50 * 1.03:
                trend_regime = "bull"
                trend_label = "상승장 (50MA)"
            elif current < ma50 * 0.97:
                trend_regime = "bear"
                trend_label = "하락장 (50MA)"
            else:
                trend_regime = "neutral"
                trend_label = "횡보 (50MA)"

        # ── volatility regime ──────────────────────────────────────
        ret = close.pct_change().dropna()
        vol20 = float(ret.tail(20).std() * np.sqrt(252) * 100)
        vol60 = float(ret.tail(min(60, len(ret))).std() * np.sqrt(252) * 100)
        vol_regime, risk_mode, size_mult = _classify_vol(vol20)

        # ── trend direction (5d / 20d slope) ───────────────────────
        change_5d = float((current / close.iloc[-6] - 1) * 100) if len(close) >= 6 else 0
        change_20d = float((current / close.iloc[-21] - 1) * 100) if len(close) >= 21 else 0

        if change_5d < -5 or change_20d < -10:
            trend_direction = "falling"
        elif change_5d > 5 or change_20d > 10:
            trend_direction = "rising"
        else:
            trend_direction = "flat"

        # ── distance from 20MA ─────────────────────────────────────
        dist_20ma_pct = (current - ma20) / ma20 * 100

        # ── build result dicts (mirror US vix/breadth shape) ───────
        vix_payload: dict = {
            "symbol": KOSPI_SYMBOL,
            "value": round(current, 2),
            "regime": vol_regime,
            "trend": trend_direction,
            "risk_mode": risk_mode,
            "size_multiplier": size_mult,
            "label": f"KOSPI {current:,.0f} (vol {vol20:.0f}%)",
            "warnings": [],
        }

        breadth_payload: dict = {
            "symbol": KOSPI_SYMBOL,
            "value": round(current, 2),
            "regime": trend_regime,
            "trend": trend_label,
            "sma20": round(ma20, 2),
            "sma50": round(ma50, 2),
            "sma200": round(ma200, 2) if ma200 is not None else None,
            "dist_20ma_pct": round(dist_20ma_pct, 2),
            "change_5d_pct": round(change_5d, 2),
            "change_20d_pct": round(change_20d, 2),
            "warnings": [],
        }

        combined_payload: dict = {
            "symbol": KOSPI_SYMBOL,
            "value": round(current, 2),
            "regime": f"{trend_regime} / vol:{vol_regime}",
            "trend": trend_direction,
            "risk_mode": risk_mode,
            "size_multiplier": size_mult,
            "volatility_20d": round(vol20, 1),
            "volatility_60d": round(vol60, 1),
            "trend_regime": trend_regime,
            "trend_label": trend_label,
            "dist_20ma_pct": round(dist_20ma_pct, 2),
            "change_5d_pct": round(change_5d, 2),
            "change_20d_pct": round(change_20d, 2),
            "warnings": [],
        }

        result = {
            "vix": vix_payload,
            "breadth": breadth_payload,
            "combined": combined_payload,
        }

        _save_cache(cache_path, result)
        return result

    except Exception as e:
        result["combined"]["warnings"].append(f"KOSPI regime fetch error: {e}")
        return result
