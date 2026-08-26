"""
Layer 4 — Pricing v4.0: 트리거 기반 진입 전략 엔진

전략을 "성격(Aggressive/Safe)"이 아니라 "트리거 유형(Support Bounce/Trend Confirm/Breakout)"으로 분류.
각 전략은 구체적 진입 조건, 맞춤형 목표가, 홀딩 기간, 무효화 조건을 포함.
"""
import math

import numpy as np

# ─── 전략 유형 정의 ─────────────────────────────────────────────

PLAN_TYPES = {
    "support_bounce": {
        "key": "support_bounce",
        "emoji": "📍",
        "label": "Support Bounce",
        "desc": "지지선 터치 → 반등 캔들 확인 후 진입",
        "hold": "1-5 days",
    },
    "trend_confirm": {
        "key": "trend_confirm",
        "emoji": "📈",
        "label": "Trend Confirm",
        "desc": "MA 돌파 종가 확인 + 거래량 확인 후 진입",
        "hold": "1-4 weeks",
    },
    "breakout": {
        "key": "breakout",
        "emoji": "⚡",
        "label": "Breakout",
        "desc": "저항 돌파 + 거래량 폭증 시 추격 매수",
        "hold": "2-8 weeks",
    },
    "no_signal": {
        "key": "no_signal",
        "emoji": "🚫",
        "label": "No Signal",
        "desc": "모든 조건 미충족 — 관망",
        "hold": None,
    },
}


# ─── 유틸리티 ──────────────────────────────────────────────────

def _closest(price: float, levels: list, direction: str = "below") -> dict | None:
    """price 기준 가장 가까운 레벨."""
    cands = [
        l for l in levels
        if isinstance(l.get("price"), (int, float))
        and ((direction == "below" and l["price"] < price)
             or (direction == "above" and l["price"] > price))
    ]
    if not cands:
        return None
    return max(cands, key=lambda x: x["price"]) if direction == "below" else min(cands, key=lambda x: x["price"])


def _dist_pct(a: float, b: float) -> float:
    return abs(a - b) / a * 100


def _safe_float(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _format_price(value: float | None, currency: str = "USD") -> str:
    if not isinstance(value, (int, float)):
        return "N/A"
    currency = str(currency or "USD").upper()
    if currency == "KRW":
        return f"₩{value:,.0f}"
    if currency == "JPY":
        return f"¥{value:,.0f}"
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}"


def _krw_tick_size(price: float) -> int:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _round_price(value: float, currency: str = "USD", direction: str = "nearest") -> float:
    currency = str(currency or "USD").upper()
    if currency == "KRW":
        tick = _krw_tick_size(value)
        match direction:
            case "up":
                return float(math.ceil(value / tick) * tick)
            case "down":
                return float(math.floor(value / tick) * tick)
            case "nearest":
                return float(round(value / tick) * tick)
            case _:
                return float(round(value / tick) * tick)
    if currency == "JPY":
        return float(round(value))
    return round(float(value), 2)


def _first_rr(plan: dict) -> float | None:
    entry = _safe_float(plan.get("entry"))
    stop = _safe_float(plan.get("stop"))
    targets = plan.get("targets") or []
    if not entry or not stop or entry <= stop or not targets:
        return None
    first_price = _safe_float(targets[0].get("price"))
    if first_price is None:
        return None
    return round((first_price - entry) / (entry - stop), 2)


def _finalize_plan(
    plan: dict, currency: str,
    consensus: dict | None = None, major_supports: list | None = None,
) -> dict:
    rr = _first_rr(plan)
    plan["currency"] = str(currency or "USD").upper()
    plan["first_target_rr"] = rr
    plan["entry_tranche_pct"] = plan.get("size_pct_base", 0)
    plan["position_sizing_note"] = (
        "entry_tranche_pct is a tactical entry tranche, not a portfolio allocation recommendation."
    )
    if rr is None:
        plan["confidence"] = "low"
    elif rr < 1.0:
        plan["confidence"] = "low"
        plan["quality"] = min(int(plan.get("quality", 0) or 0), 39)
        plan["rr_warning"] = "First target R:R is below 1.0; plan is informational only."
    elif rr < 1.5:
        plan["confidence"] = "medium"
        plan["quality"] = min(int(plan.get("quality", 0) or 0), 69)
    else:
        plan["confidence"] = "medium" if int(plan.get("quality", 0) or 0) < 80 else "high"

    # 컨센서스 연동: 합의도 낮으면 confidence 1단계 강등
    from config import AGREEMENT_CONFIDENCE_THRESHOLD
    agreement = (consensus or {}).get("agreement")
    if agreement is not None and agreement < AGREEMENT_CONFIDENCE_THRESHOLD:
        order = ["low", "medium", "high"]
        cur = plan.get("confidence", "medium")
        plan["confidence"] = order[max(0, order.index(cur) - 1)] if cur in order else "low"
        plan["consensus_warning"] = f"지표 합의도 낮음 ({agreement}/100) — 신호 혼재"
    # 직하방 major 지지 주석
    entry, stop = plan.get("entry"), plan.get("stop")
    if entry and stop and major_supports:
        between = [s["price"] for s in major_supports if stop < s["price"] < entry]
        if between:
            plan["major_support_nearby"] = max(between)
    return plan


# ─── 시장 레짐 & 13D/G 사이즈 조정 ──────────────────────────────

def _market_size_multiplier(market_regime: dict | None) -> tuple[float, str]:
    if not market_regime:
        return 1.0, ""
    m = float(market_regime.get("size_multiplier", 1.0) or 1.0)
    label = f"Market {market_regime.get('regime', '')}"
    return m, label


def _ownership_size_multiplier(ownership: dict | None) -> tuple[float, list[str]]:
    if not ownership:
        return 1.0, []
    events = ownership.get("events") or []
    if not events:
        return 1.0, []
    mult = 1.0
    notes = []
    for ev in events[:3]:
        form = str(ev.get("form_type") or "").upper()
        owner = str(ev.get("reporting_owner") or "holder")[:28]
        delta = _safe_float(ev.get("ownership_delta_pct"))
        if ev.get("dropped_below_5pct_flag"):
            mult = min(mult, 0.85)
            notes.append(f"13D/G 5% 이탈 ({owner})")
        elif delta is not None and delta <= -1.0:
            mult = min(mult, 0.92)
            notes.append(f"13D/G 지분 {delta:+.1f}pp")
        elif form.startswith("SC 13D") and (ev.get("event_type") == "new" or ev.get("converted_13g_to_13d_flag")):
            mult = min(mult, 0.95)
            notes.append(f"active 13D ({owner})")
    return max(0.35, min(1.0, mult)), notes[:3]


# ─── 트리거 평가 함수 ──────────────────────────────────────────

def _eval_support_bounce(
    current_price: float, entry_score: int,
    supports: list, resistances: list, df, atr: float | None, currency: str = "USD",
    consensus: dict | None = None, major_supports: list | None = None,
) -> dict | None:
    """지지선 반등 전략. 지지선 근접 + 반등 캔들 패턴."""
    if entry_score < 50:
        return None

    support = _closest(current_price, supports, "below")
    if not support:
        return None

    dist = _dist_pct(current_price, support["price"])
    if dist > 5:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    # 반등 캔들 조건: 이전 봉 하락 후 현재 봉 양봉 or 해머
    candle_bullish = last.Close > last.Open
    bounce_from_low = last.Close > last.Low + (last.High - last.Low) * 0.5
    not_oversold_extreme = True
    if "RSI_14" in df.columns:
        rsi_now = float(last["RSI_14"]) if not np.isnan(last["RSI_14"]) else 50
        not_oversold_extreme = rsi_now > 25

    # 트리거 텍스트
    trigger_parts = [
        f"{_format_price(support['price'], currency)} 지지선 터치",
    ]
    if candle_bullish:
        trigger_parts.append("양봉 형성")
    if bounce_from_low:
        trigger_parts.append("캔들 저점 대비 상단 복귀")
    if not_oversold_extreme:
        trigger_parts.append("RSI 과매도 아님")

    # 진입가/손절가
    entry = _round_price(support["price"] * 1.005, currency, "up")
    stop = _round_price(support["price"] * 0.97, currency, "down")
    risk_pct = round((entry - stop) / entry * 100, 2)

    # 타겟: 직전 저항까지만 (단기) — R:R 최소 1.0 이상인 것만
    targets = []
    for r in resistances:
        if r["price"] <= entry * 1.02:
            continue
        gain = (r["price"] / entry - 1) * 100
        risk_val = entry - stop
        rr = (r["price"] - entry) / risk_val if risk_val > 0 else 0
        if rr >= 1.0:
            target_price = _round_price(r["price"], currency, "down")
            targets.append({"level": 1, "price": target_price,
                            "gain_pct": round((target_price / entry - 1) * 100, 2),
                            "source": r.get("source", "resistance")})
            break
    if not targets:
        # R:R 1.0 이상 저항 없으면 5% ext
        t1 = round(entry * 1.05, 2)
        targets.append({"level": 1, "price": _round_price(t1, currency, "down"), "gain_pct": round((t1 / entry - 1) * 100, 2), "source": "5% ext"})
    # 2차 타겟 추가
    if targets:
        used_prices = {t["price"] for t in targets}
        next_resistances = sorted(
            [r for r in resistances if r.get("price", 0) > targets[0]["price"]],
            key=lambda r: r["price"],
        )
        for next_r in next_resistances:
            target_price = _round_price(next_r["price"], currency, "down")
            if target_price in used_prices:
                continue
            targets.append({"level": 2, "price": target_price,
                            "gain_pct": round((target_price / entry - 1) * 100, 2),
                            "source": next_r.get("source", "resistance")})
            break

    # 품질 점수 (0-100)
    quality = 40
    if dist < 2:
        quality += 20
    elif dist < 3:
        quality += 10
    if candle_bullish:
        quality += 15
    if bounce_from_low:
        quality += 10
    if not_oversold_extreme:
        quality += 5
    quality = min(100, quality)

    plan = {
        "type": "support_bounce",
        "quality": quality,
        "trigger": " · ".join(trigger_parts),
        "entry": entry,
        "stop": stop,
        "risk_pct": risk_pct,
        "targets": targets,
        "size_pct_base": 40,
        "hold_period": PLAN_TYPES["support_bounce"]["hold"],
        "invalidation": f"{_format_price(stop, currency)} 이탈 시 무효 (손절선)",
    }
    return _finalize_plan(plan, currency, consensus, major_supports)


def _eval_trend_confirm(
    current_price: float, entry_score: int,
    supports: list, resistances: list, df, atr: float | None, currency: str = "USD",
    consensus: dict | None = None, major_supports: list | None = None,
) -> dict | None:
    """추세 확인 전략. 20EMA 위 종가 + 거래량 확인."""
    if entry_score < 60:
        return None

    last = df.iloc[-1]
    ema20 = float(last["EMA_20"]) if "EMA_20" in df.columns and not np.isnan(last["EMA_20"]) else None
    sma20 = float(last["SMA_20"]) if "SMA_20" in df.columns and not np.isnan(last["SMA_20"]) else None
    ma = ema20 or sma20
    if not ma:
        return None

    # MA 위에 있거나 근처 (2% 이내)
    above_ma = current_price >= ma
    near_ma = _dist_pct(current_price, ma) < 2

    if not above_ma and not near_ma:
        return None

    # 거래량 확인
    vol_col = "Volume" if "Volume" in df.columns else None
    vol_ok = True
    if vol_col:
        avg_vol = float(df[vol_col].iloc[-20:].mean()) if len(df) >= 20 else 0
        cur_vol = float(last[vol_col])
        vol_ok = cur_vol >= avg_vol * 0.8 if avg_vol > 0 else True

    # 트리거
    trigger_parts = []
    if above_ma:
        trigger_parts.append(f"20EMA({_format_price(ma, currency)}) 위 종가")
    else:
        trigger_parts.append(f"20EMA({_format_price(ma, currency)}) 접근 중 ({_dist_pct(current_price, ma):.1f}%)")
    if vol_ok:
        trigger_parts.append("거래량 양호")
    else:
        trigger_parts.append("거래량 부족 (확인 필요)")

    # 진입/손절
    if above_ma:
        entry = _round_price(current_price * 1.002, currency, "up")
        stop = _round_price(ma * 0.97, currency, "down")
    else:
        entry = _round_price(ma * 1.005, currency, "up")
        stop = _round_price(ma * 0.97, currency, "down")
    risk_pct = round((entry - stop) / entry * 100, 2)

    # 타겟: 다음 2개 저항 (중기)
    targets = []
    used = set()
    for i in range(2):
        r = _closest(entry if i == 0 else targets[-1]["price"], resistances, "above")
        if r:
            target_price = _round_price(r["price"], currency, "down")
            if target_price in used:
                continue
            targets.append({"level": i + 1, "price": target_price,
                            "gain_pct": round((target_price / entry - 1) * 100, 2),
                            "source": r.get("source", f"resistance")})
            used.add(target_price)
    if not targets:
        t1 = _round_price(current_price * 1.05, currency, "down")
        targets.append({"level": 1, "price": t1, "gain_pct": round((t1 / entry - 1) * 100, 2), "source": "5% ext"})

    # 품질
    quality = 50
    if above_ma:
        quality += 15
    if vol_ok:
        quality += 15
    if entry_score >= 70:
        quality += 10
    if entry_score >= 80:
        quality += 10
    quality = min(100, quality)

    ma_label = "EMA" if ema20 else "SMA"
    plan = {
        "type": "trend_confirm",
        "quality": quality,
        "trigger": " · ".join(trigger_parts),
        "entry": entry,
        "stop": stop,
        "risk_pct": risk_pct,
        "targets": targets,
        "size_pct_base": 60,
        "hold_period": PLAN_TYPES["trend_confirm"]["hold"],
        "invalidation": f"20{ma_label}({_format_price(ma, currency)}) 이탈 시 재검토",
    }
    return _finalize_plan(plan, currency, consensus, major_supports)


def _eval_breakout(
    current_price: float, entry_score: int,
    supports: list, resistances: list, df, atr: float | None, currency: str = "USD",
    consensus: dict | None = None, major_supports: list | None = None,
) -> dict | None:
    """돌파 전략. 저항 근접 + 돌파 모멘텀."""
    if entry_score < 65:
        return None

    resist = _closest(current_price, resistances, "above")
    if not resist:
        return None

    dist = _dist_pct(current_price, resist["price"])
    if dist > 4:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    # 모멘텀 조건
    momentum_up = last.Close > prev.Close
    rsi_ok = True
    if "RSI_14" in df.columns:
        rsi_now = float(last["RSI_14"]) if not np.isnan(last["RSI_14"]) else 50
        rsi_ok = 45 < rsi_now < 75  # 과매수 아닌 상태에서 상승 모멘텀

    # 거래량
    vol_col = "Volume" if "Volume" in df.columns else None
    vol_surge = False
    if vol_col:
        avg_vol = float(df[vol_col].iloc[-20:].mean()) if len(df) >= 20 else 0
        cur_vol = float(last[vol_col])
        vol_surge = cur_vol > avg_vol * 1.3 if avg_vol > 0 else False

    # 트리거
    trigger_parts = [
        f"{_format_price(resist['price'], currency)} 저항 접근 ({dist:.1f}%)",
    ]
    if momentum_up:
        trigger_parts.append("연속 상승 모멘텀")
    if vol_surge:
        trigger_parts.append("거래량 급증")
    if rsi_ok:
        trigger_parts.append("RSI 적정 구간")

    # 진입: 저항 위 종가 확인 시
    entry = _round_price(resist["price"] * 1.003, currency, "up")
    stop = _round_price(resist["price"] * 0.97, currency, "down")
    risk_pct = round((entry - stop) / entry * 100, 2)

    # 타겟: 피보나치 확장 (장기)
    targets = []
    r2 = _closest(resist["price"], resistances, "above")
    if r2:
        target_price = _round_price(r2["price"], currency, "down")
        targets.append({"level": 1, "price": target_price,
                        "gain_pct": round((target_price / entry - 1) * 100, 2),
                        "source": r2.get("source", "next resistance")})
    high_52w = float(df.High.iloc[-252:].max()) if len(df) >= 252 else float(df.High.max())
    if high_52w > entry * 1.1:
        targets.append({"level": 2, "price": _round_price(high_52w, currency, "down"),
                        "gain_pct": round((high_52w / entry - 1) * 100, 2),
                        "source": "52-week high"})

    # 품질
    quality = 45
    if dist < 2:
        quality += 15
    if momentum_up:
        quality += 15
    if vol_surge:
        quality += 15
    if rsi_ok:
        quality += 10
    quality = min(100, quality)

    plan = {
        "type": "breakout",
        "quality": quality,
        "trigger": " · ".join(trigger_parts),
        "entry": entry,
        "stop": stop,
        "risk_pct": risk_pct,
        "targets": targets,
        "size_pct_base": 50,
        "hold_period": PLAN_TYPES["breakout"]["hold"],
        "invalidation": f"{_format_price(stop, currency)} 이탈 시 실패 (손절선)",
    }
    return _finalize_plan(plan, currency, consensus, major_supports)


# ─── 메인 엔진 ──────────────────────────────────────────────────

def build_entry_plans(
    current_price: float,
    entry_score: int,
    all_supports: list,
    all_resistances: list,
    df,
    market_regime: dict | None = None,
    ownership: dict | None = None,
    options_data: dict | None = None,
    currency: str = "USD",
    consensus: dict | None = None,
    major_supports: list | None = None,
) -> dict:
    """
    트리거 기반 진입 전략 엔진.

    Returns:
        {
            "recommended": plan | None,
            "alternatives": [plan, ...],
            "all_plans": [plan, ...],
            "summary": str,
        }
    """
    # ATR
    atr = None
    if "ATRr_14" in df.columns:
        v = df.iloc[-1]["ATRr_14"]
        atr = float(v) if not np.isnan(v) else None

    # 전략 평가
    plans = []

    sb = _eval_support_bounce(current_price, entry_score, all_supports, all_resistances, df, atr, currency,
                               consensus, major_supports)
    if sb:
        plans.append(sb)

    tc = _eval_trend_confirm(current_price, entry_score, all_supports, all_resistances, df, atr, currency,
                              consensus, major_supports)
    if tc:
        plans.append(tc)

    bo = _eval_breakout(current_price, entry_score, all_supports, all_resistances, df, atr, currency,
                         consensus, major_supports)
    if bo:
        plans.append(bo)

    # quality 내림차순 정렬
    plans.sort(key=lambda p: p["quality"], reverse=True)

    # 사이즈 조정 (market + ownership)
    m_mult, m_label = _market_size_multiplier(market_regime)
    o_mult, o_notes = _ownership_size_multiplier(ownership)

    for p in plans:
        base = p["size_pct_base"]
        adjusted = int(round(base * m_mult * o_mult))
        p["size_pct"] = adjusted
        p["entry_tranche_pct"] = adjusted
        if m_mult != 1.0:
            p["size_note"] = f"{m_label}: {base}%→{adjusted}%"
        if o_mult != 1.0:
            p["size_note"] = (p.get("size_note", "") + f" | 13D/G: →{adjusted}%").strip(" |")

    # Options expiry → valid_until
    if options_data:
        selected = options_data.get("selected_expiry") or {}
        expiry = selected.get("expiry")
        dte = selected.get("days_to_expiry")
        if expiry and dte and isinstance(dte, int) and dte <= 30:
            for p in plans:
                p["valid_until"] = f"{expiry} (DTE {dte})"

    # 추천 선정
    recommended = None
    alt_candidates = []

    if plans:
        # quality >= 60이면 추천
        if plans[0]["quality"] >= 60:
            recommended = plans[0]
            alt_candidates = plans[1:3]  # 최대 2개 대안
        elif plans[0]["quality"] >= 40:
            # quality 40-59: 조건부 추천
            recommended = plans[0]
            recommended["conditional"] = True
            alt_candidates = plans[1:2]
        else:
            # quality < 40: 관망
            pass

    # 관망 메시지
    if not recommended:
        summary = _build_no_signal_summary(current_price, entry_score, all_supports, all_resistances, df)
    else:
        summary = _build_recommendation_summary(recommended, current_price)

    # 대안에 표시 정보 축약
    for alt in alt_candidates:
        alt["_role"] = "alternative"

    # no_signal 플랜 생성
    if not recommended:
        ns = _build_no_signal_plan(current_price, entry_score, all_supports, all_resistances, df, currency)
        all_plans = plans + [ns]
        return {
            "recommended": None,
            "alternatives": [],
            "all_plans": all_plans,
            "summary": summary,
        }

    return {
        "recommended": recommended,
        "alternatives": alt_candidates,
        "all_plans": plans,
        "summary": summary,
    }


def _build_no_signal_plan(
    current_price, entry_score, supports, resistances, df, currency: str = "USD",
) -> dict:
    """관망 전략 생성."""
    reasons = []
    if entry_score < 50:
        reasons.append(f"Entry Score {entry_score} — 신호 약함")

    nearest_resist = _closest(current_price, resistances, "above")
    if nearest_resist:
        dist = _dist_pct(current_price, nearest_resist["price"])
        if dist < 2:
            reasons.append(f"저항선 {_format_price(nearest_resist['price'], currency)} 근접 ({dist:.1f}%)")

    nearest_support = _closest(current_price, supports, "below")
    if nearest_support:
        dist = _dist_pct(current_price, nearest_support["price"])
        reasons.append(f"지지선 {_format_price(nearest_support['price'], currency)}까지 {dist:.1f}% 하락 여지")

    # 다음 진입 조건
    next_conditions = []
    if nearest_resist:
        next_conditions.append(f"{_format_price(nearest_resist['price'], currency)} 돌파 확인 → Breakout 전략")
    if nearest_support:
        next_conditions.append(f"{_format_price(nearest_support['price'], currency)} 터치+반봉 → Support Bounce 전략")
    if entry_score < 60:
        next_conditions.append("Entry Score 60+ 상승 → Trend Confirm 전략 활성화")

    return {
        "type": "no_signal",
        "quality": 0,
        "trigger": " · ".join(reasons) if reasons else "조건 미충족",
        "entry": None,
        "stop": None,
        "risk_pct": None,
        "targets": [],
        "size_pct_base": 0,
        "size_pct": 0,
        "hold_period": None,
        "invalidation": None,
        "next_conditions": next_conditions,
    }


def _build_no_signal_summary(current_price, entry_score, supports, resistances, df) -> str:
    """관망 상태 요약."""
    parts = [f"Entry Score {entry_score}/100"]

    ns = _closest(current_price, supports, "below")
    nr = _closest(current_price, resistances, "above")

    if nr:
        parts.append(f"저항 {nr['price']:.2f}({_dist_pct(current_price, nr['price']):.1f}%)")
    if ns:
        parts.append(f"지지 {ns['price']:.2f}({_dist_pct(current_price, ns['price']):.1f}%)")

    return " · ".join(parts)


def _build_recommendation_summary(plan: dict, current_price: float) -> str:
    """추천 전략 한줄 요약."""
    meta = PLAN_TYPES.get(plan["type"], {})
    label = meta.get("label", plan["type"])
    t1 = plan["targets"][0] if plan.get("targets") else None
    if t1:
        return f"{label} — {plan['trigger']} → T1 ${t1['price']:.2f}({t1['gain_pct']:+.1f}%)"
    return f"{label} — {plan['trigger']}"


# ─── 레거시 호환: determine_entry_strategies ────────────────────

def determine_entry_strategies(
    current_price, entry_score, all_supports, all_resistances, df,
    market_regime=None, ownership=None, currency: str = "USD",
) -> list:
    """기존 인터페이스 호환 wrapper. build_entry_plans 결과를 레거시 포맷으로 변환."""
    result = build_entry_plans(
        current_price, entry_score, all_supports, all_resistances, df,
        market_regime=market_regime, ownership=ownership, currency=currency,
    )
    strategies = []
    for p in result["all_plans"]:
        meta = PLAN_TYPES.get(p["type"], {})
        if p["type"] == "no_signal":
            strategies.append({
                "type": f"{meta.get('emoji', '')} Wait",
                "entry": None,
                "stop": None,
                "risk_pct": None,
                "condition": p.get("trigger", "조건 미충족"),
                "size_pct": 0,
                "score_required": None,
            })
        else:
            strategies.append({
                "type": f"{meta.get('emoji', '')} {meta.get('label', p['type'])}",
                "entry": p.get("entry"),
                "stop": p.get("stop"),
                "risk_pct": p.get("risk_pct"),
                "condition": p.get("trigger", ""),
                "size_pct": p.get("size_pct", p.get("size_pct_base", 40)),
                "score_required": None,
                "targets_rr": [
                    {**t, "rr_ratio": round((t["price"] - p["entry"]) / (p["entry"] - p["stop"]), 2)
                     if p["entry"] and p["stop"] and p["entry"] > p["stop"] else 0}
                    for t in p.get("targets", [])
                ],
            })
    return strategies


# ─── 기존 인터페이스 유지 ──────────────────────────────────────

def calculate_targets(current_price, all_resistances, df) -> list:
    """3단계 목표가 계산 (기존 유지)."""
    targets = []
    r1 = _closest(current_price, all_resistances, "above")
    t1 = r1["price"] if r1 and r1["price"] > current_price * 1.02 else round(current_price * 1.05, 2)
    targets.append({"level": 1, "price": t1,
                    "gain_pct": round((t1 / current_price - 1) * 100, 2),
                    "source": r1.get("source", "") if r1 and r1["price"] > current_price * 1.02 else "5% extension"})

    r2_cands = [r for r in all_resistances if r["price"] > t1 * 1.01]
    t2 = r2_cands[0]["price"] if r2_cands else round(current_price * 1.10, 2)
    targets.append({"level": 2, "price": t2,
                    "gain_pct": round((t2 / current_price - 1) * 100, 2),
                    "source": r2_cands[0].get("source", "") if r2_cands else "10% extension"})

    high_52w = float(df.High.iloc[-252:].max()) if len(df) >= 252 else float(df.High.max())
    t3 = high_52w if high_52w > current_price * 1.15 else round(current_price * 1.20, 2)
    targets.append({"level": 3, "price": t3,
                    "gain_pct": round((t3 / current_price - 1) * 100, 2),
                    "source": "52-week high" if high_52w > current_price * 1.15 else "20% extension"})
    return targets


def calculate_risk_reward(entry, stop, targets) -> list:
    """각 목표가에 대한 R:R ratio 계산 (기존 유지)."""
    if not entry or not stop or entry <= stop:
        return []
    risk = entry - stop
    results = []
    for t in targets:
        reward = t["price"] - entry
        rr = round(reward / risk, 2) if risk > 0 else 0
        results.append({**t, "rr_ratio": rr})
    return results


def ownership_size_adjustment(ownership):
    """기존 인터페이스 호환."""
    return _ownership_size_multiplier(ownership)
