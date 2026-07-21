import os
import sys
import unittest

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer3_consensus import add_horizon_conflict, build_consensus, detect_rsi_divergence  # noqa: E402


def _c(cat, ind, pts):
    return {"category": cat, "indicator": ind, "points": pts}


class ConsensusTests(unittest.TestCase):
    def test_category_counts_include_neutral(self):
        contribs = [_c("momentum", "rsi", 10), _c("momentum", "stoch", -4)]
        out = build_consensus(contribs, df=pd.DataFrame({"Close": [100.0] * 30}))
        mom = out["categories"]["momentum"]
        self.assertEqual((mom["bull"], mom["bear"], mom["neutral"]), (1, 1, 1))  # willr 미투표=neutral

    def test_agreement_majority_ratio(self):
        contribs = [_c("momentum", "rsi", 10), _c("momentum", "stoch", 8),
                    _c("trend", "macd", 8), _c("volume", "mfi", -8)]
        out = build_consensus(contribs, df=pd.DataFrame({"Close": [100.0] * 30}))
        self.assertEqual(out["agreement"], 75)  # 4표 중 다수파(bull) 3

    def test_agreement_none_when_too_few_directional(self):
        out = build_consensus([_c("momentum", "rsi", 10)], df=pd.DataFrame({"Close": [100.0] * 30}))
        self.assertIsNone(out["agreement"])

    def test_momentum_vs_trend_conflict(self):
        contribs = [_c("momentum", "rsi", 10), _c("momentum", "stoch", 8),
                    _c("trend", "macd", -8), _c("trend", "adx", -3)]
        out = build_consensus(contribs, df=pd.DataFrame({"Close": [100.0] * 30}))
        self.assertIn("momentum_vs_trend", out["conflicts"])

    def test_bearish_divergence_detected(self):
        n = 60
        close = np.full(n, 100.0)
        close[20] = 110.0   # pivot high 1
        close[45] = 115.0   # pivot high 2 (HH)
        rsi = np.full(n, 50.0)
        rsi[20] = 75.0
        rsi[45] = 65.0      # LH
        df = pd.DataFrame({"Close": close, "RSI_14": rsi},
                          index=pd.bdate_range("2026-01-05", periods=n))
        out = detect_rsi_divergence(df, pivot_window=5, lookback=60)
        self.assertIsNotNone(out)
        self.assertEqual(out["type"], "bearish_divergence")

    def test_horizon_conflict_appended(self):
        consensus = {"conflicts": []}
        horizons = {"short": {"stance": "bullish"}, "mid": {"stance": "neutral"},
                    "long": {"stance": "bearish"}}
        add_horizon_conflict(consensus, horizons)
        self.assertIn("horizon_conflict", consensus["conflicts"])

    def test_bearish_divergence_surfaces_via_build_consensus(self):
        # Same HH-price/LH-RSI fixture shape as test_bearish_divergence_detected,
        # but driven through build_consensus(contribs, df) with the default
        # lookback (90) instead of calling detect_rsi_divergence directly —
        # len(df)=60 < 90 so df.iloc[-90:] still yields the full 60-row frame
        # and the same pivots are detected.
        n = 60
        close = np.full(n, 100.0)
        close[20] = 110.0   # pivot high 1
        close[45] = 115.0   # pivot high 2 (HH)
        rsi = np.full(n, 50.0)
        rsi[20] = 75.0
        rsi[45] = 65.0      # LH
        df = pd.DataFrame({"Close": close, "RSI_14": rsi},
                          index=pd.bdate_range("2026-01-05", periods=n))
        out = build_consensus([_c("momentum", "rsi", 10)], df)
        self.assertIsNotNone(out["divergence"])
        self.assertEqual(out["divergence"]["type"], "bearish_divergence")
        self.assertIn("bearish_divergence", out["conflicts"])

    def test_volume_vs_price_conflict_on_bearish_volume_rising_price(self):
        # Volume-category contribs majority bearish, while price rose >3%
        # over the last 21 bars -> volume_vs_price conflict.
        contribs = [_c("volume", "mfi", -8), _c("volume", "obv", -5)]
        n = 25
        close = np.linspace(100.0, 110.0, n)  # last-21 change ~ +8.2% > 3%
        df = pd.DataFrame({"Close": close})
        out = build_consensus(contribs, df)
        self.assertEqual(out["categories"]["volume"]["bull"], 0)
        self.assertEqual(out["categories"]["volume"]["bear"], 2)
        self.assertIn("volume_vs_price", out["conflicts"])
