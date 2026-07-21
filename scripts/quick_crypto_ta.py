#!/usr/bin/env python3
"""
Quick Crypto Technical Analysis — yfinance 기반 BTC/ETH 등 암호화폐 TA 요약.
margin-ta가 미국주식 전용이므로 암호화폐는 이 스크립트로 커버한다.

Usage:
  python scripts/quick_crypto_ta.py BTC-USD

Output: Rich 콘솔 테이블 없이 plain-text 요약 (RSI/MACD/BB/SR/추세 평가).
margin-ta의 전달 가이드라인(Delivery Guidelines)에 맞춰 해석 레이어를 반드시 추가할 것.
"""
import sys
import yfinance as yf
import numpy as np
from datetime import datetime


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def find_sr_levels(df, window=20, threshold=0.02):
    highs = df["High"].rolling(window=window).max()
    lows = df["Low"].rolling(window=window).min()
    resistance_levels, support_levels = [], []
    for i in range(window, len(df) - window):
        if df["High"].iloc[i] == highs.iloc[i]:
            resistance_levels.append(float(df["High"].iloc[i]))
        if df["Low"].iloc[i] == lows.iloc[i]:
            support_levels.append(float(df["Low"].iloc[i]))

    def cluster_levels(levels, threshold_pct=0.02):
        if not levels:
            return []
        levels = sorted(set(levels))
        clusters, current = [], [levels[0]]
        for lvl in levels[1:]:
            if lvl / current[-1] - 1 < threshold_pct:
                current.append(lvl)
            else:
                clusters.append(np.mean(current))
                current = [lvl]
        clusters.append(np.mean(current))
        return clusters

    return cluster_levels(support_levels), cluster_levels(resistance_levels)


def analyze(ticker: str):
    sym = yf.Ticker(ticker)
    df = sym.history(period="6mo", interval="1d")
    if df.empty:
        df = sym.history(period="1y", interval="1d")
    if df.empty:
        print(f"❌ {ticker}: 데이터 없음")
        return

    current = float(df["Close"].iloc[-1])
    prev_close = float(df["Close"].iloc[-2])
    change = current - prev_close
    change_pct = (change / prev_close) * 100

    week_ago = float(df["Close"].iloc[-7]) if len(df) >= 7 else float(df["Close"].iloc[0])
    month_ago = float(df["Close"].iloc[-30]) if len(df) >= 30 else float(df["Close"].iloc[0])
    high_6m = float(df["High"].max())
    low_6m = float(df["Low"].min())

    ma20 = float(df["Close"].rolling(20).mean().iloc[-1])
    ma50 = float(df["Close"].rolling(50).mean().iloc[-1])
    ma200 = float(df["Close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else float("nan")

    rsi = float(compute_rsi(df["Close"]).iloc[-1])
    macd_line, signal_line, histogram = compute_macd(df["Close"])
    macd_now = float(macd_line.iloc[-1])
    sig_now = float(signal_line.iloc[-1])
    hist_now = float(histogram.iloc[-1])
    hist_prev = float(histogram.iloc[-2])

    bb_sma = float(df["Close"].rolling(20).mean().iloc[-1])
    bb_std = float(df["Close"].rolling(20).std().iloc[-1])
    bb_upper = bb_sma + 2 * bb_std
    bb_lower = bb_sma - 2 * bb_std
    bb_width = ((bb_upper - bb_lower) / bb_sma) * 100
    bb_pos = (current - bb_lower) / (bb_upper - bb_lower) * 100

    supports, resistances = find_sr_levels(df)

    avg_vol = float(df["Volume"].rolling(20).mean().iloc[-1])
    vol_now = float(df["Volume"].iloc[-1])
    vol_ratio = vol_now / avg_vol

    daily_returns = df["Close"].pct_change().dropna()
    volatility = float(daily_returns.std() * np.sqrt(365) * 100)

    print("=" * 60)
    print(f"📊 {ticker} 기술적 분석 — {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 60)
    print(f"\n💰 현재가: ${current:,.2f}")
    print(f"📈 전일대비: {change:+,.2f} ({change_pct:+.2f}%)")
    print(f"📅 주간수익률: {((current / week_ago) - 1) * 100:+.2f}%")
    print(f"📅 월간수익률: {((current / month_ago) - 1) * 100:+.2f}%")
    print(f"🔺 6개월 고가: ${high_6m:,.2f}")
    print(f"🔻 6개월 저가: ${low_6m:,.2f}")
    print(f"📏 고가대비: {((current / high_6m) - 1) * 100:+.2f}%")

    print(f"\n📐 이동평균선:")
    print(f"  MA 20:  ${ma20:,.2f}  — {'🟢 상회' if current > ma20 else '🔴 하회'} ({(current / ma20 - 1) * 100:+.2f}%)")
    print(f"  MA 50:  ${ma50:,.2f}  — {'🟢 상회' if current > ma50 else '🔴 하회'} ({(current / ma50 - 1) * 100:+.2f}%)")
    if not np.isnan(ma200):
        print(f"  MA 200: ${ma200:,.2f}  — {'🟢 상회' if current > ma200 else '🔴 하회'} ({(current / ma200 - 1) * 100:+.2f}%)")

    print(f"\n📊 RSI (14): {rsi:.1f}")
    if rsi > 70:
        print("  ⚠️ 과매수 구간 — 조정 가능성")
    elif rsi < 30:
        print("  💡 과매도 구간 — 반등 가능성")
    else:
        print(f"  ➡️ 중립 구간 ({'상승' if rsi > 50 else '하락'} 모멘텀)")

    print(f"\n📉 MACD:")
    print(f"  MACD Line:  {macd_now:,.2f}")
    print(f"  Signal:     {sig_now:,.2f}")
    print(f"  Histogram:  {hist_now:,.2f}")
    if macd_now > sig_now:
        print("  🟢 MACD > Signal (강세)")
    else:
        print("  🔴 MACD < Signal (약세)")
    if hist_now > hist_prev:
        print("  📈 히스토그램 상승 중 (모멘텀 개선)")
    else:
        print("  📉 히스토그램 하락 중 (모멘텀 약화)")

    print(f"\n📏 볼린저 밴드:")
    print(f"  Upper:  ${bb_upper:,.2f}")
    print(f"  Middle: ${bb_sma:,.2f}")
    print(f"  Lower:  ${bb_lower:,.2f}")
    print(f"  Width:  {bb_width:.1f}%")
    print(f"  Position: {bb_pos:.0f}% (0=하단, 100=상단)")

    print(f"\n🏗️ 지지/저항 레벨:")
    print("  지지선:")
    for s in sorted(supports, reverse=True)[:3]:
        dist = (current / s - 1) * 100
        print(f"    ${s:,.2f}  (현재가 대비 {dist:+.1f}%)")
    print("  저항선:")
    for r in sorted(resistances)[:3]:
        if r > current:
            dist = (r / current - 1) * 100
            print(f"    ${r:,.2f}  (현재가 대비 {dist:+.1f}%)")

    print(f"\n📊 거래량:")
    print(f"  금일:    {vol_now:,.0f}")
    print(f"  20일 평균: {avg_vol:,.0f}")
    print(f"  비율:    {vol_ratio:.2f}x")

    print(f"\n🌊 연율화 변동성: {volatility:.1f}%")

    print(f"\n🎯 종합 평가:")
    signals = []
    if current > ma20 and current > ma50:
        signals.append("✅ 단기/중기 상승추세 (MA 정배열)")
    elif current < ma20 and current < ma50:
        signals.append("❌ 단기/중기 하락추세 (MA 역배열)")
    else:
        signals.append("⚠️ MA 혼조세")
    if 30 < rsi < 70:
        signals.append("✅ RSI 정상 범위")
    elif rsi > 70:
        signals.append("⚠️ RSI 과매수")
    else:
        signals.append("💡 RSI 과매도")
    if macd_now > sig_now:
        signals.append("✅ MACD 강세 신호")
    else:
        signals.append("❌ MACD 약세 신호")
    if hist_now > 0:
        signals.append("✅ 히스토그램 양수")
    else:
        signals.append("❌ 히스토그램 음수")
    for s in signals:
        print(f"  {s}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: quick_crypto_ta.py TICKER [TICKER...]")
        print("Example: quick_crypto_ta.py BTC-USD ETH-USD")
        sys.exit(1)
    for t in sys.argv[1:]:
        analyze(t)
