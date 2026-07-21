# margin-ta

A read-only technical-analysis pipeline for US and Korean equities (and crypto
pairs). It combines 30+ vectorized indicators, multi-horizon
daily/weekly/monthly stances, an indicator-consensus layer, tiered
support/resistance detection, and a macro market/sector risk dashboard into a
single CLI — and doubles as the analysis engine behind an MCP server.

The pipeline never places orders. Every output is decision-support data:
"the position exists — where does adding make technical sense?"

## Architecture (6 layers)

| Layer | Module(s) | Responsibility |
|---|---|---|
| **L0 Orchestration** | `margin_ta.py` | Ticker validation, provider selection, pipeline flow |
| **L1 Data** | `layer1_data`, `layer1_market`, `layer1_kr_market` | 2y daily OHLCV + metadata (market cap, sector, beta); yfinance and pykrx public data by default, optional Toss/KIS session hooks; market-regime fetch (VIX/breadth) |
| **L2 Indicators** | `layer2_indicators` | 30+ vectorized indicators (pandas `ta`: SMA/EMA, MACD, RSI, Stoch/StochRSI, ADX, Aroon, Vortex, PSAR, Bollinger, ATR, Ichimoku, CCI, TRIX, MFI/CMF, …) plus TA-Lib candlestick patterns |
| **L3 Signals** | `layer3_signals`, `layer3_consensus`, `layer3_horizons`, `layer3_risk`, `layer3_liquidity` | Horizontal/dynamic/Fibonacci S/R with tiering (near/intermediate/major), Entry Score (0–100, capped per category), directional indicator consensus with agreement, multi-horizon stances, market-risk scoring |
| **L4 Pricing** | `layer4_pricing` | Trigger-based entry plans (support-bounce / trend-confirm / breakout), ATR-based stops, tiered targets, risk/reward |
| **L5 Output** | `layer5_output` | Rich console tables, chart PNG (matplotlib), JSON, TradingView deep links |

## Multi-horizon analysis

`margin_ta.py` computes a stance — `bullish` / `neutral` / `bearish` — on
three horizons:

- **short**: daily-timeframe indicator consensus
- **mid**: weekly resample (≥ 60 bars ≈ 14 months)
- **long**: monthly resample (≥ 36 months)

The three stances collapse into a single `alignment` label: `aligned_bull`,
`aligned_bear`, `mixed_pullback` (long-term bull, short-term dip),
`mixed_rally` (long-term bear, short-term bounce), or `mixed`. Horizons with
too little history report `insufficient_data` instead of guessing.

## Market & sector risk

`market_risk.py` scores the macro backdrop into a single **0–100 risk score**
plus a regime — `calm` / `caution` / `stress` / `crisis` — from weighted
indicator groups (volatility, overheating, credit & rates, breadth, safe
haven). Inputs include VIX level, VXN−VIX spread, VIX term structure, VVIX,
index monthly-CCI overheating, 200-day moving-average gap, a HYG/LQD credit
proxy, the yield curve, GLD/SPY, DXY, and breadth divergence. Everything is
pulled from public yfinance tickers; FRED series are fetched from the public
CSV endpoint (no API key) on a best-effort basis.

With `--sectors`, each sector ETF (US XL suite, SMH, and KOSPI/KOSDAQ proxies)
gets its own risk score from overheating, momentum rollover, drawdown speed,
volatility rise, and volume anomaly components.

## Quickstart

```bash
git clone https://github.com/ianlyoo/margin-ta && cd margin-ta
pip install -r requirements.txt          # TA-Lib needs the C library — see note below
python scripts/margin_ta.py AAPL --json --quiet --no-tv --no-market
python scripts/market_risk.py --sectors
```

`--no-tv` skips the TradingView cross-check and `--no-market` skips the
VIX/breadth regime lookup — the minimal-dependency path. Add `--chart` for a
PNG and `--save` to persist the JSON result; `--flow`, `--ownership`, and the
`--options-*` flags layer on dark-pool/short flow, SEC 13D/G ownership, and
options-chain analysis.

Korean tickers work out of the box via pykrx: `python scripts/margin_ta.py 005930`
(market auto-detected from 6-digit codes, `.KS`, `.KQ`).

### Install (pip)

```bash
pip install git+https://github.com/ianlyoo/margin-ta
margin-ta AAPL --json --quiet --no-tv --no-market
market-risk --sectors
```

### Crypto

```bash
python scripts/crypto_ta.py BTC-USD --save --chart   # full 6-layer pipeline
python scripts/quick_crypto_ta.py BTC-USD            # plain-text summary (RSI/MACD/BB/S/R)
```

`crypto_ta.py` reuses the equity pipeline with a crypto-native regime layer
(Fear & Greed + BTC dominance) in place of VIX/breadth. Any yfinance crypto
pair works (BTC-USD, ETH-USD, SOL-USD, …).

### Nightly scan

`scripts/scan_nightly.py` ranks a watchlist (S&P 500 + NASDAQ 100 by default)
by Entry Score; `scripts/download_ohlcv_batch.py` pre-caches OHLCV so the scan
doesn't hammer yfinance.

## Configuration

Everything runs on public yfinance/pykrx data with **no configuration**.
All integrations are optional environment variables:

| Env | Effect |
|---|---|
| `MARGIN_TA_TOSS_IMPORT` | Import path to your own Toss Securities client module; enables Toss as an OHLCV/session source with automatic fallback to pykrx/yfinance |
| `KIS_ENV_FILE` | env file holding Korea Investment & Securities credentials (`APP_KEY`, `APP_SECRET`, `CANO`, `ACNT_PRDT_CD`, `URL_BASE` — `KIS_`-prefixed keys also accepted) for day/pre-market session quotes |
| `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_CANO`, `KIS_ACNT_PRDT_CD`, `KIS_URL_BASE` | Direct-env alternative to `KIS_ENV_FILE` (direct env wins) |
| `MARGIN_TA_KIS_TOKEN_CACHE` | Path for the KIS OAuth token cache (default `~/.cache/margin-ta/kis_token.json`) |
| `MARGIN_TA_GOOGLE_TOKEN` | Google Drive OAuth token file for `scan_nightly.py --gdrive-upload` |
| `ALPHAVANTAGE_API_KEY`, `TRADIER_TOKEN`, `POLYGON_API_KEY`, `UW_API_KEY` | Options-chain data providers (each independently optional) |

Credentials are read from the environment only — never hardcoded, never
echoed into output.

## TA-Lib note

The Python `TA-Lib` package (candlestick patterns) wraps a C library that must
be installed first. On Debian/Ubuntu:

```bash
apt install -y build-essential wget
wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
tar xzf ta-lib-0.4.0-src.tar.gz && cd ta-lib
./configure --prefix=/usr && make && make install
pip install TA-Lib
```

On macOS: `brew install ta-lib && pip install TA-Lib`.

## Disclaimer

Output is decision-support data, not investment advice. No guarantee of
accuracy or fitness for any purpose; trade at your own risk.

## License

[MIT](LICENSE) © 2026 AhnRyu
