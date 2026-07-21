"""
Layer 1 — Data: OHLCV + TradingView 교차검증
yfinance로 2년 daily 데이터 수집, TradingView TA로 검증.
"""
import re
import os
import io
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
import yfinance as yf
import pandas as pd
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")


KR_SUFFIXES = (".KS", ".KQ")
IGNORED_PYKRX_MESSAGES = (
    "KRX 로그인 실패: KRX_ID 또는 KRX_PW",
)


def _normalize_market(market: str | None = "auto") -> str:
    value = str(market or "auto").strip().upper()
    if value in {"KR", "KOR", "KOREA", "KOREAN", "KRX", "KOSPI", "KOSDAQ"}:
        return "KR"
    if value in {"US", "USA", "AMERICA", "NYSE", "NASDAQ", "AMEX"}:
        return "US"
    return "AUTO"


def _is_kr_symbol(symbol: str) -> bool:
    raw = str(symbol or "").strip().upper()
    base = raw.split(".", 1)[0]
    return raw.endswith(KR_SUFFIXES) or bool(re.fullmatch(r"\d{6}", base))


def _kr_base_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    base = raw.split(".", 1)[0]
    return base.zfill(6) if base.isdigit() else base


def resolve_yfinance_candidates(symbol: str, market: str | None = "auto") -> dict:
    """Return provider symbol candidates and market metadata for yfinance."""
    requested_market = _normalize_market(market)
    raw = str(symbol or "").strip().upper()
    is_kr = requested_market == "KR" or (requested_market == "AUTO" and _is_kr_symbol(raw))

    if is_kr:
        base = _kr_base_symbol(raw)
        if raw.endswith(KR_SUFFIXES):
            suffix = "." + raw.rsplit(".", 1)[1]
            suffixes = [suffix] + [s for s in KR_SUFFIXES if s != suffix]
        else:
            suffixes = list(KR_SUFFIXES)
        return {
            "market": "KR",
            "symbol": base,
            "candidates": [f"{base}{suffix}" for suffix in suffixes],
            "exchange": "KRX",
            "currency": "KRW",
            "tv_symbol": base,
            "tv_screener": "korea",
        }

    return {
        "market": "US",
        "symbol": raw,
        "candidates": [raw],
        "exchange": None,
        "currency": "USD",
        "tv_symbol": raw,
        "tv_screener": "america",
    }


def _base_market_metadata(symbol: str, market: str | None = "auto") -> dict:
    """Provider-independent market metadata used by yfinance and pykrx."""
    return resolve_yfinance_candidates(symbol, market=market)


def _get_exchange(info: dict, provider_symbol: str = "", market: str | None = "US") -> str:
    """Ticker info에서 NASDAQ vs NYSE 추론

    yfinance returns Yahoo-internal exchange codes (NYQ=NYSE, NMS=NASDAQ, etc.)
    that do not contain the exchange name verbatim.  We map known codes and
    fall back to substring matching for unlisted exchanges.
    """
    if _normalize_market(market) == "KR" or str(provider_symbol or "").upper().endswith(KR_SUFFIXES):
        return "KRX"

    exchange = info.get("exchange", "").upper()
    quote_type = info.get("quoteType", "").upper()

    # Known Yahoo exchange codes → canonical exchange
    YAHOO_EXCHANGE_MAP: dict[str, str] = {
        "NYQ": "NYSE",   # NYSE
        "NYS": "NYSE",   # NYSE (legacy/alternate)
        "NCM": "NYSE",   # NYSE Arca
        "PCX": "NYSE",   # NYSE Arca
        "ASE": "AMEX",   # NYSE American (AMEX)
        "NGM": "NASDAQ",  # Nasdaq Global Market
        "NMS": "NASDAQ",  # Nasdaq Global Select Market
        "NIM": "NASDAQ",  # Nasdaq (generic)
    }
    if exchange in YAHOO_EXCHANGE_MAP:
        return YAHOO_EXCHANGE_MAP[exchange]

    # Substring fallback for exchanges that embed their name
    if "NASDAQ" in exchange or "NASDAQ" in quote_type:
        return "NASDAQ"
    if "NYSE" in exchange or "NYSE" in quote_type or "ARCA" in exchange:
        return "NYSE"

    market = info.get("market", "").lower()
    if "nasdaq" in market:
        return "NASDAQ"
    if "nyse" in market:
        return "NYSE"

    # fallback: major US exchanges
    return "NASDAQ"


def _normalize_pykrx_ohlcv(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Convert pykrx Korean OHLCV columns to the internal yfinance-like schema."""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    column_map = {
        "시가": "Open",
        "고가": "High",
        "저가": "Low",
        "종가": "Close",
        "거래량": "Volume",
        "거래대금": "Amount",
        "등락률": "ChangePct",
    }
    df = raw_df.rename(columns=column_map).copy()
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"pykrx OHLCV missing columns: {', '.join(missing)}")

    df = df[required + [col for col in ["Amount", "ChangePct"] if col in df.columns]]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Volume"].fillna(0) > 0].copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is None:
        df.index = df.index.tz_localize("Asia/Seoul")
    return df.sort_index()


def _append_pykrx_messages(result: dict, stream: io.StringIO) -> None:
    """Collect noisy pykrx stdout/stderr lines as structured warnings."""
    for line in stream.getvalue().splitlines():
        message = line.strip()
        if not message:
            continue
        if any(ignored in message for ignored in IGNORED_PYKRX_MESSAGES):
            continue
        warning = f"pykrx 메시지: {message}"
        if warning not in result["warnings"]:
            result["warnings"].append(warning)
    stream.seek(0)
    stream.truncate(0)


def _call_pykrx(result: dict, fn, *args, report_messages: bool = True, **kwargs):
    stream = io.StringIO()
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            return fn(*args, **kwargs)
    finally:
        if report_messages:
            _append_pykrx_messages(result, stream)


def fetch_toss_data(symbol: str, market: str | None = "auto") -> dict:
    """토스증권 API로 OHLCV + 기본 정보 수집.
    Returns:
        dict with 'df', 'info_summary', 'warnings'
    """
    result = {"df": None, "info_summary": {}, "warnings": []}
    resolved = _base_market_metadata(symbol, market=market)

    from toss_loader import get_toss_client, _last_error as _toss_last_error
    client = get_toss_client()
    if client is None:
        result["warnings"].append(
            f"토스 클라이언트 미설정/로드 실패(MARGIN_TA_TOSS_IMPORT): "
            f"{_toss_last_error or 'env 미설정'}")
        return result

    toss_symbol = resolved["symbol"]  # KR: 005930, US: AAPL
    is_kr = resolved["market"] == "KR"

    # OHLCV — 200봉 단위로 pagination (2년 ≈ 500봉)
    all_candles = []
    before = None
    for _ in range(5):  # 최대 5회 = 1000봉
        try:
            resp = client.get_candles(toss_symbol, interval='1d', count=200, before=before)
        except Exception as e:
            result["warnings"].append(f"토스 candles 실패 ({toss_symbol}): {e}")
            break
        candles = resp.get('result', {}).get('candles', [])
        if not candles:
            break
        all_candles.extend(candles)
        next_before = resp.get('result', {}).get('nextBefore')
        if not next_before:
            break
        before = next_before

    if not all_candles:
        result["warnings"].append(f"토스 candles 데이터 없음 ({toss_symbol})")
        return result

    # DataFrame으로 변환 (yfinance 포맷과 호환)
    rows = []
    for c in all_candles:
        rows.append({
            'Open': float(c['openPrice']),
            'High': float(c['highPrice']),
            'Low': float(c['lowPrice']),
            'Close': float(c['closePrice']),
            'Volume': int(c['volume']),
        })
    df = pd.DataFrame(rows)
    # timestamp를 index로
    timestamps = [c['timestamp'] for c in all_candles]
    df.index = pd.to_datetime(timestamps)
    if getattr(df.index, 'tz', None) is None:
        df.index = df.index.tz_localize('Asia/Seoul' if is_kr else 'America/New_York')
    df = df.sort_index()
    df = df[df['Volume'] > 0].copy()

    if len(df) < 20:
        result["warnings"].append("거래일 20일 미만 — 신규상장 가능성")
    result["df"] = df

    # 종목 정보
    try:
        infos = client.get_stock_info(toss_symbol)
        info = infos[0] if infos else {}
    except Exception:
        info = {}

    result["info_summary"] = {
        "symbol": resolved["symbol"],
        "provider_symbol": toss_symbol,
        "toss_symbol": toss_symbol,
        "market": resolved["market"],
        "name": info.get("name", resolved["symbol"]),
        "sector": "N/A",
        "industry": "N/A",
        "market_cap": None,
        "beta": None,
        "short_float_pct": None,
        "avg_volume_10d": None,
        "avg_volume_50d": None,
        "exchange": info.get("market", resolved.get("exchange", "KRX" if is_kr else "NASDAQ")),
        "currency": info.get("currency", resolved["currency"]),
        "tv_symbol": resolved["tv_symbol"],
        "tv_screener": resolved["tv_screener"],
        "leverage_factor": info.get("leverageFactor"),
        "security_type": info.get("securityType", "STOCK"),
        "data_source": "toss",
    }
    result["warnings"].append(f"토스 데이터 소스 사용 ({len(df)}봉)")
    return result


def fetch_pykrx_data(symbol: str, market: str | None = "auto") -> dict:
    """
    pykrx로 한국주식 OHLCV + 기본 정보 수집.
    Returns:
        dict with 'df', 'info_summary', 'warnings'
    """
    result = {"df": None, "info_summary": {}, "warnings": []}
    resolved = _base_market_metadata(symbol, market=market)
    if resolved["market"] != "KR":
        result["warnings"].append("pykrx는 한국 종목에만 사용 가능")
        return result

    import_stream = io.StringIO()
    try:
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-margin-ta")
        os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)
        with redirect_stdout(import_stream), redirect_stderr(import_stream):
            from pykrx import stock
    except Exception as e:
        _append_pykrx_messages(result, import_stream)
        result["warnings"].append(f"pykrx import 실패: {e}")
        return result
    _append_pykrx_messages(result, import_stream)

    base_symbol = resolved["symbol"]
    end = datetime.now().date()
    start = end - timedelta(days=3650)
    fromdate = start.strftime("%Y%m%d")
    todate = end.strftime("%Y%m%d")

    try:
        raw_df = _call_pykrx(result, stock.get_market_ohlcv, fromdate, todate, base_symbol, adjusted=True)
        df = _normalize_pykrx_ohlcv(raw_df)
    except TypeError:
        raw_df = _call_pykrx(result, stock.get_market_ohlcv, fromdate, todate, base_symbol)
        df = _normalize_pykrx_ohlcv(raw_df)
    except Exception as e:
        result["warnings"].append(f"pykrx OHLCV 조회 실패 ({base_symbol}): {e}")
        return result

    if df.empty:
        result["warnings"].append(f"pykrx 데이터 없음 — 티커 확인 필요 ({base_symbol})")
        return result
    if len(df) < 20:
        result["warnings"].append("거래일 20일 미만 — 신규상장 가능성")
    result["df"] = df

    try:
        name = _call_pykrx(result, stock.get_market_ticker_name, base_symbol) or base_symbol
    except Exception as e:
        name = base_symbol
        result["warnings"].append(f"pykrx 종목명 조회 실패 ({base_symbol}): {e}")

    market_cap = None
    shares = None
    try:
        cap_df = _call_pykrx(
            result,
            stock.get_market_cap_by_date,
            fromdate,
            todate,
            base_symbol,
            report_messages=False,
        )
        if cap_df is not None and not cap_df.empty:
            cap_df = cap_df.sort_index()
            last_cap = cap_df.iloc[-1]
            market_cap = last_cap.get("시가총액")
            shares = last_cap.get("상장주식수")
            market_cap = int(market_cap) if pd.notna(market_cap) else None
            shares = int(shares) if pd.notna(shares) else None
    except Exception as e:
        result["warnings"].append(f"pykrx 시가총액 조회 실패 ({base_symbol}): {e}")

    result["info_summary"] = {
        "symbol": base_symbol,
        "provider_symbol": base_symbol,
        "yf_symbol": f"{base_symbol}.KS",
        "market": "KR",
        "data_source": "pykrx",
        "name": name,
        "sector": "N/A",
        "industry": "N/A",
        "market_cap": market_cap,
        "shares_outstanding": shares,
        "beta": None,
        "short_float_pct": None,
        "avg_volume_10d": int(df["Volume"].tail(10).mean()) if len(df) >= 1 else None,
        "avg_volume_50d": int(df["Volume"].tail(50).mean()) if len(df) >= 1 else None,
        "exchange": "KRX",
        "currency": "KRW",
        "tv_symbol": base_symbol,
        "tv_screener": "korea",
        "previous_close": float(df["Close"].iloc[-2]) if len(df) >= 2 else None,
        "fifty_two_week_high": float(df["High"].tail(252).max()),
        "fifty_two_week_low": float(df["Low"].tail(252).min()),
    }
    return result


def fetch_ohlcv_data(symbol: str, market: str | None = "auto", provider: str = "auto") -> dict:
    """
    Market-aware OHLCV fetcher.
    - Korean stocks (auto mode): pykrx first (10y history), toss fallback (1000d cap), yfinance last resort.
    - US stocks (auto mode): toss first, yfinance fallback.
    """
    provider = str(provider or "auto").strip().lower()
    resolved = _base_market_metadata(symbol, market=market)
    if provider not in {"auto", "toss", "pykrx", "yfinance"}:
        return {"df": None, "info_summary": {}, "warnings": [f"Unsupported OHLCV provider: {provider}"]}

    if provider == "toss":
        return fetch_toss_data(symbol, market=market)
    if provider == "pykrx":
        return fetch_pykrx_data(symbol, market=market)
    if provider == "yfinance":
        return fetch_yfinance_data(symbol, market=market)

    # auto mode — KR: pykrx(10y) → toss(1000d) → yfinance / US: toss → yfinance
    if resolved["market"] == "KR":
        pykrx_result = fetch_pykrx_data(symbol, market=market)
        if pykrx_result.get("df") is not None and not pykrx_result["df"].empty:
            return pykrx_result
        toss_result = fetch_toss_data(symbol, market=market)
        if toss_result.get("df") is not None and not toss_result["df"].empty:
            toss_result["warnings"] = pykrx_result.get("warnings", []) + toss_result.get("warnings", [])
            return toss_result
        prior_warnings = pykrx_result.get("warnings", []) + toss_result.get("warnings", [])
    else:
        toss_result = fetch_toss_data(symbol, market=market)
        if toss_result.get("df") is not None and not toss_result["df"].empty:
            return toss_result
        prior_warnings = toss_result.get("warnings", [])

    yfinance_result = fetch_yfinance_data(symbol, market=market)
    yfinance_result["warnings"] = (
        prior_warnings
        + ["상위 provider 실패로 yfinance fallback 사용"]
        + yfinance_result.get("warnings", [])
    )
    return yfinance_result


def fetch_yfinance_data(symbol: str, market: str | None = "auto") -> dict:
    """
    yfinance로 OHLCV + 기본 정보 수집.
    Returns:
        dict with 'df', 'info_summary', 'warnings'
    """
    result = {"df": None, "info_summary": {}, "warnings": []}
    resolved = resolve_yfinance_candidates(symbol, market=market)
    ticker = None
    provider_symbol = None
    df = None

    # OHLCV
    last_error = None
    for candidate in resolved["candidates"]:
        ticker = yf.Ticker(candidate)
        try:
            candidate_df = ticker.history(period="10y", interval="1d")
        except Exception as e:
            last_error = e
            result["warnings"].append(f"yfinance history() 실패 ({candidate}): {e}")
            continue
        if not candidate_df.empty:
            df = candidate_df
            provider_symbol = candidate
            break

    if df is None:
        if last_error and not result["warnings"]:
            result["warnings"].append(f"yfinance history() 실패: {last_error}")
        result["warnings"].append("데이터 없음 — 티커 확인 필요")
        return result

    if df.empty:
        result["warnings"].append("데이터 없음 — 티커 확인 필요")
        return result

    # 거래량 0 행 제거
    df = df[df.Volume > 0].copy()
    if len(df) < 20:
        result["warnings"].append("거래일 20일 미만 — 신규상장 가능성")
    result["df"] = df

    # 기본 정보
    try:
        info = ticker.info
    except Exception as e:
        info = {}
        result["warnings"].append(f"yfinance info 조회 실패 ({provider_symbol}): {e}")
    exchange = resolved["exchange"] or _get_exchange(info, provider_symbol=provider_symbol, market=resolved["market"])
    display_symbol = resolved["symbol"]
    currency = info.get("currency") or resolved["currency"]
    result["info_summary"] = {
        "symbol": display_symbol,
        "provider_symbol": provider_symbol,
        "yf_symbol": provider_symbol,
        "market": resolved["market"],
        "name": info.get("shortName") or info.get("longName", display_symbol),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "market_cap": info.get("marketCap"),
        "beta": info.get("beta"),
        "short_float_pct": info.get("shortPercentOfFloat"),
        "avg_volume_10d": info.get("averageVolume"),
        "avg_volume_50d": info.get("averageVolume50days"),
        "exchange": exchange,
        "currency": currency,
        "tv_symbol": resolved["tv_symbol"],
        "tv_screener": resolved["tv_screener"],
        "previous_close": info.get("previousClose"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
    }

    # 어닝 임박 체크
    earnings = info.get("earningsDate")
    if earnings:
        if isinstance(earnings, list):
            next_earnings = min(earnings)
        else:
            next_earnings = earnings
        days_to_earnings = (next_earnings - datetime.now().date()).days
        if 0 <= days_to_earnings <= 7:
            result["warnings"].append(
                f"⚠️ 실적발표 {days_to_earnings}일 전 — 이벤트 리스크"
            )

    return result


def fetch_tradingview_data(symbol: str, exchange: str, screener: str | None = None) -> dict:
    """
    TradingView TA 교차검증.
    Returns:
        dict with 'summary', 'indicators', 'error'
    """
    try:
        from tradingview_ta import TA_Handler, Interval
        tv_symbol = _kr_base_symbol(symbol) if _is_kr_symbol(symbol) or str(exchange).upper() in {"KRX", "KOSPI", "KOSDAQ"} else str(symbol).upper()
        tv_exchange = "KRX" if str(exchange).upper() in {"KRX", "KOSPI", "KOSDAQ"} else exchange
        tv_screener = screener or ("korea" if tv_exchange == "KRX" else "america")

        handler = TA_Handler(
            symbol=tv_symbol,
            exchange=tv_exchange,
            screener=tv_screener,
            interval=Interval.INTERVAL_1_DAY,
        )
        analysis = handler.get_analysis()

        return {
            "error": None,
            "summary": analysis.summary,  # {"BUY": N, "SELL": N, "NEUTRAL": N}
            "indicators": {
                "rsi": analysis.indicators.get("RSI"),
                "macd_macd": analysis.indicators.get("MACD.macd"),
                "macd_signal": analysis.indicators.get("MACD.signal"),
                "bb_upper": analysis.indicators.get("BB.upper"),
                "bb_lower": analysis.indicators.get("BB.lower"),
                "sma_50": analysis.indicators.get("SMA50"),
                "sma_200": analysis.indicators.get("SMA200"),
            },
        }
    except Exception as e:
        return {"error": str(e), "summary": None, "indicators": {}}
