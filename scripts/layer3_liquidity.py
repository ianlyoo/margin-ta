"""
Layer 3b — Liquidity Structure: Anchored VWAP + approximate Volume Profile.

Both features are computed from daily OHLCV, so they are structural estimates,
not tick-accurate order-flow metrics. Delta profile is intentionally excluded.
"""
import numpy as np
import pandas as pd


def _typical_price(df: pd.DataFrame) -> pd.Series:
    return (df.High + df.Low + df.Close) / 3


def _anchored_vwap_series(df: pd.DataFrame, anchor_pos: int) -> pd.Series:
    section = df.iloc[anchor_pos:].copy()
    tp = _typical_price(section)
    pv = tp * section.Volume
    avwap = pv.cumsum() / section.Volume.cumsum().replace(0, np.nan)
    full = pd.Series(index=df.index, dtype=float)
    full.iloc[anchor_pos:] = avwap
    return full


def _slope_regime(series: pd.Series, lookback: int = 10) -> tuple[str, float | None]:
    clean = series.dropna()
    if len(clean) <= lookback:
        return "unknown", None
    start = float(clean.iloc[-lookback])
    end = float(clean.iloc[-1])
    if start <= 0:
        return "unknown", None
    slope_pct = (end / start - 1) * 100
    if slope_pct > 0.5:
        return "rising", round(slope_pct, 2)
    if slope_pct < -0.5:
        return "falling", round(slope_pct, 2)
    return "flat", round(slope_pct, 2)


def _find_largest_gap_anchor(df: pd.DataFrame, lookback: int = 126, min_gap_pct: float = 3.0) -> dict | None:
    recent = df.iloc[-min(lookback, len(df)):].copy()
    if len(recent) < 2:
        return None
    prev_close = recent.Close.shift(1)
    gap_pct = ((recent.Open - prev_close) / prev_close * 100).abs()
    gap_pct = gap_pct.dropna()
    if gap_pct.empty:
        return None
    anchor_idx = gap_pct.idxmax()
    gap_value = float(gap_pct.loc[anchor_idx])
    if gap_value < min_gap_pct:
        return None
    return {
        "key": "largest_gap",
        "name": "AVWAP Gap",
        "pos": df.index.get_loc(anchor_idx),
        "reason": f"largest {lookback}d gap ({gap_value:.1f}%)",
    }


def _anchor_candidates(df: pd.DataFrame) -> list[dict]:
    anchors = []
    lookback_126 = min(126, len(df))
    lookback_252 = min(252, len(df))
    total_days = len(df)

    if lookback_126 >= 20:
        recent = df.iloc[-lookback_126:]
        anchors.append({
            "key": "swing_low_126",
            "name": "AVWAP Swing Low",
            "pos": df.index.get_loc(recent.Low.idxmin()),
            "reason": "126d swing low",
        })
        anchors.append({
            "key": "swing_high_126",
            "name": "AVWAP Swing High",
            "pos": df.index.get_loc(recent.High.idxmax()),
            "reason": "126d swing high",
        })

    gap_anchor = _find_largest_gap_anchor(df)
    if gap_anchor:
        anchors.append(gap_anchor)

    if lookback_252 >= 60:
        recent = df.iloc[-lookback_252:]
        anchors.append({
            "key": "low_52w",
            "name": "AVWAP 52W Low",
            "pos": df.index.get_loc(recent.Low.idxmin()),
            "reason": "252d low",
        })

    # Extended anchors: all-time low/high (only if significantly more data than 252d)
    if total_days > 300:
        alltime_low_pos = int(df.Low.idxmin()) if hasattr(df.Low.idxmin(), '__index__') else df.Low.values.argmin()
        alltime_low_index_pos = df.index.get_loc(df.Low.idxmin()) if hasattr(df.Low.idxmin(), 'strftime') else df.index.get_loc(df.index[alltime_low_pos])
        anchors.append({
            "key": "low_alltime",
            "name": "AVWAP All-Time Low",
            "pos": alltime_low_index_pos,
            "reason": f"all-time low ({total_days}d)",
        })

        alltime_high_pos = int(df.High.idxmax()) if hasattr(df.High.idxmax(), '__index__') else df.High.values.argmax()
        alltime_high_index_pos = df.index.get_loc(df.High.idxmax()) if hasattr(df.High.idxmax(), 'strftime') else df.index.get_loc(df.index[alltime_high_pos])
        anchors.append({
            "key": "high_alltime",
            "name": "AVWAP All-Time High",
            "pos": alltime_high_index_pos,
            "reason": f"all-time high ({total_days}d)",
        })

    # Deduplicate anchors that land on the same candle.
    unique = {}
    for anchor in anchors:
        unique.setdefault(anchor["pos"], anchor)
    return list(unique.values())


def compute_anchored_vwaps(df: pd.DataFrame, current_price: float) -> dict:
    """Return AVWAP levels anchored at swing/gap locations."""
    levels = []
    for anchor in _anchor_candidates(df):
        avwap = _anchored_vwap_series(df, anchor["pos"])
        value = avwap.dropna().iloc[-1] if not avwap.dropna().empty else np.nan
        if pd.isna(value):
            continue

        slope, slope_pct = _slope_regime(avwap)
        role = "support" if current_price > value else "resistance"
        dist_pct = abs(current_price - value) / current_price * 100
        anchor_date = df.index[anchor["pos"]]
        levels.append({
            "price": round(float(value), 2),
            "name": anchor["name"],
            "key": anchor["key"],
            "role": role,
            "distance_pct": round(float(dist_pct), 2),
            "type": "AVWAP",
            "anchor_date": anchor_date.isoformat() if hasattr(anchor_date, "isoformat") else str(anchor_date),
            "anchor_reason": anchor["reason"],
            "slope_regime": slope,
            "slope_pct_10d": slope_pct,
        })

    levels = sorted(levels, key=lambda x: x["distance_pct"])
    return {
        "levels": levels,
        "nearest": levels[0] if levels else None,
    }


def compute_volume_profile(
    df: pd.DataFrame,
    current_price: float,
    lookback: int = 126,
    bins: int = 48,
    value_area_pct: float = 0.70,
) -> dict:
    """
    Approximate daily Volume Profile by distributing each candle's volume
    uniformly across its high-low range.
    """
    recent = df.iloc[-min(lookback, len(df)):].copy()
    if recent.empty or recent.Volume.sum() <= 0:
        return {"error": "volume profile input empty", "levels": []}

    price_low = float(recent.Low.min())
    price_high = float(recent.High.max())
    if price_high <= price_low:
        return {"error": "volume profile range invalid", "levels": []}

    edges = np.linspace(price_low, price_high, bins + 1)
    volumes = np.zeros(bins, dtype=float)

    for _, row in recent.iterrows():
        lo = float(row.Low)
        hi = float(row.High)
        vol = float(row.Volume)
        if vol <= 0:
            continue
        if hi <= lo:
            idx = int(np.clip(np.searchsorted(edges, float(row.Close), side="right") - 1, 0, bins - 1))
            volumes[idx] += vol
            continue

        candle_range = hi - lo
        overlaps = np.maximum(0, np.minimum(edges[1:], hi) - np.maximum(edges[:-1], lo))
        if overlaps.sum() > 0:
            volumes += vol * overlaps / candle_range

    if volumes.sum() <= 0:
        return {"error": "volume profile has no volume", "levels": []}

    centers = (edges[:-1] + edges[1:]) / 2
    poc_idx = int(np.argmax(volumes))
    poc = float(centers[poc_idx])

    order = np.argsort(volumes)[::-1]
    selected = []
    running = 0.0
    total = float(volumes.sum())
    for idx in order:
        selected.append(int(idx))
        running += float(volumes[idx])
        if running / total >= value_area_pct:
            break

    val = float(edges[min(selected)])
    vah = float(edges[max(selected) + 1])
    in_value_area = val <= current_price <= vah

    levels = [
        {
            "price": round(poc, 2),
            "name": "Volume POC",
            "role": "support" if current_price > poc else "resistance",
            "distance_pct": round(abs(current_price - poc) / current_price * 100, 2),
            "type": "VolumeProfile",
        },
        {
            "price": round(val, 2),
            "name": "Value Area Low",
            "role": "support" if current_price > val else "resistance",
            "distance_pct": round(abs(current_price - val) / current_price * 100, 2),
            "type": "VolumeProfile",
        },
        {
            "price": round(vah, 2),
            "name": "Value Area High",
            "role": "support" if current_price > vah else "resistance",
            "distance_pct": round(abs(current_price - vah) / current_price * 100, 2),
            "type": "VolumeProfile",
        },
    ]

    hvn = [
        {"price": round(float(centers[i]), 2), "volume_pct": round(float(volumes[i] / total * 100), 2)}
        for i in order[:3]
    ]

    return {
        "lookback": int(len(recent)),
        "bins": bins,
        "poc": round(poc, 2),
        "value_area_low": round(val, 2),
        "value_area_high": round(vah, 2),
        "in_value_area": bool(in_value_area),
        "dist_from_poc_pct": round((current_price - poc) / current_price * 100, 2),
        "hvn": hvn,
        "levels": levels,
    }


def build_liquidity_package(df: pd.DataFrame, current_price: float) -> dict:
    avwap = compute_anchored_vwaps(df, current_price)
    volume_profile_126 = compute_volume_profile(df, current_price)
    # Long-term volume profile (2yr or full history)
    long_lookback = min(504, len(df))
    volume_profile_full = compute_volume_profile(df, current_price, lookback=long_lookback) if long_lookback > 126 else None
    return {
        "anchored_vwap": avwap,
        "volume_profile": volume_profile_126,
        "volume_profile_full": volume_profile_full,
    }


def liquidity_levels(liquidity: dict) -> list[dict]:
    levels = []
    for item in liquidity.get("anchored_vwap", {}).get("levels", []):
        levels.append(item)
    # Guard against volume_profile being None (defensive programming).
    for item in (liquidity.get("volume_profile") or {}).get("levels", []):
        levels.append(item)
    # Also emit 504d volume profile levels with (504d) name suffix.
    # Guard against volume_profile_full being None (short-history symbols, IPOs <127d).
    for item in (liquidity.get("volume_profile_full") or {}).get("levels", []):
        level = item.copy()
        level["name"] = f"{item.get('name', 'Liquidity')} (504d)"
        levels.append(level)
    return levels


def liquidity_confluence(liquidity: dict, levels: dict, current_price: float, threshold_pct: float = 1.0) -> dict:
    """Detect whether liquidity levels overlap existing S/R levels."""
    sr_levels = levels.get("supports", []) + levels.get("resistances", [])
    liq_levels = liquidity_levels(liquidity)
    matches = []

    for liq in liq_levels:
        for sr in sr_levels:
            dist_pct = abs(liq["price"] - sr["price"]) / current_price * 100
            if dist_pct <= threshold_pct:
                matches.append({
                    "liquidity": liq["name"],
                    "liquidity_price": liq["price"],
                    "sr_source": sr.get("source", ""),
                    "sr_price": sr["price"],
                    "distance_pct": round(dist_pct, 2),
                })

    return {
        "threshold_pct": threshold_pct,
        "count": len(matches),
        "matches": matches[:10],
        "has_confluence": bool(matches),
    }
