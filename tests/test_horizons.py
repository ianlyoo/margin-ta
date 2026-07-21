import os
import sys
import unittest

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer3_horizons import build_horizons, _swing_structure  # noqa: E402


def _trend_daily(days: int, start_price: float = 50.0, daily_ret: float = 0.002) -> pd.DataFrame:
    idx = pd.bdate_range("2016-01-04", periods=days)
    close = start_price * np.cumprod(np.full(days, 1 + daily_ret))
    return pd.DataFrame({
        "Open": close * 0.999, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": np.full(days, 1_000_000),
    }, index=idx)


def _consensus(bull: int, bear: int) -> dict:
    return {"categories": {"momentum": {"bull": bull, "bear": bear, "neutral": 0},
                           "trend": {"bull": 0, "bear": 0, "neutral": 5},
                           "volume": {"bull": 0, "bear": 0, "neutral": 4},
                           "structure": {"bull": 0, "bear": 0, "neutral": 7},
                           "candlestick": {"bull": 0, "bear": 0, "neutral": 2}}}


class SwingStructureTests(unittest.TestCase):
    def _zigzag(self, points):
        """Create a series with linear interpolation between anchor points."""
        n = max(i for i, _ in points) + 3
        vals = np.full(n, np.nan)
        for i, v in points:
            vals[i] = v
        return pd.Series(vals).interpolate().bfill().ffill()

    def test_swing_up_structure_hh_hl(self):
        """Test HH&HL pattern returns 'up'."""
        close = self._zigzag([(0, 100), (10, 95), (20, 120), (30, 105), (40, 135), (44, 130)])
        self.assertEqual(_swing_structure(close), "up")

    def test_swing_down_structure_lh_ll(self):
        """Test LH&LL pattern returns 'down'."""
        close = self._zigzag([(0, 130), (10, 135), (20, 110), (30, 125), (40, 95), (44, 100)])
        self.assertEqual(_swing_structure(close), "down")


class HorizonTests(unittest.TestCase):
    def test_long_uptrend_gives_bullish_mid_and_long(self):
        out = build_horizons(_trend_daily(2500), _consensus(3, 0))  # 10년 꾸준한 상승
        self.assertEqual(out["mid"]["stance"], "bullish")
        self.assertEqual(out["long"]["stance"], "bullish")
        self.assertEqual(out["alignment"], "aligned_bull")
        self.assertTrue(out["mid"]["basis"])

    def test_insufficient_data_short_history(self):
        out = build_horizons(_trend_daily(200), _consensus(1, 0))  # ~10개월
        self.assertEqual(out["mid"]["stance"], "insufficient_data")
        self.assertEqual(out["long"]["stance"], "insufficient_data")

    def test_short_stance_from_consensus_and_mixed_alignment(self):
        # 장기 상승 추세 + 컨센서스 bear 다수 → short bearish, long bullish → mixed_pullback
        out = build_horizons(_trend_daily(2500), _consensus(0, 5))
        self.assertEqual(out["short"]["stance"], "bearish")
        self.assertEqual(out["alignment"], "mixed_pullback")


if __name__ == "__main__":
    unittest.main()
