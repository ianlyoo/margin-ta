"""리스크 판정: 시장 지표 signal + 종합 스코어 + 섹터 스코어. Spec #2 §1-3."""
from __future__ import annotations

import pandas as pd
from config import (
    RISK_GROUP_WEIGHTS,
    RISK_SIGNAL_RULES,
    SECTOR_COMPONENT_WEIGHTS,
    SECTOR_UNIVERSE,
    SIGNAL_SCORE_MAP,
)
from ta.trend import CCIIndicator
from timeframes import resample_ohlcv

MARKET_GROUPS = {
    "volatility": ["vix_level", "vxn_minus_vix", "vix_term_structure", "vvix"],
    "overheating": ["index_cci_monthly", "ma200_gap", "monthly_rsi", "drawdown"],
    "credit_rates": ["hy_spread_change", "yield_curve"],
    "breadth": ["breadth_divergence"],
    "safe_haven": ["gld_spy_rs", "dxy_change"],
}


def _percentile(series: pd.Series, value: float) -> float | None:
    if series is None or len(series) < 1260:  # 5년 미만
        return None
    return round(float((series <= value).mean()) * 100, 1)


def _ind(value, percentile, signal, note="") -> dict:
    return {"value": value, "percentile": percentile, "signal": signal, "note": note}


def _unavail() -> dict:
    return {"value": None, "percentile": None, "signal": "unavailable", "note": ""}


def _sig_abs(value: float, warn: float, alert: float) -> str:
    v = abs(value)
    return "alert" if v >= alert else "warn" if v >= warn else "ok"


def compute_market_indicators(risk_data: dict) -> dict:
    t = risk_data.get("tickers", {})
    fred = risk_data.get("fred", {})
    out: dict = {}

    # 변동성
    if "vix" in t:
        v = float(t["vix"].iloc[-1])
        r = RISK_SIGNAL_RULES["vix_level"]
        out["vix_level"] = _ind(round(v, 1), _percentile(t["vix"], v),
                                "alert" if v >= r["alert"] else "warn" if v >= r["warn"] else "ok", "VIX 레벨")
    else:
        out["vix_level"] = _unavail()

    if "vxn" in t and "vix" in t:
        spread = float(t["vxn"].iloc[-1]) - float(t["vix"].iloc[-1])
        r = RISK_SIGNAL_RULES["vxn_minus_vix"]
        out["vxn_minus_vix"] = _ind(round(spread, 2), None,
                                    "alert" if spread >= r["alert"] else "warn" if spread >= r["warn"] else "ok",
                                    "VXN−VIX 스프레드")
    else:
        out["vxn_minus_vix"] = _unavail()

    if "vix9d" in t and "vix" in t:
        ratio = float(t["vix9d"].iloc[-1]) / max(float(t["vix"].iloc[-1]), 1e-9)
        r = RISK_SIGNAL_RULES["vix_term_structure"]
        out["vix_term_structure"] = _ind(round(ratio, 3), None,
                                          "alert" if ratio >= r["alert"] else "warn" if ratio >= r["warn"] else "ok",
                                          "VIX9D/VIX (≥1=역전)")
    else:
        out["vix_term_structure"] = _unavail()

    if "vvix" in t:
        v = float(t["vvix"].iloc[-1]); r = RISK_SIGNAL_RULES["vvix"]
        out["vvix"] = _ind(round(v, 1), _percentile(t["vvix"], v),
                           "alert" if v >= r["alert"] else "warn" if v >= r["warn"] else "ok", "VVIX")
    else:
        out["vvix"] = _unavail()

    # 과열 — 월봉 CCI (S&P 대표) : sp500 티커 사용
    if "sp500" in t:
        out["index_cci_monthly"] = _monthly_cci(t["sp500"])
        out["ma200_gap"] = _ma200_gap(t["sp500"])
        out["monthly_rsi"] = _monthly_rsi_ind(t["sp500"])
        out["drawdown"] = _drawdown(t["sp500"])
    else:
        for k in ("index_cci_monthly", "ma200_gap", "monthly_rsi", "drawdown"):
            out[k] = _unavail()

    # 크레딧/금리
    out["hy_spread_change"] = _hy_spread(t, fred)
    out["yield_curve"] = _yield_curve(t, fred)

    # 브레드스: 지수 고점권 + breadth < 50% = divergence(경고)
    out["breadth_divergence"] = _breadth_divergence(t, risk_data.get("sectors", {}), risk_data.get("breadth"))

    # 안전자산
    out["gld_spy_rs"] = _gld_spy(t)
    out["dxy_change"] = _dxy(t)

    return out


def _monthly_cci(close: pd.Series) -> dict:
    df = pd.DataFrame({"High": close, "Low": close, "Close": close})
    m = resample_ohlcv(df, "ME")
    if len(m) < 20:
        return _unavail()
    cci = CCIIndicator(m["High"], m["Low"], m["Close"], window=20).cci()
    val = float(cci.iloc[-1])
    r = RISK_SIGNAL_RULES["index_cci_monthly"]
    # 월봉 ≥200 = warn; +주봉 CCI 하향전환 = alert
    signal = "ok"
    if val >= r["warn"]:
        signal = "warn"
        w = resample_ohlcv(df, "W-FRI")
        if len(w) >= 21:
            wcci = CCIIndicator(w["High"], w["Low"], w["Close"], window=20).cci()
            if len(wcci) >= 2 and wcci.iloc[-1] < wcci.iloc[-2]:
                signal = "alert"
    return _ind(round(val, 1), None, signal, "지수 월봉 CCI(20)")


def _ma200_gap(close: pd.Series) -> dict:
    if len(close) < 200:
        return _unavail()
    ma200 = close.rolling(200).mean()
    gap = (close - ma200) / ma200 * 100
    val = float(gap.iloc[-1])
    pct = _percentile(gap.dropna(), val)
    r = RISK_SIGNAL_RULES["ma200_gap"]
    signal = "ok"
    if pct is not None:
        signal = "alert" if pct >= r["alert"] else "warn" if pct >= r["warn"] else "ok"
    return _ind(round(val, 2), pct, signal, "200일선 이격도 percentile")


def _monthly_rsi_ind(close: pd.Series) -> dict:
    from ta.momentum import RSIIndicator
    df = pd.DataFrame({"High": close, "Low": close, "Close": close})
    m = resample_ohlcv(df, "ME")
    if len(m) < 15:
        return _unavail()
    rsi = RSIIndicator(m["Close"], window=14).rsi()
    val = float(rsi.iloc[-1]); r = RISK_SIGNAL_RULES["monthly_rsi"]
    return _ind(round(val, 1), None, "alert" if val >= r["alert"] else "warn" if val >= r["warn"] else "ok", "월봉 RSI")


def _drawdown(close: pd.Series) -> dict:
    ath = float(close.cummax().iloc[-1])
    val = (float(close.iloc[-1]) - ath) / ath * 100
    r = RISK_SIGNAL_RULES["drawdown"]  # 음수: 낙폭 클수록 위험
    signal = "alert" if val <= r["alert"] else "warn" if val <= r["warn"] else "ok"
    return _ind(round(val, 2), None, signal, "ATH 대비 낙폭%")


def _hy_spread(t: dict, fred: dict) -> dict:
    # primary: HYG/LQD 비율 20일 변화; FRED OAS 있으면 note에 첨부
    if "hyg" in t and "lqd" in t:
        ratio = (t["hyg"] / t["lqd"]).dropna()
        if len(ratio) >= 21:
            change = (float(ratio.iloc[-1]) - float(ratio.iloc[-21])) / float(ratio.iloc[-21])
            # HY 악화 = HYG 하락 = 비율 하락 → 음의 변화가 위험
            r = RISK_SIGNAL_RULES["hy_spread_change"]
            mag = abs(change) if change < 0 else 0.0
            signal = "alert" if mag >= r["alert"] else "warn" if mag >= r["warn"] else "ok"
            note = "HYG/LQD 20일 변화"
            oas = fred.get("hy_oas", {}).get("last")
            if oas is not None:
                note += f" (FRED OAS {oas})"
            return _ind(round(change * 100, 2), None, signal, note)
    return _unavail()


def _yield_curve(t: dict, fred: dict) -> dict:
    # primary: ^TNX − ^IRX (10Y − 13W). 역전 = warn
    if "tnx" in t and "irx" in t:
        spread = float(t["tnx"].iloc[-1]) - float(t["irx"].iloc[-1])
        signal = "warn" if spread < 0 else "ok"
        note = "10Y−3M"
        t10y2y = fred.get("yield_10y2y", {}).get("last")
        if t10y2y is not None:
            note += f" (FRED 10Y−2Y {t10y2y})"
            if t10y2y < 0:
                signal = "warn"
        return _ind(round(spread, 2), None, signal, note)
    return _unavail()


def _gld_spy(t: dict) -> dict:
    if "gld" in t and "spy" in t:
        rs = (t["gld"] / t["spy"]).dropna()
        if len(rs) >= 20:
            slope = (float(rs.iloc[-1]) - float(rs.iloc[-20])) / float(rs.iloc[-20]) * 100
            signal = "warn" if slope > 3 else "ok"  # 금 급등 = 위험 회피
            return _ind(round(slope, 2), None, signal, "GLD/SPY 20일 기울기%")
    return _unavail()


def _dxy(t: dict) -> dict:
    if "dxy" in t:
        s = t["dxy"].dropna()
        if len(s) >= 21:
            change = (float(s.iloc[-1]) - float(s.iloc[-21])) / float(s.iloc[-21]) * 100
            r = RISK_SIGNAL_RULES["dxy_change"]
            signal = "alert" if change >= r["alert"] else "warn" if change >= r["warn"] else "ok"
            return _ind(round(change, 2), None, signal, "DXY 20일 변화%")
    return _unavail()


def _sector_breadth_pct(sectors: dict) -> float | None:
    """US 섹터 ETF 중 200일선 위 비율 — 종목 breadth 캐시 없을 때의 프록시."""
    from config import SECTOR_UNIVERSE
    vals = []
    for sym in SECTOR_UNIVERSE["us"]:
        s = sectors.get(sym)
        if s is None or len(s) < 200:
            continue
        ma200 = s.rolling(200).mean().iloc[-1]
        if pd.isna(ma200):
            continue
        vals.append(1.0 if float(s.iloc[-1]) > float(ma200) else 0.0)
    if len(vals) < 5:   # 표본 부족
        return None
    return round(sum(vals) / len(vals) * 100, 1)


def _breadth_divergence(t: dict, sectors: dict, breadth: dict | None) -> dict:
    """지수는 강한데 참여 폭이 좁으면 divergence(경고).

    breadth% 우선순위: 종목 단위 캐시(above_200ma_pct) > 섹터 ETF 프록시.
    """
    pct = None
    source = ""
    if isinstance(breadth, dict) and breadth.get("above_200ma_pct") is not None:
        pct = float(breadth["above_200ma_pct"])
        source = "종목 breadth 캐시"
    else:
        pct = _sector_breadth_pct(sectors)
        source = "섹터 ETF 프록시"
    if pct is None:
        return _unavail()

    # 지수 강세 판정: S&P가 200일선 위 (없으면 판정 불가)
    sp = t.get("sp500")
    if sp is None or len(sp) < 200:
        return _ind(pct, None, "ok", f"{source} (지수 추세 판정 불가)")
    ma200 = sp.rolling(200).mean().iloc[-1]
    index_strong = not pd.isna(ma200) and float(sp.iloc[-1]) > float(ma200)

    r = RISK_SIGNAL_RULES["breadth_divergence"]
    signal = "ok"
    if index_strong:
        if pct < r["alert"]:
            signal = "alert"
        elif pct < r["warn"]:
            signal = "warn"
    return _ind(pct, None, signal,
                f"{source}: 200일선 위 {pct}% / 지수 {'강세' if index_strong else '약세'}")


def compute_market_risk_score(indicators: dict) -> dict:
    group_scores: dict = {}
    for group, keys in MARKET_GROUPS.items():
        vals = [SIGNAL_SCORE_MAP[indicators[k]["signal"]]
                for k in keys if k in indicators and indicators[k]["signal"] in SIGNAL_SCORE_MAP]
        group_scores[group] = round(sum(vals) / len(vals)) if vals else None

    num = sum(RISK_GROUP_WEIGHTS[g] * s for g, s in group_scores.items() if s is not None)
    den = sum(RISK_GROUP_WEIGHTS[g] for g, s in group_scores.items() if s is not None)
    score = round(num / den) if den else 0

    regime = ("crisis" if score >= 75 else "stress" if score >= 55
              else "caution" if score >= 30 else "calm")
    alerts = [k for k, v in indicators.items() if v.get("signal") == "alert"]
    return {"score": score, "regime": regime, "group_scores": group_scores, "alerts": alerts}


def _clip(x: float) -> float:
    # float()로 강제해 numpy 스칼라 누출을 막는다 — 캐시의 bare json.dump가 numpy.int64에서 깨짐
    return float(max(0.0, min(100.0, float(x))))


def _sector_components(close: pd.Series, bench: pd.Series | None) -> dict:
    comps: dict = {}
    # 1. 과열도: 200일선 이격 percentile + 월봉 RSI
    if len(close) >= 200:
        ma200 = close.rolling(200).mean()
        gap = ((close - ma200) / ma200 * 100).dropna()
        pct = _percentile(gap, float(gap.iloc[-1]))
        comps["overheating"] = _clip(pct if pct is not None else min(100.0, max(0.0, float(gap.iloc[-1]) * 3)))
    # 2. 모멘텀 롤오버: vs 벤치 상대강도 20일 기울기 하향
    if bench is not None:
        aligned = pd.concat([close, bench], axis=1).dropna()
        if len(aligned) >= 40:
            rs = aligned.iloc[:, 0] / aligned.iloc[:, 1]
            recent = (float(rs.iloc[-1]) - float(rs.iloc[-20])) / float(rs.iloc[-20]) * 100
            prev = (float(rs.iloc[-20]) - float(rs.iloc[-40])) / float(rs.iloc[-20]) * 100
            # 이전엔 상승, 지금 하향 전환 = 롤오버 위험 높음
            comps["momentum_rollover"] = _clip(50 + (prev - recent) * 5)
    # 3. 드로다운 속도: ATH 낙폭 + 최근 10일 가속
    ath = float(close.cummax().iloc[-1])
    dd = (float(close.iloc[-1]) - ath) / ath * 100
    recent10 = (float(close.iloc[-1]) - float(close.iloc[-11])) / float(close.iloc[-11]) * 100 if len(close) >= 11 else 0.0
    comps["drawdown_speed"] = _clip(abs(min(0.0, dd)) * 2 + abs(min(0.0, recent10)) * 3)
    # 4. 변동성 상승: ATR%(간이 = 20일 종가 표준편차/평균) 추세
    if len(close) >= 40:
        vol_now = float(close.iloc[-20:].pct_change().std()) * 100
        vol_prev = float(close.iloc[-40:-20].pct_change().std()) * 100
        comps["volatility_rise"] = _clip(50 + (vol_now - vol_prev) * 20)
    # 5. 거래량 이상 — 종가만으론 불가, 하락일 가속 프록시
    down_days = (close.iloc[-10:].pct_change() < 0).sum() if len(close) >= 11 else 0
    comps["volume_anomaly"] = _clip(down_days * 12)
    return comps


def compute_sector_risk(risk_data: dict) -> dict:
    sectors = risk_data.get("sectors", {})
    tickers = risk_data.get("tickers", {})
    spy = tickers.get("spy")
    kospi = tickers.get("kospi")
    kr_syms = set(SECTOR_UNIVERSE["kr"])

    out: dict = {}
    for sym, close in sectors.items():
        if close is None or len(close) < 30:
            continue
        bench = kospi if sym in kr_syms else spy
        comps = _sector_components(close, bench)
        if not comps:
            continue
        num = sum(SECTOR_COMPONENT_WEIGHTS[k] * v for k, v in comps.items())
        den = sum(SECTOR_COMPONENT_WEIGHTS[k] for k in comps)
        score = round(num / den) if den else 0
        level = ("critical" if score >= 80 else "high" if score >= 60
                 else "elevated" if score >= 40 else "low")
        basis = [f"{k}={round(v)}" for k, v in sorted(comps.items(), key=lambda x: -x[1])]
        out[sym] = {"score": score, "level": level, "components": {k: round(v, 1) for k, v in comps.items()}, "basis": basis}
    return out
