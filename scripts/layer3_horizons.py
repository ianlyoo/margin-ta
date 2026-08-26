"""호라이즌(일/주/월) 스탠스 산출. Spec #1 §1."""
from __future__ import annotations

import pandas as pd
from config import HORIZONS, STANCE_BEAR_MAX, STANCE_BULL_MIN
from ta.momentum import RSIIndicator
from ta.trend import MACD
from timeframes import last_bar_incomplete, resample_ohlcv


def _stance_of(score: int) -> str:
    if score >= STANCE_BULL_MIN:
        return "bullish"
    if score <= STANCE_BEAR_MAX:
        return "bearish"
    return "neutral"


def _mid_stance(w: pd.DataFrame) -> dict:
    """주봉: RSI14·MACD·SMA10/30/40·추세 기울기 기반 룰 점수 (-100~+100)."""
    close = w["Close"]
    sma10 = close.rolling(10).mean()
    sma30 = close.rolling(30).mean()
    sma40 = close.rolling(40).mean()
    rsi = RSIIndicator(close, window=14).rsi()
    macd_hist = MACD(close, window_slow=26, window_fast=12, window_sign=9).macd_diff()

    score, basis = 0, []
    last_close = close.iloc[-1]
    if not pd.isna(sma30.iloc[-1]):
        if last_close > sma30.iloc[-1]:
            score += 15; basis.append("주봉 종가 > SMA30")
        else:
            score -= 15; basis.append("주봉 종가 < SMA30")
    if not pd.isna(sma10.iloc[-1]) and not pd.isna(sma30.iloc[-1]):
        if sma10.iloc[-1] > sma30.iloc[-1]:
            score += 10; basis.append("주봉 SMA10 > SMA30 (정배열)")
        else:
            score -= 10; basis.append("주봉 SMA10 < SMA30 (역배열)")
    if len(sma30.dropna()) >= 11:
        slope_up = sma30.iloc[-1] > sma30.iloc[-11]
        score += 10 if slope_up else -10
        basis.append("주봉 SMA30 10주 기울기 " + ("상승" if slope_up else "하락"))
    if not pd.isna(macd_hist.iloc[-1]):
        if macd_hist.iloc[-1] > 0:
            score += 15; basis.append("주봉 MACD 히스토그램 양수")
        else:
            score -= 15; basis.append("주봉 MACD 히스토그램 음수")
    if not pd.isna(rsi.iloc[-1]):
        if rsi.iloc[-1] > 60:
            score += 10; basis.append(f"주봉 RSI 강세 ({rsi.iloc[-1]:.0f})")
        elif rsi.iloc[-1] < 40:
            score -= 10; basis.append(f"주봉 RSI 약세 ({rsi.iloc[-1]:.0f})")
    if not pd.isna(sma40.iloc[-1]):
        if last_close > sma40.iloc[-1]:
            score += 10; basis.append("주봉 종가 > SMA40 (장기선 위)")
        else:
            score -= 10; basis.append("주봉 종가 < SMA40 (장기선 아래)")

    score = max(-100, min(100, score))
    return {"stance": _stance_of(score), "score": score, "basis": basis}


def _swing_structure(close: pd.Series, window: int = 2) -> str | None:
    """월봉 스윙 시퀀스: HH&HL=up / LH&LL=down / 그 외 None."""
    vals = close.to_numpy()
    highs, lows = [], []
    for i in range(window, len(vals) - window):
        seg = vals[i - window:i + window + 1]
        if vals[i] == seg.max():
            highs.append(vals[i])
        if vals[i] == seg.min():
            lows.append(vals[i])
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            return "up"
        if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            return "down"
    return None


def _long_stance(m: pd.DataFrame) -> dict:
    """월봉: MACD·SMA12/24·RSI·스윙 구조 기반 룰 점수."""
    close = m["Close"]
    sma12 = close.rolling(12).mean()
    sma24 = close.rolling(24).mean()
    rsi = RSIIndicator(close, window=14).rsi()
    macd_hist = MACD(close, window_slow=26, window_fast=12, window_sign=9).macd_diff()

    score, basis = 0, []
    if not pd.isna(macd_hist.iloc[-1]):
        if macd_hist.iloc[-1] > 0:
            score += 20; basis.append("월봉 MACD 히스토그램 양수")
        else:
            score -= 20; basis.append("월봉 MACD 히스토그램 음수")
    if not pd.isna(sma12.iloc[-1]):
        if close.iloc[-1] > sma12.iloc[-1]:
            score += 20; basis.append("월봉 종가 > SMA12")
        else:
            score -= 20; basis.append("월봉 종가 < SMA12")
    if not pd.isna(sma24.iloc[-1]) and not pd.isna(sma12.iloc[-1]):
        if sma12.iloc[-1] > sma24.iloc[-1]:
            score += 15; basis.append("월봉 SMA12 > SMA24")
        else:
            score -= 15; basis.append("월봉 SMA12 < SMA24")
    if not pd.isna(rsi.iloc[-1]):
        if rsi.iloc[-1] > 60:
            score += 10; basis.append(f"월봉 RSI 강세 ({rsi.iloc[-1]:.0f})")
        elif rsi.iloc[-1] < 40:
            score -= 10; basis.append(f"월봉 RSI 약세 ({rsi.iloc[-1]:.0f})")
    structure = _swing_structure(close)
    if structure == "up":
        score += 20; basis.append("월봉 스윙 구조 HH/HL (상승)")
    elif structure == "down":
        score -= 20; basis.append("월봉 스윙 구조 LH/LL (하락)")

    score = max(-100, min(100, score))
    return {"stance": _stance_of(score), "score": score, "basis": basis}


def _short_from_consensus(consensus: dict) -> dict:
    cats = (consensus or {}).get("categories", {})
    bull = sum(v.get("bull", 0) for v in cats.values())
    bear = sum(v.get("bear", 0) for v in cats.values())
    directional = bull + bear
    if directional == 0:
        return {"stance": "neutral", "score": 0, "basis": ["방향성 지표 없음"]}
    score = round(100 * (bull - bear) / directional)
    return {
        "stance": _stance_of(score),
        "score": score,
        "basis": [f"일봉 지표 투표 bull {bull} / bear {bear}"],
    }


def _alignment(short: dict, mid: dict, long_: dict) -> str:
    s = short.get("stance")
    l = long_.get("stance")
    if l in (None, "insufficient_data"):
        l = mid.get("stance")
    if l in (None, "insufficient_data") or s in (None, "insufficient_data"):
        return "unknown"
    if s == "bullish" and l == "bullish":
        return "aligned_bull"
    if s == "bearish" and l == "bearish":
        return "aligned_bear"
    if l == "bullish" and s == "bearish":
        return "mixed_pullback"
    if l == "bearish" and s == "bullish":
        return "mixed_rally"
    return "mixed"


def build_horizons(df_daily: pd.DataFrame, consensus: dict) -> dict:
    out: dict = {"short": _short_from_consensus(consensus)}
    for name in ("mid", "long"):
        cfg = HORIZONS[name]
        resampled = resample_ohlcv(df_daily, cfg["resample_rule"])
        if len(resampled) < cfg["min_bars"]:
            out[name] = {"stance": "insufficient_data", "bars": len(resampled)}
            continue
        result = _mid_stance(resampled) if name == "mid" else _long_stance(resampled)
        result["incomplete_bar"] = last_bar_incomplete(df_daily, cfg["resample_rule"])
        out[name] = result
    out["alignment"] = _alignment(out["short"], out["mid"], out["long"])
    return out
