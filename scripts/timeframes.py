"""Daily→weekly/monthly OHLCV resampling.

Shared by layer3_horizons (spec #1) and the risk layer (spec #2).
"""
from __future__ import annotations

import pandas as pd

_OHLCV_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """일봉 OHLCV를 rule("W-FRI" 주봉 / "ME" 월말 월봉)로 리샘플.

    마지막 봉은 진행 중(미완성)일 수 있다 — last_bar_incomplete()로 판별.
    """
    cols = [c for c in _OHLCV_AGG if c in df.columns]
    out = df[cols].resample(rule).agg({c: _OHLCV_AGG[c] for c in cols})
    return out.dropna(subset=["Close"])


def last_bar_incomplete(df_daily: pd.DataFrame, rule: str) -> bool:
    """마지막 리샘플 봉이 기간을 다 채우지 못했으면 True.

    tz-aware 인덱스(yfinance 실데이터)는 tz를 벗겨 비교한다 —
    to_period()가 tz를 조용히 버려서 naive/aware 비교 TypeError가 나기 때문.
    """
    if df_daily.empty:
        return False
    last_day = df_daily.index[-1]
    if getattr(last_day, "tzinfo", None) is not None:
        last_day = last_day.tz_localize(None)
    freq = "W-FRI" if rule.startswith("W") else "M"
    period_end = last_day.to_period(freq).end_time
    return last_day.normalize() < period_end.normalize()
