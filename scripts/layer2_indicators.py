"""
Layer 2 — Technical Indicators: ta (bukosabino) + manual implementations + TA-Lib
pandas-ta는 Python 3.12+ 요구하므로, ta + 수동 계산으로 대체.
TA-Lib로 13종 캔들스틱 패턴 검출.
"""
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")


def _ema(series: pd.Series, span: int) -> pd.Series:
    """Manual EMA using pandas ewm."""
    return series.ewm(span=span, adjust=False).mean()


def _sma(series: pd.Series, window: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=window, min_periods=window).mean()


def _compute_macd(df: pd.DataFrame, fast=12, slow=26, signal=9):
    """Manual MACD calculation."""
    ema_fast = _ema(df.Close, fast)
    ema_slow = _ema(df.Close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    df["MACD_12_26_9"] = macd_line
    df["MACDs_12_26_9"] = signal_line
    df["MACDh_12_26_9"] = histogram
    return df


def _compute_adx(df: pd.DataFrame, length=14):
    """Manual ADX calculation."""
    high, low, close = df.High, df.Low, df.Close

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up = high - high.shift(1)
    down = low.shift(1) - low

    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    atr = _ema(tr, length)
    plus_di = 100 * _ema(pd.Series(plus_dm, index=df.index), length) / atr
    minus_di = 100 * _ema(pd.Series(minus_dm, index=df.index), length) / atr

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 0.0001)
    adx = _ema(dx, length)

    df["ADX_14"] = adx
    return df


def _compute_kc(df: pd.DataFrame, length=20, scalar=2, atr_length=10):
    """Manual Keltner Channel."""
    if "ATRr_14" in df.columns:
        atr = df["ATRr_14"]
    else:
        high, low, close = df.High, df.Low, df.Close
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = _ema(tr, atr_length)

    middle = _ema(df.Close, length)
    df["KCBe_20_2.0"] = middle + scalar * atr
    df["KCBl_20_2.0"] = middle - scalar * atr
    df["KCBm_20_2.0"] = middle
    return df


def _compute_stochrsi(df: pd.DataFrame, length=14):
    """Manual Stochastic RSI."""
    if "RSI_14" in df.columns:
        rsi = df["RSI_14"]
    else:
        from ta.momentum import RSIIndicator

        rsi = RSIIndicator(close=df.Close, window=length).rsi()

    min_rsi = rsi.rolling(window=length).min()
    max_rsi = rsi.rolling(window=length).max()
    stochrsi = (rsi - min_rsi) / (max_rsi - min_rsi + 0.0001)
    df["STOCHRSI_14"] = stochrsi
    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    ta(bukosabino) + manual calculations로 모든 지표 계산.
    TA-Lib로 캔들패턴 검출.
    """
    df = df.copy()

    try:
        from ta.trend import (
            MACD, EMAIndicator, SMAIndicator, ADXIndicator, IchimokuIndicator,
            CCIIndicator, PSARIndicator, AroonIndicator, VortexIndicator, DPOIndicator,
        )
        from ta.momentum import (
            RSIIndicator, StochasticOscillator, WilliamsRIndicator, UltimateOscillator,
            StochRSIIndicator, AwesomeOscillatorIndicator,
        )
        from ta.volatility import (
            BollingerBands, AverageTrueRange, DonchianChannel, KeltnerChannel,
        )
        from ta.volume import (
            OnBalanceVolumeIndicator, MFIIndicator, ChaikinMoneyFlowIndicator,
            VolumePriceTrendIndicator, AccDistIndexIndicator, ForceIndexIndicator,
        )

        # === TREND ===
        df["SMA_20"] = SMAIndicator(close=df.Close, window=20, fillna=False).sma_indicator()
        df["SMA_50"] = SMAIndicator(close=df.Close, window=50, fillna=False).sma_indicator()
        df["SMA_100"] = SMAIndicator(close=df.Close, window=100, fillna=False).sma_indicator()
        df["SMA_200"] = SMAIndicator(close=df.Close, window=200, fillna=False).sma_indicator()
        df["EMA_20"] = EMAIndicator(close=df.Close, window=20, fillna=False).ema_indicator()
        df["EMA_50"] = EMAIndicator(close=df.Close, window=50, fillna=False).ema_indicator()

        # MACD
        macd_obj = MACD(close=df.Close, window_slow=26, window_fast=12, window_sign=9, fillna=False)
        df["MACD_12_26_9"] = macd_obj.macd()
        df["MACDs_12_26_9"] = macd_obj.macd_signal()
        df["MACDh_12_26_9"] = macd_obj.macd_diff()

        # ADX
        try:
            adx_obj = ADXIndicator(high=df.High, low=df.Low, close=df.Close, window=14, fillna=False)
            df["ADX_14"] = adx_obj.adx()
        except Exception:
            _compute_adx(df, 14)

        # === MOMENTUM ===
        df["RSI_14"] = RSIIndicator(close=df.Close, window=14, fillna=False).rsi()

        stoch = StochasticOscillator(high=df.High, low=df.Low, close=df.Close, window=14, smooth_window=3, fillna=False)
        df["STOCHk_14_3_3"] = stoch.stoch()
        df["STOCHd_14_3_3"] = stoch.stoch_signal()

        _compute_stochrsi(df, 14)
        df["WILLR_14"] = WilliamsRIndicator(high=df.High, low=df.Low, close=df.Close, lbp=14, fillna=False).williams_r()

        # === VOLATILITY ===
        bb = BollingerBands(close=df.Close, window=20, window_dev=2, fillna=False)
        df["BBL_20_2.0"] = bb.bollinger_lband()
        df["BBM_20_2.0"] = bb.bollinger_mavg()
        df["BBU_20_2.0"] = bb.bollinger_hband()
        df["BBB_20_2.0"] = bb.bollinger_wband()
        df["BBP_20_2.0"] = bb.bollinger_pband()

        atr_obj = AverageTrueRange(high=df.High, low=df.Low, close=df.Close, window=14, fillna=False)
        df["ATRr_14"] = atr_obj.average_true_range()

        _compute_kc(df, 20, 2, 10)

        # === ICHIMOKU ===
        ichi = IchimokuIndicator(high=df.High, low=df.Low, window1=9, window2=26, window3=52, fillna=False)
        df["ICH_conv"] = ichi.ichimoku_conversion_line()
        df["ICH_base"] = ichi.ichimoku_base_line()
        df["ICH_span_a"] = ichi.ichimoku_a()
        df["ICH_span_b"] = ichi.ichimoku_b()

        # === ADDITIONAL TREND ===
        df["CCI_20"] = CCIIndicator(high=df.High, low=df.Low, close=df.Close, window=20, fillna=False).cci()
        df["DPO_20"] = DPOIndicator(close=df.Close, window=20, fillna=False).dpo()

        # PSAR
        psar = PSARIndicator(high=df.High, low=df.Low, close=df.Close, step=0.02, max_step=0.2, fillna=False)
        df["PSAR_up"] = psar.psar_up()
        df["PSAR_down"] = psar.psar_down()

        # Aroon
        aroon = AroonIndicator(high=df.High, low=df.Low, window=25, fillna=False)
        df["AROON_up"] = aroon.aroon_up()
        df["AROON_down"] = aroon.aroon_down()

        # Vortex
        vortex = VortexIndicator(high=df.High, low=df.Low, close=df.Close, window=14, fillna=False)
        df["VTX_pos"] = vortex.vortex_indicator_pos()
        df["VTX_neg"] = vortex.vortex_indicator_neg()

        # Trix
        from ta.trend import TRIXIndicator
        df["TRIX_15"] = TRIXIndicator(close=df.Close, window=15, fillna=False).trix()

        # === ADDITIONAL MOMENTUM ===
        # StochRSI (library version, replaces manual)
        stochrsi = StochRSIIndicator(close=df.Close, window=14, smooth1=3, smooth2=3, fillna=False)
        df["STOCHRSIk_14"] = stochrsi.stochrsi_k()
        df["STOCHRSId_14"] = stochrsi.stochrsi_d()
        df["AO_5_34"] = AwesomeOscillatorIndicator(high=df.High, low=df.Low, window1=5, window2=34, fillna=False).awesome_oscillator()

        # === ADDITIONAL VOLATILITY ===
        # Donchian Channel (replaces/supplements BB for breakout)
        dc = DonchianChannel(high=df.High, low=df.Low, close=df.Close, window=20, fillna=False)
        df["DC_h_20"] = dc.donchian_channel_hband()
        df["DC_l_20"] = dc.donchian_channel_lband()
        df["DC_m_20"] = dc.donchian_channel_mband()

        # Keltner Channel (library version, replaces manual)
        kc = KeltnerChannel(high=df.High, low=df.Low, close=df.Close, window=20, window_atr=10, fillna=False)
        df["KC_h_20"] = kc.keltner_channel_hband()
        df["KC_l_20"] = kc.keltner_channel_lband()
        df["KC_m_20"] = kc.keltner_channel_mband()

        # Ulcer Index
        from ta.volatility import UlcerIndex
        df["UI_14"] = UlcerIndex(close=df.Close, window=14, fillna=False).ulcer_index()

        # === ADDITIONAL VOLUME ===
        df["FI_13"] = ForceIndexIndicator(close=df.Close, volume=df.Volume, window=13, fillna=False).force_index()

        # === VOLUME (기존) ===
        df["OBV"] = OnBalanceVolumeIndicator(close=df.Close, volume=df.Volume, fillna=False).on_balance_volume()
        df["MFI_14"] = MFIIndicator(high=df.High, low=df.Low, close=df.Close, volume=df.Volume, window=14, fillna=False).money_flow_index()
        try:
            df["CMF_20"] = ChaikinMoneyFlowIndicator(high=df.High, low=df.Low, close=df.Close, volume=df.Volume, window=20, fillna=False).chaikin_money_flow()
        except Exception:
            df["CMF_20"] = np.nan
        df["VWAP_D"] = (df.Volume * (df.High + df.Low + df.Close) / 3).cumsum() / df.Volume.cumsum()

    except Exception as e:
        print(f"  ⚠️  ta(bukosabino) 지표 계산 실패: {e}")
        import traceback
        traceback.print_exc()

    # === CANDLESTICK PATTERNS (TA-Lib, optional) ===
    pattern_cols = [
        "CDL_DOJI",
        "CDL_HAMMER",
        "CDL_SHOOTING_STAR",
        "CDL_ENGULFING",
        "CDL_MORNING_STAR",
        "CDL_EVENING_STAR",
        "CDL_HARAMI",
        "CDL_PIERCING",
        "CDL_3WHITESOLDIERS",
        "CDL_3BLACKCROWS",
        "CDL_MARUBOZU",
        "CDL_DRAGONFLYDOJI",
        "CDL_GRAVESTONEDOJI",
    ]
    try:
        import talib

        o, h, l, c = (
            df.Open.astype(float),
            df.High.astype(float),
            df.Low.astype(float),
            df.Close.astype(float),
        )

        # 8-key patterns for entry score
        df["CDL_DOJI"] = talib.CDLDOJI(o, h, l, c)
        df["CDL_HAMMER"] = talib.CDLHAMMER(o, h, l, c)
        df["CDL_SHOOTING_STAR"] = talib.CDLSHOOTINGSTAR(o, h, l, c)
        df["CDL_ENGULFING"] = talib.CDLENGULFING(o, h, l, c)
        df["CDL_MORNING_STAR"] = talib.CDLMORNINGSTAR(o, h, l, c)
        df["CDL_EVENING_STAR"] = talib.CDLEVENINGSTAR(o, h, l, c)
        df["CDL_HARAMI"] = talib.CDLHARAMI(o, h, l, c)
        df["CDL_PIERCING"] = talib.CDLPIERCING(o, h, l, c)

        # Bonus: extra patterns
        df["CDL_3WHITESOLDIERS"] = talib.CDL3WHITESOLDIERS(o, h, l, c)
        df["CDL_3BLACKCROWS"] = talib.CDL3BLACKCROWS(o, h, l, c)
        df["CDL_MARUBOZU"] = talib.CDLMARUBOZU(o, h, l, c)
        df["CDL_DRAGONFLYDOJI"] = talib.CDLDRAGONFLYDOJI(o, h, l, c)
        df["CDL_GRAVESTONEDOJI"] = talib.CDLGRAVESTONEDOJI(o, h, l, c)
    except Exception as e:
        print(f"  ⚠️  TA-Lib 캔들패턴 계산 스킵: {e}")
        for col in pattern_cols:
            df[col] = 0

    return df


def compute_derived(df: pd.DataFrame) -> dict:
    """파생 메트릭 계산."""
    last = df.iloc[-1]
    recent_20 = df.iloc[-20:]

    result = {}

    # Trend alignment
    if all(c in df.columns for c in ["SMA_20", "SMA_50", "SMA_200"]):
        s20, s50, s200 = float(last["SMA_20"]), float(last["SMA_50"]), float(last["SMA_200"])
        close = float(last.Close)
        result["price_vs_ma20"] = round((close - s20) / s20 * 100, 2) if not pd.isna(s20) else None
        result["price_vs_ma50"] = round((close - s50) / s50 * 100, 2) if not pd.isna(s50) else None
        result["price_vs_ma200"] = round((close - s200) / s200 * 100, 2) if not pd.isna(s200) else None
        result["ma_alignment"] = "bullish" if s20 > s50 > s200 else "bearish" if s20 < s50 < s200 else "mixed"

    # ATR
    if "ATRr_14" in df.columns:
        atr = float(last["ATRr_14"])
        result["atr"] = round(atr, 4) if not pd.isna(atr) else None
        result["atr_pct"] = round(atr / float(last.Close) * 100, 2) if result["atr"] else None

    # Daily volatility
    result["daily_volatility_20d"] = round(recent_20.Close.pct_change().std() * 100, 2)

    # Volume ratio
    avg_vol_50 = df.Volume.iloc[-50:].mean()
    result["volume_ratio"] = round(float(last.Volume) / avg_vol_50, 2) if avg_vol_50 > 0 else 1.0

    return result
