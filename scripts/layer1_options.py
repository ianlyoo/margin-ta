"""
Layer 1d - Options analysis.

Canonical options chain ingestion and deterministic feature extraction for
margin-ta. Public sources are used by default; keyed providers only enrich the
analysis when credentials are present. Cboe delayed quote-table scraping is
intentionally excluded because its page terms prohibit automated extraction.
"""
from __future__ import annotations

import json
import math
import os
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "margin-ta/options/3.3 (personal analysis; public endpoints only)"
CONTRACT_MULTIPLIER = 100


def _fetch_text(
    url: str,
    timeout: int = 8,
    attempts: int = 1,
    retry_delay: float = 0.5,
    headers: dict | None = None,
) -> str:
    last_error = None
    merged_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html",
    }
    if headers:
        merged_headers.update(headers)
    for attempt in range(max(1, attempts)):
        try:
            req = Request(url, headers=merged_headers)
            with urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as e:
            # SSL certificate verification failure — retry without verification.
            err_str = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in err_str or "certificate verify failed" in err_str:
                try:
                    ctx = ssl._create_unverified_context()
                    req = Request(url, headers=merged_headers)
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


def _fetch_json(
    url: str,
    timeout: int = 8,
    attempts: int = 1,
    retry_delay: float = 0.5,
    headers: dict | None = None,
) -> dict:
    return json.loads(
        _fetch_text(
            url,
            timeout=timeout,
            attempts=attempts,
            retry_delay=retry_delay,
            headers=headers,
        )
    )


def _url(base: str, params: dict | None = None) -> str:
    params = params or {}
    encoded = urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
    return f"{base}?{encoded}" if encoded else base


def _safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def parse_expiry_date(value: str | None):
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


def days_to_expiry(expiry: str | None) -> int | None:
    expiry_date = parse_expiry_date(expiry)
    if not expiry_date:
        return None
    return (expiry_date - datetime.now(timezone.utc).date()).days


def is_monthly_opex(expiry: str | None) -> bool:
    expiry_date = parse_expiry_date(expiry)
    if not expiry_date:
        return False
    # Standard monthly US equity options expire on the third Friday.
    return expiry_date.weekday() == 4 and 15 <= expiry_date.day <= 21


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= bid:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 100 if mid > 0 else None


def _contract_from_raw(raw: dict, side: str, strike: float, expiry: str, source: str) -> dict:
    bid = _safe_float(raw.get("bid") or raw.get("nbbo_bid"))
    ask = _safe_float(raw.get("ask") or raw.get("nbbo_ask"))
    return {
        "source": source,
        "contract_symbol": raw.get("contractSymbol")
        or raw.get("option_symbol")
        or raw.get("optionSymbol")
        or raw.get("symbol")
        or raw.get("ticker"),
        "expiry": expiry,
        "type": str(side).lower(),
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "last_price": _safe_float(
            raw.get("lastPrice")
            or raw.get("last_price")
            or raw.get("last")
            or raw.get("close")
            or raw.get("price")
        ),
        "volume": _safe_int(raw.get("volume") or raw.get("day_volume")),
        "open_interest": _safe_int(
            raw.get("oi") or raw.get("openInterest") or raw.get("open_interest")
        ),
        "implied_volatility": _safe_float(
            raw.get("impliedVolatility")
            or raw.get("implied_volatility")
            or raw.get("iv")
            or raw.get("bidIV")
            or raw.get("askIV")
        ),
        "delta": _safe_float(raw.get("delta")),
        "gamma": _safe_float(raw.get("gamma")),
        "theta": _safe_float(raw.get("theta")),
        "vega": _safe_float(raw.get("vega")),
        "rho": _safe_float(raw.get("rho")),
        "last_trade_date": raw.get("lastTradeDate")
        or raw.get("last_trade_date")
        or raw.get("updated_at")
        or raw.get("tape_time"),
        "spread_pct": round(spread_pct(bid, ask), 2) if spread_pct(bid, ask) is not None else None,
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


def _fetch_darkfina_summary(symbol: str, timeout: int) -> tuple[dict, list[str]]:
    warnings = []
    url = _url("https://darkfina.crazyrabbit.co/api/get_options_data.php", {"symbol": symbol})
    data = None
    last_error = None
    for attempt in range(3):
        try:
            data = _fetch_json(url, timeout=timeout, attempts=1)
            if data.get("success"):
                break
            last_error = data.get("error") or data.get("message") or "response unsuccessful"
        except Exception as e:
            data = None
            last_error = str(e)
        if attempt < 2:
            time.sleep(0.5 * (attempt + 1))
    if not data or not data.get("success"):
        warnings.append(f"DarkFina options response unsuccessful: {last_error or 'unknown error'}")
        return {"source": "darkfina", "expirations": [], "summary_by_expiry": []}, warnings

    summaries = []
    zero_gex_count = 0
    for item in data.get("optionsByExpiry", []) or []:
        gex = item.get("gex", {}) or {}
        if not gex.get("netGex"):
            zero_gex_count += 1
        summaries.append({
            "source": "darkfina_summary",
            "expiry": item.get("expiry"),
            "days_to_expiry": item.get("daysToExpiry"),
            "max_pain": _safe_float(item.get("maxPain")),
            "max_pain_distance_pct": _safe_float(item.get("maxPainDistance")),
            "put_call_ratio": _safe_float(item.get("putCallRatio")),
            "total_call_oi": _safe_int(item.get("totalCallOI"), 0),
            "total_put_oi": _safe_int(item.get("totalPutOI"), 0),
            "gex": gex,
        })
    if summaries and zero_gex_count == len(summaries):
        warnings.append("DarkFina option GEX values are zero/unavailable; use chain OI/PCR/Max Pain")

    return {
        "source": "darkfina",
        "current_price": _safe_float(data.get("currentPrice")),
        "expirations": data.get("expirations", []) or [s.get("expiry") for s in summaries],
        "summary_by_expiry": summaries,
    }, warnings


def _fetch_darkfina_chain(symbol: str, expiry: str, timeout: int) -> tuple[list[dict], dict]:
    url = _url(
        "https://darkfina.crazyrabbit.co/api/get_options_chain.php",
        {"symbol": symbol, "expiry": expiry},
    )
    last_warning = "DarkFina option chain response unsuccessful"
    for attempt in range(2):
        try:
            data = _fetch_json(url, timeout=timeout, attempts=1)
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
            time.sleep(0.5 * (attempt + 1))
    return [], {"warning": last_warning}


def _fetch_yfinance_expirations(symbol: str) -> tuple[list[str], str | None]:
    try:
        import yfinance as yf
        return list(yf.Ticker(symbol).options or []), None
    except Exception as e:
        return [], f"yfinance option expirations fallback failed: {e}"


def _fetch_yfinance_chain(symbol: str, expiry: str) -> tuple[list[dict], dict]:
    try:
        import yfinance as yf
    except Exception as e:
        return [], {"warning": f"yfinance unavailable for option fallback: {e}"}

    ticker = yf.Ticker(symbol)
    chain = ticker.option_chain(expiry)
    contracts = []
    for side, frame in (("call", chain.calls), ("put", chain.puts)):
        for _, row in frame.iterrows():
            strike = _safe_float(row.get("strike"))
            if strike is None:
                continue
            raw = {
                "contractSymbol": row.get("contractSymbol"),
                "bid": row.get("bid"),
                "ask": row.get("ask"),
                "lastPrice": row.get("lastPrice"),
                "volume": row.get("volume"),
                "openInterest": row.get("openInterest"),
                "impliedVolatility": row.get("impliedVolatility"),
                "lastTradeDate": str(row.get("lastTradeDate")) if row.get("lastTradeDate") is not None else None,
            }
            contracts.append(_contract_from_raw(raw, side, strike, expiry, "yfinance_chain"))
    return contracts, {"source": "yfinance"}


def _fetch_alphavantage_chain(symbol: str, timeout: int) -> tuple[list[dict], list[str]]:
    key = os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not key:
        return [], ["Alpha Vantage options skipped: missing ALPHAVANTAGE_API_KEY"]
    url = _url(
        "https://www.alphavantage.co/query",
        {"function": "HISTORICAL_OPTIONS", "symbol": symbol, "apikey": key},
    )
    try:
        data = _fetch_json(url, timeout=timeout, attempts=1)
    except Exception as e:
        return [], [f"Alpha Vantage options fetch failed: {e}"]
    rows = data.get("data") or []
    contracts = []
    for row in rows:
        expiry = row.get("expiration") or row.get("expiry")
        strike = _safe_float(row.get("strike"))
        side = str(row.get("type") or row.get("option_type") or "").lower()
        if expiry and strike is not None and side in {"call", "put"}:
            contracts.append(_contract_from_raw(row, side, strike, expiry, "alphavantage_chain"))
    if not contracts:
        note = data.get("Note") or data.get("Information") or "no Alpha Vantage option rows"
        return [], [f"Alpha Vantage options unavailable: {note}"]
    return contracts, []


def _tradier_headers() -> tuple[dict | None, str | None]:
    token = os.environ.get("TRADIER_TOKEN") or os.environ.get("TRADIER_ACCESS_TOKEN")
    if not token:
        return None, "Tradier skipped: missing TRADIER_TOKEN"
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}, None


def _fetch_tradier_expirations(symbol: str, timeout: int) -> tuple[list[str], list[str]]:
    headers, warning = _tradier_headers()
    if warning:
        return [], [warning]
    url = _url("https://api.tradier.com/v1/markets/options/expirations", {"symbol": symbol})
    try:
        data = _fetch_json(url, timeout=timeout, headers=headers)
    except Exception as e:
        return [], [f"Tradier expirations fetch failed: {e}"]
    dates = ((data.get("expirations") or {}).get("date") or [])
    if isinstance(dates, str):
        dates = [dates]
    return list(dates), []


def _fetch_tradier_chain(symbol: str, expiry: str, timeout: int) -> tuple[list[dict], dict]:
    headers, warning = _tradier_headers()
    if warning:
        return [], {"warning": warning}
    url = _url(
        "https://api.tradier.com/v1/markets/options/chains",
        {"symbol": symbol, "expiration": expiry, "greeks": "true"},
    )
    try:
        data = _fetch_json(url, timeout=timeout, headers=headers)
    except Exception as e:
        return [], {"warning": f"Tradier chain fetch failed for {expiry}: {e}"}
    rows = ((data.get("options") or {}).get("option") or [])
    if isinstance(rows, dict):
        rows = [rows]
    contracts = []
    for row in rows:
        strike = _safe_float(row.get("strike"))
        side = str(row.get("option_type") or row.get("type") or "").lower()
        greeks = row.get("greeks") or {}
        raw = {**row, **greeks}
        if strike is not None and side in {"call", "put"}:
            contracts.append(_contract_from_raw(raw, side, strike, expiry, "tradier_chain"))
    return contracts, {"source": "tradier"}


def _fetch_polygon_snapshot(symbol: str, timeout: int) -> tuple[list[dict], list[str]]:
    key = os.environ.get("POLYGON_API_KEY")
    if not key:
        return [], ["Polygon options skipped: missing POLYGON_API_KEY"]
    contracts = []
    warnings = []
    url = _url(
        f"https://api.polygon.io/v3/snapshot/options/{symbol}",
        {"limit": 250, "apiKey": key},
    )
    for _ in range(3):
        try:
            data = _fetch_json(url, timeout=timeout)
        except Exception as e:
            warnings.append(f"Polygon options snapshot fetch failed: {e}")
            break
        for row in data.get("results") or []:
            details = row.get("details") or {}
            quote = row.get("last_quote") or {}
            day = row.get("day") or {}
            greeks = row.get("greeks") or {}
            expiry = details.get("expiration_date")
            strike = _safe_float(details.get("strike_price"))
            side = str(details.get("contract_type") or "").lower()
            raw = {
                "option_symbol": details.get("ticker"),
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "last": (row.get("last_trade") or {}).get("price") or day.get("close"),
                "volume": day.get("volume"),
                "open_interest": row.get("open_interest"),
                "implied_volatility": row.get("implied_volatility"),
                **greeks,
            }
            if expiry and strike is not None and side in {"call", "put"}:
                contracts.append(_contract_from_raw(raw, side, strike, expiry, "polygon_snapshot"))
        next_url = data.get("next_url")
        if not next_url:
            break
        url = _url(next_url, {"apiKey": key})
    return contracts, warnings


def _uw_headers() -> tuple[dict | None, str | None]:
    token = os.environ.get("UW_API_KEY") or os.environ.get("UNUSUAL_WHALES_API_KEY")
    if not token:
        return None, "Unusual Whales skipped: missing UW_API_KEY or UNUSUAL_WHALES_API_KEY"
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}, None


def _fetch_unusualwhales_expirations(symbol: str, timeout: int) -> tuple[list[str], list[str]]:
    headers, warning = _uw_headers()
    if warning:
        return [], [warning]
    url = f"https://api.unusualwhales.com/api/stock/{symbol}/expiry-breakdown"
    try:
        data = _fetch_json(url, timeout=timeout, headers=headers)
    except Exception as e:
        return [], [f"Unusual Whales expiry breakdown failed: {e}"]
    rows = data.get("data") or []
    expiries = []
    for row in rows if isinstance(rows, list) else []:
        expiry = row.get("expiry") or row.get("expiration") or row.get("expiration_date")
        if expiry:
            expiries.append(str(expiry))
    return sorted(set(expiries), key=lambda x: days_to_expiry(x) if days_to_expiry(x) is not None else 9999), []


def _fetch_unusualwhales_chain(symbol: str, expiry: str, timeout: int) -> tuple[list[dict], dict]:
    headers, warning = _uw_headers()
    if warning:
        return [], {"warning": warning}
    url = _url(
        f"https://api.unusualwhales.com/api/stock/{symbol}/option-contracts",
        {
            "expiry": expiry,
            "exclude_zero_oi_chains": "true",
            "limit": 500,
        },
    )
    try:
        data = _fetch_json(url, timeout=timeout, headers=headers)
    except Exception as e:
        return [], {"warning": f"Unusual Whales option contracts failed for {expiry}: {e}"}
    rows = data.get("data") or []
    contracts = []
    for row in rows if isinstance(rows, list) else []:
        contract = row.get("contract") or row
        expiry_val = contract.get("expiry") or contract.get("expiration") or expiry
        strike = _safe_float(contract.get("strike"))
        side = str(contract.get("option_type") or contract.get("type") or "").lower()
        if strike is not None and side in {"call", "put"}:
            contracts.append(_contract_from_raw(contract, side, strike, expiry_val, "unusualwhales_chain"))
    return contracts, {"source": "unusualwhales"}


def filter_reliable_contracts(contracts: list[dict], spot: float) -> tuple[list[dict], list[str]]:
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
        spread = c.get("spread_pct")
        if spread is None:
            spread = spread_pct(bid, ask)
        if spread is None or spread > 60:
            continue
        iv = c.get("implied_volatility")
        if iv is not None and (iv < 0.01 or iv > 5.0):
            continue
        filtered.append({**c, "spread_pct": round(spread, 2)})
    if not filtered:
        warnings.append("Option chain quality filter removed all contracts")
    return filtered, warnings


def option_quality(raw_count: int, filtered: list[dict]) -> tuple[int, str]:
    filtered_count = len(filtered)
    total_oi = sum(c.get("open_interest") or 0 for c in filtered)
    valid_ratio = filtered_count / raw_count if raw_count else 0
    score = 0
    if raw_count:
        score += 15
    if filtered_count >= 60:
        score += 35
    elif filtered_count >= 30:
        score += 27
    elif filtered_count >= 12:
        score += 18
    elif filtered_count >= 4:
        score += 6
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
        return score, "good"
    if score >= 45:
        return score, "usable"
    return score, "weak"


def max_pain_from_oi(calls: dict[float, int], puts: dict[float, int]) -> float | None:
    strikes = sorted(set(calls) | set(puts))
    if not strikes:
        return None
    payouts = {}
    for candidate in strikes:
        call_payout = sum(max(candidate - strike, 0) * oi for strike, oi in calls.items())
        put_payout = sum(max(strike - candidate, 0) * oi for strike, oi in puts.items())
        payouts[candidate] = call_payout + put_payout
    return min(payouts, key=payouts.get)


def _realized_vol_20d(df) -> float | None:
    if df is None or len(df) < 22 or "Close" not in df.columns:
        return None
    try:
        returns = df.Close.pct_change().dropna().iloc[-20:]
        if returns.empty:
            return None
        return round(float(returns.std() * math.sqrt(252)), 4)
    except Exception:
        return None


def _build_strike_map(filtered: list[dict], spot: float, technical_levels: dict | None = None) -> list[dict]:
    by_strike: dict[float, dict] = {}
    for c in filtered:
        strike = float(c["strike"])
        row = by_strike.setdefault(
            strike,
            {
                "strike": round(strike, 2),
                "distance_pct": round((strike / spot - 1) * 100, 2) if spot else None,
                "call_oi": 0,
                "put_oi": 0,
                "call_volume": 0,
                "put_volume": 0,
                "call_iv": None,
                "put_iv": None,
                "call_gamma_notional": 0.0,
                "put_gamma_notional": 0.0,
                "net_gamma_notional": 0.0,
                "sr_confluence": [],
            },
        )
        oi = int(c.get("open_interest") or 0)
        vol = int(c.get("volume") or 0)
        gamma = c.get("gamma")
        gamma_notional = (gamma or 0) * oi * CONTRACT_MULTIPLIER * spot * spot * 0.01
        if c.get("type") == "call":
            row["call_oi"] += oi
            row["call_volume"] += vol
            row["call_iv"] = c.get("implied_volatility") or row["call_iv"]
            row["call_gamma_notional"] += gamma_notional
            row["net_gamma_notional"] += gamma_notional
        else:
            row["put_oi"] += oi
            row["put_volume"] += vol
            row["put_iv"] = c.get("implied_volatility") or row["put_iv"]
            row["put_gamma_notional"] += gamma_notional
            row["net_gamma_notional"] -= gamma_notional

    rows = []
    for row in by_strike.values():
        row["total_oi"] = row["call_oi"] + row["put_oi"]
        row["total_volume"] = row["call_volume"] + row["put_volume"]
        row["call_gamma_notional"] = round(row["call_gamma_notional"], 2)
        row["put_gamma_notional"] = round(row["put_gamma_notional"], 2)
        row["net_gamma_notional"] = round(row["net_gamma_notional"], 2)
        rows.append(row)
    rows.sort(key=lambda x: abs(x.get("distance_pct") or 999))
    return enrich_options_map_confluence(rows, technical_levels or {}, spot)


def enrich_options_map_confluence(options_map: list[dict], technical_levels: dict, spot: float) -> list[dict]:
    levels = []
    for role in ("supports", "resistances"):
        for lv in technical_levels.get(role, []) or []:
            price = lv.get("price")
            if isinstance(price, (int, float)):
                levels.append({"price": float(price), "role": role[:-1], "source": lv.get("source", "")})
    if not levels or not spot:
        return options_map
    enriched = []
    for row in options_map:
        strike = row.get("strike")
        matches = []
        if strike:
            for lv in levels:
                if abs(float(strike) - lv["price"]) / spot <= 0.01:
                    matches.append({
                        "role": lv["role"],
                        "source": lv["source"],
                        "level": round(lv["price"], 2),
                    })
        enriched.append({**row, "sr_confluence": matches[:3]})
    return enriched


def _expected_move_from_atm(filtered: list[dict], spot: float) -> dict:
    if not filtered or not spot:
        return {"status": "unavailable"}
    strikes = sorted({float(c["strike"]) for c in filtered})
    if not strikes:
        return {"status": "unavailable"}
    atm = min(strikes, key=lambda x: abs(x - spot))
    call = next((c for c in filtered if c.get("type") == "call" and float(c["strike"]) == atm), None)
    put = next((c for c in filtered if c.get("type") == "put" and float(c["strike"]) == atm), None)
    call_mid = None
    put_mid = None
    if call and call.get("bid") is not None and call.get("ask") is not None:
        call_mid = (call["bid"] + call["ask"]) / 2
    elif call:
        call_mid = call.get("last_price")
    if put and put.get("bid") is not None and put.get("ask") is not None:
        put_mid = (put["bid"] + put["ask"]) / 2
    elif put:
        put_mid = put.get("last_price")
    if call_mid is None or put_mid is None:
        return {"status": "unavailable", "atm_strike": atm}
    expected = call_mid + put_mid
    ivs = [x for x in [(call or {}).get("implied_volatility"), (put or {}).get("implied_volatility")] if x]
    return {
        "status": "available",
        "atm_strike": round(atm, 2),
        "atm_call_mid": round(call_mid, 3),
        "atm_put_mid": round(put_mid, 3),
        "atm_straddle": round(expected, 3),
        "expected_move": round(expected, 3),
        "expected_move_pct": round(expected / spot * 100, 2),
        "atm_iv": round(sum(ivs) / len(ivs), 4) if ivs else None,
    }


def _skew_from_chain(filtered: list[dict], spot: float) -> dict:
    puts = [c for c in filtered if c.get("type") == "put" and c.get("implied_volatility")]
    calls = [c for c in filtered if c.get("type") == "call" and c.get("implied_volatility")]
    if not puts or not calls or not spot:
        return {"status": "unavailable"}
    put = min(puts, key=lambda c: abs((c["strike"] / spot) - 0.95))
    call = min(calls, key=lambda c: abs((c["strike"] / spot) - 1.05))
    skew = (put.get("implied_volatility") or 0) - (call.get("implied_volatility") or 0)
    return {
        "status": "available",
        "put_95_iv": round(put.get("implied_volatility"), 4),
        "call_105_iv": round(call.get("implied_volatility"), 4),
        "put_call_skew": round(skew, 4),
    }


def _greeks_exposure_from_map(options_map: list[dict]) -> dict:
    if not options_map:
        return {"status": "unavailable", "source": "chain", "reason": "no options map"}
    net_gamma = sum(row.get("net_gamma_notional") or 0 for row in options_map)
    gross_gamma = sum(abs(row.get("net_gamma_notional") or 0) for row in options_map)
    if gross_gamma <= 0:
        return {
            "status": "unavailable",
            "source": "chain",
            "reason": "gamma missing from public chain",
        }
    gamma_wall = max(options_map, key=lambda row: abs(row.get("net_gamma_notional") or 0))
    zero_gamma = _zero_gamma_level(options_map)
    if net_gamma > gross_gamma * 0.15:
        regime = "positive"
    elif net_gamma < -gross_gamma * 0.15:
        regime = "negative"
    else:
        regime = "neutral"
    return {
        "status": "estimated",
        "source": "chain_greeks",
        "net_gamma_notional": round(net_gamma, 2),
        "gross_gamma_notional": round(gross_gamma, 2),
        "gamma_regime": regime,
        "gamma_wall": gamma_wall.get("strike"),
        "gamma_wall_notional": gamma_wall.get("net_gamma_notional"),
        "zero_gamma_level": zero_gamma,
        "vanna": None,
        "charm": None,
    }


def _zero_gamma_level(options_map: list[dict]) -> float | None:
    rows = sorted(
        [r for r in options_map if isinstance(r.get("strike"), (int, float))],
        key=lambda r: r["strike"],
    )
    if len(rows) < 2:
        return None
    cumulative = 0.0
    prev = None
    for row in rows:
        cumulative += row.get("net_gamma_notional") or 0
        current = {"strike": row["strike"], "cum": cumulative}
        if prev and ((prev["cum"] <= 0 <= current["cum"]) or (prev["cum"] >= 0 >= current["cum"])):
            denom = abs(prev["cum"]) + abs(current["cum"])
            if denom == 0:
                return round(float(current["strike"]), 2)
            weight = abs(prev["cum"]) / denom
            return round(prev["strike"] + (current["strike"] - prev["strike"]) * weight, 2)
        prev = current
    return None


def build_chain_feature(
    symbol: str,
    expiry: str,
    spot: float,
    contracts: list[dict],
    source: str,
    technical_levels: dict | None = None,
    realized_vol: float | None = None,
) -> dict:
    raw_count = len(contracts)
    filtered, warnings = filter_reliable_contracts(contracts, spot)
    calls: dict[float, int] = {}
    puts: dict[float, int] = {}
    call_volume = 0
    put_volume = 0
    total_call_oi = 0
    total_put_oi = 0
    near_spot_oi = 0

    for c in filtered:
        strike = float(c["strike"])
        oi = int(c.get("open_interest") or 0)
        volume = int(c.get("volume") or 0)
        if abs(strike / spot - 1) <= 0.05:
            near_spot_oi += oi
        if c.get("type") == "call":
            calls[strike] = calls.get(strike, 0) + oi
            total_call_oi += oi
            call_volume += volume
        else:
            puts[strike] = puts.get(strike, 0) + oi
            total_put_oi += oi
            put_volume += volume

    total_oi = total_call_oi + total_put_oi
    total_volume = call_volume + put_volume
    max_pain = max_pain_from_oi(calls, puts)
    call_wall = max(calls, key=calls.get) if calls else None
    put_wall = max(puts, key=puts.get) if puts else None
    top_strike_oi = 0
    for strike in set(calls) | set(puts):
        top_strike_oi = max(top_strike_oi, calls.get(strike, 0) + puts.get(strike, 0))

    quality_score, quality_status = option_quality(raw_count, filtered)
    if quality_status == "weak":
        warnings.append(f"Option chain quality weak ({quality_score}/100); scoring disabled")

    options_map = _build_strike_map(filtered, spot, technical_levels=technical_levels)
    expected_move = _expected_move_from_atm(filtered, spot)
    skew = _skew_from_chain(filtered, spot)
    dte = days_to_expiry(expiry)
    term_iv = expected_move.get("atm_iv")
    volatility = {
        **expected_move,
        "skew": skew,
        "realized_vol_20d": realized_vol,
        "iv_rv_premium": round(term_iv - realized_vol, 4) if term_iv and realized_vol else None,
    }

    oi_pcr = (total_put_oi / total_call_oi) if total_call_oi else None
    volume_pcr = (put_volume / call_volume) if call_volume else None
    return {
        "source": source,
        "symbol": symbol,
        "expiry": expiry,
        "days_to_expiry": dte,
        "is_monthly_opex": is_monthly_opex(expiry),
        "spot": round(float(spot), 4) if spot else None,
        "raw_contracts": raw_count,
        "filtered_contracts": len(filtered),
        "filtered_ratio": round(len(filtered) / raw_count, 3) if raw_count else 0,
        "quality_score": quality_score,
        "quality_status": quality_status,
        "total_oi": total_oi,
        "near_spot_oi": near_spot_oi,
        "total_volume": total_volume,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "total_call_volume": call_volume,
        "total_put_volume": put_volume,
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
        "options_map": options_map[:80],
        "volatility": volatility,
        "greeks_exposure": _greeks_exposure_from_map(options_map),
        "warnings": warnings,
    }


def _candidate_expiries(expirations: list, summaries: list[dict], max_expiries: int) -> list[str]:
    by_expiry = {}
    for expiry in expirations or []:
        if isinstance(expiry, dict):
            expiry = expiry.get("expiry") or expiry.get("date")
        if expiry:
            by_expiry[str(expiry)] = {"expiry": str(expiry)}
    for summary in summaries or []:
        expiry = summary.get("expiry")
        if expiry:
            by_expiry[str(expiry)] = {**by_expiry.get(str(expiry), {}), **summary}
    rows = []
    for expiry, row in by_expiry.items():
        dte = row.get("days_to_expiry")
        if dte is None:
            dte = days_to_expiry(expiry)
        if dte is None or dte < 0 or dte > 90:
            continue
        total_oi = (row.get("total_call_oi") or 0) + (row.get("total_put_oi") or 0)
        in_primary = dte <= 60
        monthly_large = dte <= 90 and is_monthly_opex(expiry) and total_oi > 0
        if in_primary or monthly_large:
            rows.append((dte, -total_oi, expiry))
    return [expiry for _, _, expiry in sorted(rows)[: max(1, max_expiries)]]


def rank_expiries(features: list[dict], summaries: list[dict] | None = None) -> list[dict]:
    summary_by_expiry = {s.get("expiry"): s for s in summaries or [] if s.get("expiry")}
    rankings = []
    for feature in features:
        expiry = feature.get("expiry")
        summary = summary_by_expiry.get(expiry, {})
        total_oi = feature.get("total_oi")
        if total_oi is None:
            total_oi = (summary.get("total_call_oi") or 0) + (summary.get("total_put_oi") or 0)
        near_spot_oi = feature.get("near_spot_oi") or 0
        total_volume = feature.get("total_volume") or 0
        size_score = total_oi + near_spot_oi * 1.5 + total_volume * 0.5
        if feature.get("is_monthly_opex"):
            size_score += total_oi * 0.15
        if feature.get("quality_status") == "weak":
            size_score *= 0.55
        elif feature.get("quality_status") == "unavailable":
            size_score *= 0.30
        rankings.append({
            "expiry": expiry,
            "days_to_expiry": feature.get("days_to_expiry"),
            "size_score": round(size_score, 2),
            "total_oi": total_oi,
            "near_spot_oi": near_spot_oi,
            "total_volume": total_volume,
            "monthly_opex_bonus": bool(feature.get("is_monthly_opex")),
            "quality_score": feature.get("quality_score", 0),
            "quality_status": feature.get("quality_status", "unavailable"),
            "max_pain": feature.get("max_pain") or summary.get("max_pain"),
            "put_call_ratio": feature.get("put_call_ratio") or summary.get("put_call_ratio"),
        })
    return sorted(
        rankings,
        key=lambda x: (-(x.get("size_score") or 0), x.get("days_to_expiry") if x.get("days_to_expiry") is not None else 999),
    )


def select_primary_expiry(rankings: list[dict]) -> dict | None:
    if not rankings:
        return None
    max_score = max(r.get("size_score") or 0 for r in rankings)
    quality_ok = [r for r in rankings if r.get("quality_status") in {"good", "usable"}]
    large = [r for r in quality_ok if (r.get("size_score") or 0) >= max_score * 0.60]
    if large:
        return sorted(large, key=lambda r: r.get("days_to_expiry") if r.get("days_to_expiry") is not None else 999)[0]
    return rankings[0]


def _chain_features_from_contracts(
    symbol: str,
    spot: float,
    contracts: list[dict],
    max_expiries: int,
    technical_levels: dict | None,
    realized_vol: float | None,
) -> list[dict]:
    by_expiry: dict[str, list[dict]] = {}
    for c in contracts:
        expiry = c.get("expiry")
        dte = days_to_expiry(expiry)
        if expiry and dte is not None and 0 <= dte <= 90:
            by_expiry.setdefault(expiry, []).append(c)
    expiries = sorted(by_expiry, key=lambda e: days_to_expiry(e) if days_to_expiry(e) is not None else 999)
    features = []
    for expiry in expiries[: max(1, max_expiries)]:
        feature = build_chain_feature(
            symbol,
            expiry,
            spot,
            by_expiry[expiry],
            by_expiry[expiry][0].get("source", "chain"),
            technical_levels=technical_levels,
            realized_vol=realized_vol,
        )
        features.append(feature)
    return features


def _fetch_public_features(
    symbol: str,
    spot: float,
    max_expiries: int,
    timeout: int,
    technical_levels: dict | None,
    realized_vol: float | None,
    provider: str,
) -> tuple[list[dict], list[dict], list[str], str]:
    warnings = []
    summary = {"expirations": [], "summary_by_expiry": []}
    if provider in {"auto", "public", "darkfina"}:
        summary, df_warnings = _fetch_darkfina_summary(symbol, timeout=timeout)
        warnings.extend(df_warnings)
    expirations = summary.get("expirations") or []
    if provider in {"auto", "public", "darkfina", "yfinance"}:
        yf_expirations, yf_warning = _fetch_yfinance_expirations(symbol)
        if yf_warning:
            warnings.append(yf_warning)
        expirations = list(dict.fromkeys([*expirations, *yf_expirations]))
    selected_expiries = _candidate_expiries(expirations, summary.get("summary_by_expiry", []), max_expiries)
    features = []
    for expiry in selected_expiries:
        contracts = []
        meta = {}
        if provider in {"auto", "public", "darkfina"}:
            contracts, meta = _fetch_darkfina_chain(symbol, expiry, timeout=timeout)
            if meta.get("warning"):
                warnings.append(meta["warning"])
        if (not contracts or provider == "yfinance") and provider in {"auto", "public", "darkfina", "yfinance"}:
            yf_contracts, yf_meta = _fetch_yfinance_chain(symbol, expiry)
            if yf_contracts:
                contracts = yf_contracts
                meta = yf_meta
            elif yf_meta.get("warning"):
                warnings.append(yf_meta["warning"])
        if contracts:
            feature_spot = meta.get("current_price") or spot
            features.append(
                build_chain_feature(
                    symbol,
                    expiry,
                    feature_spot,
                    contracts,
                    meta.get("source") or contracts[0].get("source", "public_chain"),
                    technical_levels=technical_levels,
                    realized_vol=realized_vol,
                )
            )
        else:
            features.append({
                "source": "none",
                "symbol": symbol,
                "expiry": expiry,
                "days_to_expiry": days_to_expiry(expiry),
                "quality_score": 0,
                "quality_status": "unavailable",
                "warnings": [f"No option chain contracts available for {expiry}"],
            })
    return features, summary.get("summary_by_expiry", []), warnings, summary.get("source", "public")


def _provider_features(
    symbol: str,
    spot: float,
    provider: str,
    max_expiries: int,
    timeout: int,
    technical_levels: dict | None,
    realized_vol: float | None,
) -> tuple[list[dict], list[str], str]:
    if provider == "alphavantage":
        contracts, warnings = _fetch_alphavantage_chain(symbol, timeout=timeout)
        return _chain_features_from_contracts(symbol, spot, contracts, max_expiries, technical_levels, realized_vol), warnings, provider
    if provider == "polygon":
        contracts, warnings = _fetch_polygon_snapshot(symbol, timeout=timeout)
        return _chain_features_from_contracts(symbol, spot, contracts, max_expiries, technical_levels, realized_vol), warnings, provider
    if provider == "tradier":
        expirations, warnings = _fetch_tradier_expirations(symbol, timeout=timeout)
        features = []
        for expiry in _candidate_expiries(expirations, [], max_expiries):
            contracts, meta = _fetch_tradier_chain(symbol, expiry, timeout=timeout)
            if contracts:
                features.append(build_chain_feature(symbol, expiry, spot, contracts, "tradier_chain", technical_levels, realized_vol))
            elif meta.get("warning"):
                warnings.append(meta["warning"])
        return features, warnings, provider
    if provider == "unusualwhales":
        expirations, warnings = _fetch_unusualwhales_expirations(symbol, timeout=timeout)
        features = []
        for expiry in _candidate_expiries(expirations, [], max_expiries):
            contracts, meta = _fetch_unusualwhales_chain(symbol, expiry, timeout=timeout)
            if contracts:
                features.append(build_chain_feature(symbol, expiry, spot, contracts, "unusualwhales_chain", technical_levels, realized_vol))
            elif meta.get("warning"):
                warnings.append(meta["warning"])
        return features, warnings, provider
    return [], [f"Unknown keyed options provider: {provider}"], provider


def _available_keyed_provider() -> str | None:
    """Pick one keyed provider for auto-enrichment when credentials are present."""
    if os.environ.get("POLYGON_API_KEY"):
        return "polygon"
    if os.environ.get("TRADIER_TOKEN") or os.environ.get("TRADIER_ACCESS_TOKEN"):
        return "tradier"
    if os.environ.get("UW_API_KEY") or os.environ.get("UNUSUAL_WHALES_API_KEY"):
        return "unusualwhales"
    if os.environ.get("ALPHAVANTAGE_API_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY"):
        return "alphavantage"
    return None


def _merge_keyed_features(public_features: list[dict], keyed_features: list[dict]) -> list[dict]:
    """Prefer keyed chains when they improve same-expiry quality or provide greeks."""
    by_expiry = {f.get("expiry"): f for f in public_features if f.get("expiry")}
    for keyed in keyed_features:
        expiry = keyed.get("expiry")
        if not expiry:
            continue
        existing = by_expiry.get(expiry)
        if not existing:
            by_expiry[expiry] = keyed
            continue
        keyed_quality = keyed.get("quality_score") or 0
        existing_quality = existing.get("quality_score") or 0
        keyed_greeks = (keyed.get("greeks_exposure") or {}).get("status") in {"estimated", "available"}
        existing_greeks = (existing.get("greeks_exposure") or {}).get("status") in {"estimated", "available"}
        if keyed_quality >= existing_quality or (keyed_greeks and not existing_greeks):
            merged_warnings = list(dict.fromkeys((existing.get("warnings") or []) + (keyed.get("warnings") or [])))
            by_expiry[expiry] = {**existing, **keyed, "warnings": merged_warnings}
    return list(by_expiry.values())


def _default_cache_dir() -> str:
    from paths import data_dir as _data_dir
    return os.path.join(_data_dir(), "options_cache")


def _snapshot_path(cache_dir: str, symbol: str, stamp: str) -> str:
    return os.path.join(cache_dir, symbol.upper(), f"{stamp}.json")


def _load_previous_snapshot(cache_dir: str, symbol: str, before_stamp: str) -> dict | None:
    symbol_dir = os.path.join(cache_dir, symbol.upper())
    if not os.path.isdir(symbol_dir):
        return None
    candidates = sorted(
        f for f in os.listdir(symbol_dir)
        if f.endswith(".json") and f[:-5] < before_stamp
    )
    if not candidates:
        return None
    try:
        with open(os.path.join(symbol_dir, candidates[-1])) as f:
            return json.load(f)
    except Exception:
        return None


def _compact_snapshot(options: dict) -> dict:
    selected = options.get("selected_expiry") or {}
    rows = {}
    for row in options.get("options_map") or []:
        strike = str(row.get("strike"))
        rows[strike] = {
            "call_oi": row.get("call_oi", 0),
            "put_oi": row.get("put_oi", 0),
            "total_oi": row.get("total_oi", 0),
        }
    return {
        "generated_at": options.get("generated_at"),
        "symbol": options.get("symbol"),
        "selected_expiry": selected.get("expiry"),
        "rows": rows,
    }


def _save_snapshot(cache_dir: str, symbol: str, options: dict) -> None:
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        symbol_dir = os.path.join(cache_dir, symbol.upper())
        os.makedirs(symbol_dir, exist_ok=True)
        with open(_snapshot_path(cache_dir, symbol, stamp), "w") as f:
            json.dump(_compact_snapshot(options), f, indent=2)
    except Exception:
        return


def _compute_oi_change(cache_dir: str, symbol: str, options: dict) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    previous = _load_previous_snapshot(cache_dir, symbol, stamp)
    if not previous:
        return {"status": "unavailable", "reason": "no previous local snapshot"}
    current = _compact_snapshot(options)
    if previous.get("selected_expiry") != current.get("selected_expiry"):
        return {
            "status": "unavailable",
            "reason": "selected expiry changed",
            "previous_expiry": previous.get("selected_expiry"),
            "current_expiry": current.get("selected_expiry"),
        }
    changes = []
    for strike, row in current.get("rows", {}).items():
        prev = (previous.get("rows") or {}).get(strike, {})
        delta = (row.get("total_oi") or 0) - (prev.get("total_oi") or 0)
        if delta:
            changes.append({"strike": _safe_float(strike), "total_oi_change": delta})
    changes = sorted(changes, key=lambda x: abs(x["total_oi_change"]), reverse=True)
    return {
        "status": "available",
        "previous_generated_at": previous.get("generated_at"),
        "top_changes": changes[:10],
    }


def _term_structure(features: list[dict]) -> list[dict]:
    rows = []
    for feature in features:
        vol = feature.get("volatility") or {}
        if vol.get("atm_iv") is not None:
            rows.append({
                "expiry": feature.get("expiry"),
                "days_to_expiry": feature.get("days_to_expiry"),
                "atm_iv": vol.get("atm_iv"),
                "expected_move_pct": vol.get("expected_move_pct"),
            })
    return sorted(rows, key=lambda x: x.get("days_to_expiry") if x.get("days_to_expiry") is not None else 999)


def _summarize_selected(feature: dict | None, term_structure: list[dict]) -> dict:
    if not feature:
        return {
            "chain_quality": {"quality_status": "unavailable", "quality_score": 0},
            "max_pain": None,
            "put_call_ratios": {},
            "walls": {},
            "options_map": [],
            "volatility": {"term_structure": term_structure},
            "greeks_exposure": {"status": "unavailable"},
        }
    volatility = dict(feature.get("volatility") or {})
    volatility["term_structure"] = term_structure
    return {
        "chain_quality": {
            "source": feature.get("source"),
            "quality_status": feature.get("quality_status"),
            "quality_score": feature.get("quality_score"),
            "raw_contracts": feature.get("raw_contracts"),
            "filtered_contracts": feature.get("filtered_contracts"),
            "filtered_ratio": feature.get("filtered_ratio"),
        },
        "max_pain": {
            "price": feature.get("max_pain"),
            "distance_pct": feature.get("max_pain_distance_pct"),
        } if feature.get("max_pain") is not None else None,
        "put_call_ratios": {
            "oi": feature.get("oi_put_call_ratio"),
            "volume": feature.get("volume_put_call_ratio"),
        },
        "walls": {
            "call_wall": feature.get("call_wall"),
            "call_wall_oi": feature.get("call_wall_oi"),
            "dist_to_call_wall_pct": feature.get("dist_to_call_wall_pct"),
            "put_wall": feature.get("put_wall"),
            "put_wall_oi": feature.get("put_wall_oi"),
            "dist_to_put_wall_pct": feature.get("dist_to_put_wall_pct"),
            "top_strike_oi_concentration": feature.get("top_strike_oi_concentration"),
        },
        "options_map": feature.get("options_map") or [],
        "volatility": volatility,
        "greeks_exposure": feature.get("greeks_exposure") or {"status": "unavailable"},
    }


def compute_options_score_overlay(
    options: dict | None,
    current_price: float,
    close_up: bool | None = None,
    close_down: bool | None = None,
) -> dict:
    if not options:
        return {"enabled": False, "raw_score": 0, "clamped_score": 0, "details": []}
    quality = options.get("chain_quality") or {}
    if quality.get("quality_status") not in {"good", "usable"}:
        return {
            "enabled": False,
            "raw_score": 0,
            "clamped_score": 0,
            "details": [("옵션 체인 품질 약함/불가 - 점수 미반영", 0)],
        }

    score = 0
    details = []
    selected = options.get("selected_expiry") or {}
    dte = selected.get("days_to_expiry")
    max_pain = options.get("max_pain") or {}
    mp = max_pain.get("price")
    if mp and current_price > 0 and dte is not None and int(dte) <= 21:
        mp_gap = (current_price / float(mp) - 1) * 100
        if mp_gap > 6:
            score -= 4; details.append((f"옵션 Max Pain 상방 과이격 ({mp_gap:.1f}%, DTE {dte})", -4))
        elif mp_gap > 3:
            score -= 2; details.append((f"옵션 Max Pain 상방 이격 ({mp_gap:.1f}%, DTE {dte})", -2))
        elif mp_gap < -6:
            score += 3; details.append((f"옵션 Max Pain 하방 과이격 ({mp_gap:.1f}%, DTE {dte})", 3))
        elif mp_gap < -3:
            score += 2; details.append((f"옵션 Max Pain 하방 이격 ({mp_gap:.1f}%, DTE {dte})", 2))

    pcr = (options.get("put_call_ratios") or {}).get("oi")
    if pcr is not None:
        pcr = float(pcr)
        if pcr >= 1.5:
            score -= 3; details.append((f"옵션 Put/Call 방어적 ({pcr:.2f})", -3))
        elif pcr >= 1.0:
            score -= 1; details.append((f"옵션 Put 우위 ({pcr:.2f})", -1))
        elif pcr <= 0.5:
            score += 2; details.append((f"옵션 Call OI 우위 ({pcr:.2f})", 2))
        elif pcr <= 0.75:
            score += 1; details.append((f"옵션 Call 우위 ({pcr:.2f})", 1))

    walls = options.get("walls") or {}
    call_wall = walls.get("call_wall")
    put_wall = walls.get("put_wall")
    call_wall_dist = walls.get("dist_to_call_wall_pct")
    put_wall_dist = walls.get("dist_to_put_wall_pct")
    if call_wall and call_wall_dist is not None:
        call_wall_dist = float(call_wall_dist)
        if 0 <= call_wall_dist <= 1.5:
            score -= 2; details.append((f"옵션 Call Wall 근접 저항 (${call_wall:.2f}, {call_wall_dist:.1f}%)", -2))
        elif -1.0 <= call_wall_dist < 0 and close_up:
            score += 1; details.append((f"옵션 Call Wall 상향 돌파 (${call_wall:.2f})", 1))
    if put_wall and put_wall_dist is not None:
        put_wall_dist = float(put_wall_dist)
        if 0 <= put_wall_dist <= 1.5:
            score += 2; details.append((f"옵션 Put Wall 근접 지지 (${put_wall:.2f}, {put_wall_dist:.1f}%)", 2))
        elif -1.0 <= put_wall_dist < 0 and close_down:
            score -= 2; details.append((f"옵션 Put Wall 하향 이탈 (${put_wall:.2f})", -2))

    concentration = walls.get("top_strike_oi_concentration")
    if concentration is not None and dte is not None and int(dte) <= 14 and float(concentration) >= 0.35:
        score -= 1; details.append((f"옵션 OI 쏠림/핀 리스크 ({float(concentration)*100:.0f}%, DTE {dte})", -1))

    greeks = options.get("greeks_exposure") or {}
    regime = greeks.get("gamma_regime")
    if regime == "positive":
        score += 1; details.append(("옵션 Gamma 양수 레짐 - 변동성 완충", 1))
    elif regime == "negative":
        score -= 2; details.append(("옵션 Gamma 음수 레짐 - 변동성 확대", -2))

    vol = options.get("volatility") or {}
    iv_rv = vol.get("iv_rv_premium")
    if iv_rv is not None and iv_rv > 0.25:
        score -= 1; details.append((f"IV/RV 프리미엄 높음 ({iv_rv:.2f})", -1))

    clamped = max(-8, min(8, score))
    return {
        "enabled": True,
        "max_abs": 8,
        "raw_score": score,
        "clamped_score": clamped,
        "details": details,
    }


def fetch_options_analysis(
    symbol: str,
    price: float | None,
    provider: str = "auto",
    max_expiries: int = 5,
    timeout: int = 8,
    cache_dir: str | None = None,
    technical_levels: dict | None = None,
    df=None,
) -> dict:
    symbol = symbol.upper()
    spot = float(price or 0)
    warnings = []
    if spot <= 0:
        return {
            "source": "none",
            "provider_requested": provider,
            "symbol": symbol,
            "spot": None,
            "selected_expiry": None,
            "expiry_rankings": [],
            "chain_quality": {"quality_status": "unavailable", "quality_score": 0},
            "max_pain": None,
            "put_call_ratios": {},
            "walls": {},
            "options_map": [],
            "volatility": {},
            "greeks_exposure": {"status": "unavailable"},
            "score_overlay": {"enabled": False, "raw_score": 0, "clamped_score": 0, "details": []},
            "warnings": ["Options analysis skipped: missing spot price"],
        }

    realized_vol = _realized_vol_20d(df)
    provider = provider or "auto"
    keyed_providers = {"alphavantage", "tradier", "polygon", "unusualwhales"}
    features = []
    summaries = []
    provider_used = "public"

    if provider in keyed_providers:
        features, keyed_warnings, provider_used = _provider_features(
            symbol, spot, provider, max_expiries, timeout, technical_levels, realized_vol
        )
        warnings.extend(keyed_warnings)
        if not features:
            fallback, summaries, fallback_warnings, provider_used = _fetch_public_features(
                symbol, spot, max_expiries, timeout, technical_levels, realized_vol, "public"
            )
            features = fallback
            warnings.extend([f"{provider} unavailable; fell back to public sources", *fallback_warnings])
    elif provider == "auto":
        features, summaries, public_warnings, provider_used = _fetch_public_features(
            symbol, spot, max_expiries, timeout, technical_levels, realized_vol, "public"
        )
        warnings.extend(public_warnings)
        keyed_provider = _available_keyed_provider()
        if keyed_provider:
            keyed_features, keyed_warnings, keyed_used = _provider_features(
                symbol, spot, keyed_provider, max_expiries, timeout, technical_levels, realized_vol
            )
            warnings.extend(keyed_warnings)
            if keyed_features:
                features = _merge_keyed_features(features, keyed_features)
                provider_used = f"public+{keyed_used}"
    else:
        features, summaries, public_warnings, provider_used = _fetch_public_features(
            symbol, spot, max_expiries, timeout, technical_levels, realized_vol, provider
        )
        warnings.extend(public_warnings)

    rankings = rank_expiries(features, summaries)
    selected_rank = select_primary_expiry(rankings)
    selected_feature = None
    if selected_rank:
        selected_feature = next((f for f in features if f.get("expiry") == selected_rank.get("expiry")), None)
    term_structure = _term_structure(features)
    selected_parts = _summarize_selected(selected_feature, term_structure)
    selected_warnings = selected_feature.get("warnings") if selected_feature else []
    selected_warnings = selected_warnings or []

    options = {
        "source": provider_used,
        "provider_requested": provider,
        "provider_used": selected_feature.get("source") if selected_feature else provider_used,
        "symbol": symbol,
        "spot": round(spot, 4),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_expiry": selected_rank,
        "expiry_rankings": rankings,
        "features_by_expiry": features,
        **selected_parts,
        "score_overlay": {"enabled": False, "raw_score": 0, "clamped_score": 0, "details": []},
        "warnings": list(dict.fromkeys([*warnings, *selected_warnings])),
    }

    cache_dir = cache_dir or _default_cache_dir()
    options["oi_change"] = _compute_oi_change(cache_dir, symbol, options)
    _save_snapshot(cache_dir, symbol, options)
    return options


def legacy_flow_options(options: dict | None) -> dict | None:
    """Return a v2.x-compatible flow.options view from canonical options."""
    if not options:
        return None
    return {
        "source": options.get("source"),
        "symbol": options.get("symbol"),
        "current_price": options.get("spot"),
        "expirations": [r.get("expiry") for r in options.get("expiry_rankings", [])],
        "summary_by_expiry": [
            {
                "expiry": r.get("expiry"),
                "days_to_expiry": r.get("days_to_expiry"),
                "max_pain": r.get("max_pain"),
                "put_call_ratio": r.get("put_call_ratio"),
                "total_call_oi": None,
                "total_put_oi": None,
                "gex": {},
            }
            for r in options.get("expiry_rankings", [])
        ],
        "chain_features_by_expiry": options.get("features_by_expiry", []),
        "primary_chain_feature": next(
            (f for f in options.get("features_by_expiry", []) if f.get("expiry") == (options.get("selected_expiry") or {}).get("expiry")),
            None,
        ),
        "warnings": options.get("warnings", []),
    }
