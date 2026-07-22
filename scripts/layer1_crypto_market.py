"""
Layer 1b — Crypto Market Regime: Fear & Greed Index + BTC Dominance.

Replaces VIX and Market Breadth from stock margin-ta with crypto-native
market health indicators.

- Fear & Greed Index: alternative.me API (free, no key)
- BTC Dominance: yfinance TOTAL2 (total crypto market cap ex-BTC)

Results are cached to avoid rate limiting during batch analysis.
"""
import json
import os
from datetime import datetime, timedelta

import pandas as pd


from paths import data_dir as _data_dir
_CACHE_DIR = _data_dir()


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


def _classify_fear_greed(value: int, trend: str) -> tuple[str, str, float]:
    """
    Fear & Greed Index classification (0-100).
    0-25: Extreme Fear → contrarian buy opportunity
    25-45: Fear → cautious
    45-55: Neutral
    55-75: Greed → trend continuation likely
    75-100: Extreme Greed → overbought, pullback risk
    """
    if value <= 20:
        # Extreme Fear: historically a good buying zone for BTC
        return "extreme_fear", "risk_on", 1.15
    if value <= 40:
        return "fear", "accumulate", 1.05
    if value <= 60:
        return "neutral", "normal", 1.00
    if value <= 80:
        return "greed", "reduce_risk", 0.85
    # Extreme Greed: overbought, high pullback risk
    return "extreme_greed", "defensive", 0.65


def fetch_fear_greed(cache_path: str | None = None, max_age_hours: int = 4) -> dict:
    """
    Fetch Crypto Fear & Greed Index from alternative.me.

    Returns dict compatible with the market regime interface.
    """
    cached = _load_cache(cache_path, max_age_hours) if cache_path else None
    if cached:
        return cached

    result = {
        "source": "alternative.me/fng",
        "value": None,
        "classification": "unavailable",
        "regime": "unavailable",
        "trend": "unknown",
        "risk_mode": "normal",
        "size_multiplier": 1.0,
        "warnings": [],
    }

    try:
        import urllib.request

        url = "https://api.alternative.me/fng/?limit=2"
        req = urllib.request.Request(url, headers={"User-Agent": "margin-ta-crypto/3.2"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())

        data = body.get("data", [])
        if not data:
            result["warnings"].append("Fear & Greed 데이터 없음")
            return result

        current = data[0]
        value = int(current.get("value", 50))
        classification = current.get("value_classification", "Neutral")

        # Trend from yesterday
        trend = "flat"
        if len(data) >= 2:
            prev_value = int(data[1].get("value", value))
            if value > prev_value + 2:
                trend = "rising"
            elif value < prev_value - 2:
                trend = "falling"

        regime, risk_mode, size_multiplier = _classify_fear_greed(value, trend)

        result.update({
            "value": value,
            "classification": classification,
            "regime": regime,
            "trend": trend,
            "risk_mode": risk_mode,
            "size_multiplier": size_multiplier,
            "timestamp": current.get("timestamp", ""),
        })
        _save_cache(cache_path, result)
        return result

    except Exception as e:
        result["warnings"].append(f"Fear & Greed 조회 실패: {e}")
        return result


def fetch_btc_dominance(cache_path: str | None = None, max_age_hours: int = 8) -> dict:
    """
    Estimate BTC Dominance using yfinance (BTC market cap / total crypto cap).

    Uses TOTAL2 (total market cap excluding BTC) from TradingView via yfinance
    to compute BTC dominance = BTC_CAP / (BTC_CAP + ALT_CAP).

    Returns dict compatible with the market regime interface (replaces breadth).
    """
    cached = _load_cache(cache_path, max_age_hours) if cache_path else None
    if cached:
        return cached

    result = {
        "source": "yfinance/BTC-USD+TOTAL2",
        "btc_dominance_pct": None,
        "regime": "unavailable",
        "risk_mode": "normal",
        "size_multiplier": 1.0,
        "trend": "unknown",
        "warnings": [],
    }

    try:
        import yfinance as yf

        # Try to get BTC market cap directly
        btc = yf.Ticker("BTC-USD")
        btc_info = btc.info or {}
        btc_cap = float(btc_info.get("marketCap", 0))

        # Fallback: estimate from price and known supply (~19.5M BTC)
        if btc_cap <= 0:
            btc_df = btc.history(period="5d", interval="1d")
            if not btc_df.empty:
                btc_price = float(btc_df["Close"].iloc[-1])
                btc_cap = btc_price * 19_500_000

        if btc_cap <= 0:
            result["warnings"].append("BTC 시가총액 계산 불가")
            return result

        # TOTAL2: try info first, then history
        total2_cap = 0
        try:
            total2 = yf.Ticker("TOTAL2")
            total2_info = total2.info or {}
            total2_cap = float(total2_info.get("marketCap", 0))
        except Exception:
            pass

        if total2_cap <= 0:
            # Fallback: use rough estimate from TOTAL crypto market cap
            # TOTAL = broader index, use if TOTAL2 unavailable
            try:
                total = yf.Ticker("TOTAL")
                total_info = total.info or {}
                total_mcap = float(total_info.get("marketCap", 0))
                if total_mcap > btc_cap:
                    total2_cap = total_mcap - btc_cap
            except Exception:
                pass

        if total2_cap <= 0:
            result["warnings"].append("TOTAL2 알트코인 시총 계산 불가 — BTC Dominance 스킵")
            result["regime"] = "neutral"
            result["risk_mode"] = "normal"
            result["size_multiplier"] = 1.0
            _save_cache(cache_path, result)
            return result

        if btc_cap > 0 and total2_cap > 0:
            total_cap = btc_cap + total2_cap
            dominance = round((btc_cap / total_cap) * 100, 1)
        else:
            result["warnings"].append("BTC Dominance 계산 불가")
            return result

        # Classify dominance regime
        if dominance >= 65:
            regime, risk_mode, multiplier = "btc_dominant", "reduce_risk_alt", 0.80
        elif dominance >= 55:
            regime, risk_mode, multiplier = "btc_strong", "normal_btc_favored", 0.95
        elif dominance >= 45:
            regime, risk_mode, multiplier = "balanced", "normal", 1.00
        elif dominance >= 40:
            regime, risk_mode, multiplier = "alt_season_early", "risk_on_alt", 1.05
        else:
            regime, risk_mode, multiplier = "alt_season", "risk_on_alt", 1.10

        result.update({
            "btc_dominance_pct": dominance,
            "regime": regime,
            "risk_mode": risk_mode,
            "size_multiplier": multiplier,
        })
        _save_cache(cache_path, result)
        return result

    except Exception as e:
        result["warnings"].append(f"BTC Dominance 조회 실패: {e}")
        return result


def combine_crypto_regimes(fear_greed: dict | None, btc_dom: dict | None) -> dict:
    """Create one conservative sizing filter from Fear & Greed and BTC Dominance."""
    fg = fear_greed or {}
    bd = btc_dom or {}
    fg_mult = float(fg.get("size_multiplier", 1.0) or 1.0)
    bd_mult = float(bd.get("size_multiplier", 1.0) or 1.0)
    multiplier = min(fg_mult, bd_mult)

    if multiplier < 0.7:
        risk_mode = "defensive"
    elif multiplier < 1.0:
        risk_mode = "reduce_risk"
    elif multiplier > 1.0:
        risk_mode = "risk_on"
    else:
        risk_mode = "normal"

    return {
        "regime": f"F&G:{fg.get('regime', 'n/a')} / BTC.D:{bd.get('regime', 'n/a')}",
        "trend": f"F&G:{fg.get('trend', 'n/a')}",
        "risk_mode": risk_mode,
        "size_multiplier": multiplier,
    }
