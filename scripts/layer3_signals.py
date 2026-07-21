"""
Layer 3 — Signal: 지지/저항 탐지 + Entry Score
Horizontal S/R, Dynamic MA S/R, Fibonacci Retracement, Entry Score (0-100)
"""
import numpy as np
import pandas as pd
from datetime import datetime

from config import SCORE_CATEGORY_CAPS
from layer1_options import compute_options_score_overlay


def find_horizontal_sr(df: pd.DataFrame, window: int = 5, wick_ratio: float = 1.5):
    """
    피벗 기반 Horizontal 지지/저항 탐지 (Screeni-py + Stackademic 합성).
    window: 좌우 확인 봉 수
    wick_ratio: 꼬리/몸통 비율이 이 이상이어야 유효 레벨
    """
    supports = []
    resistances = []

    for i in range(window, len(df) - window):
        # Support: 현재 low가 좌우 window 내 최저
        left_lows = df.Low.iloc[i - window : i]
        right_lows = df.Low.iloc[i + 1 : i + window + 1]
        is_support = df.Low.iloc[i] <= left_lows.min() and df.Low.iloc[i] <= right_lows.min()

        if is_support:
            body = abs(df.Close.iloc[i] - df.Open.iloc[i])
            lower_wick = min(df.Open.iloc[i], df.Close.iloc[i]) - df.Low.iloc[i]
            if lower_wick > body * wick_ratio and body > 0:
                strength = min(lower_wick / body, 10.0)
                supports.append({
                    "price": round(float(df.Low.iloc[i]), 2),
                    "date": df.index[i],
                    "strength": round(strength, 1),
                })

        # Resistance: 현재 high가 좌우 window 내 최고
        left_highs = df.High.iloc[i - window : i]
        right_highs = df.High.iloc[i + 1 : i + window + 1]
        is_resistance = df.High.iloc[i] >= left_highs.max() and df.High.iloc[i] >= right_highs.max()

        if is_resistance:
            body = abs(df.Close.iloc[i] - df.Open.iloc[i])
            upper_wick = df.High.iloc[i] - max(df.Open.iloc[i], df.Close.iloc[i])
            if upper_wick > body * wick_ratio and body > 0:
                strength = min(upper_wick / body, 10.0)
                resistances.append({
                    "price": round(float(df.High.iloc[i]), 2),
                    "date": df.index[i],
                    "strength": round(strength, 1),
                })

    # Cluster: ±1% 내 레벨 병합
    supports = _cluster_levels(supports)
    resistances = _cluster_levels(resistances)

    return supports, resistances


def _cluster_levels(levels: list, threshold_pct: float = 0.01) -> list:
    """±threshold_pct 이내의 가까운 레벨을 가중평균으로 병합."""
    if not levels:
        return []

    levels = sorted(levels, key=lambda x: x["price"])
    clusters = []
    current = [levels[0]]

    for l in levels[1:]:
        avg_price = sum(x["price"] for x in current) / len(current)
        if abs(l["price"] - avg_price) / avg_price < threshold_pct:
            current.append(l)
        else:
            clusters.append(current)
            current = [l]
    clusters.append(current)

    merged = []
    for group in clusters:
        total_strength = sum(g["strength"] for g in group)
        weighted_price = sum(g["price"] * g["strength"] for g in group) / max(total_strength, 0.01)
        merged.append({
            "price": round(weighted_price, 2),
            "strength": round(min(total_strength, 10), 1),
            "touches": len(group),
            "most_recent": max(g["date"] for g in group),
        })

    return sorted(merged, key=lambda x: x["strength"], reverse=True)


def find_dynamic_sr(df: pd.DataFrame, current_price: float) -> list:
    """주요 MA + 볼린저밴드를 동적 S/R로 변환."""
    levels = []
    last = df.iloc[-1]

    ma_specs = [
        ("20EMA", "EMA_20"),
        ("20SMA", "SMA_20"),
        ("50SMA", "SMA_50"),
        ("100SMA", "SMA_100"),
        ("200SMA", "SMA_200"),
    ]

    for name, col in ma_specs:
        if col not in df.columns:
            continue
        val = last[col]
        if pd.isna(val):
            continue
        role = "support" if current_price > val else "resistance"
        dist_pct = abs(current_price - val) / current_price * 100
        levels.append({
            "price": round(float(val), 2),
            "name": name,
            "role": role,
            "distance_pct": round(dist_pct, 2),
            "type": "MA",
        })

    # Bollinger Bands
    for bb_col, bb_name, bb_role in [
        ("BBL_20_2.0", "BB Lower", "support"),
        ("BBU_20_2.0", "BB Upper", "resistance"),
        ("BBM_20_2.0", "BB Mid", "support" if current_price > last.get("BBM_20_2.0", current_price) else "resistance"),
    ]:
        if bb_col in df.columns:
            val = last[bb_col]
            if not pd.isna(val):
                levels.append({
                    "price": round(float(val), 2),
                    "name": bb_name,
                    "role": bb_role,
                    "distance_pct": round(abs(current_price - val) / current_price * 100, 2),
                    "type": "Bollinger",
                })

    return sorted(levels, key=lambda x: x["distance_pct"])


def _compute_fib_set(df: pd.DataFrame, current_price: float, lookback_days: int, label: str, ratios=None) -> dict:
    """단일 기간 피보나치 되돌림 계산 (내부 헬퍼)."""
    if ratios is None:
        ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
    lookback = min(lookback_days, len(df))
    recent = df.iloc[-lookback:]
    high = float(recent.High.max())
    low = float(recent.Low.min())
    diff = high - low

    if diff <= 0:
        return {"error": "고저 차이 없음", "levels": {}, "label": label}

    # 상승/하락 추세 판단 (60일 기준)
    mid = df.Close.iloc[-60] if len(df) >= 60 else df.Close.iloc[0]
    is_uptrend = current_price > mid

    levels = {}
    for ratio in ratios:
        if is_uptrend:
            price = high - (diff * ratio)
        else:
            price = low + (diff * ratio)
        levels[f"Fib {ratio}"] = {
            "price": round(price, 2),
            "distance_pct": round(abs(current_price - price) / current_price * 100, 2),
            "role": "support" if price < current_price else "resistance",
        }

    return {
        "trend": "up" if is_uptrend else "down",
        "high": round(high, 2),
        "low": round(low, 2),
        "levels": levels,
        "label": label,
        "lookback_days": lookback,
    }


def _detect_fib_confluence(fib_sets: list[dict], current_price: float, threshold_pct: float = 0.01) -> list[dict]:
    """Detect where Fibonacci levels from different timeframes converge within threshold_pct."""
    confluence = []
    if len(fib_sets) < 2:
        return confluence

    for ratio in [0.236, 0.382, 0.5, 0.618, 0.786]:
        key = f"Fib {ratio}"
        prices = []
        for fs in fib_sets:
            lv = fs.get("levels", {}).get(key)
            if lv and "price" in lv:
                prices.append((fs["label"], lv["price"], lv["role"]))

        # Find clusters within threshold_pct
        for i in range(len(prices)):
            cluster = [prices[i]]
            for j in range(i + 1, len(prices)):
                avg = sum(p[1] for p in cluster) / len(cluster)
                if abs(prices[j][1] - avg) / current_price < threshold_pct:
                    cluster.append(prices[j])
            if len(cluster) >= 2:
                avg_price = sum(p[1] for p in cluster) / len(cluster)
                roles = [p[2] for p in cluster]
                dominant_role = "support" if roles.count("support") > roles.count("resistance") else "resistance"
                confluence.append({
                    "ratio": ratio,
                    "price": round(avg_price, 2),
                    "timeframes": [p[0] for p in cluster],
                    "count": len(cluster),
                    "role": dominant_role,
                    "distance_pct": round(abs(current_price - avg_price) / current_price * 100, 2),
                })

    # Deduplicate by price proximity
    seen = set()
    unique = []
    for c in sorted(confluence, key=lambda x: x["count"], reverse=True):
        key = round(c["price"] / current_price, 3)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def find_fibonacci_levels(df: pd.DataFrame, current_price: float, lookback_days: int = 252) -> dict:
    """멀티 타임프레임 피보나치 되돌림 (52w + 104w + All-Time + 컨플루언스)."""
    total_days = len(df)

    fib_sets = []
    # Primary: 52-week (252 trading days)
    fib_52w = _compute_fib_set(df, current_price, 252, "52W")
    fib_sets.append(fib_52w)

    # Secondary: 104-week (504 trading days) — only if enough data
    if total_days > 252:
        lookback_104w = min(504, total_days)
        fib_104w = _compute_fib_set(df, current_price, lookback_104w, "104W")
        fib_sets.append(fib_104w)
    else:
        fib_104w = None

    # All-Time: full history — only if significantly more than 52w
    if total_days > 300:
        fib_alltime = _compute_fib_set(df, current_price, total_days, "All-Time")
        fib_sets.append(fib_alltime)
    else:
        fib_alltime = None

    # Detect confluence
    confluence = _detect_fib_confluence(fib_sets, current_price)

    result = {
        "primary": fib_52w,
        "secondary": fib_104w,
        "alltime": fib_alltime,
        "confluence": confluence,
        # Backward compat: primary levels accessible as fib_data["levels"]
        "trend": fib_52w.get("trend", "unknown"),
        "high": fib_52w.get("high", 0),
        "low": fib_52w.get("low", 0),
        "levels": fib_52w.get("levels", {}),
    }
    return result


def _add_market_regime_score(score: int, details: list, market: dict | None) -> int:
    """Small overlay for broad market risk. Position sizing remains the main market filter."""
    if not market:
        return score

    vix = market.get("vix") or {}
    breadth = market.get("breadth") or {}

    # ── Korean market path (KOSPI trend × volatility regimes) ──────
    kr_regime = breadth.get("regime", "")
    if kr_regime in ("strong_bull", "bull", "bear", "strong_bear", "neutral"):
        # Trend regime
        if kr_regime == "strong_bull":
            score += 3; details.append(("KOSPI 강한 상승장", 3))
        elif kr_regime == "bull":
            score += 2; details.append(("KOSPI 상승장", 2))
        elif kr_regime == "strong_bear":
            score -= 5; details.append(("KOSPI 강한 하락장", -5))
        elif kr_regime == "bear":
            score -= 3; details.append(("KOSPI 하락장", -3))
        # neutral → no change

        # Volatility regime (uses vix mirror)
        vol_regime = vix.get("regime", "")
        vol_trend = vix.get("trend", "")
        if vol_regime == "extreme":
            pts = -5 if vol_trend == "rising" else -4
            score += pts; details.append((f"KOSPI 극변동성 ({vol_trend})", pts))
        elif vol_regime == "high":
            pts = -3 if vol_trend == "rising" else -2
            score += pts; details.append((f"KOSPI 고변동성 ({vol_trend})", pts))
        elif vol_regime == "low":
            score += 2; details.append(("KOSPI 저변동 안정", 2))
        elif vol_regime == "normal" and vol_trend == "falling":
            score += 1; details.append(("KOSPI 변동성 완화", 1))

        return score

    # ── US market path (existing) ───────────────────────────────────
    vix_value = vix.get("value")
    vix_regime = vix.get("regime")
    vix_trend = vix.get("trend")
    if vix_value is not None:
        if vix_regime == "extreme":
            pts = -6 if vix_trend == "rising" else -5
            score += pts; details.append((f"VIX 극단 레짐 ({vix_value:.1f}, {vix_trend})", pts))
        elif vix_regime == "high":
            pts = -4 if vix_trend == "rising" else -3
            score += pts; details.append((f"VIX 고변동 레짐 ({vix_value:.1f}, {vix_trend})", pts))
        elif vix_regime == "low":
            score += 2; details.append((f"VIX 저변동 risk-on ({vix_value:.1f})", 2))
        elif vix_regime == "normal" and vix_trend == "falling":
            score += 1; details.append((f"VIX 하락 안정화 ({vix_value:.1f})", 1))

    breadth_regime = breadth.get("regime")
    ad_trend = breadth.get("ad_line_trend")
    above_200 = breadth.get("above_200ma_pct")
    above_50 = breadth.get("above_50ma_pct")
    if breadth_regime == "strong":
        score += 3; details.append((f"시장 Breadth 강함 (200MA {above_200}%)", 3))
    elif breadth_regime == "weak":
        score -= 5; details.append((f"시장 Breadth 약함 (200MA {above_200}%)", -5))
    elif breadth_regime == "deteriorating":
        score -= 4; details.append((f"시장 Breadth 악화 (50MA {above_50}%, AD {ad_trend})", -4))
    elif above_200 is not None and above_200 >= 55 and ad_trend == "rising":
        score += 2; details.append((f"시장 참여 확대 (200MA {above_200}%, AD rising)", 2))

    return score


def _add_tradingview_score(score: int, details: list, tradingview: dict | None) -> int:
    """Low-weight external TA cross-check to avoid double-counting local indicators."""
    if not tradingview or tradingview.get("error"):
        return score

    summary = tradingview.get("summary") or {}
    buy = int(summary.get("BUY") or 0)
    sell = int(summary.get("SELL") or 0)
    neutral = int(summary.get("NEUTRAL") or 0)
    edge = buy - sell
    if edge >= 8:
        score += 3; details.append((f"TradingView 강한 BUY 우위 ({buy}/{neutral}/{sell})", 3))
    elif edge >= 4:
        score += 1; details.append((f"TradingView BUY 우위 ({buy}/{neutral}/{sell})", 1))
    elif edge <= -8:
        score -= 3; details.append((f"TradingView 강한 SELL 우위 ({buy}/{neutral}/{sell})", -3))
    elif edge <= -4:
        score -= 1; details.append((f"TradingView SELL 우위 ({buy}/{neutral}/{sell})", -1))

    return score


def _nearest_option_summary(flow: dict | None) -> dict | None:
    options = ((flow or {}).get("options") or {})
    primary_chain = options.get("primary_chain_feature") or {}
    if (
        primary_chain
        and primary_chain.get("quality_status") in {"good", "usable"}
        and primary_chain.get("filtered_contracts", 0) >= 10
    ):
        return primary_chain

    summaries = options.get("summary_by_expiry") or []
    if not summaries:
        return None
    usable = [s for s in summaries if s.get("max_pain") and s.get("days_to_expiry") is not None]
    if not usable:
        return summaries[0]
    return sorted(usable, key=lambda x: abs(int(x.get("days_to_expiry") or 0)))[0]


def _add_flow_score(score: int, details: list, flow: dict | None, current_price: float, last, prev) -> int:
    """
    External flow overlay. These signals are intentionally low weight because
    dark-pool/short-volume data is not always directional by itself.
    """
    if not flow:
        return score

    close_up = last.Close > last.Open
    close_down = last.Close < last.Open
    cmf = last.get("CMF_20", 0)
    cmf = 0 if pd.isna(cmf) else float(cmf)
    ema20 = last.get("EMA_20", last.get("SMA_20", np.nan))
    above_ema20 = not pd.isna(ema20) and last.Close > ema20

    # Dark pool: use only when current ratio deviates from its own recent baseline.
    dark_pool = flow.get("dark_pool") or {}
    dp_current = dark_pool.get("current") or {}
    dp_history = dark_pool.get("history") or {}
    dp_stats = dp_history.get("stats") or {}
    dp_pct = dp_current.get("dark_pool_percentage")
    dp_avg = dp_stats.get("avgDarkRatio") or dp_current.get("dark_pool_30d_average_pct")
    if dp_pct is not None and dp_avg is not None:
        dp_pct = float(dp_pct)
        dp_avg = float(dp_avg)
        dp_gap = dp_pct - dp_avg
        if dp_gap >= 4 and close_up and cmf >= 0:
            score += 3; details.append((f"Dark Pool 평균상회+양봉 ({dp_pct:.1f}% vs {dp_avg:.1f}%)", 3))
        elif dp_gap >= 4 and close_down and cmf <= 0:
            score -= 3; details.append((f"Dark Pool 평균상회+음봉 ({dp_pct:.1f}% vs {dp_avg:.1f}%)", -3))
        elif dp_gap <= -5:
            score -= 1; details.append((f"Dark Pool 참여 저하 ({dp_pct:.1f}% vs {dp_avg:.1f}%)", -1))

    # Options are scored through canonical top-level options data.

    # Short data: distinguish daily short volume from official short-interest squeeze setup.
    short = flow.get("short") or {}
    short_current = short.get("current") or {}
    sv_pct = short_current.get("short_percent")
    short_stats = ((short.get("short_volume") or {}).get("stats") or {})
    avg_sv_20 = short_stats.get("avg_short_percent_20d")
    if sv_pct is not None:
        sv_pct = float(sv_pct)
        sv_gap = sv_pct - float(avg_sv_20) if avg_sv_20 is not None else 0
        if sv_pct >= 55 and close_up:
            score += 2; details.append((f"Short Volume 고비율 흡수 ({sv_pct:.1f}%)", 2))
        elif sv_pct >= 55 and close_down:
            score -= 3; details.append((f"Short Volume 고비율+음봉 ({sv_pct:.1f}%)", -3))
        elif sv_gap >= 5 and close_up:
            score += 2; details.append((f"Short Volume 평균상회+양봉 ({sv_pct:.1f}%)", 2))
        elif sv_gap >= 5 and close_down:
            score -= 2; details.append((f"Short Volume 평균상회+음봉 ({sv_pct:.1f}%)", -2))

    si_pct = float(short_current.get("short_interest_percent") or 0)
    dtc = float(short_current.get("days_to_cover") or 0)
    ctb = float(short_current.get("ctb_rate") or 0)
    squeeze_risk = str(short_current.get("squeeze_risk") or "").upper()
    htb_status = str(short_current.get("htb_status") or "").upper()
    if si_pct >= 10 or dtc >= 5 or ctb >= 10 or squeeze_risk in {"HIGH", "EXTREME"} or htb_status == "HTB":
        if above_ema20 and close_up:
            score += 4; details.append((f"공매도 squeeze 조건+상승확인 (SI {si_pct:.1f}%, CTB {ctb:.1f}%)", 4))
        elif close_down:
            score -= 3; details.append((f"공매도 부담 확대+음봉 (SI {si_pct:.1f}%, CTB {ctb:.1f}%)", -3))
        else:
            score += 1; details.append((f"공매도 squeeze 후보 (SI {si_pct:.1f}%, DTC {dtc:.1f})", 1))

    return score


def _add_options_score(
    score: int,
    details: list,
    options: dict | None,
    current_price: float,
    last,
    prev,
) -> int:
    """Bounded options overlay; weak chains are reported but do not affect score."""
    if not options:
        return score
    overlay = compute_options_score_overlay(
        options,
        current_price,
        close_up=last.Close > last.Open,
        close_down=last.Close < last.Open,
    )
    options["score_overlay"] = overlay
    for name, pts in overlay.get("details", []):
        if pts:
            details.append((name, pts))
    return score + int(overlay.get("clamped_score") or 0)


def _as_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _add_ownership_score(score: int, details: list, ownership: dict | None) -> int:
    """
    SEC 13D/G overlay. Keep it low-weight because this is ownership context,
    not a direct intraday/technical timing signal.
    """
    if not ownership:
        return score
    events = ownership.get("events") or []
    if not events:
        return score

    raw_adjustment = 0
    labels = []
    for event in events[:5]:
        form_type = str(event.get("form_type") or "").upper()
        owner = str(event.get("reporting_owner") or "holder")
        delta = _as_float(event.get("ownership_delta_pct"))
        pct = _as_float(event.get("ownership_pct"))
        pts = 0
        label = ""

        if event.get("converted_13g_to_13d_flag"):
            pts = 3
            label = "13G→13D active 전환"
        elif form_type.startswith("SC 13D"):
            if event.get("dropped_below_5pct_flag"):
                pts = -4
                label = "13D 5% 미만 이탈"
            elif delta is not None and delta >= 1.0:
                pts = 2
                label = f"13D 지분 증가 {delta:+.1f}pp"
            elif delta is not None and delta <= -1.0:
                pts = -3
                label = f"13D 지분 감소 {delta:+.1f}pp"
            elif event.get("event_type") == "new":
                pts = 3
                label = "신규 13D active holder"
            else:
                pts = 1
                label = "13D active ownership update"
        elif form_type.startswith("SC 13G"):
            if event.get("dropped_below_5pct_flag"):
                pts = -2
                label = "13G 5% 미만 이탈"
            elif delta is not None and delta >= 1.0:
                pts = 1
                label = f"13G passive 지분 증가 {delta:+.1f}pp"
            elif delta is not None and delta <= -1.0:
                pts = -1
                label = f"13G passive 지분 감소 {delta:+.1f}pp"
            elif event.get("event_type") == "new":
                pts = 1
                label = "신규 13G 대량보유자"

        if pts:
            raw_adjustment += pts
            pct_text = f" {pct:.1f}%" if pct is not None else ""
            labels.append(f"{label}: {owner[:40]}{pct_text}")

    adjustment = max(-4, min(3, raw_adjustment))
    if adjustment and labels:
        suffix = f" 외 {len(labels) - 1}건" if len(labels) > 1 else ""
        details.append((f"13D/G {labels[0]}{suffix}", adjustment))
    return score + adjustment


def _record(
    contribs: list,
    details: list,
    category: str,
    indicator: str,
    label: str,
    points: int,
) -> None:
    """Record one core-category scoring contribution.

    Does NOT mutate `score` — core categories accumulate into `contribs` only;
    `calculate_entry_score` sums+clamps each category's subtotal against
    config.SCORE_CATEGORY_CAPS and adds the clamped result to `score` in one
    place, after all core categories run and before the overlay calls.
    `details` keeps its existing flat (label, points) shape for backward
    compatibility.
    """
    contribs.append({"category": category, "indicator": indicator, "label": label, "points": points})
    details.append((label, points))


def calculate_entry_score(
    df: pd.DataFrame,
    current_price: float,
    all_supports: list,
    liquidity: dict | None = None,
    market: dict | None = None,
    options: dict | None = None,
    flow: dict | None = None,
    ownership: dict | None = None,
    tradingview: dict | None = None,
    fib_data: dict | None = None,
) -> dict:
    """
    Entry Score v2.4 — technical core + low-weight market/flow overlays, 0-100.
    ta(bukosabino) 지표 기반 5개 핵심 카테고리에 외부 레짐/플로우를 보조 반영.

    Score 해석:
      70+: 🟢 STRONG — 진입 신호 강함
      50-69: 🟡 MODERATE — 조건부 진입
      30-49: 🟠 WEAK — 관망 권장
      <30: 🔴 AVOID — 진입 비추천
    """
    score = 50  # neutral base (50 = 중립, 신호들이 더하고 뺌)
    details = []
    contribs: list = []
    last = df.iloc[-1]
    prev = df.iloc[-2]

    # ═══════════════════════════════════════════
    # CATEGORY 1: MOMENTUM — 과매수/과매도 (max ±24)
    # ═══════════════════════════════════════════

    # 1a. RSI (14) — ±10
    if "RSI_14" in df.columns:
        rsi = last["RSI_14"]
        if not pd.isna(rsi):
            if rsi < 25:
                _record(contribs, details, "momentum", "rsi", f"RSI 과매도 ({rsi:.0f})", 10)
            elif rsi < 35:
                _record(contribs, details, "momentum", "rsi", f"RSI 과매도 근접 ({rsi:.0f})", 7)
            elif rsi < 45:
                _record(contribs, details, "momentum", "rsi", f"RSI 약세 ({rsi:.0f})", 3)
            elif rsi > 85:
                _record(contribs, details, "momentum", "rsi", f"RSI 과매수 ({rsi:.0f})", -10)
            elif rsi > 75:
                _record(contribs, details, "momentum", "rsi", f"RSI 과매수 근접 ({rsi:.0f})", -7)

    # 1b. Stochastic %K (14/3/3) — ±8
    if "STOCHk_14_3_3" in df.columns:
        stoch_k = last["STOCHk_14_3_3"]
        if not pd.isna(stoch_k):
            if stoch_k < 20:
                _record(contribs, details, "momentum", "stoch", f"Stoch 과매도 ({stoch_k:.0f})", 8)
            elif stoch_k < 30:
                _record(contribs, details, "momentum", "stoch", f"Stoch 약세 ({stoch_k:.0f})", 4)
            elif stoch_k > 95:
                _record(contribs, details, "momentum", "stoch", f"Stoch 과매수 ({stoch_k:.0f})", -8)
            elif stoch_k > 85:
                _record(contribs, details, "momentum", "stoch", f"Stoch 강세 ({stoch_k:.0f})", -4)

    # 1c. Williams %R (14) — ±6
    if "WILLR_14" in df.columns:
        willr = last["WILLR_14"]
        if not pd.isna(willr):
            if willr < -80:
                _record(contribs, details, "momentum", "willr", f"Will%R 과매도 ({willr:.0f})", 6)
            elif willr < -70:
                _record(contribs, details, "momentum", "willr", f"Will%R 약세 ({willr:.0f})", 3)
            elif willr > -10:
                _record(contribs, details, "momentum", "willr", f"Will%R 과매수 ({willr:.0f})", -6)
            elif willr > -20:
                _record(contribs, details, "momentum", "willr", f"Will%R 강세 ({willr:.0f})", -3)

    # ═══════════════════════════════════════════
    # CATEGORY 2: TREND — 방향 & 강도 (max ±26)
    # ═══════════════════════════════════════════

    # 2a. MACD (12/26/9) — ±8
    if "MACDh_12_26_9" in df.columns:
        macd_h = last["MACDh_12_26_9"]
        macd_h_prev = prev.get("MACDh_12_26_9", macd_h)
        if not pd.isna(macd_h) and not pd.isna(macd_h_prev):
            if macd_h > 0 and macd_h_prev <= 0:
                _record(contribs, details, "trend", "macd", "MACD 골든크로스", 8)
            elif macd_h < 0 and macd_h_prev >= 0:
                _record(contribs, details, "trend", "macd", "MACD 데드크로스", -8)
            elif macd_h > 0:
                _record(contribs, details, "trend", "macd", "MACD 히스토그램 양수", 2)

    # 2b. ADX (14) — ±5
    if "ADX_14" in df.columns:
        adx = last["ADX_14"]
        if not pd.isna(adx):
            if adx > 40:
                _record(contribs, details, "trend", "adx", f"ADX 매우강함 ({adx:.0f})", 5)
            elif adx > 25:
                _record(contribs, details, "trend", "adx", f"ADX 추세강함 ({adx:.0f})", 3)
            elif adx < 15:
                _record(contribs, details, "trend", "adx", f"ADX 추세약함 ({adx:.0f})", -3)

    # 2c. Parabolic SAR — ±5
    if "PSAR_up" in df.columns and "PSAR_down" in df.columns:
        psar_up = last["PSAR_up"]
        psar_down = last["PSAR_down"]
        if not pd.isna(psar_down) and last.Close > psar_down:
            _record(contribs, details, "trend", "psar", "PSAR 상승시그널", 5)
        elif not pd.isna(psar_up) and last.Close < psar_up:
            _record(contribs, details, "trend", "psar", "PSAR 하락시그널", -5)

    # 2d. Aroon (25) — ±4
    if "AROON_up" in df.columns and "AROON_down" in df.columns:
        aroon_up = last["AROON_up"]; aroon_down = last["AROON_down"]
        if not pd.isna(aroon_up) and not pd.isna(aroon_down):
            if aroon_up > 70 and aroon_down < 30:
                _record(contribs, details, "trend", "aroon", f"Aroon 강세 (↑{aroon_up:.0f} ↓{aroon_down:.0f})", 4)
            elif aroon_down > 70 and aroon_up < 30:
                _record(contribs, details, "trend", "aroon", f"Aroon 약세 (↑{aroon_up:.0f} ↓{aroon_down:.0f})", -4)
            elif aroon_up > aroon_down:
                _record(contribs, details, "trend", "aroon", f"Aroon 상승우위 ({aroon_up:.0f}/{aroon_down:.0f})", 2)

    # 2e. Vortex (14) — ±4
    if "VTX_pos" in df.columns and "VTX_neg" in df.columns:
        vtx_p = last["VTX_pos"]; vtx_n = last["VTX_neg"]
        if not pd.isna(vtx_p) and not pd.isna(vtx_n):
            if vtx_p > vtx_n and vtx_p > 1.0:
                _record(contribs, details, "trend", "vortex", "Vortex 상승크로스", 4)
            elif vtx_n > vtx_p and vtx_n > 1.0:
                _record(contribs, details, "trend", "vortex", "Vortex 하락크로스", -4)

    # ═══════════════════════════════════════════
    # CATEGORY 3: VOLUME — 자금 흐름 (max ±20)
    # ═══════════════════════════════════════════

    # 3a. MFI (14) — ±8
    if "MFI_14" in df.columns:
        mfi = last["MFI_14"]
        if not pd.isna(mfi):
            if mfi < 20:
                _record(contribs, details, "volume", "mfi", f"MFI 과매도 ({mfi:.0f})", 8)
            elif mfi < 30:
                _record(contribs, details, "volume", "mfi", f"MFI 약세 ({mfi:.0f})", 4)
            elif mfi > 80:
                _record(contribs, details, "volume", "mfi", f"MFI 과매수 ({mfi:.0f})", -8)
            elif mfi > 70:
                _record(contribs, details, "volume", "mfi", f"MFI 강세 ({mfi:.0f})", -4)

    # 3b. CMF (20) — ±7
    if "CMF_20" in df.columns:
        cmf = last["CMF_20"]
        if not pd.isna(cmf):
            if cmf > 0.2:
                _record(contribs, details, "volume", "cmf", f"CMF 강한 매수압력 ({cmf:.2f})", 7)
            elif cmf > 0.05:
                _record(contribs, details, "volume", "cmf", f"CMF 매수우위 ({cmf:.2f})", 3)
            elif cmf < -0.2:
                _record(contribs, details, "volume", "cmf", f"CMF 강한 매도압력 ({cmf:.2f})", -7)
            elif cmf < -0.05:
                _record(contribs, details, "volume", "cmf", f"CMF 매도우위 ({cmf:.2f})", -3)

    # 3c. Force Index (13) — ±5
    if "FI_13" in df.columns:
        fi = last["FI_13"]; fi_prev = prev.get("FI_13", fi)
        if not pd.isna(fi) and not pd.isna(fi_prev):
            if fi > 0 and fi_prev <= 0:
                _record(contribs, details, "volume", "force_index", "Force Index 상승전환", 5)
            elif fi < 0 and fi_prev >= 0:
                _record(contribs, details, "volume", "force_index", "Force Index 하락전환", -5)

    # Also: legacy volume spike signal
    avg_vol_50 = df.Volume.iloc[-50:].mean()
    vol_ratio = last.Volume / avg_vol_50 if avg_vol_50 > 0 else 1
    if vol_ratio > 2.0 and last.Close > last.Open:
        _record(contribs, details, "volume", "volume_spike", f"거래량 급증+양봉 (x{vol_ratio:.1f})", 4)
    elif vol_ratio < 0.3 and last.get("RSI_14", 50) < 40:
        _record(contribs, details, "volume", "volume_spike", f"거래량 바닥+RSI약세 (x{vol_ratio:.1f})", 3)
    elif vol_ratio > 3.0 and last.Close < last.Open:
        _record(contribs, details, "volume", "volume_spike", f"거래량 급증+음봉 (x{vol_ratio:.1f})", -4)

    # ═══════════════════════════════════════════
    # CATEGORY 4: STRUCTURE — 가격 위치 (max ±20)
    # ═══════════════════════════════════════════

    # 4a. 지지선 거리 — ±8
    if all_supports:
        supports_below = [s for s in all_supports if s["price"] < current_price]
        if supports_below:
            nearest = max(supports_below, key=lambda x: x["price"])
            dist_pct = (current_price - nearest["price"]) / current_price * 100
            if dist_pct < 1:
                _record(contribs, details, "structure", "support_distance", f"지지선 초근접 ({nearest['price']:.2f}, {dist_pct:.1f}%)", 8)
            elif dist_pct < 2:
                _record(contribs, details, "structure", "support_distance", f"지지선 근접 ({nearest['price']:.2f}, {dist_pct:.1f}%)", 5)
            elif dist_pct < 4:
                _record(contribs, details, "structure", "support_distance", f"지지선 부근 ({nearest['price']:.2f}, {dist_pct:.1f}%)", 3)
            elif dist_pct > 10:
                _record(contribs, details, "structure", "support_distance", f"지지선 원거리 ({nearest['price']:.2f}, {dist_pct:.1f}%)", -8)

    # 4b. 볼린저밴드 포지션 (20/2) — ±6
    if "BBL_20_2.0" in df.columns and "BBU_20_2.0" in df.columns:
        bb_l = last["BBL_20_2.0"]; bb_u = last["BBU_20_2.0"]
        if not pd.isna(bb_l) and not pd.isna(bb_u) and bb_u - bb_l > 0:
            bb_pct = (last.Close - bb_l) / (bb_u - bb_l)
            if bb_pct < 0.1:
                _record(contribs, details, "structure", "bollinger", f"BB 하단 터치 ({bb_pct*100:.0f}%)", 6)
            elif bb_pct < 0.25:
                _record(contribs, details, "structure", "bollinger", f"BB 하단 부근 ({bb_pct*100:.0f}%)", 3)
            elif bb_pct > 0.9:
                _record(contribs, details, "structure", "bollinger", f"BB 상단 터치 ({bb_pct*100:.0f}%)", -6)

    # 4c. Donchian Channel (20) — ±6
    if "DC_h_20" in df.columns and "DC_l_20" in df.columns:
        dc_h = last["DC_h_20"]; dc_l = last["DC_l_20"]
        if not pd.isna(dc_h) and not pd.isna(dc_l) and dc_h - dc_l > 0:
            dc_pct = (last.Close - dc_l) / (dc_h - dc_l)
            if dc_pct < 0.15:
                _record(contribs, details, "structure", "donchian", f"Donchian 하단 ({dc_pct*100:.0f}%)", 6)
            elif dc_pct < 0.3:
                _record(contribs, details, "structure", "donchian", f"Donchian 하단부근 ({dc_pct*100:.0f}%)", 3)
            elif dc_pct > 0.85:
                _record(contribs, details, "structure", "donchian", f"Donchian 상단 ({dc_pct*100:.0f}%)", -6)

    # 4d. Liquidity structure — AVWAP / Volume Profile confluence (max ±8)
    if liquidity:
        confluence = liquidity.get("confluence", {})
        if confluence.get("has_confluence"):
            _record(contribs, details, "structure", "liquidity_confluence", f"유동성 레벨 confluence ({confluence.get('count', 0)}개)", 2)

    # 4e. Fibonacci multi-timeframe confluence (v3.2)
    if fib_data:
        fib_confluence = fib_data.get("confluence", [])
        if fib_confluence:
            max_count = max(cf.get("count", 0) for cf in fib_confluence)
            if max_count >= 3:
                _record(contribs, details, "structure", "fib_confluence", f"Fib 3중 컨플루언스 ({max_count}개 타임프레임 일치)", 5)
            elif max_count >= 2:
                _record(contribs, details, "structure", "fib_confluence", f"Fib 2중 컨플루언스 ({max_count}개 타임프레임 일치)", 3)

        avwap_levels = liquidity.get("anchored_vwap", {}).get("levels", [])
        if avwap_levels:
            nearest_avwap = avwap_levels[0]
            if nearest_avwap.get("distance_pct", 999) <= 1.5:
                slope = nearest_avwap.get("slope_regime")
                if nearest_avwap.get("role") == "support":
                    if slope == "rising":
                        _record(contribs, details, "structure", "avwap", f"{nearest_avwap['name']} 상승 지지 근접", 3)
                    elif slope in {"flat", "unknown"}:
                        _record(contribs, details, "structure", "avwap", f"{nearest_avwap['name']} 지지 근접", 2)
                elif nearest_avwap.get("role") == "resistance":
                    if slope == "falling":
                        _record(contribs, details, "structure", "avwap", f"{nearest_avwap['name']} 하락 저항 근접", -3)
                    else:
                        _record(contribs, details, "structure", "avwap", f"{nearest_avwap['name']} 저항 근접", -2)

        vp = liquidity.get("volume_profile", {})
        if not vp.get("error"):
            poc = vp.get("poc")
            val = vp.get("value_area_low")
            vah = vp.get("value_area_high")
            if poc:
                poc_dist = abs(current_price - poc) / current_price * 100
                if poc < current_price and poc_dist <= 1.5:
                    _record(contribs, details, "structure", "volume_profile", f"Volume POC 지지 근접 ({poc:.2f})", 2)
                elif poc > current_price and poc_dist <= 1.5:
                    _record(contribs, details, "structure", "volume_profile", f"Volume POC 저항 근접 ({poc:.2f})", -2)
            if val and abs(current_price - val) / current_price * 100 <= 1.0:
                _record(contribs, details, "structure", "volume_profile", f"Value Area Low 근접 ({val:.2f})", 3)
            if vah and abs(current_price - vah) / current_price * 100 <= 1.0:
                _record(contribs, details, "structure", "volume_profile", f"Value Area High 근접 ({vah:.2f})", -3)

    # ═══════════════════════════════════════════
    # CATEGORY 5: CANDLESTICK — 패턴 (max ±10)
    # ═══════════════════════════════════════════

    for col, name, pts in [
        ("CDL_HAMMER", "Hammer", 10),
        ("CDL_MORNING_STAR", "Morning Star", 10),
        ("CDL_ENGULFING", "Bullish Engulfing", 8),
        ("CDL_PIERCING", "Piercing", 7),
    ]:
        if col in df.columns and float(last[col]) > 0:
            _record(contribs, details, "candlestick", "patterns", name, pts)
            break
    else:
        for col, name, pts in [
            ("CDL_SHOOTING_STAR", "Shooting Star", -10),
            ("CDL_EVENING_STAR", "Evening Star", -10),
        ]:
            if col in df.columns and float(last[col]) < 0:
                _record(contribs, details, "candlestick", "patterns", name, pts)
                break
        else:
            if "CDL_DOJI" in df.columns and float(last["CDL_DOJI"]) > 0 and last.get("RSI_14", 50) < 40:
                _record(contribs, details, "candlestick", "doji_weak_rsi", "Doji + RSI약세", 5)

    # ═══════════════════════════════════════════
    # CATEGORY CAPS — sum + clamp each core category's contribs subtotal
    # against config.SCORE_CATEGORY_CAPS, then fold into score. Must run
    # after all 5 core categories and before any overlay call below.
    # ═══════════════════════════════════════════
    category_totals: dict[str, int] = {}
    for contrib in contribs:
        cat = contrib["category"]
        category_totals[cat] = category_totals.get(cat, 0) + contrib["points"]

    for category, cap in SCORE_CATEGORY_CAPS.items():
        subtotal = category_totals.get(category, 0)
        score += max(-cap, min(cap, subtotal))

    # ═══════════════════════════════════════════
    # CATEGORY 6: MARKET REGIME — VIX / Breadth overlay (low weight)
    # ═══════════════════════════════════════════
    score = _add_market_regime_score(score, details, market)
    score = _add_tradingview_score(score, details, tradingview)

    # ═══════════════════════════════════════════
    # CATEGORY 7: OPTIONS STRUCTURE — canonical options overlay (bounded)
    # ═══════════════════════════════════════════
    score = _add_options_score(score, details, options, current_price, last, prev)

    # ═══════════════════════════════════════════
    # CATEGORY 8: EXTERNAL FLOW — Dark pool / Short overlay (opt-in)
    # ═══════════════════════════════════════════
    score = _add_flow_score(score, details, flow, current_price, last, prev)

    # ═══════════════════════════════════════════
    # CATEGORY 9: OWNERSHIP CONTEXT — SEC 13D/G overlay (opt-in)
    # ═══════════════════════════════════════════
    score = _add_ownership_score(score, details, ownership)

    # ═══════════════════════════════════════════
    # FINAL: clamp + verdict
    # ═══════════════════════════════════════════
    score = min(max(score, 0), 100)

    if score >= 70:
        verdict = "🟢 STRONG — 진입 신호 강함"
    elif score >= 50:
        verdict = "🟡 MODERATE — 조건부 진입"
    elif score >= 30:
        verdict = "🟠 WEAK — 관망 권장"
    else:
        verdict = "🔴 AVOID — 진입 비추천"

    return {
        "score": score,
        "verdict": verdict,
        "details": details,
        "contribs": contribs,
    }


def compile_levels(
    horz_supports: list,
    horz_resistances: list,
    dynamic_levels: list,
    fib_data: dict,
    current_price: float,
    extra_levels: list | None = None,
) -> dict:
    """
    모든 S/R 레벨을 취합하여 정리된 dict로 반환.
    Max distance: ±30% of current price.
    """
    MAX_DIST_PCT = 0.30
    all_supports = []
    all_resistances = []

    # Horizontal — filter by proximity
    for s in horz_supports:
        if abs(s["price"] - current_price) / current_price < MAX_DIST_PCT:
            all_supports.append({"price": s["price"], "source": "Horizontal", "strength": s["strength"]})
    for r in horz_resistances:
        if abs(r["price"] - current_price) / current_price < MAX_DIST_PCT:
            # Resistance that's below current price = broken resistance → support
            if r["price"] < current_price:
                all_supports.append({"price": r["price"], "source": "Horizontal (broken R)", "strength": r["strength"] * 0.7})
            else:
                all_resistances.append({"price": r["price"], "source": "Horizontal", "strength": r["strength"]})

    # Dynamic (MA + Bollinger)
    for dl in dynamic_levels:
        entry = {"price": dl["price"], "source": dl["name"], "distance_pct": dl["distance_pct"]}
        if dl["role"] == "support":
            all_supports.append(entry)
        else:
            all_resistances.append(entry)

    # Fibonacci (multi-timeframe: primary=52w, secondary=104w, alltime)
    if "levels" in fib_data:
        timeframe_tag = ""
        if fib_data.get("primary") is fib_data:
            timeframe_tag = ""
        for name, lv in fib_data["levels"].items():
            entry = {"price": lv["price"], "source": name, "distance_pct": lv["distance_pct"]}
            if lv["role"] == "support":
                all_supports.append(entry)
            else:
                all_resistances.append(entry)

    # Secondary Fib (104w) — tag as separate source
    if fib_data.get("secondary") and "levels" in fib_data["secondary"]:
        for name, lv in fib_data["secondary"]["levels"].items():
            entry = {"price": lv["price"], "source": f"{name} (104W)", "distance_pct": lv["distance_pct"]}
            if lv["role"] == "support":
                all_supports.append(entry)
            else:
                all_resistances.append(entry)

    # All-Time Fib
    if fib_data.get("alltime") and "levels" in fib_data["alltime"]:
        for name, lv in fib_data["alltime"]["levels"].items():
            entry = {"price": lv["price"], "source": f"{name} (All-Time)", "distance_pct": lv["distance_pct"]}
            if lv["role"] == "support":
                all_supports.append(entry)
            else:
                all_resistances.append(entry)

    # Fib confluence — strong levels tagged as "Confluence"
    for cf in fib_data.get("confluence", []):
        entry = {
            "price": cf["price"],
            "source": f"Fib {cf['ratio']} Confluence ({'×'.join(cf['timeframes'])})",
            "distance_pct": cf["distance_pct"],
            "confluence_count": cf["count"],
        }
        if cf["role"] == "support":
            all_supports.append(entry)
        else:
            all_resistances.append(entry)

    # Liquidity levels: AVWAP, Volume POC, Value Area.
    for lv in extra_levels or []:
        entry = {
            "price": lv["price"],
            "source": lv.get("name", lv.get("type", "Liquidity")),
            "distance_pct": lv.get("distance_pct"),
            "type": lv.get("type", "Liquidity"),
        }
        if lv.get("role") == "support":
            all_supports.append(entry)
        else:
            all_resistances.append(entry)

    # Sort
    all_supports = sorted(all_supports, key=lambda x: x["price"], reverse=True)
    all_resistances = sorted(all_resistances, key=lambda x: x["price"])

    return {
        "supports": all_supports,
        "resistances": all_resistances,
        "current_price": current_price,
    }


def find_weekly_pivots(df_weekly, window: int | None = None) -> tuple:
    """주봉 순수 스윙 피벗 (꼬리 조건 없음). major 클러스터(2.5%)."""
    from config import SR_TIERS, WEEKLY_PIVOT_WINDOW
    window = window or WEEKLY_PIVOT_WINDOW
    supports, resistances = [], []
    lows, highs = df_weekly["Low"].to_numpy(), df_weekly["High"].to_numpy()
    for i in range(window, len(df_weekly) - window):
        if lows[i] == lows[i - window:i + window + 1].min():
            supports.append({"price": float(lows[i]), "date": df_weekly.index[i], "strength": 3.0})
        if highs[i] == highs[i - window:i + window + 1].max():
            resistances.append({"price": float(highs[i]), "date": df_weekly.index[i], "strength": 3.0})
    cluster_pct = SR_TIERS["major"]["cluster_pct"]
    return _cluster_levels(supports, cluster_pct), _cluster_levels(resistances, cluster_pct)


def find_monthly_swings(df_monthly) -> tuple:
    """월봉 스윙 고/저 (window=2). major 클러스터(2.5%)."""
    from config import SR_TIERS
    supports, resistances = [], []
    if len(df_monthly) < 6:
        return supports, resistances
    lows, highs = df_monthly["Low"].to_numpy(), df_monthly["High"].to_numpy()
    for i in range(2, len(df_monthly) - 2):
        if lows[i] == lows[i - 2:i + 3].min():
            supports.append({"price": float(lows[i]), "date": df_monthly.index[i], "strength": 4.0})
        if highs[i] == highs[i - 2:i + 3].max():
            resistances.append({"price": float(highs[i]), "date": df_monthly.index[i], "strength": 4.0})
    cluster_pct = SR_TIERS["major"]["cluster_pct"]
    return _cluster_levels(supports, cluster_pct), _cluster_levels(resistances, cluster_pct)


def _tier_of(level: dict) -> str:
    src = str(level.get("source", ""))
    if "200SMA" in src:
        return "major"
    if "100SMA" in src:
        return "intermediate"
    if "Confluence" in src:
        return "major" if level.get("confluence_count", 0) >= 3 else "intermediate"
    if "(104W)" in src or "(All-Time)" in src:
        return "intermediate"
    if src.startswith("Horizontal"):
        return "intermediate" if level.get("strength", 0) >= 5 else "near"
    if level.get("type"):  # liquidity 계열
        # POC from 504d VolumeProfile → major; all other liquidity → intermediate
        if "POC" in src and "(504d)" in src and level.get("type") == "VolumeProfile":
            return "major"
        return "intermediate"
    return "near"


def tier_levels(all_levels, weekly, monthly, df_daily, current_price):
    from config import (CROSS_TIER_DEDUPE_PCT, NEAR_BAND_MAX_PER_SIDE,
                        NEAR_BAND_WIDTH_PCT)
    for side in ("supports", "resistances"):
        for lv in all_levels[side]:
            lv["tier"] = _tier_of(lv)

    def _add_major(price, source):
        entry = {"price": round(float(price), 2), "source": source, "tier": "major",
                 "distance_pct": round((price - current_price) / current_price * 100, 2)}
        (all_levels["supports"] if price < current_price else all_levels["resistances"]).append(entry)

    for s in weekly[0]:
        _add_major(s["price"], "Weekly Pivot")
    for r in weekly[1]:
        _add_major(r["price"], "Weekly Pivot")
    for s in monthly[0]:
        _add_major(s["price"], "Monthly Swing")
    for r in monthly[1]:
        _add_major(r["price"], "Monthly Swing")
    _add_major(float(df_daily["High"].max()), "All-Time High")
    _add_major(float(df_daily["High"].iloc[-252:].max()), "52W High")
    _add_major(float(df_daily["Low"].iloc[-252:].min()), "52W Low")

    rank = {"major": 2, "intermediate": 1, "near": 0}
    for side in ("supports", "resistances"):
        kept = []
        for lv in sorted(all_levels[side], key=lambda x: -rank[x["tier"]]):
            dup = next((k for k in kept
                        if abs(k["price"] - lv["price"]) / max(current_price, 1e-9) < CROSS_TIER_DEDUPE_PCT),
                       None)
            if dup is None:
                kept.append(lv)
        # near 밴드 상한
        band_lo = current_price * (1 - NEAR_BAND_WIDTH_PCT)
        band_hi = current_price * (1 + NEAR_BAND_WIDTH_PCT)
        in_band_near = [lvl for lvl in kept if lvl["tier"] == "near" and band_lo <= lvl["price"] <= band_hi]
        others = [lvl for lvl in kept if lvl not in in_band_near]
        in_band_near.sort(key=lambda lvl: (-lvl.get("confluence_count", 0),
                                           -float(lvl.get("strength", 1.0)),
                                           abs(lvl["price"] - current_price)))
        kept = others + in_band_near[:NEAR_BAND_MAX_PER_SIDE]
        reverse = side == "supports"
        all_levels[side] = sorted(kept, key=lambda x: x["price"], reverse=reverse)
    return all_levels


def build_sr_tiers(all_levels, current_price):
    def _fmt(lv):
        return {"price": lv["price"], "source": lv["source"], "tier": lv["tier"],
                "distance_pct": round((lv["price"] - current_price) / current_price * 100, 2),
                "strength": lv.get("strength")}

    majors_below = [_fmt(lvl) for lvl in all_levels["supports"] if lvl["tier"] == "major"]
    majors_above = [_fmt(lvl) for lvl in all_levels["resistances"] if lvl["tier"] == "major"]
    inter_below = [_fmt(lvl) for lvl in all_levels["supports"] if lvl["tier"] == "intermediate"]
    inter_above = [_fmt(lvl) for lvl in all_levels["resistances"] if lvl["tier"] == "intermediate"]

    def _key_rank(lv):
        return (-(lv.get("strength") or 1.0), abs(lv["distance_pct"]))

    key_below = sorted(majors_below, key=_key_rank) + sorted(inter_below, key=_key_rank)
    key_above = sorted(majors_above, key=_key_rank) + sorted(inter_above, key=_key_rank)
    return {
        "major_supports": majors_below,
        "major_resistances": majors_above,
        "key_below_top3": key_below[:3],
        "key_above_top3": key_above[:3],
    }
