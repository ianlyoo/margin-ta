"""지표 컨센서스: 카테고리 집계·합의도·충돌 패턴·RSI 다이버전스.

Spec #1 §2. contribs는 layer3_signals.calculate_entry_score가 기록한
{"category", "indicator", "points"} 목록.
"""
from __future__ import annotations

import pandas as pd
from config import CATEGORY_INDICATORS, CONSENSUS_MIN_DIRECTIONAL


def _majority(cat_counts: dict) -> str | None:
    if cat_counts["bull"] > cat_counts["bear"]:
        return "bull"
    if cat_counts["bear"] > cat_counts["bull"]:
        return "bear"
    return None


def build_consensus(contribs: list, df: pd.DataFrame) -> dict:
    """카테고리별 bull/bear/neutral 집계 + agreement + 충돌 패턴 (horizon 제외)."""
    categories: dict = {}
    for cat, ids in CATEGORY_INDICATORS.items():
        votes = {}
        for c in contribs:
            if c["category"] == cat:
                votes[c["indicator"]] = c["points"]  # 같은 id 복수 기록 시 마지막 값
        bull = sum(1 for p in votes.values() if p > 0)
        bear = sum(1 for p in votes.values() if p < 0)
        categories[cat] = {
            "bull": bull,
            "bear": bear,
            "neutral": max(0, len(ids) - bull - bear),
        }

    bull_total = sum(v["bull"] for v in categories.values())
    bear_total = sum(v["bear"] for v in categories.values())
    directional = bull_total + bear_total
    agreement = (
        round(100 * max(bull_total, bear_total) / directional)
        if directional >= CONSENSUS_MIN_DIRECTIONAL else None
    )

    conflicts: list = []
    mom, trend = _majority(categories["momentum"]), _majority(categories["trend"])
    if mom and trend and mom != trend:
        conflicts.append("momentum_vs_trend")

    vol = _majority(categories["volume"])
    if vol and len(df) >= 21:
        change_20d = (df["Close"].iloc[-1] - df["Close"].iloc[-21]) / df["Close"].iloc[-21]
        if abs(change_20d) > 0.03:
            price_dir = "bull" if change_20d > 0 else "bear"
            if vol != price_dir:
                conflicts.append("volume_vs_price")

    divergence = detect_rsi_divergence(df) if "RSI_14" in df.columns else None
    if divergence:
        conflicts.append(divergence["type"])

    return {
        "agreement": agreement,
        "categories": categories,
        "conflicts": conflicts,
        "divergence": divergence,
    }


def detect_rsi_divergence(df: pd.DataFrame, pivot_window: int = 5, lookback: int = 90) -> dict | None:
    """최근 lookback 봉의 가격/RSI 피벗 2개 비교 클래식 다이버전스.

    가격 HH + RSI LH → bearish_divergence / 가격 LL + RSI HL → bullish_divergence.
    """
    if "RSI_14" not in df.columns or len(df) < pivot_window * 2 + 10:
        return None
    win = df.iloc[-lookback:]
    close = win["Close"]
    rsi = win["RSI_14"]

    def _pivots(series: pd.Series, mode: str) -> list:
        idxs = []
        vals = series.to_numpy()
        for i in range(pivot_window, len(vals) - pivot_window):
            seg = vals[i - pivot_window:i + pivot_window + 1]
            if mode == "high" and vals[i] == seg.max() and vals[i] > vals[i - 1]:
                idxs.append(i)
            elif mode == "low" and vals[i] == seg.min() and vals[i] < vals[i - 1]:
                idxs.append(i)
        return idxs

    highs = _pivots(close, "high")
    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        if close.iloc[i2] > close.iloc[i1] and rsi.iloc[i2] < rsi.iloc[i1]:
            return {
                "type": "bearish_divergence",
                "detail": (f"price HH {close.iloc[i1]:.2f}->{close.iloc[i2]:.2f} "
                           f"vs RSI LH {rsi.iloc[i1]:.0f}->{rsi.iloc[i2]:.0f}"),
            }
    lows = _pivots(close, "low")
    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        if close.iloc[i2] < close.iloc[i1] and rsi.iloc[i2] > rsi.iloc[i1]:
            return {
                "type": "bullish_divergence",
                "detail": (f"price LL {close.iloc[i1]:.2f}->{close.iloc[i2]:.2f} "
                           f"vs RSI HL {rsi.iloc[i1]:.0f}->{rsi.iloc[i2]:.0f}"),
            }
    return None


def add_horizon_conflict(consensus: dict, horizons: dict) -> None:
    """short vs long(없으면 mid) 스탠스가 반대면 horizon_conflict 추가."""
    short = (horizons.get("short") or {}).get("stance")
    long_ = (horizons.get("long") or {}).get("stance")
    if long_ in (None, "insufficient_data"):
        long_ = (horizons.get("mid") or {}).get("stance")
    if {short, long_} == {"bullish", "bearish"}:
        consensus.setdefault("conflicts", []).append("horizon_conflict")
