"""
Layer 1c — External Flow Data: DarkFina / ChartExchange.

This module only reads public, unauthenticated endpoints/pages and keeps the
feature opt-in via margin_ta.py --flow. It does not bypass login, premium
gates, or rate limits.
"""
import json
import re
import ssl
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from layer1_options import fetch_options_analysis, legacy_flow_options
except Exception:
    fetch_options_analysis = None
    legacy_flow_options = None


USER_AGENT = "margin-ta/2.2 (+https://darkfina.crazyrabbit.co; personal analysis)"


def _fetch_text(url: str, timeout: int = 10, attempts: int = 1, retry_delay: float = 0.5) -> str:
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json"})
            with urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as e:
            # SSL certificate verification failure — retry without verification.
            # This handles server-side cert expiry (e.g. Let's Encrypt auto-renewal gaps).
            # Only applied to public unauthenticated endpoints — no auth/PII involved.
            err_str = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in err_str or "certificate verify failed" in err_str:
                try:
                    ctx = ssl._create_unverified_context()
                    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json"})
                    with urlopen(req, timeout=timeout, context=ctx) as response:
                        return response.read().decode("utf-8", errors="replace")
                except Exception as e2:
                    last_error = e2
                    if attempt < attempts - 1:
                        time.sleep(retry_delay * (attempt + 1))
                    continue
            last_error = e
            if attempt < attempts - 1:
                time.sleep(retry_delay * (attempt + 1))
    raise last_error


def _fetch_json(url: str, timeout: int = 10, attempts: int = 1, retry_delay: float = 0.5) -> dict:
    return json.loads(_fetch_text(url, timeout=timeout, attempts=attempts, retry_delay=retry_delay))


def _url(base: str, params: dict) -> str:
    return f"{base}?{urlencode({k: v for k, v in params.items() if v is not None})}"


def _safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _short_pressure(short_percent: float | None, ctb_rate: float | None = None, short_interest_pct: float | None = None) -> str:
    short_percent = short_percent or 0
    ctb_rate = ctb_rate or 0
    short_interest_pct = short_interest_pct or 0
    if short_percent >= 55 or ctb_rate >= 30 or short_interest_pct >= 20:
        return "extreme"
    if short_percent >= 45 or ctb_rate >= 10 or short_interest_pct >= 10:
        return "high"
    if short_percent >= 35 or ctb_rate >= 3 or short_interest_pct >= 5:
        return "elevated"
    return "normal"


def _parse_date(value: str | None):
    if not value:
        return None
    text = str(value).strip()
    candidates = [text, text.split("T")[0], text.split(" ")[0]]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"):
        for candidate in candidates:
            try:
                return datetime.strptime(candidate, fmt).date()
            except Exception:
                continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _days_to_expiry(expiry: str | None) -> int | None:
    expiry_date = _parse_date(expiry)
    if not expiry_date:
        return None
    return (expiry_date - datetime.now(timezone.utc).date()).days


def _selected_expiries(expirations: list, min_dte: int = 7, max_dte: int = 60, max_count: int = 3) -> list[str]:
    """Pick a small, reliable expiry window so --flow does not over-call public endpoints."""
    candidates = []
    for expiry in expirations or []:
        if isinstance(expiry, dict):
            expiry = expiry.get("expiry") or expiry.get("date")
        dte = _days_to_expiry(expiry)
        if expiry and dte is not None and min_dte <= dte <= max_dte:
            candidates.append((dte, str(expiry)))
    return [expiry for _, expiry in sorted(candidates)[:max_count]]


def _spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= bid:
        return None
    mid = (bid + ask) / 2
    return ((ask - bid) / mid * 100) if mid > 0 else None


def _contract_from_raw(raw: dict, side: str, strike: float, expiry: str, source: str) -> dict:
    return {
        "source": source,
        "expiry": expiry,
        "type": side,
        "strike": strike,
        "bid": _safe_float(raw.get("bid")),
        "ask": _safe_float(raw.get("ask")),
        "last_price": _safe_float(raw.get("lastPrice") or raw.get("last_price") or raw.get("last")),
        "volume": _safe_int(raw.get("volume")),
        "open_interest": _safe_int(raw.get("oi") or raw.get("openInterest") or raw.get("open_interest")),
        "implied_volatility": _safe_float(
            raw.get("impliedVolatility") or raw.get("iv") or raw.get("bidIV") or raw.get("askIV")
        ),
        "delta": _safe_float(raw.get("delta")),
        "gamma": _safe_float(raw.get("gamma")),
        "theta": _safe_float(raw.get("theta")),
        "vega": _safe_float(raw.get("vega")),
        "last_trade_date": raw.get("lastTradeDate") or raw.get("last_trade_date"),
    }


def _normalize_darkfina_chain(data: dict, expiry: str) -> list[dict]:
    contracts = []
    for row in data.get("chain") or []:
        strike = _safe_float(row.get("strike"))
        if strike is None:
            continue
        for side in ("call", "put"):
            raw = row.get(side) or row.get(side.capitalize()) or {}
            if raw:
                contracts.append(_contract_from_raw(raw, side, strike, expiry, "darkfina_chain"))
    return contracts


def _fetch_darkfina_option_chain(symbol: str, expiry: str) -> tuple[list[dict], dict]:
    url = _url(
        "https://darkfina.crazyrabbit.co/api/get_options_chain.php",
        {"symbol": symbol.upper(), "expiry": expiry},
    )
    last_warning = "DarkFina option chain response unsuccessful"
    for attempt in range(2):
        try:
            data = _fetch_json(url, timeout=8)
            if data.get("success"):
                return _normalize_darkfina_chain(data, expiry), {
                    "source": data.get("dataSource") or "darkfina",
                    "current_price": _safe_float(data.get("currentPrice")),
                }
            last_warning = (
                f"DarkFina option chain unsuccessful for {expiry}: "
                f"{data.get('error') or data.get('message') or 'unknown error'}"
            )
        except Exception as e:
            last_warning = f"DarkFina option chain fetch failed for {expiry}: {e}"
        if attempt < 1:
            time.sleep(0.6 * (attempt + 1))
    return [], {"warning": last_warning}


def _fetch_yfinance_option_chain(symbol: str, expiry: str) -> tuple[list[dict], dict]:
    try:
        import yfinance as yf
    except Exception as e:
        return [], {"warning": f"yfinance unavailable for option fallback: {e}"}

    ticker = yf.Ticker(symbol.upper())
    chain = ticker.option_chain(expiry)
    contracts = []
    for side, frame in (("call", chain.calls), ("put", chain.puts)):
        for _, row in frame.iterrows():
            raw = {
                "bid": row.get("bid"),
                "ask": row.get("ask"),
                "lastPrice": row.get("lastPrice"),
                "volume": row.get("volume"),
                "openInterest": row.get("openInterest"),
                "impliedVolatility": row.get("impliedVolatility"),
                "lastTradeDate": str(row.get("lastTradeDate")) if row.get("lastTradeDate") is not None else None,
            }
            strike = _safe_float(row.get("strike"))
            if strike is not None:
                contracts.append(_contract_from_raw(raw, side, strike, expiry, "yfinance_chain"))
    return contracts, {"source": "yfinance"}


def _fetch_yfinance_expirations(symbol: str) -> tuple[list[str], str | None]:
    try:
        import yfinance as yf
        return list(yf.Ticker(symbol.upper()).options or []), None
    except Exception as e:
        return [], f"yfinance option expirations fallback failed: {e}"


def _filter_reliable_contracts(contracts: list[dict], spot: float) -> tuple[list[dict], list[str]]:
    warnings = []
    if not spot or spot <= 0:
        return [], ["Option chain filter skipped: missing spot price"]

    filtered = []
    for c in contracts:
        strike = c.get("strike")
        if strike is None or abs(strike / spot - 1) > 0.20:
            continue
        oi = c.get("open_interest")
        if oi is None or oi <= 0:
            continue
        bid = c.get("bid")
        ask = c.get("ask")
        spread = _spread_pct(bid, ask)
        if spread is None or spread > 60:
            continue
        iv = c.get("implied_volatility")
        if iv is not None and (iv < 0.01 or iv > 5.0):
            continue
        filtered.append({**c, "spread_pct": round(spread, 2)})

    if not filtered:
        warnings.append("Option chain quality filter removed all contracts")
    return filtered, warnings


def _option_quality(raw_count: int, filtered: list[dict]) -> tuple[int, str]:
    filtered_count = len(filtered)
    total_oi = sum(c.get("open_interest") or 0 for c in filtered)
    valid_ratio = filtered_count / raw_count if raw_count else 0
    score = 0
    if raw_count:
        score += 15
    if filtered_count >= 40:
        score += 35
    elif filtered_count >= 20:
        score += 25
    elif filtered_count >= 10:
        score += 15
    elif filtered_count >= 4:
        score += 5
    if valid_ratio >= 0.35:
        score += 20
    elif valid_ratio >= 0.20:
        score += 12
    elif valid_ratio >= 0.10:
        score += 5
    if total_oi >= 100000:
        score += 20
    elif total_oi >= 10000:
        score += 12
    elif total_oi >= 1000:
        score += 5
    if filtered_count and all(c.get("spread_pct") is not None for c in filtered):
        score += 10

    score = min(score, 100)
    if score >= 70:
        status = "good"
    elif score >= 45:
        status = "usable"
    else:
        status = "weak"
    return score, status


def _max_pain_from_oi(calls: dict[float, int], puts: dict[float, int]) -> float | None:
    strikes = sorted(set(calls) | set(puts))
    if not strikes:
        return None
    payouts = {}
    for candidate in strikes:
        call_payout = sum(max(candidate - strike, 0) * oi for strike, oi in calls.items())
        put_payout = sum(max(strike - candidate, 0) * oi for strike, oi in puts.items())
        payouts[candidate] = call_payout + put_payout
    return min(payouts, key=payouts.get)


def _build_option_feature(symbol: str, expiry: str, spot: float, contracts: list[dict], source: str) -> dict:
    raw_count = len(contracts)
    filtered, warnings = _filter_reliable_contracts(contracts, spot)
    calls = {}
    puts = {}
    call_volume = 0
    put_volume = 0
    total_call_oi = 0
    total_put_oi = 0

    for c in filtered:
        strike = float(c["strike"])
        oi = int(c.get("open_interest") or 0)
        volume = int(c.get("volume") or 0)
        if c.get("type") == "call":
            calls[strike] = calls.get(strike, 0) + oi
            total_call_oi += oi
            call_volume += volume
        else:
            puts[strike] = puts.get(strike, 0) + oi
            total_put_oi += oi
            put_volume += volume

    total_oi = total_call_oi + total_put_oi
    max_pain = _max_pain_from_oi(calls, puts)
    call_wall = max(calls, key=calls.get) if calls else None
    put_wall = max(puts, key=puts.get) if puts else None
    top_strike_oi = 0
    for strike in set(calls) | set(puts):
        top_strike_oi = max(top_strike_oi, calls.get(strike, 0) + puts.get(strike, 0))

    quality_score, quality_status = _option_quality(raw_count, filtered)
    if quality_status == "weak":
        warnings.append(f"Option chain quality weak ({quality_score}/100); scoring should fall back to summary")

    dte = _days_to_expiry(expiry)
    oi_pcr = (total_put_oi / total_call_oi) if total_call_oi else None
    volume_pcr = (put_volume / call_volume) if call_volume else None
    return {
        "source": source,
        "symbol": symbol.upper(),
        "expiry": expiry,
        "days_to_expiry": dte,
        "spot": round(float(spot), 4) if spot else None,
        "raw_contracts": raw_count,
        "filtered_contracts": len(filtered),
        "filtered_ratio": round(len(filtered) / raw_count, 3) if raw_count else 0,
        "quality_score": quality_score,
        "quality_status": quality_status,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "put_call_ratio": round(oi_pcr, 3) if oi_pcr is not None else None,
        "oi_put_call_ratio": round(oi_pcr, 3) if oi_pcr is not None else None,
        "volume_put_call_ratio": round(volume_pcr, 3) if volume_pcr is not None else None,
        "call_wall": round(call_wall, 2) if call_wall is not None else None,
        "call_wall_oi": calls.get(call_wall) if call_wall is not None else None,
        "dist_to_call_wall_pct": round((call_wall / spot - 1) * 100, 2) if call_wall and spot else None,
        "put_wall": round(put_wall, 2) if put_wall is not None else None,
        "put_wall_oi": puts.get(put_wall) if put_wall is not None else None,
        "dist_to_put_wall_pct": round((spot / put_wall - 1) * 100, 2) if put_wall and spot else None,
        "max_pain": round(max_pain, 2) if max_pain is not None else None,
        "max_pain_distance_pct": round((spot / max_pain - 1) * 100, 2) if max_pain and spot else None,
        "top_strike_oi_concentration": round(top_strike_oi / total_oi, 3) if total_oi else None,
        "warnings": warnings,
    }


def _fetch_chain_feature(symbol: str, expiry: str, spot: float) -> dict:
    features = []
    warnings = []

    try:
        contracts, meta = _fetch_darkfina_option_chain(symbol, expiry)
        if contracts:
            chain_spot = meta.get("current_price") or spot
            features.append(_build_option_feature(symbol, expiry, chain_spot, contracts, "darkfina_chain"))
        elif meta.get("warning"):
            warnings.append(meta["warning"])
    except Exception as e:
        warnings.append(f"DarkFina option chain fetch failed for {expiry}: {e}")

    best = max(features, key=lambda x: x.get("quality_score", 0), default=None)
    if not best or best.get("quality_status") == "weak":
        try:
            contracts, meta = _fetch_yfinance_option_chain(symbol, expiry)
            if contracts:
                yf_feature = _build_option_feature(symbol, expiry, spot, contracts, "yfinance_chain")
                features.append(yf_feature)
                best = max(features, key=lambda x: x.get("quality_score", 0), default=best)
            elif meta.get("warning"):
                warnings.append(meta["warning"])
        except Exception as e:
            warnings.append(f"yfinance option chain fallback failed for {expiry}: {e}")

    if best:
        best["warnings"] = list(dict.fromkeys((best.get("warnings") or []) + warnings))
        return best
    return {
        "source": "none",
        "symbol": symbol.upper(),
        "expiry": expiry,
        "days_to_expiry": _days_to_expiry(expiry),
        "quality_score": 0,
        "quality_status": "unavailable",
        "warnings": warnings or ["No option chain contracts available"],
    }


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text)


def fetch_darkfina_darkpool(symbol: str, price: float | None = None) -> dict:
    """Fetch dark pool summary, history, and price levels from DarkFina."""
    symbol = symbol.upper()
    result = {
        "source": "darkfina",
        "symbol": symbol,
        "current": None,
        "history": None,
        "price_levels": [],
        "warnings": [],
    }

    try:
        current = _fetch_json(_url(
            "https://darkfina.crazyrabbit.co/api/get_chartexchange_cached.php",
            {"symbol": symbol, "include_price_levels": "true"},
        ))
        if current.get("success"):
            exchange_volume = current.get("data", {}).get("exchangeVolume", {})
            total_volume_raw = str(exchange_volume.get("totalVolume", "0")).replace(",", "")
            total_volume = int(float(total_volume_raw)) if total_volume_raw else 0
            dark_pct = float(exchange_volume.get("darkPoolPercentage") or 0)
            dark_volume = round(total_volume * dark_pct / 100)
            result["current"] = {
                "trading_date": current.get("tradingDate"),
                "datetime": exchange_volume.get("datetime"),
                "total_volume": total_volume,
                "dark_pool_percentage": round(dark_pct, 2),
                "dark_pool_volume": dark_volume,
                "dark_pool_amount": round(dark_volume * price, 2) if price else None,
                "exchanges": exchange_volume.get("exchanges", []),
                "cache_info": current.get("cache_info", {}),
            }
            result["price_levels"] = (current.get("priceLevels") or [])[:20]
        else:
            result["warnings"].append("DarkFina current dark pool response unsuccessful")
    except Exception as e:
        result["warnings"].append(f"DarkFina current dark pool fetch failed: {e}")

    try:
        history = _fetch_json(_url(
            "https://darkfina.crazyrabbit.co/api/get_darkpool_history.php",
            {"symbol": symbol, "days": 30},
        ))
        if history.get("success"):
            result["history"] = {
                "stats": history.get("stats", {}),
                "chart": history.get("chart", {}),
            }
        else:
            result["warnings"].append("DarkFina dark pool history response unsuccessful")
    except Exception as e:
        result["warnings"].append(f"DarkFina dark pool history fetch failed: {e}")

    return result


def fetch_darkfina_options(symbol: str, price: float | None = None) -> dict:
    """Fetch option summary plus a small set of quality-filtered option chains."""
    symbol = symbol.upper()
    result = {
        "source": "darkfina",
        "symbol": symbol,
        "current_price": None,
        "expirations": [],
        "summary_by_expiry": [],
        "chain_features_by_expiry": [],
        "primary_chain_feature": None,
        "warnings": [],
    }

    try:
        data = None
        last_error = None
        url = _url(
            "https://darkfina.crazyrabbit.co/api/get_options_data.php",
            {"symbol": symbol},
        )
        for attempt in range(3):
            try:
                data = _fetch_json(url, timeout=8)
                if data.get("success"):
                    break
                last_error = data.get("error") or data.get("message") or "response unsuccessful"
            except Exception as e:
                data = None
                last_error = str(e)
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))

        if not data or not data.get("success"):
            result["warnings"].append(f"DarkFina options response unsuccessful: {last_error or 'unknown error'}")
            result["current_price"] = price
            yf_expirations, yf_warning = _fetch_yfinance_expirations(symbol)
            if yf_warning:
                result["warnings"].append(yf_warning)
            result["expirations"] = yf_expirations
            selected = _selected_expiries(yf_expirations, min_dte=7, max_dte=60, max_count=2)
            if price and selected:
                features = []
                for expiry in selected:
                    feature = _fetch_chain_feature(symbol, expiry, float(price))
                    features.append(feature)
                    result["warnings"].extend(feature.get("warnings") or [])
                result["chain_features_by_expiry"] = features
                usable = [
                    f for f in features
                    if f.get("quality_status") in {"good", "usable"} and f.get("max_pain")
                ]
                if usable:
                    result["primary_chain_feature"] = sorted(
                        usable,
                        key=lambda f: (abs(int(f.get("days_to_expiry") or 999)), -int(f.get("quality_score") or 0)),
                    )[0]
                elif features:
                    result["primary_chain_feature"] = max(features, key=lambda f: f.get("quality_score", 0))
            elif not price:
                result["warnings"].append("Option chain fallback skipped: missing current price")
            return result

        result["current_price"] = _safe_float(data.get("currentPrice")) or price
        result["expirations"] = data.get("expirations", [])
        summaries = []
        zero_gex_count = 0
        for item in data.get("optionsByExpiry", []):
            gex = item.get("gex", {}) or {}
            if not gex.get("netGex"):
                zero_gex_count += 1
            summaries.append({
                "expiry": item.get("expiry"),
                "days_to_expiry": item.get("daysToExpiry"),
                "max_pain": item.get("maxPain"),
                "max_pain_distance_pct": item.get("maxPainDistance"),
                "put_call_ratio": item.get("putCallRatio"),
                "total_call_oi": item.get("totalCallOI"),
                "total_put_oi": item.get("totalPutOI"),
                "gex": gex,
            })
        result["summary_by_expiry"] = summaries
        if summaries and zero_gex_count == len(summaries):
            result["warnings"].append("DarkFina option GEX values are zero/unavailable; use Max Pain/PCR/OI only")

        spot = result["current_price"] or price
        expiries = result["expirations"] or [s.get("expiry") for s in summaries]
        selected = _selected_expiries(expiries, min_dte=7, max_dte=60, max_count=3)
        if spot and selected:
            features = []
            for expiry in selected:
                feature = _fetch_chain_feature(symbol, expiry, float(spot))
                features.append(feature)
                result["warnings"].extend(feature.get("warnings") or [])
            result["chain_features_by_expiry"] = features
            usable = [
                f for f in features
                if f.get("quality_status") in {"good", "usable"} and f.get("max_pain")
            ]
            if usable:
                result["primary_chain_feature"] = sorted(
                    usable,
                    key=lambda f: (abs(int(f.get("days_to_expiry") or 999)), -int(f.get("quality_score") or 0)),
                )[0]
            elif features:
                result["primary_chain_feature"] = max(features, key=lambda f: f.get("quality_score", 0))
        elif not spot:
            result["warnings"].append("Option chain enrichment skipped: missing current price")
        return result
    except Exception as e:
        result["warnings"].append(f"DarkFina options fetch failed: {e}")
        return result


def fetch_darkfina_short(symbol: str, price: float | None = None) -> dict:
    """Fetch short volume, short interest, and CTB from DarkFina public endpoints."""
    symbol = symbol.upper()
    result = {
        "source": "darkfina",
        "symbol": symbol,
        "current": None,
        "short_volume": None,
        "short_interest": None,
        "ctb": None,
        "warnings": [],
    }

    try:
        data = _fetch_json(_url(
            "https://darkfina.crazyrabbit.co/api/get_chartexchange_short_volume.php",
            {"symbol": symbol, "page_size": 30},
        ))
        rows = data.get("data") or []
        if data.get("dataAvailable") and rows:
            latest = rows[0]
            short_percent_values = [
                pct for pct in (_safe_float(row.get("shortPercent")) for row in rows)
                if pct is not None
            ]
            short_volume = _safe_int(latest.get("shortVolume"))
            short_amount = round(short_volume * price, 2) if price and short_volume is not None else None
            result["short_volume"] = {
                "source": data.get("dataSource"),
                "timestamp": data.get("timestamp"),
                "record_count": data.get("recordCount"),
                "current": {
                    "date": latest.get("date"),
                    "reported_total": _safe_int(latest.get("reportedTotal")),
                    "short_volume": short_volume,
                    "long_volume": _safe_int(latest.get("longVolume")),
                    "finra_short": _safe_int(latest.get("finraShort")),
                    "finra_exempt": _safe_int(latest.get("finraExempt")),
                    "short_percent": _safe_float(latest.get("shortPercent")),
                    "exempt_percent": _safe_float(latest.get("exemptPercent")),
                    "short_amount": short_amount,
                },
                "stats": {
                    "avg_short_percent_5d": _avg(short_percent_values[:5]),
                    "avg_short_percent_20d": _avg(short_percent_values[:20]),
                    "max_short_percent_20d": round(max(short_percent_values[:20]), 2) if short_percent_values[:20] else None,
                },
                "history": rows[:30],
            }
        else:
            result["warnings"].append("DarkFina short volume response unavailable")
    except Exception as e:
        result["warnings"].append(f"DarkFina short volume fetch failed: {e}")

    try:
        data = _fetch_json(_url(
            "https://darkfina.crazyrabbit.co/api/get_chartexchange_short_interest.php",
            {"symbol": symbol},
        ))
        if data.get("dataAvailable"):
            current = data.get("current") or {}
            result["short_interest"] = {
                "source": data.get("source"),
                "timestamp": data.get("timestamp"),
                "official": data.get("official"),
                "daily": data.get("daily"),
                "current": {
                    "date": current.get("date"),
                    "short_interest_percent": _safe_float(current.get("shortInterestPercent")),
                    "short_shares": _safe_int(current.get("shortShares")),
                    "days_to_cover": _safe_float(current.get("daysTocover") or current.get("daysToCover")),
                    "change_number": _safe_int(current.get("changeNumber")),
                    "change_percent": _safe_float(current.get("changePercent")),
                    "previous_date": current.get("previousDate"),
                    "previous_short_percent": _safe_float(current.get("previousShortPercent")),
                    "trend": current.get("trend"),
                },
                "official_history": (data.get("officialHistory") or [])[:20],
                "daily_history": (data.get("dailyHistory") or [])[:20],
            }
        else:
            result["warnings"].append("DarkFina short interest response unavailable")
    except Exception as e:
        result["warnings"].append(f"DarkFina short interest fetch failed: {e}")

    try:
        data = _fetch_json(_url(
            "https://darkfina.crazyrabbit.co/api/get_chartexchange_ctb.php",
            {"symbol": symbol},
        ))
        if data.get("dataAvailable") and data.get("current"):
            current = data.get("current") or {}
            risk = current.get("risk") or {}
            result["ctb"] = {
                "source": data.get("source"),
                "timestamp": data.get("timestamp"),
                "current": {
                    "date": current.get("date") or current.get("timestamp"),
                    "rate": _safe_float(current.get("rate")),
                    "available": _safe_int(current.get("available")),
                    "rebate": _safe_float(current.get("rebate")),
                    "risk_level": risk.get("level"),
                    "risk_description": risk.get("description"),
                },
                "trend": data.get("trend"),
                "risk_analysis": data.get("riskAnalysis"),
            }
        else:
            result["warnings"].append("DarkFina CTB response unavailable")
    except Exception as e:
        result["warnings"].append(f"DarkFina CTB fetch failed: {e}")

    short_volume_current = (result.get("short_volume") or {}).get("current") or {}
    short_interest_current = (result.get("short_interest") or {}).get("current") or {}
    ctb_current = (result.get("ctb") or {}).get("current") or {}
    if short_volume_current or short_interest_current or ctb_current:
        result["current"] = {
            "short_volume_date": short_volume_current.get("date"),
            "short_percent": short_volume_current.get("short_percent"),
            "short_volume": short_volume_current.get("short_volume"),
            "short_interest_date": short_interest_current.get("date"),
            "short_interest_percent": short_interest_current.get("short_interest_percent"),
            "short_shares": short_interest_current.get("short_shares"),
            "days_to_cover": short_interest_current.get("days_to_cover"),
            "ctb_rate": ctb_current.get("rate"),
            "ctb_available": ctb_current.get("available"),
            "squeeze_risk": ((result.get("ctb") or {}).get("risk_analysis") or {}).get("squeezeRisk"),
            "htb_status": ((result.get("ctb") or {}).get("risk_analysis") or {}).get("htbStatus"),
            "pressure": _short_pressure(
                short_volume_current.get("short_percent"),
                ctb_current.get("rate"),
                short_interest_current.get("short_interest_percent"),
            ),
        }

    return result


def _chart_exchange_slug(symbol: str, exchange: str | None) -> str:
    prefix = "nasdaq"
    if exchange and exchange.upper().startswith("NYSE"):
        prefix = "nyse"
    return f"{prefix}-{symbol.lower()}"


def fetch_chartexchange_darkpool(symbol: str, exchange: str | None = "NASDAQ") -> dict:
    """Best-effort parser for public ChartExchange dark pool summary HTML."""
    symbol = symbol.upper()
    slug = _chart_exchange_slug(symbol, exchange)
    url = f"https://chartexchange.com/symbol/{slug}/exchange-volume/dark-pool-levels/"
    result = {
        "source": "chartexchange_html",
        "symbol": symbol,
        "url": url,
        "current": None,
        "warnings": [],
    }

    try:
        html = _fetch_text(url, timeout=12)
        summary = re.search(
            r"Today's Off Exchange &amp; Dark Pool volume is\s*([\d,]+),\s*which is\s*([\d.]+)%",
            html,
        ) or re.search(
            r"Today's Off Exchange & Dark Pool volume is\s*([\d,]+),\s*which is\s*([\d.]+)%",
            html,
        )
        avg = re.search(
            r"past 30 days, the average Off Exchange &amp; Dark Pool volume has been\s*([\d.]+)%",
            html,
        ) or re.search(
            r"past 30 days, the average Off Exchange & Dark Pool volume has been\s*([\d.]+)%",
            html,
        )
        if summary:
            result["current"] = {
                "dark_pool_volume": int(summary.group(1).replace(",", "")),
                "dark_pool_percentage": float(summary.group(2)),
                "dark_pool_30d_average_pct": float(avg.group(1)) if avg else None,
            }
        else:
            result["warnings"].append("ChartExchange public summary not found in HTML")
        return result
    except Exception as e:
        result["warnings"].append(f"ChartExchange dark pool fetch failed: {e}")
        return result


def fetch_chartexchange_short(symbol: str, exchange: str | None = "NASDAQ") -> dict:
    """Best-effort parser for public ChartExchange short summaries."""
    symbol = symbol.upper()
    slug = _chart_exchange_slug(symbol, exchange)
    volume_url = f"https://chartexchange.com/symbol/{slug}/short-volume/"
    interest_url = f"https://chartexchange.com/symbol/{slug}/short-interest/"
    result = {
        "source": "chartexchange_html",
        "symbol": symbol,
        "url": volume_url,
        "current": None,
        "short_volume": None,
        "short_interest": None,
        "warnings": [],
    }

    try:
        text = _strip_html(_fetch_text(volume_url, timeout=12))
        summary = re.search(
            r"Short Volume Summary for ([A-Z][a-z]+ \d{1,2}, \d{4}).*?"
            r"Today's Short Volume is ([\d,]+), which is ([\d.]+)%.*?"
            r"average Short Volume has been ([\d.]+)%",
            text,
        )
        if summary:
            result["short_volume"] = {
                "current": {
                    "date": summary.group(1),
                    "short_volume": _safe_int(summary.group(2)),
                    "short_percent": _safe_float(summary.group(3)),
                    "avg_short_percent_30d": _safe_float(summary.group(4)),
                },
            }
        else:
            result["warnings"].append("ChartExchange short volume summary not found in HTML")
    except Exception as e:
        result["warnings"].append(f"ChartExchange short volume fetch failed: {e}")

    try:
        text = _strip_html(_fetch_text(interest_url, timeout=12))
        summary = re.search(
            r"As of (\d{4}-\d{2}-\d{2}), there were ([\d,]+) shares short with a short interest of ([\d.]+)%",
            text,
        )
        if summary:
            result["short_interest"] = {
                "current": {
                    "date": summary.group(1),
                    "short_shares": _safe_int(summary.group(2)),
                    "short_interest_percent": _safe_float(summary.group(3)),
                },
            }
        else:
            result["warnings"].append("ChartExchange short interest summary not found in HTML")
    except Exception as e:
        result["warnings"].append(f"ChartExchange short interest fetch failed: {e}")

    short_volume_current = (result.get("short_volume") or {}).get("current") or {}
    short_interest_current = (result.get("short_interest") or {}).get("current") or {}
    if short_volume_current or short_interest_current:
        result["current"] = {
            "short_volume_date": short_volume_current.get("date"),
            "short_percent": short_volume_current.get("short_percent"),
            "short_volume": short_volume_current.get("short_volume"),
            "short_interest_date": short_interest_current.get("date"),
            "short_interest_percent": short_interest_current.get("short_interest_percent"),
            "short_shares": short_interest_current.get("short_shares"),
            "pressure": _short_pressure(
                short_volume_current.get("short_percent"),
                None,
                short_interest_current.get("short_interest_percent"),
            ),
        }

    return result


def fetch_external_flow(
    symbol: str,
    price: float | None = None,
    exchange: str | None = "NASDAQ",
    source: str = "darkfina",
    include_options: bool = True,
    options_data: dict | None = None,
) -> dict:
    """Fetch external dark pool/short flow package, with legacy options when requested."""
    symbol = symbol.upper()
    legacy_options = legacy_flow_options(options_data) if options_data and legacy_flow_options else None
    if source == "chartexchange":
        dark_pool = fetch_chartexchange_darkpool(symbol, exchange)
        short = fetch_chartexchange_short(symbol, exchange)
        warnings = ["ChartExchange HTML source provides best-effort public summaries only"]
        if legacy_options:
            warnings.extend(legacy_options.get("warnings", []))
        warnings.extend(dark_pool.get("warnings", []))
        warnings.extend(short.get("warnings", []))
        return {
            "source": source,
            "symbol": symbol,
            "dark_pool": dark_pool,
            "options": legacy_options,
            "short": short,
            "warnings": warnings,
        }

    dark_pool = fetch_darkfina_darkpool(symbol, price=price)
    options = legacy_options
    if include_options and options is None:
        if fetch_options_analysis and legacy_flow_options:
            canonical = fetch_options_analysis(symbol, price=price, provider="darkfina")
            options = legacy_flow_options(canonical)
        else:
            options = fetch_darkfina_options(symbol, price=price)
    short = fetch_darkfina_short(symbol, price=price)
    warnings = []
    warnings.extend(dark_pool.get("warnings", []))
    warnings.extend((options or {}).get("warnings", []))
    warnings.extend(short.get("warnings", []))
    return {
        "source": "darkfina",
        "symbol": symbol,
        "dark_pool": dark_pool,
        "options": options,
        "short": short,
        "warnings": warnings,
    }


# ── Proactive SSL certificate monitoring ──────────────────────────

DARKFINA_CERT_HOSTS = [
    "darkfina.crazyrabbit.co",
    "darkfina.com",
]

DARKFINA_CERT_WARN_DAYS = 14  # alert when expiry is within 14 days


def check_darkfina_cert(host: str | None = None, warn_days: int | None = None):
    """Check DarkFina SSL certificate expiry date.

    Returns dict with keys: host, ok, issuer, not_before, not_after,
    days_left, warning (str|None).  When called without a specific host,
    returns list[dict] for all configured hosts.
    """
    if warn_days is None:
        warn_days = DARKFINA_CERT_WARN_DAYS
    hosts = [host] if host else DARKFINA_CERT_HOSTS
    results = []
    for h in hosts:
        result = {"host": h, "ok": False, "issuer": None, "not_before": None,
                  "not_after": None, "days_left": None, "warning": None}
        try:
            import subprocess
            import tempfile
            proc = subprocess.run(
                ["openssl", "s_client", "-connect", f"{h}:443",
                 "-servername", h, "-prexit"],
                input=b"", capture_output=True, timeout=15,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace")
            cert_match = re.search(
                r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----",
                stdout, re.DOTALL,
            )
            if not cert_match:
                result["warning"] = f"No certificate found in openssl output for {h}"
                results.append(result)
                continue
            cert_pem = cert_match.group(0)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as tmp:
                tmp.write(cert_pem)
                tmp_path = tmp.name
            try:
                proc2 = subprocess.run(
                    ["openssl", "x509", "-in", tmp_path, "-noout",
                     "-dates", "-issuer", "-subject"],
                    capture_output=True, timeout=5,
                )
                out = proc2.stdout.decode("utf-8", errors="replace")
                not_before_m = re.search(r"notBefore=(.+)", out)
                not_after_m = re.search(r"notAfter=(.+)", out)
                issuer_m = re.search(r"issuer=(.+)", out)
                if not_after_m:
                    not_after_str = not_after_m.group(1).strip()
                    days_left = None
                    try:
                        not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z")
                    except ValueError:
                        not_after = datetime.strptime(
                            not_after_str.split(" GMT")[0], "%b %d %H:%M:%S %Y"
                        )
                    days_left = (not_after - datetime.now()).days
                    result["not_after"] = not_after.isoformat()
                    result["days_left"] = days_left
                    result["ok"] = days_left > 0
                if not_before_m:
                    result["not_before"] = not_before_m.group(1).strip()
                if issuer_m:
                    result["issuer"] = issuer_m.group(1).strip()
                if result["ok"] and days_left is not None and days_left <= warn_days:
                    result["warning"] = (
                        f"Certificate expires in {days_left} day(s) ({result['not_after']}). "
                        "DarkFina may need Let's Encrypt auto-renewal fix."
                    )
                elif not result["ok"] and days_left is not None:
                    result["warning"] = (
                        f"Certificate EXPIRED {abs(days_left)} day(s) ago ({result['not_after']}). "
                        "SSL fallback will be used for DarkFina requests until renewed."
                    )
            finally:
                import os
                os.unlink(tmp_path)
        except FileNotFoundError:
            result["warning"] = "openssl not found — cannot check certificate"
        except Exception as e:
            result["warning"] = f"Certificate check failed: {e}"
        results.append(result)
    return results if len(results) > 1 else results[0]


if __name__ == "__main__":
    import sys
    hosts = sys.argv[1:] if len(sys.argv) > 1 else DARKFINA_CERT_HOSTS
    for host in hosts:
        result = check_darkfina_cert(host=host)
        status = "✅ OK" if result["ok"] else "🔴 EXPIRED"
        print(f"{status} {result['host']}: {result.get('days_left', '?')}d left, issuer={result.get('issuer', '?')}")
        if result.get("warning"):
            print(f"  ⚠️  {result['warning']}")
