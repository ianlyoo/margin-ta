import os
import sys
import unittest

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer3_liquidity import liquidity_levels  # noqa: E402
from layer3_signals import (  # noqa: E402
    _tier_of,
    build_sr_tiers,
    find_weekly_pivots,
    tier_levels,
)


def _weekly_df():
    n = 30
    close = np.full(n, 100.0)
    low = np.full(n, 99.0)
    high = np.full(n, 101.0)
    low[10] = 90.0    # 뚜렷한 스윙 저점
    high[20] = 120.0  # 뚜렷한 스윙 고점
    idx = pd.date_range("2025-01-03", periods=n, freq="W-FRI")
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": np.full(n, 1e6)}, index=idx)


class SRTierTests(unittest.TestCase):
    def test_weekly_pivots_found(self):
        sup, res = find_weekly_pivots(_weekly_df(), window=3)
        self.assertTrue(any(abs(s["price"] - 90.0) < 1e-6 for s in sup))
        self.assertTrue(any(abs(r["price"] - 120.0) < 1e-6 for r in res))

    def test_tier_levels_adds_major_and_dedupes(self):
        daily_idx = pd.bdate_range("2022-01-03", periods=1000)
        df_daily = pd.DataFrame({
            "Open": 100.0, "High": np.linspace(100, 130, 1000),
            "Low": np.linspace(95, 100, 1000), "Close": np.linspace(98, 115, 1000),
            "Volume": 1e6,
        }, index=daily_idx)
        all_levels = {
            "supports": [
                {"price": 90.05, "source": "Horizontal", "strength": 2.0},  # major 90.0과 중복 → 제거
                {"price": 111.0, "source": "200SMA", "distance_pct": 3.5},
            ],
            "resistances": [{"price": 118.0, "source": "Horizontal", "strength": 6.0}],
            "current_price": 115.0,
        }
        weekly = ([{"price": 90.0, "strength": 3.0, "touches": 1}], [])
        out = tier_levels(all_levels, weekly, ([], []), df_daily, 115.0)
        sup_pairs = [(s["price"], s.get("tier")) for s in out["supports"]]
        self.assertIn((90.0, "major"), sup_pairs)
        self.assertNotIn(90.05, [p for p, _ in sup_pairs])
        self.assertEqual([s["tier"] for s in out["supports"] if s["source"] == "200SMA"], ["major"])
        self.assertEqual(
            [r["tier"] for r in out["resistances"] if r["source"] == "Horizontal"],
            ["intermediate"],  # strength 6 ≥ 5
        )
        self.assertTrue(any(r["source"] == "All-Time High" for r in out["resistances"]))

    def test_build_sr_tiers_key_levels(self):
        all_levels = {
            "supports": [
                {"price": 90.0, "source": "Weekly Pivot", "tier": "major", "strength": 3.0},
                {"price": 113.0, "source": "20EMA", "tier": "near"},
            ],
            "resistances": [{"price": 130.0, "source": "All-Time High", "tier": "major"}],
            "current_price": 115.0,
        }
        tiers = build_sr_tiers(all_levels, 115.0)
        self.assertEqual(len(tiers["major_supports"]), 1)
        self.assertEqual(tiers["key_below_top3"][0]["price"], 90.0)
        self.assertEqual(tiers["key_above_top3"][0]["price"], 130.0)

    def test_tier_of_504d_poc_major(self):
        """504d Volume POC should tier as major."""
        level_504d = {"source": "Volume POC (504d)", "type": "VolumeProfile"}
        self.assertEqual(_tier_of(level_504d), "major")

    def test_tier_of_126d_poc_intermediate(self):
        """126d Volume POC (without 504d marker) should tier as intermediate."""
        level_126d = {"source": "Volume POC", "type": "VolumeProfile"}
        self.assertEqual(_tier_of(level_126d), "intermediate")

    def test_liquidity_levels_emits_504d_with_suffix(self):
        """liquidity_levels should emit 504d volume profile with (504d) name suffix."""
        liquidity = {
            "anchored_vwap": {"levels": []},
            "volume_profile": {
                "levels": [
                    {"price": 100.0, "name": "Volume POC", "role": "support", "type": "VolumeProfile", "distance_pct": 0.0},
                ]
            },
            "volume_profile_full": {
                "levels": [
                    {"price": 102.0, "name": "Volume POC", "role": "resistance", "type": "VolumeProfile", "distance_pct": 2.0},
                    {"price": 99.0, "name": "Value Area Low", "role": "support", "type": "VolumeProfile", "distance_pct": 1.0},
                ]
            },
        }
        levels = liquidity_levels(liquidity)
        # Should have 3 levels: 1 from 126d + 2 from 504d
        self.assertEqual(len(levels), 3)
        # Find the 504d POC
        poc_504d = next((lvl for lvl in levels if "(504d)" in lvl.get("name", "")), None)
        self.assertIsNotNone(poc_504d, "504d POC should be present with (504d) suffix")
        self.assertEqual(poc_504d["name"], "Volume POC (504d)")
        self.assertEqual(poc_504d["price"], 102.0)
        # Verify 126d POC is still there without suffix
        poc_126d = next((lvl for lvl in levels if lvl.get("name") == "Volume POC"), None)
        self.assertIsNotNone(poc_126d, "126d POC should be present without suffix")
        self.assertEqual(poc_126d["price"], 100.0)

    def test_liquidity_levels_guards_none_volume_profile_full(self):
        """Regression: liquidity_levels should not crash when volume_profile_full is None (short-history symbols)."""
        liquidity = {
            "anchored_vwap": {"levels": []},
            "volume_profile": {
                "levels": [
                    {"price": 100.0, "name": "Volume POC", "role": "support", "type": "VolumeProfile", "distance_pct": 0.0},
                ]
            },
            "volume_profile_full": None,  # IPOs, <127d data
        }
        # Should not raise AttributeError
        levels = liquidity_levels(liquidity)
        # Should only have the 126d level
        self.assertEqual(len(levels), 1)
        self.assertEqual(levels[0]["name"], "Volume POC")
        self.assertEqual(levels[0]["price"], 100.0)

    def test_liquidity_levels_guards_both_none(self):
        """Regression: liquidity_levels should not crash when both profiles are None."""
        liquidity = {
            "anchored_vwap": {"levels": []},
            "volume_profile": None,
            "volume_profile_full": None,
        }
        # Should not raise AttributeError
        levels = liquidity_levels(liquidity)
        # Should return empty list (no levels from any source)
        self.assertEqual(len(levels), 0)
