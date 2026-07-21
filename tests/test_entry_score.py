import os
import sys
import unittest

import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from config import CATEGORY_INDICATORS, SCORE_CATEGORY_CAPS  # noqa: E402
from layer3_signals import calculate_entry_score  # noqa: E402


def _base_df(rows: int = 5) -> pd.DataFrame:
    """Minimal OHLCV frame — Open/Close/Volume are read unconditionally by the
    legacy volume-spike branch, so every fixture needs them even when no other
    indicator columns are present."""
    return pd.DataFrame(
        {
            "Open": [100.0] * rows,
            "High": [101.0] * rows,
            "Low": [99.0] * rows,
            "Close": [100.0] * rows,
            "Volume": [1000] * rows,
        }
    )


class EntryScoreContribsTests(unittest.TestCase):
    def test_neutral_input_yields_neutral_score_and_empty_contribs(self):
        df = _base_df()
        result = calculate_entry_score(df, current_price=100.0, all_supports=[])

        self.assertEqual(result["score"], 50)
        self.assertEqual(result["details"], [])
        self.assertEqual(result["contribs"], [])

    def test_one_hit_per_core_category_records_contribs_with_valid_ids(self):
        df = _base_df()
        df.loc[df.index[-1], "RSI_14"] = 20.0          # momentum/rsi  -> +10
        df.loc[df.index[-1], "ADX_14"] = 45.0           # trend/adx     -> +5
        df.loc[df.index[-1], "MFI_14"] = 15.0           # volume/mfi    -> +8
        df.loc[df.index[-1], "CDL_HAMMER"] = 100.0      # candlestick/patterns -> +10

        result = calculate_entry_score(
            df,
            current_price=100.0,
            all_supports=[{"price": 99.5}],             # structure/support_distance -> +8
        )

        contribs = result["contribs"]
        by_indicator = {c["indicator"]: c for c in contribs}

        expected_points = {
            "rsi": 10,
            "adx": 5,
            "mfi": 8,
            "patterns": 10,
            "support_distance": 8,
        }
        self.assertEqual(set(by_indicator), set(expected_points))
        for indicator, points in expected_points.items():
            self.assertEqual(by_indicator[indicator]["points"], points)

        # Every recorded id must belong to the config vocabulary for its category.
        for contrib in contribs:
            category = contrib["category"]
            self.assertIn(category, CATEGORY_INDICATORS)
            self.assertIn(contrib["indicator"], CATEGORY_INDICATORS[category])

        # details stays a flat (label, points) 2-tuple list — backward compatible.
        for entry in result["details"]:
            self.assertIsInstance(entry, tuple)
            self.assertEqual(len(entry), 2)
        self.assertEqual(sorted(pts for _, pts in result["details"]), [5, 8, 8, 10, 10])

        # None of these single hits exceed their category cap, so score is a
        # plain, unclamped sum: 50 + 10 + 5 + 8 + 8 + 10 = 91.
        self.assertEqual(result["score"], 91)

    def test_volume_category_is_clamped_at_positive_cap(self):
        df = _base_df(5)
        # Legacy volume-spike branch: last bar volume >> 50-bar average, bullish candle.
        df.loc[df.index[-1], "Volume"] = 1000.0
        df.loc[:3, "Volume"] = 100.0
        df.loc[df.index[-1], "Open"] = 100.0
        df.loc[df.index[-1], "Close"] = 105.0
        df["MFI_14"] = 50.0
        df.loc[df.index[-1], "MFI_14"] = 15.0            # +8
        df["CMF_20"] = 0.0
        df.loc[df.index[-1], "CMF_20"] = 0.25             # +7
        df["FI_13"] = 0.0
        df.loc[df.index[-2], "FI_13"] = -1.0
        df.loc[df.index[-1], "FI_13"] = 1.0                # crossover +5
        # legacy volume-spike (+4) computed above from the Volume/Open/Close setup

        result = calculate_entry_score(df, current_price=100.0, all_supports=[])

        volume_contribs = [c for c in result["contribs"] if c["category"] == "volume"]
        raw_subtotal = sum(c["points"] for c in volume_contribs)
        self.assertEqual(raw_subtotal, 24)  # 8 + 7 + 5 + 4, above the cap
        self.assertEqual(SCORE_CATEGORY_CAPS["volume"], 20)

        # Score reflects the *clamped* subtotal (20), not the raw one (24).
        self.assertEqual(result["score"], 50 + 20)

    def test_structure_category_is_clamped_at_negative_cap(self):
        df = _base_df(5)
        df.loc[df.index[-1], "Close"] = 99.5
        df["BBL_20_2.0"] = 90.0
        df["BBU_20_2.0"] = 100.0                          # bb_pct = 0.95 -> -6
        df["DC_h_20"] = 100.0
        df["DC_l_20"] = 90.0                               # dc_pct = 0.95 -> -6

        liquidity = {
            "anchored_vwap": {
                "levels": [
                    {
                        "name": "AVWAP-X",
                        "distance_pct": 1.0,
                        "role": "resistance",
                        "slope_regime": "falling",         # -3
                    }
                ]
            },
            "volume_profile": {
                "error": None,
                "poc": None,
                "value_area_low": None,
                "value_area_high": 100.3,                  # within 1% -> -3
            },
        }
        # fib_data must be truthy for the avwap/volume_profile blocks to run at
        # all (they are nested under `if fib_data:` in the real function), even
        # though its own confluence list is empty here.
        fib_data = {"confluence": []}

        result = calculate_entry_score(
            df,
            current_price=100.0,
            all_supports=[{"price": 80.0}],                # dist_pct = 20% -> -8
            liquidity=liquidity,
            fib_data=fib_data,
        )

        structure_contribs = [c for c in result["contribs"] if c["category"] == "structure"]
        raw_subtotal = sum(c["points"] for c in structure_contribs)
        self.assertEqual(raw_subtotal, -26)  # -8 -6 -6 -3 -3, below the -20 floor
        self.assertEqual(SCORE_CATEGORY_CAPS["structure"], 20)

        self.assertEqual(result["score"], 50 - 20)

    def test_overlay_applies_after_category_caps_and_is_not_capped(self):
        """Market-regime overlay is untouched by this refactor: it must still run
        after the cap-insertion point and add on top of the (already-clamped)
        core score, uncapped itself."""
        df = _base_df()
        df.loc[df.index[-1], "RSI_14"] = 20.0
        df.loc[df.index[-1], "ADX_14"] = 45.0
        df.loc[df.index[-1], "MFI_14"] = 15.0
        df.loc[df.index[-1], "CDL_HAMMER"] = 100.0

        market = {"vix": {}, "breadth": {"regime": "strong_bull"}}  # +3, no cap

        result = calculate_entry_score(
            df,
            current_price=100.0,
            all_supports=[{"price": 99.5}],
            market=market,
        )

        # Same core contributions as the one-hit-per-category test (score 91),
        # plus the +3 market overlay on top, uncapped and outside contribs.
        self.assertEqual(result["score"], 91 + 3)
        self.assertTrue(any("KOSPI" in label for label, _ in result["details"]))
        self.assertFalse(any(c["category"] == "market" for c in result["contribs"]))


if __name__ == "__main__":
    unittest.main()
