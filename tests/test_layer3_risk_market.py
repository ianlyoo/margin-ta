import os
import sys
import unittest

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer3_risk import (  # noqa: E402
    compute_market_indicators,
    compute_market_risk_score,
)


def _flat(val, n=1300):
    idx = pd.bdate_range("2019-01-01", periods=n)
    return pd.Series([val] * n, index=idx)


def _trend(n=260, start=100.0, slope=0.5):
    """Linear trend series — endpoint sits above its trailing 200DMA when slope>0,
    below it when slope<0. Long enough (>200 bars) for the 200DMA to be defined."""
    idx = pd.bdate_range("2019-01-01", periods=n)
    vals = [start + slope * i for i in range(n)]
    return pd.Series(vals, index=idx)


# 10 of the 11 US sector ETFs (SECTOR_UNIVERSE["us"]) — denominator of 10 for clean %.
_SECTOR_10 = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC"]


def _sectors_with_pct_above(pct_above: int) -> dict:
    """Build a sectors dict where exactly `pct_above`% of _SECTOR_10 close above
    their own 200DMA (uptrend) and the rest close below (downtrend)."""
    n_above = round(len(_SECTOR_10) * pct_above / 100)
    out = {}
    for i, sym in enumerate(_SECTOR_10):
        out[sym] = _trend(slope=0.5) if i < n_above else _trend(slope=-0.5)
    return out


class MarketIndicatorTests(unittest.TestCase):
    def test_vxn_minus_vix_alert_on_wide_spread(self):
        rd = {"tickers": {"vxn": _flat(30.0), "vix": _flat(18.0)}, "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["vxn_minus_vix"]["signal"], "alert")   # 12 ≥ 10
        self.assertAlmostEqual(ind["vxn_minus_vix"]["value"], 12.0, places=1)

    def test_vix_term_structure_inversion_alert(self):
        rd = {"tickers": {"vix9d": _flat(22.0), "vix": _flat(20.0)}, "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["vix_term_structure"]["signal"], "alert")  # 22/20=1.1 ≥ 1.0

    def test_missing_ticker_is_unavailable_not_crash(self):
        rd = {"tickers": {}, "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["vvix"]["signal"], "unavailable")

    def test_composite_score_excludes_unavailable_groups(self):
        indicators = {
            "vxn_minus_vix": {"signal": "alert"}, "vvix": {"signal": "ok"},
            "buffett": {"signal": "unavailable"},
        }
        out = compute_market_risk_score(indicators)
        self.assertIn(out["regime"], ("calm", "caution", "stress", "crisis"))
        self.assertIsInstance(out["score"], int)
        self.assertIn("vxn_minus_vix", out["alerts"])  # alert 지표는 alerts에

    def test_vix_level_alert_at_35(self):
        rd = {"tickers": {"vix": _flat(35.0)}, "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["vix_level"]["signal"], "alert")   # 35 >= 30
        self.assertAlmostEqual(ind["vix_level"]["value"], 35.0, places=1)

    def test_vix_level_warn_at_22(self):
        rd = {"tickers": {"vix": _flat(22.0)}, "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["vix_level"]["signal"], "warn")    # 20 <= 22 < 30

    def test_vix_level_ok_at_15(self):
        rd = {"tickers": {"vix": _flat(15.0)}, "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["vix_level"]["signal"], "ok")      # 15 < 20

    def test_vix_level_missing_ticker_is_unavailable(self):
        rd = {"tickers": {}, "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["vix_level"]["signal"], "unavailable")

    def test_vix_level_contributes_to_volatility_group(self):
        # vix_level alone alert 이고 나머지 변동성 지표는 unavailable → volatility 그룹 점수는
        # vix_level 단독 기여로 100이어야 한다 (구성 리스트에 실제로 포함됐는지 검증).
        indicators = {
            "vix_level": {"signal": "alert"},
            "vxn_minus_vix": {"signal": "unavailable"},
            "vix_term_structure": {"signal": "unavailable"},
            "vvix": {"signal": "unavailable"},
        }
        out = compute_market_risk_score(indicators)
        self.assertEqual(out["group_scores"]["volatility"], 100)
        self.assertIn("vix_level", out["alerts"])

    # ── breadth_divergence ──────────────────────────────────────────────
    def test_breadth_divergence_index_strong_breadth_40_warns(self):
        rd = {"tickers": {"sp500": _trend(slope=0.5)},
              "sectors": _sectors_with_pct_above(40), "breadth": None,
              "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["breadth_divergence"]["signal"], "warn")
        self.assertAlmostEqual(ind["breadth_divergence"]["value"], 40.0, places=1)

    def test_breadth_divergence_index_strong_breadth_30_alerts(self):
        rd = {"tickers": {"sp500": _trend(slope=0.5)},
              "sectors": _sectors_with_pct_above(30), "breadth": None,
              "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["breadth_divergence"]["signal"], "alert")
        self.assertAlmostEqual(ind["breadth_divergence"]["value"], 30.0, places=1)

    def test_breadth_divergence_index_strong_breadth_70_ok(self):
        rd = {"tickers": {"sp500": _trend(slope=0.5)},
              "sectors": _sectors_with_pct_above(70), "breadth": None,
              "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["breadth_divergence"]["signal"], "ok")
        self.assertAlmostEqual(ind["breadth_divergence"]["value"], 70.0, places=1)

    def test_breadth_divergence_index_weak_breadth_30_is_ok(self):
        # divergence는 지수 강세가 전제 — 지수가 약할 때는 참여 폭이 좁아도 ok.
        rd = {"tickers": {"sp500": _trend(slope=-0.5)},
              "sectors": _sectors_with_pct_above(30), "breadth": None,
              "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["breadth_divergence"]["signal"], "ok")

    def test_breadth_divergence_unavailable_without_cache_or_enough_sectors(self):
        rd = {"tickers": {"sp500": _trend(slope=0.5)},
              "sectors": {"XLK": _trend(slope=0.5), "XLF": _trend(slope=-0.5)},
              "breadth": None, "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["breadth_divergence"]["signal"], "unavailable")

    def test_breadth_divergence_stock_cache_takes_precedence_over_sector_proxy(self):
        # above_200ma_pct=42 → warn (35<=42<50); sectors deliberately built to a
        # different %(would be alert at 100% proxy-below) to prove cache wins.
        rd = {"tickers": {"sp500": _trend(slope=0.5)},
              "sectors": _sectors_with_pct_above(0),
              "breadth": {"above_200ma_pct": 42.0, "regime": "normal"},
              "fred": {}, "unavailable": []}
        ind = compute_market_indicators(rd)
        self.assertEqual(ind["breadth_divergence"]["signal"], "warn")
        self.assertAlmostEqual(ind["breadth_divergence"]["value"], 42.0, places=1)
        self.assertIn("캐시", ind["breadth_divergence"]["note"])
