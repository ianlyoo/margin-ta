"""
Layer 1d — Session Quote: yfinance regular/pre-market + KIS day-market.

Regular daily OHLCV stays on yfinance. This module only supplies an optional
live/session quote to replace the analysis price when the US day-market is open.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
KIS_DEFAULT_URL_BASE = "https://openapi.koreainvestment.com:9443"


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


def _is_us_dst(now_kst: datetime) -> bool:
    return bool(now_kst.astimezone(NEW_YORK).dst())


def get_us_session(now_kst: datetime | None = None) -> dict[str, Any]:
    """Return the current US equity session in KST. (하드코딩 로직 — get_us_session_from_toss가 더 정확)"""
    if now_kst is None:
        now_kst = datetime.now(KST)
    elif now_kst.tzinfo is None:
        now_kst = now_kst.replace(tzinfo=KST)
    else:
        now_kst = now_kst.astimezone(KST)

    hm = now_kst.hour * 60 + now_kst.minute
    weekday = now_kst.weekday()
    is_dst = _is_us_dst(now_kst)

    day_start = 9 * 60 if is_dst else 10 * 60
    day_end = 17 * 60 if is_dst else 18 * 60
    pre_start = 17 * 60 if is_dst else 18 * 60
    regular_start = 22 * 60 + 30 if is_dst else 23 * 60 + 30
    regular_end = 5 * 60 if is_dst else 6 * 60
    after_end = 8 * 60 if is_dst else 9 * 60

    session = "closed"
    is_open = False
    if weekday < 5 and day_start <= hm < day_end:
        session = "day_market"
        is_open = True
    elif weekday < 5 and pre_start <= hm < regular_start:
        session = "premarket"
        is_open = True
    elif (weekday < 5 and hm >= regular_start) or (0 < weekday <= 5 and hm < regular_end):
        session = "regular"
        is_open = True
    elif 0 < weekday <= 5 and regular_end <= hm < after_end:
        session = "aftermarket"
        is_open = True

    return {
        "session": session,
        "is_open": is_open,
        "is_dst": is_dst,
        "now_kst": now_kst.isoformat(),
        "window_kst": {
            "day_market": f"{day_start // 60:02d}:{day_start % 60:02d}-{day_end // 60:02d}:{day_end % 60:02d}",
            "premarket": f"{pre_start // 60:02d}:{pre_start % 60:02d}-{regular_start // 60:02d}:{regular_start % 60:02d}",
            "regular": f"{regular_start // 60:02d}:{regular_start % 60:02d}-{regular_end // 60:02d}:{regular_end % 60:02d}+1",
            "aftermarket": f"{regular_end // 60:02d}:{regular_end % 60:02d}-{after_end // 60:02d}:{after_end % 60:02d}",
        },
    }


def _is_kor_regular_session(now_kst: datetime | None = None) -> bool:
    if now_kst is None:
        now_kst = datetime.now(KST)
    elif now_kst.tzinfo is None:
        now_kst = now_kst.replace(tzinfo=KST)
    else:
        now_kst = now_kst.astimezone(KST)
    if now_kst.weekday() >= 5:
        return False
    hm = now_kst.hour * 60 + now_kst.minute
    return (9 * 60) <= hm <= (15 * 60 + 30)


def get_korea_session(now_kst: datetime | None = None) -> dict[str, Any]:
    """Return the current Korean equity session in KST."""
    if now_kst is None:
        now_kst = datetime.now(KST)
    elif now_kst.tzinfo is None:
        now_kst = now_kst.replace(tzinfo=KST)
    else:
        now_kst = now_kst.astimezone(KST)

    hm = now_kst.hour * 60 + now_kst.minute
    weekday = now_kst.weekday()
    session = "closed"
    is_open = False
    if weekday < 5 and 8 * 60 + 30 <= hm < 9 * 60:
        session = "preopen"
    elif weekday < 5 and 9 * 60 <= hm <= 15 * 60 + 30:
        session = "regular"
        is_open = True
    elif weekday < 5 and 15 * 60 + 40 <= hm < 20 * 60:
        session = "afterhours"
        is_open = True

    return {
        "session": session,
        "is_open": is_open,
        "now_kst": now_kst.isoformat(),
        "window_kst": {
            "preopen": "08:30-09:00",
            "regular": "09:00-15:30",
            "afterhours": "15:40-20:00",
            "afterhours_venues": ["KRX closing/after-hours", "NXT integrated after-market"],
        },
    }


def _kis_excd(exchange: str, day_market: bool) -> str | None:
    exchange = str(exchange or "").strip().upper()
    if day_market:
        return {
            "NASDAQ": "BAQ",
            "NASD": "BAQ",
            "NAS": "BAQ",
            "NYSE": "BAY",
            "NYS": "BAY",
            "AMEX": "BAA",
            "AMS": "BAA",
        }.get(exchange)
    return {
        "NASDAQ": "NAS",
        "NASD": "NAS",
        "NAS": "NAS",
        "NYSE": "NYS",
        "NYS": "NYS",
        "AMEX": "AMS",
        "AMS": "AMS",
    }.get(exchange)


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from an env file. Missing/unreadable file -> {}."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_kis_credentials() -> dict[str, str] | None:
    """Load KIS credentials; direct env wins, then KIS_ENV_FILE. None if unconfigured.

    Direct env: KIS_APP_KEY/KIS_APP_SECRET/KIS_CANO/KIS_ACNT_PRDT_CD (+ KIS_URL_BASE).
    KIS_ENV_FILE: env file where prefixed or unprefixed keys are allowed
    (APP_KEY/APP_SECRET/CANO/ACNT_PRDT_CD or their KIS_* variants).
    Direct env vars win; an env file (KIS_ENV_FILE) is the fallback.
    """
    file_env: dict[str, str] = {}
    env_file = os.environ.get("KIS_ENV_FILE", "").strip()
    if env_file:
        file_env = _read_env_file(Path(env_file).expanduser())

    def _cred(env_name: str, *file_names: str) -> str:
        # Direct process env: prefixed name only.
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
        # KIS_ENV_FILE tier: prefixed or unprefixed keys allowed.
        for name in (env_name, *file_names):
            value = (file_env.get(name) or "").strip()
            if value:
                return value
        return ""

    app_key = _cred("KIS_APP_KEY", "APP_KEY")
    app_secret = _cred("KIS_APP_SECRET", "APP_SECRET")
    if not app_key or not app_secret:
        return None
    url_base = _cred("KIS_URL_BASE", "URL_BASE") or KIS_DEFAULT_URL_BASE
    return {
        "app_key": app_key,
        "app_secret": app_secret,
        "cano": _cred("KIS_CANO", "CANO"),
        "acnt_prdt_cd": _cred("KIS_ACNT_PRDT_CD", "ACNT_PRDT_CD"),
        "url_base": url_base.rstrip("/"),
    }


def _kis_token_cache_path() -> Path:
    """KIS access-token cache path: env MARGIN_TA_KIS_TOKEN_CACHE or generic default."""
    env = os.environ.get("MARGIN_TA_KIS_TOKEN_CACHE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "margin-ta" / "kis_token.json"


def _token_scope_key(app_key: str, app_secret: str) -> str:
    raw = f"{str(app_key or '').strip()}::{str(app_secret or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cached_token(scope_key: str) -> str | None:
    cache_path = _kis_token_cache_path()
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        entry = (payload.get("entries") or {}).get(scope_key) or {}
        token = str(entry.get("access_token") or "").strip()
        issued_at = float(entry.get("issued_at") or 0)
        expires_at = float(entry.get("expires_at") or 0)
        now = time.time()
        if token and issued_at > 0 and expires_at > 0 and now < max(expires_at - 60, issued_at):
            return token
    except Exception:
        return None
    return None


def _save_cached_token(scope_key: str, token: str, issued_at: float, expires_at: float) -> None:
    cache_path = _kis_token_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": {}}
    if cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {"entries": {}}
    entries = payload.setdefault("entries", {})
    entries[scope_key] = {
        "access_token": token,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    fd, tmp_path = tempfile.mkstemp(prefix="token_cache_", suffix=".tmp", dir=str(cache_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, cache_path)
        try:
            os.chmod(cache_path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _get_kis_access_token(url_base: str, app_key: str, app_secret: str) -> str | None:
    scope_key = _token_scope_key(app_key, app_secret)
    cached = _load_cached_token(scope_key)
    if cached:
        return cached

    issued_at = time.time()
    response = requests.post(
        f"{url_base}/oauth2/tokenP",
        headers={"content-type": "application/json"},
        data=json.dumps({
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        }),
        timeout=10,
    )
    if response.status_code != 200:
        raise RuntimeError(f"KIS token request failed: {response.status_code} {response.text[:120]}")
    body = response.json()
    token = str(body.get("access_token") or "").strip()
    expires_in = _safe_int(body.get("expires_in"), 43200) or 43200
    if not token:
        raise RuntimeError("KIS token response did not include access_token")
    _save_cached_token(scope_key, token, issued_at, issued_at + expires_in)
    return token


def _load_kis_config():
    """Resolve KIS (url_base, app_key, app_secret, token). None when unconfigured."""
    creds = load_kis_credentials()
    if creds is None:
        return None
    token = _get_kis_access_token(creds["url_base"], creds["app_key"], creds["app_secret"])
    if not token:
        raise RuntimeError("KIS access token acquisition failed")
    return creds["url_base"], creds["app_key"], creds["app_secret"], token


def fetch_kis_overseas_price(symbol: str, exchange: str, day_market: bool = False) -> dict[str, Any]:
    """Fetch KIS overseas current price. Day-market uses BAQ/BAY/BAA."""
    symbol = symbol.upper().strip()
    excd = _kis_excd(exchange, day_market=day_market)
    if not excd:
        return {
            "source": "kis",
            "ok": False,
            "symbol": symbol,
            "exchange": exchange,
            "warnings": [f"Unsupported exchange for KIS quote: {exchange}"],
        }

    try:
        cfg = _load_kis_config()
        if cfg is None:
            return {
                "source": "kis",
                "ok": False,
                "symbol": symbol,
                "exchange": exchange,
                "excd": excd,
                "warnings": [
                    "KIS not configured — set KIS_APP_KEY/KIS_APP_SECRET or KIS_ENV_FILE "
                    "(session quote disabled; daily close remains the source)"
                ],
            }
        url_base, app_key, app_secret, token = cfg
        url = f"{url_base}/uapi/overseas-price/v1/quotations/price"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "HHDFS00000300",
        }
        params = {"AUTH": "", "EXCD": excd, "SYMB": symbol}
        response = requests.get(url, headers=headers, params=params, timeout=8)
        data = response.json()
        if response.status_code != 200 or str(data.get("rt_cd")) != "0":
            return {
                "source": "kis",
                "ok": False,
                "symbol": symbol,
                "exchange": exchange,
                "excd": excd,
                "status_code": response.status_code,
                "warnings": [f"KIS quote failed: {data.get('msg1') or response.text[:120]}"],
            }

        output = data.get("output") or {}
        last = _safe_float(output.get("last"))
        return {
            "source": "kis_day_market" if day_market else "kis_overseas_price",
            "ok": bool(last and last > 0),
            "symbol": symbol,
            "exchange": exchange,
            "excd": excd,
            "price": last,
            "previous_close": _safe_float(output.get("base")),
            "change": _safe_float(output.get("diff")),
            "change_pct": _safe_float(output.get("rate")),
            "volume": _safe_int(output.get("tvol")),
            "amount": _safe_float(output.get("tamt")),
            "orderable": output.get("ordy"),
            "raw": output,
            "warnings": [] if last else ["KIS quote returned no usable last price"],
        }
    except Exception as e:
        return {
            "source": "kis",
            "ok": False,
            "symbol": symbol,
            "exchange": exchange,
            "excd": excd,
            "warnings": [f"KIS quote exception: {e}"],
        }


def _normalize_domestic_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    base = raw.split(".", 1)[0]
    return base.zfill(6) if base.isdigit() else base


def _extract_domestic_quote_prices(output: dict) -> tuple[float | None, float | None]:
    regular_price = None
    after_price = None
    for key in ["stck_prpr", "prpr"]:
        value = _safe_float(output.get(key))
        if value and value > 0:
            regular_price = value
            break
    for key in ["ovtm_untp_prpr", "ovtm_vi_cls_prc", "ovtm_prpr"]:
        value = _safe_float(output.get(key))
        if value and value > 0:
            after_price = value
            break
    return regular_price, after_price


def fetch_kis_domestic_price(symbol: str, regular_session: bool | None = None) -> dict[str, Any]:
    """Fetch KIS domestic current price for Korean listed stocks."""
    normalized = _normalize_domestic_symbol(symbol)
    if regular_session is None:
        regular_session = _is_kor_regular_session()
    market_order = ["J", "UN", "NX"] if regular_session else ["NX", "UN", "J"]
    best_regular_quote = None

    try:
        cfg = _load_kis_config()
        if cfg is None:
            return {
                "source": "kis_domestic_price",
                "ok": False,
                "symbol": normalized,
                "exchange": "KRX",
                "warnings": [
                    "KIS not configured — set KIS_APP_KEY/KIS_APP_SECRET or KIS_ENV_FILE "
                    "(session quote disabled; daily close remains the source)"
                ],
            }
        url_base, app_key, app_secret, token = cfg
        url = f"{url_base}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "FHKST01010100",
        }

        for market_div_code in market_order:
            params = {
                "FID_COND_MRKT_DIV_CODE": market_div_code,
                "FID_INPUT_ISCD": normalized,
            }
            response = requests.get(url, headers=headers, params=params, timeout=8)
            data = response.json()
            if response.status_code != 200 or str(data.get("rt_cd")) != "0":
                continue
            output = data.get("output") or {}
            if not isinstance(output, dict) or not output:
                continue

            regular_price, after_price = _extract_domestic_quote_prices(output)
            if regular_session:
                price = regular_price or after_price
                if price and price > 0:
                    return {
                        "source": "kis_domestic_price",
                        "ok": True,
                        "symbol": normalized,
                        "exchange": "KRX",
                        "market_div_code": market_div_code,
                        "price": price,
                        "regular_price": regular_price,
                        "after_price": after_price,
                        "previous_close": _safe_float(output.get("stck_sdpr") or output.get("prdy_clpr")),
                        "change": _safe_float(output.get("prdy_vrss")),
                        "change_pct": _safe_float(output.get("prdy_ctrt")),
                        "volume": _safe_int(output.get("acml_vol")),
                        "raw": output,
                        "warnings": [],
                    }
                continue

            if after_price and after_price > 0:
                return {
                    "source": "kis_domestic_price",
                    "ok": True,
                    "symbol": normalized,
                    "exchange": "KRX",
                    "market_div_code": market_div_code,
                    "price": after_price,
                    "regular_price": regular_price,
                    "after_price": after_price,
                    "previous_close": _safe_float(output.get("stck_sdpr") or output.get("prdy_clpr")),
                    "change": _safe_float(output.get("prdy_vrss")),
                    "change_pct": _safe_float(output.get("prdy_ctrt")),
                    "volume": _safe_int(output.get("acml_vol")),
                    "raw": output,
                    "warnings": [],
                }
            if best_regular_quote is None and regular_price and regular_price > 0:
                best_regular_quote = (market_div_code, regular_price, after_price, output)

        if best_regular_quote:
            market_div_code, regular_price, after_price, output = best_regular_quote
            return {
                "source": "kis_domestic_price",
                "ok": True,
                "symbol": normalized,
                "exchange": "KRX",
                "market_div_code": market_div_code,
                "price": regular_price,
                "regular_price": regular_price,
                "after_price": after_price,
                "previous_close": _safe_float(output.get("stck_sdpr") or output.get("prdy_clpr")),
                "change": _safe_float(output.get("prdy_vrss")),
                "change_pct": _safe_float(output.get("prdy_ctrt")),
                "volume": _safe_int(output.get("acml_vol")),
                "raw": output,
                "warnings": ["KIS domestic quote used regular price fallback outside regular session"],
            }

        return {
            "source": "kis_domestic_price",
            "ok": False,
            "symbol": normalized,
            "exchange": "KRX",
            "warnings": ["KIS domestic quote returned no usable price"],
        }
    except Exception as e:
        return {
            "source": "kis_domestic_price",
            "ok": False,
            "symbol": normalized,
            "exchange": "KRX",
            "warnings": [f"KIS domestic quote exception: {e}"],
        }


def _fast_info_attr(ticker, attr: str) -> float | None:
    """Safely read a fast_info attribute that may not exist or be None."""
    try:
        val = getattr(ticker.fast_info, attr, None)
        return _safe_float(val) if val is not None else None
    except Exception:
        return None


def fetch_toss_prices(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """토스증권 현재가 조회. {symbol: {price, source, ok, warnings}} 형태 반환.

    토스 클라이언트는 옵셔널(toss_loader, env MARGIN_TA_TOSS_IMPORT).
    미설정/로드 실패 시 ok=False + 경고로 우아하게 실패한다.
    """
    result = {}
    from toss_loader import _last_error as _toss_last_error
    from toss_loader import get_toss_client
    client = get_toss_client()
    if client is None:
        message = (
            "토스 클라이언트 미설정/로드 실패(MARGIN_TA_TOSS_IMPORT): "
            f"{_toss_last_error or 'env 미설정'}"
        )
        for sym in symbols:
            result[sym] = {'price': None, 'source': 'toss', 'ok': False, 'warnings': [message]}
        return result
    try:
        prices = client.get_prices(symbols)
        for p in prices:
            sym = p.get('symbol', '')
            price = _safe_float(p.get('lastPrice'))
            result[sym] = {
                'price': price,
                'source': 'toss',
                'ok': price is not None and price > 0,
                'warnings': [],
                'currency': p.get('currency', 'USD'),
                'timestamp': p.get('timestamp'),
            }
    except Exception as e:
        for sym in symbols:
            result[sym] = {'price': None, 'source': 'toss', 'ok': False, 'warnings': [str(e)]}
    return result


def get_us_session_from_toss(now_kst: datetime | None = None) -> dict[str, Any] | None:
    """토스 market-calendar API로 US 세션 정보 가져오기. 미설정/실패 시 None."""
    try:
        from toss_loader import get_toss_client
        client = get_toss_client()
        if client is None:
            return None
        cal = client.get_market_calendar('US')
        today = cal.get('today', {})
        if not today:
            return None

        if now_kst is None:
            now_kst = datetime.now(KST)
        elif now_kst.tzinfo is None:
            now_kst = now_kst.replace(tzinfo=KST)
        else:
            now_kst = now_kst.astimezone(KST)

        # Determine current session from calendar times
        session = 'closed'
        is_open = False
        for session_name, key in [('day_market', 'dayMarket'), ('premarket', 'preMarket'),
                                    ('regular', 'regularMarket'), ('aftermarket', 'afterMarket')]:
            sess = today.get(key)
            if not sess:
                continue
            start = sess.get('startTime')
            end = sess.get('endTime')
            if not start or not end:
                continue
            try:
                t_start = datetime.fromisoformat(start)
                t_end = datetime.fromisoformat(end)
                if t_start <= now_kst <= t_end:
                    session = session_name
                    is_open = True
                    break
            except Exception:
                continue

        return {
            'session': session,
            'is_open': is_open,
            'is_dst': bool(now_kst.astimezone(NEW_YORK).dst()),
            'now_kst': now_kst.isoformat(),
            'source': 'toss_calendar',
            'calendar': today,
        }
    except Exception:
        return None


def fetch_yfinance_session_quote(symbol: str, session: str) -> dict[str, Any]:
    """Best-effort Yahoo session quote for pre/after-market fallback.

    Uses fast_info first (more responsive for session prices) with info dict
    as fallback.  Known yfinance issue: info['preMarketPrice'] / fast pre_market_price
    can return stale data hours after the session closes.  We add a staleness
    check to flag suspicious values.
    """
    symbol = symbol.upper().strip()
    result = {
        "source": "yfinance_session",
        "ok": False,
        "symbol": symbol,
        "session": session,
        "price": None,
        "warnings": [],
    }
    price = None
    regular_market_price = None
    pre_market_price = None
    post_market_price = None
    previous_close = None
    market_state = None
    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            ticker = yf.Ticker(symbol)
            # 1) Try fast_info first — it updates more frequently for session prices
            attr_by_session = {
                "premarket": "pre_market_price",
                "aftermarket": "post_market_price",
                "regular": "regular_market_price",
            }
            preferred_attr = attr_by_session.get(session)
            if preferred_attr:
                price = _fast_info_attr(ticker, preferred_attr)
                if price and price > 0:
                    break  # got a valid price from fast_info
            # 2) If fast_info didn't give us a price (or session not in map),
            #    try the full info dict as fallback
            info = ticker.info
            field_by_session = {
                "premarket": "preMarketPrice",
                "aftermarket": "postMarketPrice",
                "regular": "regularMarketPrice",
            }
            preferred_field = field_by_session.get(session)
            info_price = _safe_float(info.get(preferred_field)) if preferred_field else None
            if info_price and info_price > 0:
                price = info_price
            if price is None:
                price = _safe_float(
                    info.get("currentPrice") or info.get("regularMarketPrice")
                )
            market_state = info.get("marketState")
            regular_market_price = _safe_float(
                info.get("regularMarketPrice") or info.get("currentPrice")
            )
            pre_market_price = _safe_float(info.get("preMarketPrice"))
            post_market_price = _safe_float(info.get("postMarketPrice"))
            previous_close = _safe_float(info.get("previousClose"))
            if price and price > 0:
                break
            # Brief backoff before retry
            if attempt < max_attempts - 1:
                time.sleep(1)
        except Exception as e:
            if attempt == max_attempts - 1:
                result["warnings"].append(f"yfinance session quote failed: {e}")
            else:
                time.sleep(1)

    # Staleness check: premarket price should be different from previous close
    if session == "premarket" and price and previous_close and abs(price - previous_close) < 0.01:
        result["warnings"].append(
            "yfinance premarket price equals previous close — possibly stale; confirm with user"
        )

    result.update({
        "ok": bool(price and price > 0),
        "price": price,
        "market_state": market_state,
        "regular_market_price": regular_market_price,
        "pre_market_price": pre_market_price,
        "post_market_price": post_market_price,
        "previous_close": previous_close,
    })
    if not result["ok"]:
        result["warnings"].append("yfinance session quote returned no usable price")
    return result


def fetch_session_quote(
    symbol: str,
    exchange: str,
    regular_close: float | None = None,
    provider: str = "auto",
    market: str = "US",
) -> dict[str, Any]:
    """
    Decide whether a live/session quote should override the yfinance daily close.
    Provider choices: "auto" | "toss" | "kis" | "yfinance" | "off"
    Auto mode:
      - day_market: KIS BAQ/BAY/BAA
      - pre/after-market: yfinance session quote
      - regular/closed: no override; daily yfinance remains the source
    Toss mode: uses the optional Toss client (MARGIN_TA_TOSS_IMPORT) for all markets (Korean & US).
    """
    market = str(market or "US").upper()
    is_korean = market in {"KR", "KOR", "KOREA"} or str(exchange or "").upper() == "KRX"
    session_info = get_korea_session() if is_korean else get_us_session()
    provider = str(provider or "auto").lower()
    result: dict[str, Any] = {
        "enabled": provider != "off",
        "provider": provider,
        "market": "KR" if is_korean else "US",
        "session": session_info,
        "quote": None,
        "active_price": None,
        "active_source": "yfinance_daily",
        "regular_close": regular_close,
        "warnings": [],
        "provider_notes": {
            "day_market": "KIS is required because yfinance does not expose the Korean US day-market/Blue Ocean session.",
            "premarket": "KIS is better for broker-actionable quotes when configured; yfinance is a zero-setup fallback and remains less explicit about venue/timeliness.",
            "regular": "Daily OHLCV remains yfinance by design; KIS can be enabled later for intraday execution quotes.",
        },
    }
    if provider == "off":
        return result

    session = session_info.get("session")
    quote = None
    if provider == "toss":
        # 토스 단독 모드
        toss_prices = fetch_toss_prices([symbol])
        tp = toss_prices.get(symbol, {})
        if tp.get('ok'):
            quote = {"ok": True, "price": tp["price"], "source": "toss", "warnings": tp.get("warnings", [])}
        else:
            quote = {"ok": False, "price": None, "source": "toss", "warnings": tp.get("warnings", ["토스 시세 없음"])}
    elif is_korean:
        if provider in {"auto", "kis"}:
            quote = fetch_kis_domestic_price(symbol, regular_session=(session == "regular"))
        elif provider == "yfinance":
            result["warnings"].append("Korean yfinance session quote is not implemented; using daily close")
    elif provider == "kis" or (provider == "auto" and session == "day_market"):
        quote = fetch_kis_overseas_price(symbol, exchange, day_market=(session == "day_market"))
    elif provider == "yfinance" or (provider == "auto" and session in {"premarket", "aftermarket"}):
        quote = fetch_yfinance_session_quote(symbol, str(session))

    if quote:
        result["quote"] = quote
        result["warnings"].extend(quote.get("warnings") or [])
        price = quote.get("price")
        if quote.get("ok") and price and float(price) > 0:
            result["active_price"] = float(price)
            result["active_source"] = quote.get("source")
    return result
